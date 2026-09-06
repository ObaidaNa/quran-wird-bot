"""Daily backup of the SQLite database.

The whole khatmah — every group's page, every streak, every subscription — is
one small file. Copying it with `cp` while the bot is running can capture a
torn write, so this uses SQLite's own online backup API, which takes a
consistent snapshot of a live database without stopping it.

The job runs inside the bot rather than as a separate cron entry so that a
deployment is one unit: `systemctl start` gives you backups too.
"""

from __future__ import annotations

import datetime as dt
import logging
import sqlite3
from pathlib import Path
from zoneinfo import ZoneInfo

from telegram.ext import ContextTypes, JobQueue

from ..config import Settings

log = logging.getLogger(__name__)

BACKUP_JOB = "backup"
STAMP_FORMAT = "%Y%m%d-%H%M%S"
PREFIX = "bot-"
SUFFIX = ".db"


def backup_now(
    db_path: Path, backup_dir: Path, *, keep: int = 7, now: dt.datetime | None = None
) -> Path | None:
    """Snapshot the database, prune old copies, and return the new file.

    Returns None if there is no database to copy yet, which is the normal state
    of a bot that has never run.
    """
    if not db_path.exists():
        log.warning("no database at %s; nothing to back up", db_path)
        return None

    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = (now or dt.datetime.now()).strftime(STAMP_FORMAT)
    target = backup_dir / f"{PREFIX}{stamp}{SUFFIX}"

    # Read-only source: a backup must never be the thing that writes to the
    # database it is protecting.
    source = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        destination = sqlite3.connect(target)
        try:
            with destination:
                source.backup(destination)
        finally:
            destination.close()
    finally:
        source.close()

    removed = prune(backup_dir, keep=keep)
    log.info(
        "database backed up to %s (%s KB); %s old copy(ies) removed",
        target.name,
        target.stat().st_size // 1024,
        removed,
    )
    return target


def existing_backups(backup_dir: Path) -> list[Path]:
    """Backups oldest first. The timestamped names sort chronologically."""
    if not backup_dir.exists():
        return []
    return sorted(backup_dir.glob(f"{PREFIX}*{SUFFIX}"))


def prune(backup_dir: Path, *, keep: int) -> int:
    """Delete all but the newest `keep` backups. Returns how many went."""
    if keep < 1:
        return 0
    backups = existing_backups(backup_dir)
    doomed = backups[: max(0, len(backups) - keep)]
    for path in doomed:
        path.unlink(missing_ok=True)
    return len(doomed)


async def job_backup(context: ContextTypes.DEFAULT_TYPE) -> None:
    """JobQueue entry point. One backup for the whole bot, not one per group."""
    settings: Settings = context.job.data
    try:
        backup_now(settings.db_path, settings.backup_dir, keep=settings.backup_keep)
    except (sqlite3.Error, OSError):
        # A failed backup must never take the bot down with it; tomorrow's run
        # will try again.
        log.exception("the database backup failed")


def schedule_backup(job_queue: JobQueue, settings: Settings) -> bool:
    """Queue the nightly backup. Returns whether it was scheduled."""
    for job in job_queue.jobs(pattern=f"^{BACKUP_JOB}$"):
        job.schedule_removal()

    if not settings.backup_enabled:
        log.info("database backups are disabled")
        return False

    at = dt.time(
        hour=settings.backup_time.hour,
        minute=settings.backup_time.minute,
        tzinfo=ZoneInfo(settings.default_timezone),
    )
    job_queue.run_daily(job_backup, time=at, data=settings, name=BACKUP_JOB)
    log.info(
        "database backup scheduled daily at %s %s, keeping %s copies in %s",
        settings.backup_time.strftime("%H:%M"),
        settings.default_timezone,
        settings.backup_keep,
        settings.backup_dir,
    )
    return True
