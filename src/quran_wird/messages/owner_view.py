"""The owner panel: its Arabic screens and its button layouts.

Read by one person about every group at once, so it says nothing a group would
want and everything an operator would: how many groups there are, how many are
still reading, and how far the khatmahs have travelled between them.

Counts go through `render.counted`, because a screen that is almost entirely
numbers is a screen where every plural has to agree. Group titles come from
Telegram and are escaped like any other name — an owner reading a list of groups
is still reading text other people chose.
"""

from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from ..domain.schemas import TOTAL_PAGES, GroupDigest, OwnerStats
from ..tg.mentions import safe_name
from .render import NOUNS, THOUSANDS, ar_date, ar_num, counted

CB = "own"

# Longer than this and the panel becomes a scroll rather than a reading.
MAX_LISTED = 10
MAX_NEAREST = 5


def num(value: int) -> str:
    """A bare count, thousands separated, for lines that supply their own noun."""
    return ar_num(f"{value:,}").replace(",", THOUSANDS)


def pct(value: float) -> str:
    return f"{ar_num(round(value))}٪"


def decimal(value: float) -> str:
    """One decimal place, with the Arabic decimal separator ٫ (U+066B)."""
    return ar_num(f"{value:.1f}").replace(".", "\u066b")


def clock(hour: int, minute: int) -> str:
    return f"{ar_num(f'{hour:02d}')}:{ar_num(f'{minute:02d}')}"


def group_name(digest: GroupDigest) -> str:
    """The group's title, or its id when Telegram never gave the bot one."""
    return safe_name(digest.title or f"#{digest.chat_id}")


# ------------------------------------------------------------------- buttons


def _btn(label: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(label, callback_data=f"{CB}:{data}")


def _nav() -> list[list[InlineKeyboardButton]]:
    return [
        [_btn("🏘️ المجموعات", "go:groups"), _btn("🧍 الأعضاء", "go:members")],
        [_btn("📈 النشاط", "go:activity"), _btn("📖 الختمات", "go:khatmahs")],
        [_btn("🔄 تحديث", "go:home"), _btn("✕ إغلاق", "done")],
    ]


def _back() -> list[list[InlineKeyboardButton]]:
    return [[_btn("‹ رجوع", "go:home"), _btn("🔄 تحديث", "go:home")]]


# --------------------------------------------------------------------- home


def home(stats: OwnerStats) -> tuple[str, InlineKeyboardMarkup]:
    """The one screen that answers "how is the bot doing" without a second press."""
    text = "\n".join(
        [
            "👑 <b>لوحة المالك</b>",
            "",
            f"🏘️ <b>{counted(stats.groups_total, NOUNS['group'])}</b>"
            f" — {num(stats.groups_active)} نشطة · {num(stats.groups_dormant)} متوقّفة",
            f"🧍 <b>{counted(stats.unique_users, NOUNS['person'])}</b>"
            f" · {counted(stats.subscriptions_active, NOUNS['subscription'])} نشطًا",
            f"📖 <b>{counted(stats.khatmahs_completed, NOUNS['khatmah'])}</b> مكتملة"
            f" · <b>{num(stats.khatmahs_running)}</b> جارية الآن",
            f"📄 <b>{counted(stats.pages_read, NOUNS['page'])}</b> مقروءة"
            f" — نحو {counted(stats.khatmahs_equivalent, NOUNS['mushaf'])}",
            f"✅ معدّل الإنجاز: <b>{pct(stats.completion_rate)}</b>",
            "",
            f"📈 اليوم: {counted(stats.wirds_today, NOUNS['wird'])}"
            f" · {counted(stats.completions_today, NOUNS['completion'])}",
            "",
            f"<i>حُدِّثت {ar_date(stats.generated_at.date())}"
            f" — {clock(stats.generated_at.hour, stats.generated_at.minute)}</i>",
        ]
    )
    return text, InlineKeyboardMarkup(_nav())


# ------------------------------------------------------------------- screens


def members(stats: OwnerStats) -> tuple[str, InlineKeyboardMarkup]:
    text = "\n".join(
        [
            "🧍 <b>الأعضاء</b>",
            "",
            f"<b>{counted(stats.unique_users, NOUNS['person'])}</b>",
            "<i>يُحسب الشخص مرّة واحدة مهما تعدّدت مجموعاته.</i>",
            "",
            f"الاشتراكات النشطة: <b>{num(stats.subscriptions_active)}</b>"
            f" من {num(stats.subscriptions_total)}",
            f"متوسّط المشتركين في المجموعة: <b>{decimal(stats.members_per_group)}</b>",
            "",
            f"🔥 على سلسلة جارية: <b>{counted(stats.members_on_streak, NOUNS['member'])}</b>",
            f"🏆 أطول سلسلة الآن: <b>{counted(stats.longest_streak, NOUNS['day'])}</b>",
            "",
            f"✅ أُتمّت: <b>{counted(stats.days_done, NOUNS['day'])}</b>",
            f"⏳ فاتت: <b>{counted(stats.days_missed, NOUNS['day'])}</b>",
            f"📊 معدّل الإنجاز: <b>{pct(stats.completion_rate)}</b>",
        ]
    )
    return text, InlineKeyboardMarkup(_back())


def activity(stats: OwnerStats) -> tuple[str, InlineKeyboardMarkup]:
    text = "\n".join(
        [
            "📈 <b>النشاط</b>",
            "",
            f"<b>اليوم:</b> {counted(stats.wirds_today, NOUNS['wird'])}"
            f" · {counted(stats.completions_today, NOUNS['completion'])}",
            f"<b>آخر سبعة أيام:</b> {counted(stats.wirds_week, NOUNS['wird'])}"
            f" · {counted(stats.completions_week, NOUNS['completion'])}",
            "",
            f"🏘️ قرأت هذا الأسبوع: <b>{num(stats.groups_reading_week)}</b>"
            f" من {counted(stats.groups_active, NOUNS['group'])} نشطة",
            f"✨ مجموعات جديدة: <b>{num(stats.groups_new_week)}</b> هذا الأسبوع"
            f" · {num(stats.groups_new_month)} هذا الشهر",
            "",
            "<b>منذ البداية</b>",
            f"📤 الأوراد المُرسَلة: <b>{num(stats.wirds_total)}</b>",
            f"✅ الإنجازات المسجّلة: <b>{num(stats.completions_total)}</b>",
            "",
            "<i>يوم الورد محلّي لكل مجموعة، فعدّ «اليوم» تقريبيّ عبر المناطق الزمنية.</i>",
        ]
    )
    return text, InlineKeyboardMarkup(_back())


def khatmahs(stats: OwnerStats, nearest: list[GroupDigest]) -> tuple[str, InlineKeyboardMarkup]:
    parts = [
        "📖 <b>الختمات</b>",
        "",
        f"✅ مكتملة: <b>{counted(stats.khatmahs_completed, NOUNS['khatmah'])}</b>",
        f"🔄 جارية الآن: <b>{counted(stats.khatmahs_running, NOUNS['khatmah'])}</b>",
        f"📄 <b>{counted(stats.pages_read, NOUNS['page'])}</b> مقروءة"
        f" — نحو {counted(stats.khatmahs_equivalent, NOUNS['mushaf'])}",
    ]
    if nearest:
        parts += ["", "<b>الأقرب إلى الختم:</b>"]
        parts += [
            f"{ar_num(index)}. {group_name(digest)}"
            f" — الصفحة {ar_num(digest.current_page)} · {pct(digest.percent)}"
            + ("" if digest.khatmah_number == 1 else f" · الختمة {ar_num(digest.khatmah_number)}")
            for index, digest in enumerate(nearest, start=1)
        ]
    return "\n".join(parts), InlineKeyboardMarkup(_back())


def groups(stats: OwnerStats, digests: list[GroupDigest]) -> tuple[str, InlineKeyboardMarkup]:
    parts = [
        f"🏘️ <b>{counted(stats.groups_total, NOUNS['group'])}</b>"
        f" — {num(stats.groups_active)} نشطة · {num(stats.groups_dormant)} متوقّفة",
    ]
    if not digests:
        parts += ["", "لا مجموعة بعد."]
        return "\n".join(parts), InlineKeyboardMarkup(_back())

    # The heading only claims a "largest" when the list is actually a selection.
    heading = (
        f"<b>الأكبر ({num(len(digests))} من {num(stats.groups_total)}):</b>"
        if len(digests) < stats.groups_total
        else "<b>كلّها:</b>"
    )
    parts += ["", heading, ""]
    for index, digest in enumerate(digests, start=1):
        paused = "" if digest.is_active else " ⏸️"
        last = f"آخر ورد {ar_date(digest.last_wird)}" if digest.last_wird else "لا ورد بعد"
        parts += [
            f"{ar_num(index)}. <b>{group_name(digest)}</b>{paused}"
            f" — {counted(digest.subscribers, NOUNS['subscriber'])}",
            f"    الصفحة {ar_num(digest.current_page)} من {ar_num(TOTAL_PAGES)}"
            f" · الختمة {ar_num(digest.khatmah_number)} · {last}",
        ]
    return "\n".join(parts), InlineKeyboardMarkup(_back())
