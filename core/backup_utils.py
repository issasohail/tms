import codecs
import json
import gzip
import os
import shutil
import sqlite3
import subprocess
import tempfile
import time
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from django.conf import settings
from django.db import connections


SETTINGS_FILE_NAME = "backup_settings.json"
BACKUP_MANIFEST_FILE_NAME = "backup_manifest.json"
MIN_PROTECTED_BACKUPS = 3
MYSQL_CONCURRENT_DDL_RETRY_ATTEMPTS = 3


def default_backup_settings():
    root = getattr(settings, "TMS_BACKUP_ROOT", settings.BASE_DIR / "tms_backups")
    return {
        "backup_root": str(root),
        "retention_count": 3,
        "auto_delete_old_backups": True,
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
    def file_exists(self):
        return Path(self.display_path).is_file()

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


def _load_backup_manifest(root):
    path = Path(root) / BACKUP_MANIFEST_FILE_NAME
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_backup_manifest(root, manifest):
    path = Path(root) / BACKUP_MANIFEST_FILE_NAME
    path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")


def _backup_type_from_name(name):
    name = name.lower()
    if name.startswith("tms_db_backup_"):
        return "db"
    if name.startswith("tms_media_backup_"):
        return "media"
    if name.startswith("tms_code_backup_"):
        return "code"
    if name.startswith("tms_full_backup_"):
        return "full"
    return None


def _remove_manifest_entry(root, backup_id):
    manifest = _load_backup_manifest(root)
    if manifest.pop(backup_id, None) is not None:
        _save_backup_manifest(root, manifest)


def list_backups(config=None):
    root = ensure_backup_root(config)
    manifest = _load_backup_manifest(root)
    for path in root.iterdir():
        if not path.is_file() or path.name in {SETTINGS_FILE_NAME, BACKUP_MANIFEST_FILE_NAME}:
            continue
        btype = _backup_type_from_name(path.name)
        if not btype:
            continue
        stat = path.stat()
        manifest[path.name] = {
            "backup_type": btype,
            "size": stat.st_size,
            "created_at": datetime.fromtimestamp(stat.st_mtime).isoformat(),
            "storage_mode": "zip" if path.suffix.lower() == ".zip" else path.suffix.lower().lstrip("."),
        }
    _save_backup_manifest(root, manifest)

    items = []
    for backup_id, metadata in manifest.items():
        try:
            created_at = datetime.fromisoformat(metadata["created_at"])
        except (KeyError, TypeError, ValueError):
            created_at = datetime.fromtimestamp(0)
        path = root / backup_id
        items.append(BackupItem(
            id=backup_id,
            name=Path(backup_id).stem,
            backup_type=metadata.get("backup_type") or _backup_type_from_name(backup_id) or "unknown",
            status="success" if path.is_file() else "missing",
            size=metadata.get("size") or 0,
            created_at=created_at,
            display_path=str(path),
            storage_mode=metadata.get("storage_mode") or Path(backup_id).suffix.lower().lstrip("."),
        ))
    return sorted(items, key=lambda item: item.created_at, reverse=True)


def choices_for(backups, backup_type):
    choices = [("", "----------")]
    choices.extend((b.id, f"{b.name} ({b.created_at:%Y-%m-%d %H:%M})") for b in backups if b.backup_type == backup_type)
    return choices


def retention_count(config):
    return max(int(config.get("retention_count") or MIN_PROTECTED_BACKUPS), 1)


def protected_backup_ids(backups, keep_count=MIN_PROTECTED_BACKUPS):
    """Protect the newest existing backup files, regardless of backup type."""
    return {
        backup.id
        for backup in [item for item in backups if item.file_exists][:max(int(keep_count), 1)]
    }


def backup_storage_summary(config):
    root = ensure_backup_root(config)
    usage = shutil.disk_usage(root)
    backup_bytes = sum(
        backup.size for backup in list_backups(config) if backup.file_exists
    )
    return {
        "backup_bytes": backup_bytes,
        "disk_total": usage.total,
        "disk_used": usage.used,
        "disk_free": usage.free,
    }


def purge_old_backups(config, keep_count=None):
    root = ensure_backup_root(config)
    backups = list_backups(config)
    keep_count = retention_count(config) if keep_count is None else max(int(keep_count), 1)
    protected = protected_backup_ids(backups, keep_count=keep_count)
    deleted = []
    reclaimed_bytes = 0
    for backup in backups:
        if not backup.file_exists or backup.id in protected:
            continue
        path = Path(backup.display_path).resolve()
        if root.resolve() not in path.parents or not path.is_file():
            continue
        path.unlink()
        _remove_manifest_entry(root, backup.id)
        deleted.append(backup.id)
        reclaimed_bytes += backup.size
    return {"deleted": deleted, "reclaimed_bytes": reclaimed_bytes}


def _timestamp():
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def detect_uploaded_backup_type(uploaded_file):
    name = str(getattr(uploaded_file, "name", "") or "").lower()
    if name.endswith((".sql", ".sql.gz", ".sqlite3")):
        return "db"
    if not name.endswith(".zip"):
        raise RuntimeError(
            "Backup uploads must be .sql, .sql.gz, .sqlite3, or .zip files."
        )

    original_position = uploaded_file.tell() if hasattr(uploaded_file, "tell") else 0
    try:
        uploaded_file.seek(0)
        with zipfile.ZipFile(uploaded_file) as archive:
            names = [name.replace("\\", "/").lower() for name in archive.namelist()]
    except (zipfile.BadZipFile, OSError) as exc:
        raise RuntimeError("The uploaded ZIP file is not a valid backup archive.") from exc
    finally:
        uploaded_file.seek(original_position)

    return "full" if any(name.startswith("full/") for name in names) else "media"


def save_uploaded_backup(config, backup_type, uploaded_file):
    root = ensure_backup_root(config)
    lower_name = uploaded_file.name.lower()
    suffix = ".sql.gz" if lower_name.endswith(".sql.gz") else Path(lower_name).suffix
    prefixes = {
        "db": "tms_db_backup",
        "media": "tms_media_backup",
        "full": "tms_full_backup",
    }
    if backup_type not in prefixes:
        raise RuntimeError("Invalid backup type.")
    if backup_type == "db" and suffix not in {".sql", ".sql.gz", ".sqlite3"}:
        raise RuntimeError("Database backup uploads must be .sql, .sql.gz, or .sqlite3.")
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


def _mysql_client_environment(db):
    env = os.environ.copy()
    if db.get("PASSWORD"):
        # Avoid exposing database credentials in the operating-system process list.
        env["MYSQL_PWD"] = str(db["PASSWORD"])
    return env


def _mysqldump_compatibility_args(mysqldump_path):
    """Return privilege-safe options supported by the installed client."""
    try:
        help_result = subprocess.run(
            [mysqldump_path, "--help"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            check=False,
        )
        help_text = help_result.stdout or ""
    except OSError:
        help_text = ""
    args = []
    if "--no-tablespaces" in help_text:
        args.append("--no-tablespaces")
    if "--masking-policies" in help_text:
        args.append("--skip-masking-policies")
    return args


def _run_mysql_command(cmd, *, operation, stdin=None, stdout=None, env=None, text=True):
    try:
        return subprocess.run(
            cmd,
            stdin=stdin,
            stdout=stdout,
            stderr=subprocess.PIPE,
            text=text,
            check=True,
            env=env,
        )
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr or (b"" if not text else "")
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", errors="replace")
        detail = " ".join(stderr.strip().split())
        if not detail:
            detail = f"MySQL client exited with code {exc.returncode}."
        raise RuntimeError(f"{operation} failed: {detail}") from exc


def _run_mysql_dump_to_gzip(cmd, target, *, env=None):
    """Stream mysqldump stdout through gzip without buffering the dump in memory."""
    target = Path(target)
    with tempfile.TemporaryFile(mode="w+b") as stderr_file:
        try:
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=stderr_file,
                env=env,
            )
        except OSError as exc:
            raise RuntimeError(f"Database backup failed: {exc}") from exc

        try:
            if process.stdout is None:
                raise RuntimeError("Database backup failed: mysqldump stdout was unavailable.")
            with process.stdout, gzip.open(target, "wb", compresslevel=6) as output:
                shutil.copyfileobj(process.stdout, output, length=1024 * 1024)
            return_code = process.wait()
        except Exception:
            process.kill()
            process.wait()
            target.unlink(missing_ok=True)
            raise

        if return_code != 0:
            target.unlink(missing_ok=True)
            stderr_file.seek(0)
            detail = " ".join(
                stderr_file.read().decode("utf-8", errors="replace").strip().split()
            )
            if not detail:
                detail = f"MySQL client exited with code {return_code}."
            raise RuntimeError(f"Database backup failed: {detail}")


def _copy_utf8_sanitized_sql(source, target, chunk_size=1024 * 1024):
    """Copy SQL bytes while replacing only malformed UTF-8 byte sequences."""
    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
    while True:
        chunk = source.read(chunk_size)
        if not chunk:
            break
        target.write(decoder.decode(chunk).encode("utf-8"))
    target.write(decoder.decode(b"", final=True).encode("utf-8"))


def _is_mysql_concurrent_ddl_error(exc):
    detail = str(exc).lower()
    return "concurrent ddl" in detail or "(1684)" in detail or " 1684" in detail


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
    compress = bool(config.get("compress_backups", True))
    target = root / f"tms_db_backup_{ts}.sql{'.gz' if compress else ''}"
    cmd = [
        mysqldump_path,
        "--single-transaction",
        "--quick",
        *_mysqldump_compatibility_args(mysqldump_path),
        f"--host={db.get('HOST') or 'localhost'}",
        f"--port={db.get('PORT') or 3306}",
        f"--user={db.get('USER') or ''}",
    ]
    cmd.append(db["NAME"])
    for attempt in range(1, MYSQL_CONCURRENT_DDL_RETRY_ATTEMPTS + 1):
        try:
            if compress:
                _run_mysql_dump_to_gzip(
                    cmd,
                    target,
                    env=_mysql_client_environment(db),
                )
            else:
                with target.open("w", encoding="utf-8") as output:
                    _run_mysql_command(
                        cmd,
                        operation="Database backup",
                        stdout=output,
                        env=_mysql_client_environment(db),
                    )
            break
        except RuntimeError as exc:
            target.unlink(missing_ok=True)
            if not _is_mysql_concurrent_ddl_error(exc):
                raise
            if attempt >= MYSQL_CONCURRENT_DDL_RETRY_ATTEMPTS:
                raise RuntimeError(
                    "Database backup could not obtain a stable schema after 3 attempts because "
                    "a concurrent DDL statement was still running. Wait for migrations or ALTER "
                    "TABLE operations to finish, then retry the restore. The database restore was "
                    "not started."
                ) from exc
            time.sleep(attempt * 2)
        except Exception:
            target.unlink(missing_ok=True)
            raise
    return target


def _zip_directory(target, source, skip_names=None):
    source = Path(source)
    skip_names = set(skip_names or ())
    with zipfile.ZipFile(
        target,
        "w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=6,
        allowZip64=True,
    ) as archive:
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
    try:
        with zipfile.ZipFile(target, "w", allowZip64=True) as archive:
            for piece in pieces:
                # ZIP/GZIP pieces are already compressed. Storing them avoids wasting CPU
                # trying to compress the same bytes a second time.
                compression = (
                    zipfile.ZIP_STORED
                    if piece.suffix.lower() in {".zip", ".gz"}
                    else zipfile.ZIP_DEFLATED
                )
                archive.write(piece, f"full/{piece.name}", compress_type=compression)
    finally:
        for piece in pieces:
            piece.unlink(missing_ok=True)
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
        "--binary-mode=1",
        "--default-character-set=utf8mb4",
        f"--host={db.get('HOST') or 'localhost'}",
        f"--port={db.get('PORT') or 3306}",
        f"--user={db.get('USER') or ''}",
    ]
    cmd.append(db["NAME"])
    input_context = (
        gzip.open(path, "rb")
        if path.suffix.lower() == ".gz"
        else path.open("rb")
    )
    # Validate/decompress the complete dump before changing the database. Some
    # third-party dump tools can emit isolated non-UTF-8 bytes inside text
    # columns; MySQL rejects the entire INSERT unless those bytes are replaced.
    with input_context as input_sql, tempfile.TemporaryFile(mode="w+b") as sanitized_sql:
        _copy_utf8_sanitized_sql(input_sql, sanitized_sql)
        sanitized_sql.seek(0)

        # Release this request's database connection before the external client
        # starts dropping and recreating tables. Django will reconnect on demand.
        connections.close_all()
        _run_mysql_command(
            cmd,
            operation="Database restore",
            stdin=sanitized_sql,
            env=_mysql_client_environment(db),
            text=False,
        )


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


def delete_backup(config, backup_id):
    backups = list_backups(config)
    item = next((backup for backup in backups if backup.id == backup_id), None)
    if not item:
        raise RuntimeError("Selected backup was not found.")
    protected_ids = protected_backup_ids(backups, keep_count=retention_count(config))
    if item.id in protected_ids:
        raise RuntimeError(
            f"The latest {retention_count(config)} backups are protected and cannot be deleted."
        )
    root = ensure_backup_root(config).resolve()
    path = Path(item.display_path).resolve()
    if root not in path.parents or not path.is_file():
        raise RuntimeError("Invalid backup path.")
    path.unlink()
    _remove_manifest_entry(root, item.id)
    return item


def prune_old_backups(config):
    if not config.get("auto_delete_old_backups", True):
        return {"deleted": [], "reclaimed_bytes": 0}
    return purge_old_backups(config, keep_count=retention_count(config))
