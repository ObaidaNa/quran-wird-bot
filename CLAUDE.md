# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

An Arabic Telegram bot that posts a daily Quran reading into a group, tracks who
has finished, reminds latecomers, and carries the group through a shared
604-page khatmah. The full spec and the settled product decisions are in
`docs/PLAN.md` — read section 11 before reopening any product question.

## Commands

Everything runs through `uv`; there is no `pip` or bare `python` path that works.

```bash
uv run pytest -q                              # the suite; -k 'name' for one test
uv run ruff check --fix . && uv run ruff format .   # run both before committing
uv run -m quran_wird                          # start the bot (needs BOT_TOKEN in .env)
uv run alembic revision --autogenerate -m "…" # then edit it; see Migrations below
uv run alembic upgrade head
uv run scripts/build_index.py                 # one-off: mushaf index into the DB
uv run scripts/build_pages.py                 # one-off: 604 page PNGs (~106 MB)
```

The build scripts download a 91 MB archive from Quranpedia on first use. They are
idempotent; `--force` rebuilds existing images.

## Layering — the rules that keep the shape

1. **`domain/` never imports `telegram`.** That is what makes the rules testable
   without a network. Handlers and jobs stay thin.
2. **All database access goes through `db/repo/*`.** No queries in handlers or jobs.
3. **Every user-facing string lives in `messages/`** — `ar.py` for whole messages,
   `phrases.py` for the reviewed phrase pools, `render.py` and `*_view.py` for
   assembly. Never inline an Arabic string in a handler or a job.
4. **Comments and docstrings are English; user-facing text is Arabic.** Arabic in
   code is data (surah names, the `"سورة "` prefix), not explanation.
5. **Tests never touch the network.** `tests/fakes.py` has `FakeBot` and
   `FakeSessions`; `tests/conftest.py` has the `session`, `deps` and `pages_dir`
   fixtures. Name a test for the behaviour and its reason, not the function.

## Arabic text

The phrase pools in `messages/phrases.py` are reviewed and approved (docs/PLAN.md
§7); three reviewed phrases were rejected and a test asserts they stay out. Do not
add, remove or reword a phrase without asking.

When you write or change any user-facing Arabic, **render it and show it** — a
short script that prints it, or an artifact — rather than asserting the wording
reads well. It cannot be reviewed in a terminal.

## Idempotency is the restart strategy

Jobs live only in memory and are rebuilt from the database on startup
(`jobs/scheduler.reschedule_all`), so restarting is always safe. Anything that must
not fire twice is guarded by a database row instead, and new work must follow suit:

- `send_wird` refuses a second wird for the same `task_date`
- `TaskRepo.log_reminder(task_id, seq)` is claimed *before* sending
- `close_day` closes the task, so a replayed job finds nothing open
- `WeeklyReportRepo.claim` is taken before the board is posted
- `StatsRepo.record_done` is idempotent per date

`replace_wird` is the one place a task row is deleted rather than guarded. It is
confined to today's still-open wird, and refuses once the pages already match the
settings — which is what makes a second press of the same button do nothing, since
SQLite hands the replacement the rowid the withdrawn wird just gave up.

A group's jobs must also be scheduled the moment it exists — being added and
`/start` both call `reschedule_chat`. Forgetting this once meant new groups
received nothing at all until the process restarted.

## Migrations

`--autogenerate` emits a `NOT NULL` column with **no `server_default`**, which
fails on any database that already has rows. Add the default by hand and verify
the upgrade against a seeded database before committing. `render_as_batch=True`
is set because SQLite has no full `ALTER TABLE`.

## Gotchas that have already cost a debugging session

- **PTB weekdays are not Python's.** `JobQueue.run_daily(days=)` is 0=Sunday;
  `date.weekday()` (what the DB stores) is 0=Monday. Convert with
  `jobs/scheduler.to_ptb_weekdays()`.
- **Never pass a `Path` to `InputMediaPhoto`** — it hardcodes `local_mode=True`
  and produces a `file://` URI the Bot API cannot fetch. Pass bytes + a filename.
- **`session.delete()` does not flush**; a later `session.get()` in the same
  session returns the row from the identity map. Flush after deleting.
- **A media group needs 2–10 items.** One page must go via `send_photo`; more than
  ten splits across albums. Handled in `tg/media.py`.
- **Quiet hours wrap midnight**, so `start <= now < end` is wrong. Test fixtures
  that send reminders must disable quiet hours, or the suite fails nightly.
- **A `StrEnum` column comes back as a plain `str`.** `status`, `advance_rule` and
  friends are stored in `String` columns, so a row loaded in a fresh session
  hands back `'closed'`, not `TaskStatus.CLOSED` — `is` comparisons are always
  False and silently disable the guard around them. This let a member tick a
  wird whose day had closed. Use `DailyTask.is_closed`, or `==`; never `is`.
- **`Forbidden` is the one error retrying cannot fix** — it means the bot was
  kicked. Jobs that swallow their own send errors must re-raise it so the group
  can be deactivated.

## Storage conventions

`DateTime` columns are UTC. `Date` and `Time` columns are **local to the group's
timezone** — the wird day is the day members see, not the server's. Lists are JSON
columns; bounded values are `StrEnum`.

## Repo

Committing directly to `main` is fine here; outside contributors open PRs. Commit
messages: a short first line, then a paragraph explaining *why* — the diff already
shows what. Contributor-facing guidance lives in the README.
