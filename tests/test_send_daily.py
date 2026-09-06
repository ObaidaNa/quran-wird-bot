"""Daily send: weekday conversion, page selection, repeats, and media caching."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from telegram import InputFile

from quran_wird.config import Settings
from quran_wird.db.models import PageIndex, TaskStatus
from quran_wird.db.repo import GroupRepo, MediaRepo, SubscriberRepo, TaskRepo
from quran_wird.deps import Deps
from quran_wird.jobs.scheduler import to_ptb_weekdays
from quran_wird.jobs.send_daily import is_active_day, send_wird
from quran_wird.tg.media import PageImageMissing, send_pages

CHAT = -100123


# --------------------------------------------------------------- fake Telegram


@dataclass
class FakePhotoSize:
    file_id: str


@dataclass
class FakeMessage:
    message_id: int
    photo: tuple[FakePhotoSize, ...] = ()
    text: str | None = None
    caption: str | None = None
    reply_markup: object = None


class FakeBot:
    """Records what would have been sent, and hands back plausible file_ids."""

    def __init__(self, fail_on_file_id: bool = False) -> None:
        self.albums: list[list] = []
        self.photos: list[dict] = []
        self.messages: list[FakeMessage] = []
        self.fail_on_file_id = fail_on_file_id
        self._next_id = 1000

    def _id(self) -> int:
        self._next_id += 1
        return self._next_id

    async def send_media_group(self, chat_id, media, **kw):
        from telegram.error import BadRequest

        if self.fail_on_file_id and any(isinstance(m.media, str) for m in media):
            raise BadRequest("wrong file identifier/HTTP URL specified")
        self.albums.append(list(media))
        return tuple(
            FakeMessage(self._id(), photo=(FakePhotoSize(f"FID_{i}"),)) for i, _ in enumerate(media)
        )

    async def send_photo(self, chat_id, photo, caption=None, **kw):
        from telegram.error import BadRequest

        if self.fail_on_file_id and isinstance(photo, str):
            raise BadRequest("wrong file identifier/HTTP URL specified")
        self.photos.append({"photo": photo, "caption": caption})
        return FakeMessage(self._id(), photo=(FakePhotoSize("FID_SOLO"),))

    async def send_message(self, chat_id, text, reply_markup=None, **kw):
        msg = FakeMessage(self._id(), text=text, reply_markup=reply_markup)
        self.messages.append(msg)
        return msg


@dataclass
class FakeSessions:
    """Hands the same test session to every session_scope() call."""

    session: object
    committed: int = field(default=0)

    def __call__(self):
        return self

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, *exc):
        return False


@pytest.fixture
def pages_dir(tmp_path) -> Path:
    d = tmp_path / "pages"
    d.mkdir()
    for page in range(1, 12):
        (d / f"{page:03d}.png").write_bytes(b"\x89PNG fake")
    return d


@pytest.fixture
def deps(session, pages_dir, tmp_path):
    settings = Settings(
        bot_token="123:FAKE",
        db_path=tmp_path / "bot.db",
        pages_dir=pages_dir,
    )
    return Deps(settings=settings, engine=None, sessions=FakeSessions(session))


async def seed_index(session, upto: int = 12) -> None:
    for page in range(1, upto + 1):
        session.add(
            PageIndex(
                page_no=page,
                juz=1,
                hizb=1,
                first_surah=1,
                first_ayah=1,
                last_surah=2,
                last_ayah=5,
                surah_names="البقرة",
                ayah_count=5,
            )
        )
    await session.flush()


# ------------------------------------------------------------------- weekdays


class TestWeekdayConversion:
    def test_monday_maps_to_ptb_one(self):
        # date.weekday() 0=Monday; PTB run_daily 0=Sunday. Getting this wrong
        # sends the wird on the wrong days.
        assert to_ptb_weekdays([0]) == (1,)

    def test_sunday_maps_to_ptb_zero(self):
        assert to_ptb_weekdays([6]) == (0,)

    def test_saturday_maps_to_ptb_six(self):
        assert to_ptb_weekdays([5]) == (6,)

    def test_all_days_round_trip(self):
        assert to_ptb_weekdays([0, 1, 2, 3, 4, 5, 6]) == (0, 1, 2, 3, 4, 5, 6)

    def test_weekend_only(self):
        # Friday+Saturday in Python terms -> PTB 5 and 6
        assert to_ptb_weekdays([4, 5]) == (5, 6)


class TestActiveDay:
    async def test_respects_configured_weekdays(self, session):
        group, _ = await GroupRepo(session).get_or_create(CHAT)
        group.active_weekdays = [5, 6]  # Saturday, Sunday
        assert is_active_day(group, dt.date(2026, 9, 5))  # Saturday
        assert not is_active_day(group, dt.date(2026, 9, 7))  # Monday


# ------------------------------------------------------------------ send_wird


class TestSendWird:
    async def _group(self, session, **kw):
        group, _ = await GroupRepo(session).get_or_create(CHAT)
        for key, value in kw.items():
            setattr(group, key, value)
        await session.flush()
        return group

    async def test_sends_pages_and_message(self, session, deps):
        await seed_index(session)
        await self._group(session, active_weekdays=list(range(7)))
        bot = FakeBot()

        task_id = await send_wird(bot, deps, CHAT)

        assert task_id is not None
        assert len(bot.albums) == 1
        assert len(bot.albums[0]) == 2  # two pages a day
        assert len(bot.messages) == 1
        assert "ورد اليوم" in bot.messages[0].text

    async def test_starts_at_page_one(self, session, deps):
        await seed_index(session)
        await self._group(session, active_weekdays=list(range(7)))
        await send_wird(FakeBot(), deps, CHAT)

        task = await TaskRepo(session).get_open(CHAT)
        assert (task.page_start, task.page_end) == (1, 2)

    async def test_skips_inactive_weekday(self, session, deps):
        await seed_index(session)
        today = dt.datetime.now().date()
        # Every weekday except today.
        await self._group(session, active_weekdays=[d for d in range(7) if d != today.weekday()])
        bot = FakeBot()

        assert await send_wird(bot, deps, CHAT) is None
        assert bot.messages == []

    async def test_force_overrides_inactive_weekday(self, session, deps):
        await seed_index(session)
        today = dt.datetime.now().date()
        await self._group(session, active_weekdays=[d for d in range(7) if d != today.weekday()])
        assert await send_wird(FakeBot(), deps, CHAT, force=True) is not None

    async def test_never_sends_twice_in_one_day(self, session, deps):
        await seed_index(session)
        await self._group(session, active_weekdays=list(range(7)))
        assert await send_wird(FakeBot(), deps, CHAT) is not None
        # Even with force, an admin cannot double-post the same day.
        assert await send_wird(FakeBot(), deps, CHAT, force=True) is None

    async def test_skips_inactive_group(self, session, deps):
        await seed_index(session)
        await self._group(session, is_active=False)
        assert await send_wird(FakeBot(), deps, CHAT) is None

    async def test_unknown_group_is_skipped(self, session, deps):
        assert await send_wird(FakeBot(), deps, -999) is None

    async def test_single_page_uses_send_photo(self, session, deps):
        await seed_index(session)
        await self._group(session, pages_per_day=1, active_weekdays=list(range(7)))
        bot = FakeBot()
        await send_wird(bot, deps, CHAT)
        # A media group needs 2-10 items, so one page must go via send_photo.
        assert bot.albums == []
        assert len(bot.photos) == 1

    async def test_records_subscriber_count_in_message(self, session, deps):
        await seed_index(session)
        await self._group(session, active_weekdays=list(range(7)))
        subs = SubscriberRepo(session)
        for uid in (1, 2, 3):
            await subs.subscribe(CHAT, uid, display_name=f"عضو {uid}")

        bot = FakeBot()
        await send_wird(bot, deps, CHAT)
        assert "لم يُنجز أحد بعد" in bot.messages[0].text

    async def test_missing_page_images_raise_clearly(self, session, deps, pages_dir):
        await seed_index(session)
        await self._group(session, active_weekdays=list(range(7)))
        for f in pages_dir.glob("*.png"):
            f.unlink()

        with pytest.raises(PageImageMissing):
            await send_wird(FakeBot(), deps, CHAT)

    async def test_unfinished_wird_is_repeated_next_day(self, session, deps):
        await seed_index(session)
        await self._group(session, active_weekdays=list(range(7)))
        tasks = TaskRepo(session)

        yesterday = dt.date.today() - dt.timedelta(days=1)
        stale = await tasks.create(CHAT, task_date=yesterday, page_start=5, page_end=6)

        bot = FakeBot()
        task_id = await send_wird(bot, deps, CHAT)

        new_task = await tasks.get(task_id)
        assert (new_task.page_start, new_task.page_end) == (5, 6)
        assert new_task.is_repeat_of == stale.id
        assert "نُعيد ورد الأمس" in bot.messages[0].text
        # The stale task is closed so it cannot be repeated forever.
        assert (await tasks.get(stale.id)).status is TaskStatus.CLOSED


# --------------------------------------------------------------- media caching


class TestMediaCaching:
    async def test_first_send_uploads_then_caches(self, session, pages_dir):
        bot = FakeBot()
        media = MediaRepo(session)

        await send_pages(bot, CHAT, [1, 2], pages_dir=pages_dir, media=media, caption="x")

        # First send goes out as real uploads...
        assert all(isinstance(m.media, InputFile) for m in bot.albums[0])
        # ...and the returned ids are remembered.
        assert await media.get_file_id(1) == "FID_0"
        assert await media.get_file_id(2) == "FID_1"

    async def test_second_send_uses_cached_ids(self, session, pages_dir):
        media = MediaRepo(session)
        await send_pages(FakeBot(), CHAT, [1, 2], pages_dir=pages_dir, media=media)

        bot = FakeBot()
        await send_pages(bot, CHAT, [1, 2], pages_dir=pages_dir, media=media)
        assert all(isinstance(m.media, str) for m in bot.albums[0])

    async def test_stale_file_id_falls_back_to_upload(self, session, pages_dir):
        media = MediaRepo(session)
        await media.remember(1, "STALE")
        await media.remember(2, "ALSO_STALE")

        bot = FakeBot(fail_on_file_id=True)
        ids = await send_pages(bot, CHAT, [1, 2], pages_dir=pages_dir, media=media)

        # The bad ids are dropped and the pages re-uploaded rather than the whole
        # wird failing.
        assert len(ids) == 2
        assert all(isinstance(m.media, InputFile) for m in bot.albums[-1])

    async def test_album_splits_above_ten_pages(self, session, pages_dir):
        bot = FakeBot()
        await send_pages(
            bot, CHAT, list(range(1, 12)), pages_dir=pages_dir, media=MediaRepo(session)
        )
        # 11 pages -> one album of 10 and a single photo.
        assert len(bot.albums) == 1
        assert len(bot.albums[0]) == 10
        assert len(bot.photos) == 1

    async def test_no_pages_sends_nothing(self, session, pages_dir):
        bot = FakeBot()
        assert await send_pages(bot, CHAT, [], pages_dir=pages_dir, media=MediaRepo(session)) == []
        assert bot.albums == [] and bot.photos == []

    async def test_caption_only_on_first_item(self, session, pages_dir):
        bot = FakeBot()
        await send_pages(
            bot, CHAT, [1, 2, 3], pages_dir=pages_dir, media=MediaRepo(session), caption="عنوان"
        )
        captions = [m.caption for m in bot.albums[0]]
        assert captions[0] == "عنوان"
        assert captions[1:] == [None, None]
