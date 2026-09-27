"""Explicit offline import/archive commands. Defaults to dry-run."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from services.runtime_maintenance import archive_runtime_data, import_legacy_sqlite  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    importer = sub.add_parser("import-sqlite")
    importer.add_argument("--source", required=True, type=Path)
    importer.add_argument("--mapping", required=True, type=Path)
    importer.add_argument("--apply", action="store_true")
    archive = sub.add_parser("archive")
    archive.add_argument("--tenant", required=True)
    archive.add_argument("--before", required=True)
    archive.add_argument("--output", required=True, type=Path)
    archive.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if args.command == "import-sqlite":
        report = import_legacy_sqlite(
            args.source, json.loads(args.mapping.read_text(encoding="utf-8")), apply=args.apply
        )
    else:
        report = archive_runtime_data(args.tenant, args.before, args.output, apply=args.apply)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
