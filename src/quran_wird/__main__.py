"""Entry point: uv run -m quran_wird"""

from __future__ import annotations

import logging
import sys

from .app import build_application, run
from .config import load_settings
from .db.session import run_migrations


def setup_logging(level: str) -> None:
    logging.basicConfig(
        format="%(asctime)s %(levelname)-8s %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        level=getattr(logging, level.upper(), logging.INFO),
    )
    # httpx logs every Bot API call at INFO, which drowns out everything else.
    logging.getLogger("httpx").setLevel(logging.WARNING)


def main() -> int:
    try:
        settings = load_settings()
    except Exception as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        print("copy .env.example to .env and set BOT_TOKEN", file=sys.stderr)
        return 1

    setup_logging(settings.log_level)
    log = logging.getLogger("quran_wird")

    if not settings.pages_dir.exists():
        log.warning(
            "page images missing at %s - run: uv run scripts/build_pages.py",
            settings.pages_dir,
        )

    run_migrations(settings.db_path)
    log.info("database ready at %s", settings.db_path)

    run(build_application(settings), settings)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
