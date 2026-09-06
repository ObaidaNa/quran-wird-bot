"""The settings panel: bounds, steppers, toggles, rescheduling, and /setpage."""

from __future__ import annotations

import datetime as dt

import pytest

from quran_wird.db.models import AdvanceRule, PageIndex
from quran_wird.db.repo import GroupRepo
from quran_wird.domain.settings import (
    bump_int,
    bump_time,
    days_to_finish,
    parse_page,
    toggle_weekday,
)
from quran_wird.handlers.settings import apply
from quran_wird.messages import settings_view as view

CHAT = -100123


@pytest.fixture
async def group(session):
    g, _ = await GroupRepo(session).get_or_create(CHAT)
    await session.flush()
    return g


async def press(deps, data: str):
    return await apply(deps, CHAT, data)


# ------------------------------------------------------------ pure arithmetic


class TestSteppers:
    def test_numbers_clamp_at_both_ends(self):
        # The end of a range is a quiet wall, not an error.
        assert bump_int(1, -1, lo=1, hi=20) == 1
        assert bump_int(20, 1, lo=1, hi=20) == 20
        assert bump_int(2, 1, lo=1, hi=20) == 3

    def test_time_wraps_around_midnight(self):
        assert bump_time(dt.time(0, 0), -30) == dt.time(23, 30)
        assert bump_time(dt.time(23, 45), 30) == dt.time(0, 15)
        assert bump_time(dt.time(5, 0), 60) == dt.time(6, 0)

    def test_weekday_toggles_on_and_off(self):
        assert toggle_weekday([0, 1, 2], 3) == [0, 1, 2, 3]
        assert toggle_weekday([0, 1, 2], 1) == [0, 2]

    def test_the_last_active_day_cannot_be_removed(self):
        # A group with no active day would never send again and would look
        # broken; pausing is what `is_active` is for.
        assert toggle_weekday([3], 3) == [3]

    def test_khatmah_length_rounds_up(self):
        assert days_to_finish(2) == 302
        assert days_to_finish(7) == 87  # 604 / 7 is not whole

    def test_page_numbers_accept_arabic_digits(self):
        assert parse_page("٣٥٠") == 350
        assert parse_page(" 350 ") == 350
        assert parse_page("605") is None
        assert parse_page("0") is None
        assert parse_page("صفحة") is None


# ------------------------------------------------------------------ the panel


class TestPanel:
    async def test_home_shows_the_current_settings(self, deps, group):
        screen = await press(deps, "home")
        assert "إعدادات الورد" in screen.text
        assert "الصفحة الحالية" in screen.text

    async def test_a_stepper_changes_the_value_and_stays_put(self, deps, group):
        screen = await press(deps, "n:pages:1")
        assert group.pages_per_day == 3
        # The panel stays in the editor the button belongs to.
        assert "الصفحات اليومية" in screen.text

    async def test_a_stepper_stops_at_its_bound(self, deps, group):
        group.pages_per_day = 20
        await press(deps, "n:pages:1")
        assert group.pages_per_day == 20

    async def test_time_steppers_move_in_minutes(self, deps, group):
        await press(deps, "t:send:-30")
        assert group.send_time == dt.time(4, 30)

    async def test_weekday_buttons_toggle_the_reading_days(self, deps, group):
        await press(deps, "d:4")
        assert 4 not in group.active_weekdays
        await press(deps, "d:4")
        assert 4 in group.active_weekdays

    async def test_timezone_is_chosen_from_the_list(self, deps, group):
        await press(deps, "tz:Asia/Riyadh")
        assert group.timezone == "Asia/Riyadh"

    async def test_an_unlisted_timezone_is_ignored(self, deps, group):
        assert await press(deps, "tz:Mars/Olympus") is None
        assert group.timezone == "Asia/Damascus"

    async def test_advance_rule_is_chosen_from_the_list(self, deps, group):
        await press(deps, "a:majority")
        assert group.advance_rule is AdvanceRule.MAJORITY

    async def test_quiet_hours_clear_both_ends_together(self, deps, group):
        # A window with only one end has no meaning.
        await press(deps, "quiet:off")
        assert group.quiet_hours_start is None and group.quiet_hours_end is None
        await press(deps, "quiet:on")
        assert group.quiet_hours_start == dt.time(23, 0)
        assert group.quiet_hours_end == dt.time(7, 0)

    async def test_the_weekly_report_can_be_switched_off(self, deps, group):
        await press(deps, "we")
        assert group.weekly_report_enabled is False

    async def test_pausing_keeps_the_progress(self, deps, group):
        group.current_page = 120
        await press(deps, "pause:off")
        assert group.is_active is False
        assert group.current_page == 120
        assert "موقوف مؤقتًا" in (await press(deps, "home")).text

    async def test_the_inert_value_button_changes_nothing(self, deps, group):
        screen = await press(deps, "noop")
        assert group.pages_per_day == 2
        assert screen is not None

    async def test_an_unknown_action_is_ignored(self, deps, group):
        assert await press(deps, "wat:1") is None

    async def test_a_group_the_bot_does_not_know_draws_nothing(self, deps):
        assert await apply(deps, -999, "home") is None


class TestKhatmahAndSubscribers:
    async def test_a_new_khatmah_starts_over_at_page_one(self, deps, group):
        group.current_page = 300
        await press(deps, "khatmah:new")
        assert (group.khatmah_number, group.current_page) == (2, 1)

    async def test_starting_a_khatmah_takes_two_presses(self, deps, group):
        # The confirm screen is the only guard on a group-wide reset.
        screen = await press(deps, "go:khatmah")
        assert group.khatmah_number == 1
        actions = [b.callback_data for row in screen.keyboard.inline_keyboard for b in row]
        assert "cfg:khatmah:new" in actions

    async def test_a_subscriber_can_be_removed_from_the_panel(self, session, deps, group):
        from quran_wird.db.repo import SubscriberRepo

        subs = SubscriberRepo(session)
        await subs.subscribe(CHAT, 7, display_name="أحمد")
        await subs.subscribe(CHAT, 8, display_name="محمد")

        screen = await press(deps, "sub:7")

        assert [m.user_id for m in await subs.list_active(CHAT)] == [8]
        # The record survives, so /join brings back the streak rather than zeroing it.
        assert await subs.get(CHAT, 7) is not None
        assert "محمد" in screen.text or "المشتركون" in screen.text

    async def test_the_empty_subscriber_list_still_renders(self, deps, group):
        assert "لا مشترك بعد" in (await press(deps, "go:subs")).text


class TestRescheduling:
    async def test_timing_changes_ask_for_a_reschedule(self, deps, group):
        # Jobs live in memory keyed to these values; missing one leaves the old
        # send time running until the next restart.
        for data in ("t:send:30", "d:4", "tz:Asia/Riyadh", "pause:off"):
            assert (await press(deps, data)).reschedule is True, data

    async def test_other_changes_do_not(self, deps, group):
        for data in ("n:pages:1", "n:rmax:-1", "a:all", "we"):
            assert (await press(deps, data)).reschedule is False, data


EDITORS = (
    "pages",
    "send",
    "close",
    "days",
    "rem",
    "quiet",
    "tz",
    "advance",
    "weekly",
    "wstart",
    "page",
    "pause",
    "khatmah",
    "subs",
)


class TestScreens:
    @pytest.mark.parametrize("where", EDITORS)
    async def test_each_editor_renders_with_buttons(self, deps, group, where):
        # One bad f-string turns the whole panel into an error message, and the
        # only way to see it is to draw every screen.
        screen = await press(deps, f"go:{where}")
        assert screen.text
        assert screen.keyboard.inline_keyboard

    @pytest.mark.parametrize("where", EDITORS)
    async def test_each_editor_renders_for_a_paused_group(self, deps, group, where):
        # The states with something switched off are where the f-strings break.
        group.is_active = False
        group.quiet_hours_start = None
        group.quiet_hours_end = None
        group.weekly_report_enabled = False
        group.reminder_max_count = 0
        group.active_weekdays = [4]
        assert (await press(deps, f"go:{where}")).text

    async def test_every_button_carries_a_handled_action(self, deps, group):
        # A button whose callback nothing answers is a dead button in a group.
        seen = set()
        for where in ("home", *(f"go:{e}" for e in EDITORS)):
            screen = await press(deps, where)
            for row in screen.keyboard.inline_keyboard:
                for button in row:
                    seen.add(button.callback_data)

        for data in sorted(seen):
            payload = data.split(":", 1)[1]
            if payload == "done":  # handled in the callback handler, not apply()
                continue
            assert await press(deps, payload) is not None, data


# ---------------------------------------------------------------- resuming


class TestResumeAKhatmah:
    """A group already part way through a khatmah when the bot arrives."""

    async def test_a_typed_page_number_becomes_the_starting_page(self, session, deps, group):
        # What /setpage does: read the number, then set where tomorrow starts.
        page = parse_page("٣٥٠")
        await GroupRepo(session).set_current_page(CHAT, page)
        assert group.current_page == 350

    async def test_the_page_editor_names_the_command(self, deps, group):
        # Buttons cannot reach page 350, so the editor has to point at /setpage.
        screen = await press(deps, "go:page")
        assert "/setpage" in screen.text

    async def test_the_page_stepper_still_corrects_small_slips(self, deps, group):
        group.current_page = 350
        await press(deps, "n:page:10")
        assert group.current_page == 360
        await press(deps, "n:page:-1")
        assert group.current_page == 359

    async def test_the_page_cannot_leave_the_mushaf(self, deps, group):
        group.current_page = 604
        await press(deps, "n:page:10")
        assert group.current_page == 604


class TestSetPageReply:
    async def test_the_confirmation_names_the_juz_and_surah(self, session, deps, group):
        session.add(
            PageIndex(
                page_no=350,
                juz=14,
                hizb=27,
                first_surah=16,
                first_ayah=1,
                last_surah=16,
                last_ayah=6,
                surah_names="النحل",
                ayah_count=6,
            )
        )
        await session.flush()

        from quran_wird.db.repo import MushafRepo
        from quran_wird.messages import ar

        info = await MushafRepo(session).page(350)
        text = ar.SETPAGE_OK.format(
            page=view.ar_num(350), juz=view.ar_num(info.juz), surahs=info.surah_names
        )
        # So an admin can see at a glance they picked the right spot.
        assert "٣٥٠" in text and "١٤" in text and "النحل" in text
