#!/usr/bin/env python
"""Back up the SQLite database by hand.

The bot already backs itself up nightly; this is for the moments before a
migration or a risky change, when you want a copy right now.

    uv run scripts/backup.py                 # into the configured backup dir
    uv run scripts/backup.py /mnt/usb        # somewhere else
    uv run scripts/backup.py --keep 30       # prune to a different depth
    uv run scripts/backup.py --list          # what is already there
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from quran_wird.config import load_settings  # noqa: E402
from quran_wird.jobs.backup import backup_now, existing_backups  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", nargs="?", type=Path, help="where to write the copy")
    parser.add_argument("--keep", type=int, help="how many copies to keep")
    parser.add_argument("--list", action="store_true", help="list existing backups and exit")
    args = parser.parse_args()

    settings = load_settings()
    target_dir = args.directory or settings.backup_dir
    keep = args.keep if args.keep is not None else settings.backup_keep

    if args.list:
        backups = existing_backups(target_dir)
        if not backups:
            print(f"no backups in {target_dir}")
            return 0
        for path in backups:
            print(f"{path}  {path.stat().st_size // 1024} KB")
        return 0

    written = backup_now(settings.db_path, target_dir, keep=keep)
    if written is None:
        print(f"no database at {settings.db_path}", file=sys.stderr)
        return 1

    print(f"{written}  {written.stat().st_size // 1024} KB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
