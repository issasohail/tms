import json
import os
import shutil
import sqlite3
import subprocess
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from django.conf import settings


SETTINGS_FILE_NAME = "backup_settings.json"


def default_backup_settings():
    root = getattr(settings, "TMS_BACKUP_ROOT", settings.BASE_DIR / "tms_backups")
    return {
        "backup_root": str(root),
        "retention_count": 10,
        "mysqldump_path": getattr(settings, "MYSQLDUMP_PATH", "mysqldump"),
        "mysql_path": getattr(settings, "MYSQL_PATH", "mysql"),
        "include_db_in_full": True,
        "include_media_in_full": True,
        "include_code_in_full": False,
        "compress_backups": True,
        "enable_db_backup": True,
        "enable_media_backup": True,
        "enable_code_backup": False,
        "enable_full_backup": True,
        "fresh_reset_enabled": False,
    }


def _settings_file(root=None):
    root_path = Path(root or default_backup_settings()["backup_root"])
    return root_path / SETTINGS_FILE_NAME


def load_backup_settings():
    data = default_backup_settings()
    path = _settings_file(data["backup_root"])
    if path.exists():
        try:
            data.update(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            pass
    return data


def save_backup_settings(data):
    root = Path(data["backup_root"])
    root.mkdir(parents=True, exist_ok=True)
    path = _settings_file(root)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


@dataclass
class BackupItem:
    id: str
    name: str
    backup_type: str
    status: str
    size: int
    created_at: datetime
    display_path: str
    storage_mode: str = "zip"

    @property
    def human_size(self):
        size = float(self.size or 0)
        for unit in ("B", "KB", "MB", "GB"):
            if size < 1024 or unit == "GB":
                return f"{size:.2f} {unit}" if unit != "B" else f"{int(size)} B"
            size /= 1024

    def get_backup_type_display(self):
        return {
            "db": "Database",
            "media": "Media",
            "code": "Code",
            "full": "Full",
        }.get(self.backup_type, self.backup_type.title())

    def get_storage_mode_display(self):
        return self.storage_mode.title()


def ensure_backup_root(config=None):
    config = config or load_backup_settings()
    root = Path(config["backup_root"])
    root.mkdir(parents=True, exist_ok=True)
    return root


def list_backups(config=None):
    root = ensure_backup_root(config)
    items = []
    for path in root.iterdir():
        if not path.is_file() or path.name == SETTINGS_FILE_NAME:
            continue
        name = path.name.lower()
        if name.startswith("tms_db_backup_"):
            btype = "db"
        elif name.startswith("tms_media_backup_"):
            btype = "media"
        elif name.startswith("tms_code_backup_"):
            btype = "code"
        elif name.startswith("tms_full_backup_"):
            btype = "full"
        else:
            continue
        stat = path.stat()
        items.append(BackupItem(
            id=path.name,
            name=path.stem,
            backup_type=btype,
            status="success",
            size=stat.st_size,
            created_at=datetime.fromtimestamp(stat.st_mtime),
            display_path=str(path),
            storage_mode="zip" if path.suffix.lower() == ".zip" else path.suffix.lower().lstrip("."),
        ))
    return sorted(items, key=lambda item: item.created_at, reverse=True)


def choices_for(backups, backup_type):
    choices = [("", "----------")]
    choices.extend((b.id, f"{b.name} ({b.created_at:%Y-%m-%d %H:%M})") for b in backups if b.backup_type == backup_type)
    return choices


def _timestamp():
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def save_uploaded_backup(config, backup_type, uploaded_file):
    root = ensure_backup_root(config)
    suffix = Path(uploaded_file.name).suffix.lower()
    prefixes = {
        "db": "tms_db_backup",
        "media": "tms_media_backup",
        "full": "tms_full_backup",
    }
    if backup_type not in prefixes:
        raise RuntimeError("Invalid backup type.")
    if backup_type == "db" and suffix not in {".sql", ".sqlite3"}:
        raise RuntimeError("Database backup uploads must be .sql or .sqlite3.")
    if backup_type in {"media", "full"} and suffix != ".zip":
        raise RuntimeError("Media and full backup uploads must be .zip files.")
    target = root / f"{prefixes[backup_type]}_uploaded_{_timestamp()}{suffix}"
    with target.open("wb") as out:
        for chunk in uploaded_file.chunks():
            out.write(chunk)
    return target


def _db_settings():
    return settings.DATABASES["default"]


def _resolve_mysql_executable(configured_path, executable_name, label):
    configured_path = (configured_path or executable_name).strip()
    direct_path = Path(configured_path)
    if direct_path.exists():
        return str(direct_path)

    found = shutil.which(configured_path)
    if found:
        return found

    candidate_paths = [
        Path("/usr/bin") / executable_name,
        Path("/usr/local/bin") / executable_name,
        Path("/opt/homebrew/bin") / executable_name,
    ]
    if os.name == "nt":
        exe_name = executable_name if executable_name.lower().endswith(".exe") else f"{executable_name}.exe"
        candidate_paths.extend([
            Path("C:/Program Files/MySQL"),
            Path("C:/Program Files (x86)/MySQL"),
            Path("C:/Program Files/MariaDB"),
            Path("C:/Program Files (x86)/MariaDB"),
        ])
        discovered = []
        for base_path in candidate_paths[3:]:
            if base_path.exists():
                discovered.extend(base_path.glob(f"**/{exe_name}"))
        candidate_paths.extend(discovered)

    for path in candidate_paths:
        if path.is_file():
            return str(path)

    raise RuntimeError(
        f"{label} was not found. Install MySQL client tools or set the full {label} "
        "in Backup Settings, for example C:\\Program Files\\MySQL\\MySQL Server 9.7\\bin\\"
        f"{executable_name}.exe."
    )


def create_db_backup(config):
    if not config.get("enable_db_backup"):
        raise RuntimeError("Database backup is disabled in backup settings.")
    root = ensure_backup_root(config)
    db = _db_settings()
    ts = _timestamp()
    if "sqlite3" in db["ENGINE"]:
        src = Path(db["NAME"])
        target = root / f"tms_db_backup_{ts}.sqlite3"
        with sqlite3.connect(src) as source, sqlite3.connect(target) as dest:
            source.backup(dest)
        return target

    mysqldump_path = _resolve_mysql_executable(
        config.get("mysqldump_path"),
        "mysqldump",
        "mysqldump path",
    )
    target = root / f"tms_db_backup_{ts}.sql"
    cmd = [
        mysqldump_path,
        "--single-transaction",
        "--quick",
        f"--host={db.get('HOST') or 'localhost'}",
        f"--port={db.get('PORT') or 3306}",
        f"--user={db.get('USER') or ''}",
    ]
    if db.get("PASSWORD"):
        cmd.append(f"--password={db['PASSWORD']}")
    cmd.append(db["NAME"])
    with target.open("w", encoding="utf-8") as output:
        subprocess.run(cmd, stdout=output, stderr=subprocess.PIPE, text=True, check=True)
    return target


def _zip_directory(target, source, skip_names=None):
    source = Path(source)
    skip_names = set(skip_names or ())
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in source.rglob("*"):
            if any(part in skip_names for part in path.parts):
                continue
            if path.is_file():
                archive.write(path, path.relative_to(source))
    return target


def create_media_backup(config):
    if not config.get("enable_media_backup"):
        raise RuntimeError("Media backup is disabled in backup settings.")
    root = ensure_backup_root(config)
    target = root / f"tms_media_backup_{_timestamp()}.zip"
    return _zip_directory(target, settings.MEDIA_ROOT)


def create_code_backup(config):
    if not config.get("enable_code_backup"):
        raise RuntimeError("Code backup is disabled in backup settings.")
    root = ensure_backup_root(config)
    target = root / f"tms_code_backup_{_timestamp()}.zip"
    skip = {".git", ".venv", "__pycache__", "media", "staticfiles", root.name}
    return _zip_directory(target, settings.BASE_DIR, skip_names=skip)


def create_full_backup(config):
    if not config.get("enable_full_backup"):
        raise RuntimeError("Full backup is disabled in backup settings.")
    root = ensure_backup_root(config)
    target = root / f"tms_full_backup_{_timestamp()}.zip"
    pieces = []
    if config.get("include_db_in_full"):
        pieces.append(create_db_backup({**config, "enable_db_backup": True}))
    if config.get("include_media_in_full"):
        pieces.append(create_media_backup({**config, "enable_media_backup": True}))
    if config.get("include_code_in_full"):
        pieces.append(create_code_backup({**config, "enable_code_backup": True}))
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for piece in pieces:
            archive.write(piece, f"full/{piece.name}")
    return target


def _backup_path(config, backup_id):
    root = ensure_backup_root(config)
    candidate = (root / backup_id).resolve()
    if root.resolve() not in candidate.parents and candidate != root.resolve():
        raise RuntimeError("Invalid backup path.")
    if not candidate.exists():
        raise RuntimeError("Selected backup was not found.")
    return candidate


def restore_database(config, backup_id):
    path = _backup_path(config, backup_id)
    db = _db_settings()
    if "sqlite3" in db["ENGINE"]:
        shutil.copy2(path, db["NAME"])
        return
    if path.suffix.lower() == ".zip":
        raise RuntimeError("Choose a database .sql backup, not a zip archive.")
    mysql_path = _resolve_mysql_executable(
        config.get("mysql_path"),
        "mysql",
        "mysql path",
    )
    cmd = [
        mysql_path,
        f"--host={db.get('HOST') or 'localhost'}",
        f"--port={db.get('PORT') or 3306}",
        f"--user={db.get('USER') or ''}",
    ]
    if db.get("PASSWORD"):
        cmd.append(f"--password={db['PASSWORD']}")
    cmd.append(db["NAME"])
    with path.open("r", encoding="utf-8") as input_sql:
        subprocess.run(cmd, stdin=input_sql, stderr=subprocess.PIPE, text=True, check=True)


def restore_media(config, backup_id):
    path = _backup_path(config, backup_id)
    media_root = Path(settings.MEDIA_ROOT)
    media_root.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path) as archive:
        archive.extractall(media_root)


def restore_full(config, backup_id):
    path = _backup_path(config, backup_id)
    temp_dir = ensure_backup_root(config) / f"_restore_{_timestamp()}"
    temp_dir.mkdir(parents=True, exist_ok=True)
    try:
        with zipfile.ZipFile(path) as archive:
            archive.extractall(temp_dir)
        for piece in (temp_dir / "full").glob("tms_db_backup_*"):
            restore_database(config, str(piece))
            break
        for piece in (temp_dir / "full").glob("tms_media_backup_*.zip"):
            with zipfile.ZipFile(piece) as archive:
                archive.extractall(settings.MEDIA_ROOT)
            break
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def prune_old_backups(config):
    retention = int(config.get("retention_count") or 10)
    backups = list_backups(config)
    for item in backups[retention:]:
        try:
            Path(item.display_path).unlink()
        except OSError:
            pass
