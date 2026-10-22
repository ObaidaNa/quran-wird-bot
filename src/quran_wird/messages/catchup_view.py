"""Rendering the private catch-up plan: pieces, nudges, and the status view.

Arabic wording reviewed in docs/PHRASES-CATCHUP.md. Everything here is
private-chat text except the group announcement, which lives in ar.py.
"""

from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from ..tg.mentions import safe_name
from .render import NOUNS, counted, pages_label

# Callback payload prefixes. The chat id travels in the payload because a
# member may have a plan in more than one group; the private chat itself
# identifies nothing.
CB_DONE = "cud"
CB_SHOW = "cus"
CB_PAUSE = "cup"
CB_RESUME = "cur"


def cb_data(prefix: str, chat_id: int) -> str:
    return f"{prefix}:{chat_id}"


def state_note(state: str) -> str:
    """One line on where the plan stands, for /catchup. Keyed by the stored
    string, so this layer never imports the models."""
    return {
        "active": "مقطعك يصلك تباعًا… وبالزر تطلبه الآن.",
        "paused": "متوقف مؤقتًا — بالاستئناف يعود.",
        "dormant": "نائم… ومتى ضغطت الزر عاد يعمل من حيث وقف.",
        "blocked": "لا أستطيع مراسلتك — ألغِ حظر البوت ثم اضغط استئناف.",
        "caught_up": "لحقتَ بإخوتك 🎉 — وردك الآن مع المجموعة أولًا بأول.",
    }.get(state, "")


def next_piece_keyboard(chat_id: int) -> InlineKeyboardMarkup:
    """Offered after a finished piece: pull the next one instead of waiting."""
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("📖 المقطع التالي الآن", callback_data=cb_data(CB_SHOW, chat_id))]]
    )


def piece_caption(pages: list[int], group_title: str | None = None) -> str:
    """Caption under the original mushaf page photos."""
    title = f"\n{safe_name(group_title)}" if group_title else ""
    return f"🌱 مقطع التدارك — {pages_label(pages[0], pages[-1])}{title}"


def piece_progress_note(pages_left: int) -> str:
    """The short text under a whole-page piece, carrying the button."""
    return f"🌱 <b>مقطع التدارك</b>\n\n<i>بقي عليك: {counted(pages_left, NOUNS['page'])}</i>"


def piece_keyboard(chat_id: int, token: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("✅ أتممت المقطع", callback_data=f"{CB_DONE}:{chat_id}:{token}")],
            [InlineKeyboardButton("⏸️ أمهلني", callback_data=cb_data(CB_PAUSE, chat_id))],
        ]
    )


def nudge_message(phrase: str, chat_id: int) -> tuple[str, InlineKeyboardMarkup]:
    """A knock on the door: one phrase and a button that brings the piece back."""
    keyboard = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📖 أرسل مقطعي", callback_data=cb_data(CB_SHOW, chat_id))],
            [InlineKeyboardButton("⏸️ أمهلني", callback_data=cb_data(CB_PAUSE, chat_id))],
        ]
    )
    return phrase, keyboard


def status_message(
    *, pages_left: int, dose: int, state_text: str, group_title: str | None = None
) -> str:
    """/catchup: where the plan stands, in two lines."""
    return "\n".join(
        [
            "🌱 <b>تداركك</b>" + (f" — {safe_name(group_title)}" if group_title else ""),
            f"بقي عليك: <b>{counted(pages_left, NOUNS['page'])}</b>"
            f" · حجم المقطع: {counted(dose, NOUNS['page'])}",
            state_text,
        ]
    )


def status_keyboard(chat_id: int, *, paused: bool, has_debt: bool) -> InlineKeyboardMarkup | None:
    buttons: list[list[InlineKeyboardButton]] = []
    if has_debt:
        buttons.append(
            [InlineKeyboardButton("📖 أرسل مقطعي", callback_data=cb_data(CB_SHOW, chat_id))]
        )
    if paused:
        buttons.append(
            [InlineKeyboardButton("▶️ استئناف", callback_data=cb_data(CB_RESUME, chat_id))]
        )
    return InlineKeyboardMarkup(buttons) if buttons else None
