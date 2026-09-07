"""Rendering the Arabic wird message and its keyboard."""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from ..domain.schemas import TOTAL_PAGES, DaySummaryView, ReplaceOffer, WirdView
from ..tg.mentions import mention, safe_name
from . import ar
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

# Arabic separates thousands with ٬ (U+066C), not the Latin comma, which reads
# as a decimal point beside Arabic-Indic digits.
THOUSANDS = "\u066c"


@dataclass(frozen=True)
class Noun:
    """One noun in the forms Arabic counting needs.

    A number does not simply sit in front of a noun in Arabic: it changes the
    noun's form and its case. "٩ مشتركًا" is wrong the way "9 subscriber" is
    wrong in English, and a dashboard full of counts shows every mistake.
    """

    one: str  # مشترك واحد
    two: str  # مشتركان
    few: str  # ٥ مشتركين — for 3 to 10
    many: str  # ٢٤ مشتركًا — for 11 to 99
    bare: str  # ١٠٠ مشترك — for round hundreds, and for "no ..."


def counted(value: int, noun: Noun) -> str:
    """A count with its noun in the right form, in Arabic-Indic digits.

    The tamyiz follows the last part of the number, so 103 counts like 3 —
    hence the modulo rather than a test on the whole value.
    """
    remainder = value % 100
    if value == 0:
        return f"لا {noun.bare}"
    if value == 1:
        return noun.one
    if value == 2:
        return noun.two
    number = ar_num(f"{value:,}").replace(",", THOUSANDS)
    if 3 <= remainder <= 10:
        return f"{number} {noun.few}"
    if remainder == 0:
        return f"{number} {noun.bare}"
    return f"{number} {noun.many}"


NOUNS: dict[str, Noun] = {
    "group": Noun("مجموعة واحدة", "مجموعتان", "مجموعات", "مجموعة", "مجموعة"),
    "person": Noun("شخص واحد", "شخصان", "أشخاص", "شخصًا", "شخص"),
    "subscriber": Noun("مشترك واحد", "مشتركان", "مشتركين", "مشتركًا", "مشترك"),
    "member": Noun("عضو واحد", "عضوان", "أعضاء", "عضوًا", "عضو"),
    "day": Noun("يوم واحد", "يومان", "أيام", "يومًا", "يوم"),
    "wird": Noun("ورد واحد", "وردان", "أوراد", "وردًا", "ورد"),
    "completion": Noun("إنجاز واحد", "إنجازان", "إنجازات", "إنجازًا", "إنجاز"),
    "khatmah": Noun("ختمة واحدة", "ختمتان", "ختمات", "ختمة", "ختمة"),
    "page": Noun("صفحة واحدة", "صفحتان", "صفحات", "صفحة", "صفحة"),
    "mushaf": Noun("مصحف كامل", "مصحفان كاملان", "مصاحف كاملة", "مصحفًا كاملًا", "مصحف كامل"),
    "subscription": Noun("اشتراك واحد", "اشتراكان", "اشتراكات", "اشتراكًا", "اشتراك"),
}

CB_DONE = "done"
CB_WHO = "who"
CB_REPLACE = "redo"


def ar_num(value: int | str) -> str:
    """Western digits to Arabic-Indic, for text the group reads."""
    return str(value).translate(_ARABIC_DIGITS)


def ar_date(day: dt.date) -> str:
    return f"{WEEKDAY_NAMES[day.weekday()]} {ar_num(day.day)} {MONTH_NAMES[day.month - 1]}"


def pages_label(start: int, end: int) -> str:
    """Renders "page N", or "pages N – M", in Arabic-Indic digits."""
    if start == end:
        return f"الصفحة {ar_num(start)}"
    return f"الصفحات {ar_num(start)} – {ar_num(end)}"


def pages_line(view: WirdView) -> str:
    label = pages_label(view.pages.start, view.pages.end)
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


REPEAT_NOTICE_NONE = "🔁 <i>نُعيد ورد الأمس — لم يُتمّه أحد.</i>"
REPEAT_NOTICE_PARTIAL = "🔁 <i>نُعيد ورد الأمس — لم يُتمّه الجميع بعد.</i>"


def repeat_notice(repeat_done_count: int) -> str:
    """Why the same pages come round again.

    Under the default `anyone` rule a repeat always means nobody read, but the
    stricter rules repeat a wird that some members did finish, and telling them
    they read nothing would be wrong.
    """
    return REPEAT_NOTICE_NONE if repeat_done_count == 0 else REPEAT_NOTICE_PARTIAL


def wird_message(view: WirdView, phrase: str) -> str:
    parts = [
        f"📖 <b>ورد اليوم</b> — {ar_date(view.task_date)}",
        pages_line(view),
    ]
    if view.is_repeat:
        parts.append(f"\n{repeat_notice(view.repeat_done_count)}")
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


def replace_offer(offer: ReplaceOffer) -> str:
    """Ask an admin to confirm taking today's wird back and sending the new one."""
    parts = [
        ar.REPLACE_OFFER.format(
            current=pages_label(offer.current.start, offer.current.end),
            proposed=pages_label(offer.proposed.start, offer.proposed.end),
        )
    ]
    if offer.done_count:
        parts.append(ar.REPLACE_OFFER_DONE.format(count=ar_num(offer.done_count)))
    parts.append(ar.REPLACE_OFFER_HINT)
    return "\n\n".join(parts)


def replace_keyboard(task_id: int) -> InlineKeyboardMarkup:
    """The single confirm button under a replacement offer.

    The task id travels in the payload so a button pressed long after the fact —
    a second admin, or the next day — is recognised as stale rather than acted on.
    """
    button = InlineKeyboardButton(
        "🔄 نعم، استبدل ورد اليوم", callback_data=f"{CB_REPLACE}:{task_id}"
    )
    return InlineKeyboardMarkup([[button]])


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
    parts = [f"⏰ <b>تذكير بورد اليوم</b> — {pages_label(start, end)}", ""]
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


def day_summary(view: DaySummaryView) -> str:
    """The short report posted when the day closes.

    It names the finishers and nobody else: who fell behind is said privately in
    the reminders, never announced to the group.
    """
    parts = [
        f"🌙 <b>خُلاصة اليوم</b> — {ar_date(view.task_date)}",
        pages_label(view.pages.start, view.pages.end),
        "",
    ]

    if view.subscriber_count:
        rate = ar_num(round(view.completion_rate))
        parts.append(
            f"✅ <b>أنجز {ar_num(view.done_count)} من {ar_num(view.subscriber_count)}</b> ({rate}٪)"
        )
    else:
        parts.append(f"✅ <b>أنجز {ar_num(view.done_count)}</b>")

    if view.done_names:
        shown = "، ".join(safe_name(n) for n in view.done_names[:MAX_NAMES_SHOWN])
        more = len(view.done_names) - MAX_NAMES_SHOWN
        if more > 0:
            shown += f" و{ar_num(more)} غيرهم"
        parts.append(shown)
    else:
        parts.append("لم يُتمّ الورد أحدٌ اليوم… وغدًا يومٌ جديد 🌿")

    # A streak of one day is just today; only a real run is worth announcing.
    if view.top_streak_name and view.top_streak_days > 1:
        parts.append(
            f"\n🔥 أطول سلسلة: {safe_name(view.top_streak_name)}"
            f" — {ar_num(view.top_streak_days)} يومًا"
        )

    parts.append(f"\n{next_wird_line(view)}")
    return "\n".join(parts)


def next_wird_line(view: DaySummaryView) -> str:
    """What tomorrow holds: new pages, the same ones again, or a new khatmah."""
    if view.khatmah_completed:
        return "🎉 تمّت الختمة بحمد الله — وغدًا نبدأ ختمة جديدة من أوّل المصحف."
    if view.advanced and view.next_pages is not None:
        return f"➡️ ورد الغد: {pages_label(view.next_pages.start, view.next_pages.end)}"
    if view.done_count == 0:
        return "🔁 نُعيد صفحات اليوم غدًا بإذن الله — وباب الخير لا يُغلق."
    return "🔁 لم يكتمل الورد للجميع، فنُعيد صفحات اليوم غدًا بإذن الله."
