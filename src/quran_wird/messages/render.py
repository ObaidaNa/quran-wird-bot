"""Rendering the Arabic wird message and its keyboard."""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from ..domain.schemas import TOTAL_PAGES, WirdView
from ..tg.mentions import mention, safe_name
from .phrases import phrases

_ARABIC_DIGITS = str.maketrans("0123456789", "٠١٢٣٤٥٦٧٨٩")

# Indexed by date.weekday(): 0=Monday .. 6=Sunday
WEEKDAY_NAMES = (
    "الاثنين",
    "الثلاثاء",
    "الأربعاء",
    "الخميس",
    "الجمعة",
    "السبت",
    "الأحد",
)

# Levantine month names, matching the default Asia/Damascus timezone.
MONTH_NAMES = (
    "كانون الثاني",
    "شباط",
    "آذار",
    "نيسان",
    "أيار",
    "حزيران",
    "تموز",
    "آب",
    "أيلول",
    "تشرين الأول",
    "تشرين الثاني",
    "كانون الأول",
)

# Beyond this, the finisher list is summarised rather than spelled out.
MAX_NAMES_SHOWN = 6

CB_DONE = "done"
CB_WHO = "who"


def ar_num(value: int | str) -> str:
    """Western digits to Arabic-Indic, for text the group reads."""
    return str(value).translate(_ARABIC_DIGITS)


def ar_date(day: dt.date) -> str:
    return f"{WEEKDAY_NAMES[day.weekday()]} {ar_num(day.day)} {MONTH_NAMES[day.month - 1]}"


def pages_line(view: WirdView) -> str:
    pages = view.pages
    if pages.count == 1:
        label = f"الصفحة {ar_num(pages.start)}"
    else:
        label = f"الصفحات {ar_num(pages.start)} – {ar_num(pages.end)}"
    return f"{label} · الجزء {ar_num(view.juz)} · {view.surah_names}"


def album_caption(view: WirdView) -> str:
    """Short caption under the images; the full message carries the rest."""
    return f"📖 ورد اليوم — {ar_date(view.task_date)}\n{pages_line(view)}"


def done_section(view: WirdView) -> str:
    if not view.done_names:
        return "لم يُنجز أحد بعد — كن أوّلهم 🌿"

    names = "  ".join(f"• {safe_name(n)}" for n in view.done_names)
    total = view.subscriber_count
    if total:
        header = f"✅ <b>أنجز {ar_num(view.done_count)} من {ar_num(total)}:</b>"
    else:
        header = f"✅ <b>أنجز {ar_num(view.done_count)}:</b>"
    return f"{header}\n{names}"


def wird_message(view: WirdView, phrase: str) -> str:
    parts = [
        f"📖 <b>ورد اليوم</b> — {ar_date(view.task_date)}",
        pages_line(view),
    ]
    if view.is_repeat:
        parts.append("\n🔁 <i>نُعيد ورد الأمس — لم يُتمّه أحد.</i>")
    parts.append(f"\n{phrase}\n")
    parts.append(done_section(view))
    return "\n".join(parts)


def wird_keyboard(task_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ قرأتُ وردي", callback_data=f"{CB_DONE}:{task_id}"),
                InlineKeyboardButton("📊 من أنجز؟", callback_data=f"{CB_WHO}:{task_id}"),
            ]
        ]
    )


def khatmah_progress_line(current_page: int, khatmah_number: int) -> str:
    from ..domain.progress import progress_bar

    done = current_page - 1
    percent = round(done / TOTAL_PAGES * 100)
    return (
        f"📖 <b>تقدّم الختمة (رقم {ar_num(khatmah_number)}):</b>\n"
        f"   {progress_bar(current_page)} {ar_num(percent)}٪\n"
        f"   الصفحة {ar_num(current_page)} من {ar_num(TOTAL_PAGES)} · "
        f"بقيت {ar_num(TOTAL_PAGES - done)} صفحة"
    )


def reminder_message(
    *,
    seq: int,
    chat_id: int,
    pages: tuple[int, int],
    batch: Sequence,
    missed: dict[int, int],
    done_names: Sequence[str],
    pending_total: int,
    subscriber_total: int,
) -> str:
    """One reminder message addressed to a batch of members.

    Only `batch` is mentioned, so a large group is reminded across several
    messages instead of one that pings everybody at once.
    """
    start, end = pages
    pages_label = (
        f"الصفحة {ar_num(start)}" if start == end else f"الصفحات {ar_num(start)} – {ar_num(end)}"
    )

    parts = [f"⏰ <b>تذكير بورد اليوم</b> — {pages_label}", ""]
    parts.append(" ".join(mention(m.user_id, m.display_name) for m in batch))
    parts.append("")
    parts.append(phrases.reminder(seq).pick(chat_id))

    if done_names:
        shown = "، ".join(safe_name(n) for n in done_names[:MAX_NAMES_SHOWN])
        more = len(done_names) - MAX_NAMES_SHOWN
        if more > 0:
            shown += f" و{ar_num(more)} غيرهم"
        parts.append(
            f"\n✅ أنجز {ar_num(len(done_names))} من {ar_num(subscriber_total)}: {shown}"
            f"\n⏳ وبقي {ar_num(pending_total)}"
        )
    else:
        parts.append(f"\n⏳ لم يُنجز أحد بعد من {ar_num(subscriber_total)} — كن أوّلهم 🌿")

    # The تقصير notice is per person, so it goes on its own line under the batch.
    notices = [
        f"🔸 {safe_name(m.display_name)}: "
        + phrases.missed.pick(chat_id).format(n=ar_num(missed[m.user_id]))
        for m in batch
        if m.user_id in missed
    ]
    if notices:
        parts.append("")
        parts.extend(notices)

    return "\n".join(parts)
