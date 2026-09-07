"""Rendering tests: Arabic numerals, dates, and the wird message."""

from __future__ import annotations

import datetime as dt

import pytest

from quran_wird.domain.schemas import PageRange, WirdView
from quran_wird.messages import render
from quran_wird.messages.phrases import (
    ALL_DONE,
    DONE,
    MISSED,
    REMINDER_FIRST,
    REMINDER_LAST,
    REMINDER_SECOND,
    SEND,
    WEEKLY_CLOSER,
    Phrases,
    ShuffleBag,
)

SATURDAY = dt.date(2026, 9, 5)


def make_view(**kw):
    defaults = dict(
        task_id=1,
        task_date=SATURDAY,
        pages=PageRange(start=120, end=121),
        juz=6,
        surah_names="المائدة",
        subscriber_count=7,
    )
    return WirdView(**(defaults | kw))


class TestArabicNumerals:
    def test_digits_are_converted(self):
        assert render.ar_num(604) == "٦٠٤"
        assert render.ar_num(0) == "٠"

    def test_date_uses_levantine_month(self):
        # 2026-09-05 is a Saturday
        assert render.ar_date(SATURDAY) == "السبت ٥ أيلول"

    def test_weekday_table_matches_python_convention(self):
        # date.weekday(): 0=Monday .. 6=Sunday
        monday = dt.date(2026, 9, 7)
        assert monday.weekday() == 0
        assert render.ar_date(monday).startswith("الاثنين")

    def test_all_twelve_months_present(self):
        assert len(render.MONTH_NAMES) == 12
        for month in range(1, 13):
            assert render.ar_date(dt.date(2026, month, 1))


class TestArabicCounting:
    """A number changes the noun after it in Arabic, and the panel is all numbers.

    "٩ مشتركًا" is wrong the way "9 subscriber" is wrong in English, so the rule
    is pinned here rather than left to whoever writes the next screen.
    """

    def test_zero_names_the_noun_without_a_digit(self):
        assert render.counted(0, render.NOUNS["subscriber"]) == "لا مشترك"

    def test_one_and_two_have_their_own_forms_and_no_digit(self):
        assert render.counted(1, render.NOUNS["subscriber"]) == "مشترك واحد"
        assert render.counted(2, render.NOUNS["subscriber"]) == "مشتركان"

    def test_three_to_ten_take_the_plural(self):
        assert render.counted(3, render.NOUNS["subscriber"]) == "٣ مشتركين"
        assert render.counted(9, render.NOUNS["subscriber"]) == "٩ مشتركين"
        assert render.counted(10, render.NOUNS["subscriber"]) == "١٠ مشتركين"

    def test_eleven_to_ninety_nine_take_the_singular(self):
        assert render.counted(11, render.NOUNS["subscriber"]) == "١١ مشتركًا"
        assert render.counted(24, render.NOUNS["subscriber"]) == "٢٤ مشتركًا"
        assert render.counted(99, render.NOUNS["subscriber"]) == "٩٩ مشتركًا"

    def test_a_round_hundred_takes_the_bare_noun(self):
        assert render.counted(100, render.NOUNS["subscriber"]) == "١٠٠ مشترك"

    def test_the_form_follows_the_last_part_of_a_large_number(self):
        # مئة وثلاثة مشتركين — the tamyiz agrees with the 3, not the 103.
        assert render.counted(103, render.NOUNS["subscriber"]) == "١٠٣ مشتركين"
        assert render.counted(115, render.NOUNS["subscriber"]) == "١١٥ مشتركًا"

    def test_thousands_use_the_arabic_separator_not_a_comma(self):
        # A Latin comma beside Arabic-Indic digits reads as a decimal point.
        assert render.counted(5014, render.NOUNS["page"]) == "٥٬٠١٤ صفحة"
        assert "," not in render.counted(5014, render.NOUNS["page"])

    def test_only_two_changes_form_with_its_case(self):
        """«يومان» standing alone, «في يومين» after a preposition.

        The dual is the one count whose written form moves with its case even
        unvocalised, so it is the only one `oblique` may change.
        """
        day = render.NOUNS["day"]
        assert render.counted(2, day) == "يومان"
        assert render.counted(2, day, oblique=True) == "يومين"

        for value in (0, 1, 3, 7, 11, 100, 103):
            assert render.counted(value, day) == render.counted(value, day, oblique=True)

    def test_an_adjective_travels_with_the_noun_it_describes(self):
        # "متتالي" has to agree in number and case just as the noun does.
        streak = render.NOUNS["streak_day"]
        assert render.counted(2, streak) == "يومان متتاليان"
        assert render.counted(5, streak) == "٥ أيام متتالية"
        assert render.counted(23, streak) == "٢٣ يومًا متتاليًا"

    def test_feminine_nouns_agree_too(self):
        assert render.counted(1, render.NOUNS["group"]) == "مجموعة واحدة"
        assert render.counted(2, render.NOUNS["khatmah"]) == "ختمتان"
        assert render.counted(6, render.NOUNS["group"]) == "٦ مجموعات"


class TestPagesLine:
    def test_two_pages(self):
        line = render.pages_line(make_view())
        assert "الصفحات ١٢٠ – ١٢١" in line
        assert "الجزء ٦" in line
        assert "المائدة" in line

    def test_single_page_is_singular(self):
        line = render.pages_line(make_view(pages=PageRange(start=604, end=604)))
        assert line.startswith("الصفحة ٦٠٤")
        assert "الصفحات" not in line


class TestWirdMessage:
    def test_nobody_done_yet(self):
        text = render.wird_message(make_view(), "عبارة")
        assert "لم يُنجز أحد بعد" in text
        assert "عبارة" in text

    def test_lists_finishers_with_counts(self):
        view = make_view(done_names=["أحمد", "سارة"])
        text = render.wird_message(view, "عبارة")
        assert "أنجز ٢ من ٧" in text
        assert "أحمد" in text and "سارة" in text

    def test_repeat_is_announced(self):
        text = render.wird_message(make_view(is_repeat=True), "عبارة")
        assert "نُعيد ورد الأمس" in text

    def test_normal_day_has_no_repeat_notice(self):
        assert "نُعيد ورد الأمس" not in render.wird_message(make_view(), "عبارة")

    def test_member_names_are_html_escaped(self):
        # HTML is the default parse mode, so a name with markup must not break it.
        view = make_view(done_names=["<b>خطر</b>"])
        assert "&lt;b&gt;" in render.wird_message(view, "عبارة")

    def test_keyboard_carries_the_task_id(self):
        markup = render.wird_keyboard(42)
        data = [b.callback_data for row in markup.inline_keyboard for b in row]
        assert data == ["done:42", "who:42"]


class TestShuffleBag:
    def test_exhausts_the_pool_before_repeating(self):
        bag = ShuffleBag(["a", "b", "c"])
        assert sorted(bag.pick("g") for _ in range(3)) == ["a", "b", "c"]

    def test_keys_are_independent(self):
        bag = ShuffleBag(["only"])
        assert bag.pick("group1") == "only"
        assert bag.pick("group2") == "only"

    def test_refills_after_exhaustion(self):
        bag = ShuffleBag(["a", "b"])
        assert len([bag.pick("g") for _ in range(6)]) == 6

    def test_empty_pool_is_rejected(self):
        with pytest.raises(ValueError):
            ShuffleBag([])


class TestApprovedPhrases:
    @pytest.mark.parametrize(
        ("pool", "expected"),
        [
            (SEND, 8),
            (REMINDER_FIRST, 5),
            (REMINDER_SECOND, 3),
            (REMINDER_LAST, 5),
            (MISSED, 4),
            (DONE, 4),
            (ALL_DONE, 2),
            (WEEKLY_CLOSER, 4),
        ],
    )
    def test_pool_sizes_match_the_approved_review(self, pool, expected):
        assert len(pool) == expected

    def test_total_is_the_thirty_five_reusable_phrases(self):
        pools = [
            SEND,
            REMINDER_FIRST,
            REMINDER_SECOND,
            REMINDER_LAST,
            MISSED,
            DONE,
            ALL_DONE,
            WEEKLY_CLOSER,
        ]
        assert sum(len(p) for p in pools) == 35

    def test_rejected_phrases_are_absent(self):
        everything = "\n".join(
            p for pool in (SEND, REMINDER_FIRST, REMINDER_SECOND, REMINDER_LAST) for p in pool
        )
        # r1e, r2b and r2d were rejected in review.
        assert "وصفحاتٌ قليلة تفصلك عن أجر اليوم" not in everything
        assert "وَسَارِعُوا إِلَىٰ مَغْفِرَةٍ" not in everything
        assert "الماهر بالقرآن مع السفرة" not in everything

    def test_missed_phrases_accept_the_day_count(self):
        formatted = [p.format(n="٣") for p in MISSED]
        assert any("٣" in f for f in formatted)

    def test_reminder_pool_selection_by_sequence(self):
        p = Phrases()
        assert p.reminder(1) is p.reminder_first
        assert p.reminder(2) is p.reminder_second
        assert p.reminder(3) is p.reminder_last
        # Anything beyond the configured maximum reuses the last pool.
        assert p.reminder(9) is p.reminder_last
