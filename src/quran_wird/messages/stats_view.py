"""Rendering the reports: /me, /progress, /top, and the weekly honors board.

The board's template is the approved one from docs/PLAN.md section 7.9. Counts
go through `render.counted`, which reproduces that template exactly for the
numbers it was written with and fixes the ones it was not — «٣ يومًا» was as
wrong as "3 day", and the template simply never showed a number that small.

Names here are plain text, escaped, never mentions. That is a settled decision:
the honors board is a celebration, not a notification, and the only place the
bot taps someone on the shoulder is a reminder aimed at them.
"""

from __future__ import annotations

import datetime as dt

from ..domain.progress import progress_bar
from ..domain.schemas import TOTAL_PAGES, MemberProgress
from ..domain.weekly import MemberWeek, WeekSummary
from ..tg.mentions import safe_name
from .phrases import WEEK_EVERYONE_PERFECT, WEEK_NOBODY_PERFECT, phrases
from .render import MONTH_NAMES, NOUNS, ar_date, ar_num, counted

# Medals for the first three places; the rest are numbered.
MEDALS = ("🥇", "🥈", "🥉")

# Longer than this and the honors board becomes a scroll rather than a list.
MAX_BOARD_NAMES = 12
MAX_LEADERBOARD = 10


def day_month(day: dt.date) -> str:
    return f"{ar_num(day.day)} {MONTH_NAMES[day.month - 1]}"


def date_range(start: dt.date, end: dt.date) -> str:
    return f"{ar_date(start)} — {ar_date(end)}"


def about(value: int) -> str:
    """The "roughly" marker, dropped when the count shows no digit.

    One and two are words in Arabic — «أسبوعين», not «٢ أسبوع» — and a tilde in
    front of a word reads as a typo rather than an approximation. The template
    in docs/PLAN.md section 7.9 keeps its «~١٤ أسبوعًا» unchanged.
    """
    return "~" if value > 2 else ""


def place(index: int) -> str:
    return MEDALS[index] if index < len(MEDALS) else f"{ar_num(index + 1)}."


# ------------------------------------------------------------------- /me


def member_report(progress: MemberProgress, *, subscribed: bool, badges: int = 0) -> str:
    """One member's own record, replied to them in the group."""
    parts = [f"📊 <b>{safe_name(progress.display_name)}</b>", ""]

    if progress.current_streak:
        streak = counted(progress.current_streak, NOUNS["streak_day"])
        parts.append(f"🔥 <b>{streak}</b> — لا تقطعها")
    else:
        parts.append("🔥 لا سلسلة جارية — ورد اليوم يبدأ واحدة جديدة")

    parts.append(f"✅ أتممت: <b>{counted(progress.total_done, NOUNS['day'], oblique=True)}</b>")
    if progress.best_streak > progress.current_streak:
        best = counted(progress.best_streak, NOUNS["day"])
        parts.append(f"🏆 أطول سلسلة لك: <b>{best}</b>")
    if progress.total_missed:
        parts.append(f"⏳ فاتك: {counted(progress.total_missed, NOUNS['day'])}")
    if badges:
        parts.append(f"🏅 <b>{counted(badges, NOUNS['perfect_week'])}</b> بلا تخلّف")
    if progress.last_done_date:
        parts.append(f"🕊 آخر ورد أتممته: {ar_date(progress.last_done_date)}")

    if not subscribed:
        parts.append("\n<i>لست مشتركًا — أرسل /join ليُحسب لك التقدّم وتظهر في لوحة الشرف.</i>")
    return "\n".join(parts)


# -------------------------------------------------------------- /progress


def khatmah_report(
    *,
    current_page: int,
    khatmah_number: int,
    started_on: dt.date | None,
    pages_per_day: int,
    subscriber_count: int,
    weeks_left: int | None = None,
) -> str:
    """The group's progress through the mushaf."""
    done = current_page - 1
    percent = round(done / TOTAL_PAGES * 100)
    left = TOTAL_PAGES - done

    parts = [
        f"📖 <b>تقدّم الختمة رقم {ar_num(khatmah_number)}</b>",
        "",
        f"{progress_bar(current_page)} {ar_num(percent)}٪",
        f"الصفحة <b>{ar_num(current_page)}</b> من {ar_num(TOTAL_PAGES)}"
        f" · بقيت {counted(left, NOUNS['page'])}",
    ]
    if started_on:
        parts.append(f"بدأنا في {day_month(started_on)}")
    parts.append("")
    parts.append(
        f"🗓 المعدّل: {counted(pages_per_day, NOUNS['page'])} في اليوم"
        f" · {counted(subscriber_count, NOUNS['subscriber'])}"
    )
    if weeks_left:
        weeks = counted(weeks_left, NOUNS["week"], oblique=True)
        parts.append(f"⏳ على هذا المعدّل نختم خلال {about(weeks_left)}{weeks} بإذن الله")
    return "\n".join(parts)


# ------------------------------------------------------------------- /top


def leaderboard(rows: list[MemberProgress]) -> str:
    """The standings, by current streak then by total days read."""
    if not rows:
        return "لا إحصاءات بعد — أوّل ورد يبدأ السجلّ 🌿"

    lines = ["🏆 <b>المتصدّرون</b>", ""]
    for index, member in enumerate(rows[:MAX_LEADERBOARD]):
        streak = f"🔥 {ar_num(member.current_streak)}" if member.current_streak else "🔥 —"
        lines.append(
            f"{place(index)} {safe_name(member.display_name)}"
            f" — {streak} · ✅ {ar_num(member.total_done)}"
        )
    lines.append("\n<i>الترتيب بالأيام المتتالية، ثم بمجموع ما أُتمّ.</i>")
    return "\n".join(lines)


# --------------------------------------------------------- weekly board


def board_line(index: int, entry: MemberWeek) -> str:
    return (
        f"   {place(index)} {safe_name(entry.member.display_name)}"
        f" — {ar_num(entry.done)}/{ar_num(entry.eligible)}"
    )


def weekly_report(
    *,
    chat_id: int,
    week_start: dt.date,
    week_end: dt.date,
    summary: WeekSummary,
    current_page: int,
    khatmah_number: int,
    top_streak: MemberProgress | None,
    weeks_left: int | None,
) -> str:
    """The Friday honors board, following the approved template."""
    done = current_page - 1
    percent = round(done / TOTAL_PAGES * 100)

    parts = [
        "🏅 <b>لوحة شرف الأسبوع</b>",
        date_range(week_start, week_end),
        "",
    ]

    perfect = summary.perfect
    if summary.everyone_perfect:
        parts.append(WEEK_EVERYONE_PERFECT)
    elif perfect:
        parts.append("✨ <b>أتمّوا الأسبوع كاملًا بلا تخلّف:</b>")
        parts.extend(board_line(i, entry) for i, entry in enumerate(perfect[:MAX_BOARD_NAMES]))
        more = len(perfect) - MAX_BOARD_NAMES
        if more > 0:
            parts.append(f"   و{ar_num(more)} غيرهم")
    else:
        parts.append(WEEK_NOBODY_PERFECT)

    if top_streak and top_streak.current_streak > 1:
        parts.append(
            f"\n🔥 <b>أطول سلسلة متتالية:</b> {safe_name(top_streak.display_name)}"
            f" — {counted(top_streak.current_streak, NOUNS['day'])}"
        )

    parts.append(
        f"\n📖 <b>تقدّم الختمة (رقم {ar_num(khatmah_number)}):</b>\n"
        f"   {progress_bar(current_page)} {ar_num(percent)}٪\n"
        f"   الصفحة {ar_num(current_page)} من {ar_num(TOTAL_PAGES)}"
        f" · بقيت {counted(TOTAL_PAGES - done, NOUNS['page'])}\n"
        f"   قرأنا هذا الأسبوع: {counted(summary.pages_read, NOUNS['page'])}"
        f" في {counted(summary.active_days, NOUNS['day'], oblique=True)}"
    )

    if summary.possible_completions:
        parts.append(
            f"\n📊 <b>إنجاز المجموعة:</b> {ar_num(round(summary.completion_rate))}٪"
            f" ({ar_num(summary.completions)} من {ar_num(summary.possible_completions)})"
        )

    if weeks_left:
        weeks = counted(weeks_left, NOUNS["week"], oblique=True)
        parts.append(f"\n⏳ على هذا المعدّل نختم بإذن الله خلال {about(weeks_left)}{weeks}")

    parts.append(f"\n{phrases.weekly_closer.pick(chat_id)}")
    return "\n".join(parts)
