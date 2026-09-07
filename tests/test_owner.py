"""The owner panel: who may open it, and whether its numbers are true.

The counts are the whole point of the feature, so most of these seed a small
fleet by hand and check the figure the operator would read against the one the
fixture built.
"""

from __future__ import annotations

import datetime as dt

import pytest
from fakes import FakeSessions

from quran_wird.config import Settings
from quran_wird.db.repo import GroupRepo, StatsRepo, SubscriberRepo, TaskRepo
from quran_wird.deps import DEPS_KEY, Deps
from quran_wird.handlers.owner import gather, is_owner, on_button, owner_command, screen
from quran_wird.messages import ar
from quran_wird.messages import owner_view as view

TODAY = dt.date.today()


# --------------------------------------------------------------------- stubs


class FakeMessage:
    def __init__(self) -> None:
        self.replies: list[str] = []
        self.keyboards: list[object] = []

    async def reply_text(self, text, reply_markup=None, **kw):
        self.replies.append(text)
        self.keyboards.append(reply_markup)


class FakeUser:
    def __init__(self, user_id: int) -> None:
        self.id = user_id


class FakeUpdate:
    def __init__(self, user_id: int) -> None:
        self.effective_user = FakeUser(user_id)
        self.effective_message = FakeMessage()
        self.callback_query = None


class FakeQuery:
    def __init__(self, data: str, user_id: int) -> None:
        self.data = data
        self.from_user = FakeUser(user_id)
        self.answers: list[str | None] = []
        self.edits: list[str] = []
        self.cleared = False

    async def answer(self, text=None, show_alert=False):
        self.answers.append(text)

    async def edit_message_text(self, text, reply_markup=None):
        self.edits.append(text)

    async def edit_message_reply_markup(self, reply_markup=None):
        self.cleared = True


class FakeCallbackUpdate:
    def __init__(self, data: str, user_id: int) -> None:
        self.callback_query = FakeQuery(data, user_id)
        self.effective_user = FakeUser(user_id)
        self.effective_message = None


class FakeContext:
    def __init__(self, deps) -> None:
        self.bot_data = {DEPS_KEY: deps}


OWNER = 999


def owner_deps(session, tmp_path, pages_dir, owner_ids: str = str(OWNER)) -> Deps:
    settings = Settings(
        bot_token="123:FAKE",
        db_path=tmp_path / "bot.db",
        pages_dir=pages_dir,
        owner_ids=owner_ids,
    )
    return Deps(settings=settings, engine=None, sessions=FakeSessions(session))


@pytest.fixture
def odeps(session, tmp_path, pages_dir):
    return owner_deps(session, tmp_path, pages_dir)


async def seed_fleet(session) -> None:
    """Three groups, six subscriptions, five people, two finished khatmahs.

    Group -100 holds four members and -200 holds two, so the ordering the panel
    lists them in is decided by the data and not by a tie.

    One person is in two groups, so "people" and "subscriptions" must differ —
    that is the distinction the panel exists to get right.
    """
    groups = GroupRepo(session)
    subs = SubscriberRepo(session)
    tasks = TaskRepo(session)

    big, _ = await groups.get_or_create(-100, title="مجموعة الورد")
    big.khatmah_number = 3  # two finished
    big.current_page = 101  # plus 100 pages into the third
    small, _ = await groups.get_or_create(-200, title="حلقة القرآن")
    small.current_page = 51
    gone, _ = await groups.get_or_create(-300, title="مجموعة قديمة")
    await groups.set_active(-300, False)

    for user_id in (1, 2, 3, 4):
        await subs.subscribe(-100, user_id, display_name=f"عضو {user_id}")
    # user 3 is in both groups; user 5 only in the second.
    for user_id in (3, 5):
        await subs.subscribe(-200, user_id, display_name=f"عضو {user_id}")

    today = await tasks.create(-100, task_date=TODAY, page_start=101, page_end=102)
    await tasks.create(-200, task_date=TODAY, page_start=51, page_end=52)
    await tasks.create(-100, task_date=TODAY - dt.timedelta(days=1), page_start=99, page_end=100)
    # Well outside the seven-day window.
    await tasks.create(-100, task_date=TODAY - dt.timedelta(days=40), page_start=1, page_end=2)

    await tasks.mark_done(today.id, 1, was_subscriber=True)
    await tasks.mark_done(today.id, 2, was_subscriber=True)

    stats = StatsRepo(session)
    for user_id in (1, 2):
        await stats.record_done(-100, user_id, TODAY)
    await stats.record_missed(-100, 3)
    await session.flush()


# --------------------------------------------------------------------- access


class TestAccess:
    def test_owner_ids_are_read_from_one_comma_separated_string(self, tmp_path, pages_dir):
        settings = Settings(
            bot_token="t", db_path=tmp_path / "b.db", pages_dir=pages_dir, owner_ids="1, 2,3"
        )
        assert settings.owners == frozenset({1, 2, 3})

    def test_junk_in_the_list_is_ignored_rather_than_crashing_the_bot(self, tmp_path, pages_dir):
        # A typo in .env must not stop the bot from starting.
        settings = Settings(
            bot_token="t", db_path=tmp_path / "b.db", pages_dir=pages_dir, owner_ids="7,,abc, 8 "
        )
        assert settings.owners == frozenset({7, 8})

    def test_unset_means_nobody_owns_the_panel(self, tmp_path, pages_dir):
        settings = Settings(bot_token="t", db_path=tmp_path / "b.db", pages_dir=pages_dir)
        assert settings.owners == frozenset()

    async def test_an_unconfigured_panel_says_so_instead_of_going_quiet(
        self, session, tmp_path, pages_dir
    ):
        """The operator is the only one who ever sees this.

        Silence here would look identical to a broken bot, and the only fix is a
        variable they have to be told about.
        """
        deps = owner_deps(session, tmp_path, pages_dir, owner_ids="")
        update = FakeUpdate(OWNER)
        await owner_command(update, FakeContext(deps))

        assert update.effective_message.replies == [ar.OWNER_NOT_CONFIGURED]

    async def test_a_stranger_gets_no_answer_at_all(self, session, odeps):
        # Answering "not for you" would confirm the panel exists.
        update = FakeUpdate(12345)
        await owner_command(update, FakeContext(odeps))
        assert update.effective_message.replies == []

    async def test_the_owner_gets_the_overview(self, session, odeps):
        await seed_fleet(session)
        update = FakeUpdate(OWNER)
        await owner_command(update, FakeContext(odeps))

        assert len(update.effective_message.replies) == 1
        assert "لوحة المالك" in update.effective_message.replies[0]
        assert update.effective_message.keyboards[0] is not None

    async def test_a_stranger_pressing_a_forwarded_button_is_refused(self, session, odeps):
        """The panel is a message, and a message can be forwarded.

        Checking only when it is opened would hand live buttons to whoever
        received the forward.
        """
        await seed_fleet(session)
        update = FakeCallbackUpdate(f"{view.CB}:go:groups", 12345)
        await on_button(update, FakeContext(odeps))

        assert update.callback_query.edits == []
        assert update.callback_query.answers == [ar.OWNER_ONLY]

    async def test_is_owner_rejects_a_missing_user(self, odeps):
        assert is_owner(odeps, OWNER)
        assert not is_owner(odeps, None)


# --------------------------------------------------------------------- counts


class TestCounts:
    async def test_groups_are_split_into_active_and_dormant(self, session, odeps):
        await seed_fleet(session)
        stats, _, _ = await gather(odeps)

        assert stats.groups_total == 3
        assert stats.groups_active == 2
        assert stats.groups_dormant == 1

    async def test_a_person_in_two_groups_counts_once(self, session, odeps):
        """Six subscriptions, five people — the number the operator actually wants."""
        await seed_fleet(session)
        stats, _, _ = await gather(odeps)

        assert stats.subscriptions_active == 6
        assert stats.unique_users == 5

    async def test_finished_khatmahs_are_counted_from_the_khatmah_number(self, session, odeps):
        # A group on khatmah 3 has finished two; groups on khatmah 1 have finished none.
        await seed_fleet(session)
        stats, _, _ = await gather(odeps)

        assert stats.khatmahs_completed == 2

    async def test_running_khatmahs_are_the_active_groups(self, session, odeps):
        # One shared khatmah per group is the settled model (PLAN section 11).
        await seed_fleet(session)
        stats, _, _ = await gather(odeps)

        assert stats.khatmahs_running == 2

    async def test_pages_read_span_finished_khatmahs_and_the_current_one(self, session, odeps):
        """2 × 604 finished + 100 into the third, + 50 in the second, + 0 in the third."""
        await seed_fleet(session)
        stats, _, _ = await gather(odeps)

        assert stats.pages_read == 2 * 604 + 100 + 50
        assert stats.khatmahs_equivalent == 2

    async def test_todays_wirds_and_ticks_are_separated_from_the_totals(self, session, odeps):
        await seed_fleet(session)
        stats, _, _ = await gather(odeps)

        assert stats.wirds_today == 2
        assert stats.wirds_week == 3  # the forty-day-old one falls outside
        assert stats.wirds_total == 4
        assert stats.completions_today == 2
        assert stats.completions_total == 2

    async def test_the_completion_rate_weighs_done_against_missed(self, session, odeps):
        await seed_fleet(session)
        stats, _, _ = await gather(odeps)

        # Two members finished, one missed.
        assert stats.days_done == 2
        assert stats.days_missed == 1
        assert round(stats.completion_rate) == 67

    async def test_only_groups_that_received_a_wird_count_as_reading(self, session, odeps):
        await seed_fleet(session)
        stats, _, _ = await gather(odeps)
        assert stats.groups_reading_week == 2

    async def test_an_empty_database_reports_zeroes_and_does_not_divide_by_them(
        self, session, odeps
    ):
        """The panel is opened on day one, before any group exists."""
        stats, largest, nearest = await gather(odeps)

        assert stats.groups_total == 0
        assert stats.unique_users == 0
        assert stats.completion_rate == 0.0
        assert stats.members_per_group == 0.0
        assert largest == [] and nearest == []


# -------------------------------------------------------------------- listing


class TestListing:
    async def test_the_largest_groups_come_first(self, session, odeps):
        await seed_fleet(session)
        _, largest, _ = await gather(odeps)

        assert [d.chat_id for d in largest] == [-100, -200, -300]
        assert largest[0].subscribers == 4
        assert largest[1].subscribers == 2
        assert largest[2].subscribers == 0

    async def test_a_group_carries_its_last_wird_date(self, session, odeps):
        await seed_fleet(session)
        _, largest, _ = await gather(odeps)

        by_id = {d.chat_id: d for d in largest}
        assert by_id[-100].last_wird == TODAY
        assert by_id[-300].last_wird is None

    async def test_the_nearest_to_finishing_are_ordered_by_page_not_by_khatmah(
        self, session, odeps
    ):
        """The heading promises "nearest to finishing", so the page decides.

        Ordering by khatmah number first would put a group on its fourth khatmah
        at page 200 above one at page 590 on its first, which is the opposite of
        what the screen says.
        """
        await GroupRepo(session).get_or_create(-400, title="على وشك الختم")
        near = await GroupRepo(session).set_current_page(-400, 590)
        near.khatmah_number = 1
        await session.flush()

        _, _, nearest = await gather(odeps)
        assert [d.current_page for d in nearest] == sorted(
            (d.current_page for d in nearest), reverse=True
        )
        assert nearest[0].chat_id == -400

    async def test_a_paused_group_is_left_out_of_the_nearest_list(self, session, odeps):
        # It is not going to finish anything while it is paused.
        await seed_fleet(session)
        groups = GroupRepo(session)
        await groups.set_current_page(-300, 600)
        await session.flush()

        _, _, nearest = await gather(odeps)
        assert -300 not in {d.chat_id for d in nearest}

    async def test_progress_is_reported_per_khatmah_not_per_lifetime(self, session, odeps):
        await seed_fleet(session)
        _, largest, _ = await gather(odeps)
        digest = next(d for d in largest if d.chat_id == -100)

        assert digest.percent == round(100 / 604 * 100)
        assert digest.pages_read == 2 * 604 + 100


# -------------------------------------------------------------------- screens


class TestScreens:
    @pytest.mark.parametrize(
        ("where", "marker"),
        [
            ("home", "لوحة المالك"),
            ("groups", "مجموعات"),
            ("members", "الأعضاء"),
            ("activity", "النشاط"),
            ("khatmahs", "الختمات"),
        ],
    )
    async def test_every_screen_draws(self, session, odeps, where, marker):
        await seed_fleet(session)
        text, keyboard = await screen(odeps, where)

        assert marker in text
        assert keyboard.inline_keyboard

    async def test_an_unknown_screen_falls_back_to_the_overview(self, session, odeps):
        # A button from an older version of the panel must not break it.
        await seed_fleet(session)
        text, _ = await screen(odeps, "nonsense")
        assert "لوحة المالك" in text

    async def test_a_group_title_cannot_smuggle_html_into_the_panel(self, session, odeps):
        """Group titles are chosen by other people and rendered in HTML mode.

        An unescaped one would break the message, or worse, style it.
        """
        await GroupRepo(session).get_or_create(-400, title="<b>مجموعة</b>")
        await session.flush()

        text, _ = await screen(odeps, "groups")
        assert "&lt;b&gt;مجموعة&lt;/b&gt;" in text
        assert "<b>مجموعة</b>" not in text

    async def test_a_group_without_a_title_is_listed_by_its_id(self, session, odeps):
        await GroupRepo(session).get_or_create(-500)
        await session.flush()

        text, _ = await screen(odeps, "groups")
        assert "#-500" in text

    async def test_pressing_a_screen_button_rewrites_the_same_message(self, session, odeps):
        await seed_fleet(session)
        update = FakeCallbackUpdate(f"{view.CB}:go:khatmahs", OWNER)
        await on_button(update, FakeContext(odeps))

        assert len(update.callback_query.edits) == 1
        assert "الختمات" in update.callback_query.edits[0]

    async def test_closing_clears_the_keyboard(self, session, odeps):
        await seed_fleet(session)
        update = FakeCallbackUpdate(f"{view.CB}:done", OWNER)
        await on_button(update, FakeContext(odeps))

        assert update.callback_query.cleared
        assert update.callback_query.edits == []
