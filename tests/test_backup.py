"""Database backups: consistency while running, and rotation."""

from __future__ import annotations

import datetime as dt
import sqlite3

from quran_wird.jobs.backup import backup_now, existing_backups, prune


def make_db(path, rows: int = 3) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE groups (chat_id INTEGER PRIMARY KEY, page INTEGER)")
    conn.executemany("INSERT INTO groups VALUES (?, ?)", [(i, i * 10) for i in range(rows)])
    conn.commit()
    conn.close()


def read_pages(path) -> list[int]:
    conn = sqlite3.connect(path)
    try:
        return [row[0] for row in conn.execute("SELECT page FROM groups ORDER BY chat_id")]
    finally:
        conn.close()


class TestBackup:
    def test_the_copy_holds_the_data(self, tmp_path):
        db = tmp_path / "bot.db"
        make_db(db)

        copy = backup_now(db, tmp_path / "backups")

        assert copy is not None and copy.exists()
        assert read_pages(copy) == [0, 10, 20]

    def test_it_copies_a_database_that_is_still_open(self, tmp_path):
        # The bot never stops for its own backup, so the snapshot has to be
        # taken from a live database — this is why it is not `cp`.
        db = tmp_path / "bot.db"
        make_db(db)
        live = sqlite3.connect(db)
        live.execute("INSERT INTO groups VALUES (99, 604)")
        live.commit()

        copy = backup_now(db, tmp_path / "backups")

        assert 604 in read_pages(copy)
        live.close()

    def test_an_uncommitted_write_is_not_captured(self, tmp_path):
        # A snapshot mid-transaction must be the last consistent state, never a
        # half-written one.
        db = tmp_path / "bot.db"
        make_db(db)
        live = sqlite3.connect(db)
        live.execute("INSERT INTO groups VALUES (99, 604)")  # no commit

        copy = backup_now(db, tmp_path / "backups")

        assert read_pages(copy) == [0, 10, 20]
        live.rollback()
        live.close()

    def test_the_backup_directory_is_created(self, tmp_path):
        db = tmp_path / "bot.db"
        make_db(db)
        target = tmp_path / "deep" / "nested" / "backups"

        assert backup_now(db, target) is not None
        assert target.is_dir()

    def test_a_missing_database_is_not_an_error(self, tmp_path):
        # A bot that has never run has nothing to protect yet.
        assert backup_now(tmp_path / "absent.db", tmp_path / "backups") is None

    def test_the_source_is_opened_read_only(self, tmp_path):
        # A backup must never be the thing that writes to the database it is
        # protecting, so the file's modification time must not move.
        db = tmp_path / "bot.db"
        make_db(db)
        before = db.stat().st_mtime_ns

        backup_now(db, tmp_path / "backups")

        assert db.stat().st_mtime_ns == before


class TestRotation:
    def test_only_the_newest_are_kept(self, tmp_path):
        db = tmp_path / "bot.db"
        make_db(db)
        backups = tmp_path / "backups"

        base = dt.datetime(2026, 9, 1, 3, 30)
        for day in range(10):
            backup_now(db, backups, keep=3, now=base + dt.timedelta(days=day))

        kept = existing_backups(backups)
        assert len(kept) == 3
        # The names sort chronologically, so the survivors are the last three.
        assert [p.name for p in kept] == [
            "bot-20260908-033000.db",
            "bot-20260909-033000.db",
            "bot-20260910-033000.db",
        ]

    def test_nothing_is_pruned_below_the_limit(self, tmp_path):
        db = tmp_path / "bot.db"
        make_db(db)
        backups = tmp_path / "backups"
        base = dt.datetime(2026, 9, 1, 3, 30)
        for day in range(2):
            backup_now(db, backups, keep=7, now=base + dt.timedelta(days=day))

        assert len(existing_backups(backups)) == 2

    def test_a_keep_of_zero_prunes_nothing(self, tmp_path):
        # Refusing to delete is the safe reading of a nonsensical setting.
        backups = tmp_path / "backups"
        backups.mkdir()
        (backups / "bot-20260901-033000.db").write_bytes(b"x")

        assert prune(backups, keep=0) == 0
        assert len(existing_backups(backups)) == 1

    def test_unrelated_files_are_left_alone(self, tmp_path):
        backups = tmp_path / "backups"
        backups.mkdir()
        (backups / "notes.txt").write_text("keep me")
        for stamp in ("20260901", "20260902", "20260903"):
            (backups / f"bot-{stamp}-033000.db").write_bytes(b"x")

        prune(backups, keep=1)

        assert (backups / "notes.txt").exists()
        assert len(existing_backups(backups)) == 1
