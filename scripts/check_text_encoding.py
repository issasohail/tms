"""Find damaged UTF-8 text in project source; optionally repair known cases.

Run: python scripts/check_text_encoding.py [--fix-known]
"""

import argparse
import os
import re
import shutil
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE_SUFFIXES = {
    ".py", ".html", ".js", ".css", ".md", ".txt", ".json",
    ".yml", ".yaml", ".toml", ".xml", ".sh", ".bat", ".ps1",
}
SKIP_DIRECTORIES = {
    ".git", ".venv", "venv", "node_modules", "__pycache__",
    "media", "staticfiles", "backups", ".codex_backups", ".codex_tmp",
}
SKIP_FILES = {"iesco_test_output.txt"}  # Generated UTF-16 test output.
# These sequences are UTF-8 bytes that were decoded as Windows-1252 and saved again.
DAMAGED_TEXT = re.compile(
    r"(?:\u00e2[\u0080-\u02ff\u2000-\u206f]"
    r"|\u00c3[\u0080-\u02ff\u2000-\u206f]"
    r"|\u00c2[\u0080-\u02ff\u2000-\u206f]"
    r"|\u00f0\u0178|\ufffd)"
)

KNOWN_REPLACEMENTS = {
    "core/templates/core/pending_approval_detail.html": {
        "\u00e2\u20ac\u201d": "&mdash;",
    },
    "smart_meter/templates/smart_meter/meter_detail.html": {
        "\u00e2\u20ac\u201d": "&mdash;",
    },
    "smart_meter/templates/smart_meter/energy_system_detail.html": {
        "\u00e2\u20ac\u201d": "&mdash;",
        "\u00e2\u02c6\u2019": "&minus;",
    },
    "smart_meter/models.py": {
        "\u00e2\u2020\u2019": "->",
        "\u00e2\u20ac\u201d": "-",
        "\u00e2\u201a\u00b9": "Rs. ",
        "\u00e2\u0161\u00a1": "[power]",
        "\u00e2\u20ac\u2122": "'",
    },
    "smart_meter/management/commands/meter_listener.py": {
        "\u00e2\u2020\u2019": "->",
        "\u00e2\u00ac\u2021\u00ef\u00b8\u008f": "[raw]",
        "\u00f0\u0178\u2019\u201c": "[heartbeat]",
        "\u00f0\u0178\u00a7\u00b1": "[frame]",
        "\u00f0\u0178\u00a7\u00a9": "[parse]",
        "\u00f0\u0178\u201c\u00a5": "[received]",
        "\u00f0\u0178\u201c\u00a4": "[sent]",
        "\u00f0\u0178\u2020\u2022": "[new]",
        "\u00e2\u0153\u2026": "[ok]",
        "\u00f0\u0178\u2014\u201a\u00ef\u00b8\u008f": "[poller]",
        "\u00f0\u0178\u201d\u00a7": "[debug]",
    },
}


def source_files():
    for directory, subdirs, filenames in os.walk(ROOT):
        subdirs[:] = [name for name in subdirs if name not in SKIP_DIRECTORIES]
        for name in filenames:
            if name in SKIP_FILES or name.endswith(".bak") or ".backup_" in name:
                continue
            path = Path(directory, name)
            if path.suffix.lower() in SOURCE_SUFFIXES:
                yield path


def scan_project():
    """Return path:line diagnostics for invalid UTF-8 or familiar damaged text."""
    issues = []
    for path in source_files():
        relative = path.relative_to(ROOT)
        raw = path.read_bytes()
        if raw.startswith(b"%PDF-"):  # Legacy vendor PDF has a .py filename.
            continue
        try:
            content = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            issues.append(f"{relative}: invalid UTF-8 at byte {exc.start}")
            continue
        for line_number, line in enumerate(content.splitlines(), 1):
            if DAMAGED_TEXT.search(line):
                issues.append(f"{relative}:{line_number}: damaged text")
    return issues


def fix_known():
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    for relative, replacements in KNOWN_REPLACEMENTS.items():
        path = ROOT / relative
        original = path.read_bytes()
        content = original.decode("utf-8")
        for damaged, replacement in replacements.items():
            content = content.replace(damaged, replacement)
        repaired = content.encode("utf-8")
        if repaired != original:
            shutil.copy2(path, path.with_name(f"{path.name}.{stamp}.bak"))
            path.write_bytes(repaired)
            print(f"Repaired {relative}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fix-known", action="store_true", help="Repair the reviewed source strings")
    args = parser.parse_args()
    if args.fix_known:
        fix_known()
    issues = scan_project()
    for issue in issues:
        print(issue)
    if issues:
        print(f"Found {len(issues)} source encoding issue(s).")
        return 1
    print("Source text is UTF-8 with no known damaged sequences.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
