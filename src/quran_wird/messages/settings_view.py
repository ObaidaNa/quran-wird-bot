"""The /settings panel: its Arabic screens and its button layouts.

One message, edited in place, that walks between a home screen and one editor
per setting. Every value is changed by a button — there is no free text to type
and no conversation state to lose across a restart, which also means the panel
survives the bot being redeployed mid-edit.

`render.py` renders the wird; this renders the panel. Both are the message layer.
"""

from __future__ import annotations

import datetime as dt

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from ..db.models import AdvanceRule, Group, Subscriber
from ..domain.schemas import TOTAL_PAGES
from ..domain.settings import days_to_finish
from ..tg.mentions import safe_name
from .render import NOUNS, WEEKDAY_NAMES, ar_num, counted

CB = "cfg"

# Common timezones for the bot's audience, with the city the group would name.
TIMEZONES: tuple[tuple[str, str], ...] = (
    ("Asia/Damascus", "دمشق"),
    ("Asia/Beirut", "بيروت"),
    ("Asia/Amman", "عمّان"),
    ("Asia/Jerusalem", "القدس"),
    ("Asia/Riyadh", "الرياض"),
    ("Asia/Baghdad", "بغداد"),
    ("Asia/Dubai", "دبي"),
    ("Asia/Tehran", "طهران"),
    ("Asia/Karachi", "كراتشي"),
    ("Africa/Cairo", "القاهرة"),
    ("Africa/Khartoum", "الخرطوم"),
    ("Africa/Tripoli", "طرابلس"),
    ("Africa/Tunis", "تونس"),
    ("Africa/Algiers", "الجزائر"),
    ("Africa/Casablanca", "الدار البيضاء"),
    ("Europe/Istanbul", "إسطنبول"),
    ("Europe/Berlin", "برلين"),
    ("Europe/London", "لندن"),
    ("America/New_York", "نيويورك"),
    ("UTC", "توقيت غرينتش"),
)

CITY_BY_TZ = dict(TIMEZONES)

ADVANCE_LABELS: dict[AdvanceRule, str] = {
    AdvanceRule.ANYONE: "بإنجاز مشترك واحد",
    AdvanceRule.MAJORITY: "بإنجاز أكثر من النصف",
    AdvanceRule.ALL: "بإنجاز الجميع",
    AdvanceRule.ALWAYS: "كل يوم مهما كان",
}

ADVANCE_HINTS: dict[AdvanceRule, str] = {
    AdvanceRule.ANYONE: "تتقدّم الصفحات إن أتمّ الوردَ مشتركٌ واحد على الأقل.",
    AdvanceRule.MAJORITY: "لا تتقدّم الصفحات حتى يُتمّه أكثر من نصف المشتركين.",
    AdvanceRule.ALL: "لا تتقدّم الصفحات حتى يُتمّه كل المشتركين — قد يُعيد الورد كثيرًا.",
    AdvanceRule.ALWAYS: "تتقدّم الصفحات كل يوم ولو لم يُتمّه أحد.",
}

# Short weekday names, indexed like date.weekday(): 0=Monday … 6=Sunday.
SHORT_WEEKDAYS = ("اثنين", "ثلاثاء", "أربعاء", "خميس", "جمعة", "سبت", "أحد")


def clock(value: dt.time | None) -> str:
    if value is None:
        return "—"
    return f"{ar_num(f'{value.hour:02d}')}:{ar_num(f'{value.minute:02d}')}"


def city(timezone: str) -> str:
    return CITY_BY_TZ.get(timezone, timezone)


def weekdays_label(days: list[int] | None) -> str:
    chosen = sorted(days or [])
    if len(chosen) == 7:
        return "كل الأيام"
    if not chosen:
        return "لا يوم"
    return "، ".join(SHORT_WEEKDAYS[d] for d in chosen)


def quiet_label(group: Group) -> str:
    if group.quiet_hours_start is None or group.quiet_hours_end is None:
        return "معطّلة"
    return f"{clock(group.quiet_hours_start)} – {clock(group.quiet_hours_end)}"


# ------------------------------------------------------------------- buttons


def _btn(label: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(label, callback_data=f"{CB}:{data}")


def _value(label: str) -> InlineKeyboardButton:
    """A button that shows a value and does nothing when pressed."""
    return _btn(label, "noop")


def _back() -> list[InlineKeyboardButton]:
    return [_btn("‹ رجوع", "home")]


def _stepper(key: str, label: str, *, kind: str = "n", step: int = 1) -> list[InlineKeyboardButton]:
    """A − value + row. The middle button is inert and carries the reading."""
    return [
        _btn("−", f"{kind}:{key}:{-step}"),
        _value(label),
        _btn("+", f"{kind}:{key}:{step}"),
    ]


# --------------------------------------------------------------------- home


def home(group: Group, subscriber_count: int) -> tuple[str, InlineKeyboardMarkup]:
    khatmah_days = days_to_finish(group.pages_per_day)
    reminders = (
        f"{ar_num(group.reminder_max_count)} · أوّلها بعد {ar_num(group.first_reminder_after_hours)}"
        f" ساعات ثم كل {ar_num(group.reminder_interval_hours)}"
        if group.reminder_max_count
        else "معطّلة"
    )
    weekly = (
        f"{WEEKDAY_NAMES[group.weekly_report_weekday]} {clock(group.weekly_report_time)}"
        if group.weekly_report_enabled
        else "معطّل"
    )
    state = "" if group.is_active else "\n\n⏸️ <b>الورد موقوف مؤقتًا</b> — لن تُرسل أي صفحات."

    text = (
        "⚙️ <b>إعدادات الورد</b>\n"
        f"المشتركون: {ar_num(subscriber_count)}\n\n"
        f"📖 الصفحات اليومية: <b>{counted(group.pages_per_day, NOUNS['page'])}</b>"
        f" · ختمة كل {ar_num(khatmah_days)} يوم قراءة\n"
        f"🕔 موعد الإرسال: <b>{clock(group.send_time)}</b>\n"
        f"📅 أيام الورد: <b>{weekdays_label(group.active_weekdays)}</b>\n"
        f"⏰ التذكيرات: <b>{reminders}</b>\n"
        f"🌙 ساعات الهدوء: <b>{quiet_label(group)}</b>\n"
        f"🌍 التوقيت: <b>{city(group.timezone)}</b>\n"
        f"📈 تقدّم الصفحات: <b>{ADVANCE_LABELS[group.advance_rule]}</b>\n"
        f"🏅 تقرير الأسبوع: <b>{weekly}</b>\n"
        f"📄 الصفحة الحالية: <b>{ar_num(group.current_page)}</b>"
        f" من {ar_num(TOTAL_PAGES)} · الختمة رقم {ar_num(group.khatmah_number)}"
        f"{state}"
    )

    rows = [
        [_btn("📖 الصفحات", "go:pages"), _btn("🕔 الإرسال", "go:send")],
        [_btn("📅 الأيام", "go:days"), _btn("⏰ التذكيرات", "go:rem")],
        [_btn("🌙 الهدوء", "go:quiet"), _btn("🌍 التوقيت", "go:tz")],
        [_btn("📈 التقدّم", "go:advance"), _btn("🏅 الأسبوعي", "go:weekly")],
        [_btn("📄 الصفحة الحالية", "go:page"), _btn("🔄 الختمة", "go:khatmah")],
        [_btn("👥 المشتركون", "go:subs")],
        [
            _btn("▶️ استئناف الورد" if not group.is_active else "⏸️ إيقاف مؤقت", "go:pause"),
            _btn("✅ تمّ", "done"),
        ],
    ]
    return text, InlineKeyboardMarkup(rows)


# ------------------------------------------------------------------ editors


def pages(group: Group) -> tuple[str, InlineKeyboardMarkup]:
    text = (
        "📖 <b>الصفحات اليومية</b>\n\n"
        f"القيمة الآن: <b>{counted(group.pages_per_day, NOUNS['page'])}</b> في اليوم\n"
        f"بهذا المعدّل تكتمل الختمة في نحو "
        f"<b>{counted(days_to_finish(group.pages_per_day), NOUNS['day'], oblique=True)}</b>"
        f" من القراءة.\n\n"
        "<i>فوق عشر صفحات يُقسَّم الألبوم إلى أكثر من رسالة.</i>\n"
        "<i>وإن كان ورد اليوم قد أُرسل، فالأمر /sendnow يعرض استبداله بالعدد الجديد.</i>"
    )
    rows = [_stepper("pages", counted(group.pages_per_day, NOUNS["page"])), _back()]
    return text, InlineKeyboardMarkup(rows)


def send(group: Group) -> tuple[str, InlineKeyboardMarkup]:
    text = (
        "🕔 <b>موعد إرسال الورد</b>\n\n"
        f"القيمة الآن: <b>{clock(group.send_time)}</b> بتوقيت {city(group.timezone)}\n\n"
        "<i>وقت التذكيرات يُحسب من لحظة الإرسال، فتغييره يُزيح التذكيرات معه.</i>\n"
        "<i>والتثبيت يُبقي الورد في أعلى المجموعة نهارَه، ويُرفع عند إغلاق اليوم "
        "(يحتاج أن يكون البوت مشرفًا).</i>"
    )
    rows = [
        [_btn("− ساعة", "t:send:-60"), _value(clock(group.send_time)), _btn("+ ساعة", "t:send:60")],
        [_btn("− ٣٠ د", "t:send:-30"), _btn("+ ٣٠ د", "t:send:30")],
        [_btn("📌 تثبيت الورد: مفعّل" if group.pin_wird else "📌 تثبيت الورد: معطّل", "pin")],
        [_btn("🌙 وقت إغلاق اليوم", "go:close")],
        _back(),
    ]
    return text, InlineKeyboardMarkup(rows)


def close_time(group: Group) -> tuple[str, InlineKeyboardMarkup]:
    text = (
        "🌙 <b>وقت إغلاق اليوم</b>\n\n"
        f"القيمة الآن: <b>{clock(group.day_close_time)}</b>\n\n"
        "<i>عنده تُحسب سلاسل الإنجاز، وتتقدّم الصفحات، وتُنشر خُلاصة اليوم.</i>"
    )
    rows = [
        [
            _btn("− ساعة", "t:close:-60"),
            _value(clock(group.day_close_time)),
            _btn("+ ساعة", "t:close:60"),
        ],
        [_btn("− ٣٠ د", "t:close:-30"), _btn("+ ٣٠ د", "t:close:30")],
        _back(),
    ]
    return text, InlineKeyboardMarkup(rows)


def days(group: Group) -> tuple[str, InlineKeyboardMarkup]:
    chosen = set(group.active_weekdays or [])
    text = (
        "📅 <b>أيام الورد</b>\n\n"
        f"الأيام المُفعّلة: <b>{weekdays_label(group.active_weekdays)}</b>\n\n"
        "<i>في اليوم المعطّل لا يُرسل ورد ولا تذكير، والصفحات لا تتقدّم. "
        "ولا بدّ من إبقاء يوم واحد على الأقل.</i>"
    )
    marks = [
        _btn(f"{'✅' if d in chosen else '▫️'} {SHORT_WEEKDAYS[d]}", f"d:{d}") for d in range(7)
    ]
    rows = [marks[:4], marks[4:], [_btn("🗓 بداية الأسبوع", "go:wstart")], _back()]
    return text, InlineKeyboardMarkup(rows)


def reminders(group: Group) -> tuple[str, InlineKeyboardMarkup]:
    text = (
        "⏰ <b>التذكيرات</b>\n\n"
        "تُرسل للمشتركين الذين لم يُتمّوا الورد، بالمنشن، على دفعات.\n"
        f"العدد <b>{ar_num(group.reminder_max_count)}</b> ·"
        f" أوّلها بعد <b>{ar_num(group.first_reminder_after_hours)}</b> ساعات ·"
        f" ثم كل <b>{ar_num(group.reminder_interval_hours)}</b> ساعات\n\n"
        "<i>اجعل العدد صفرًا لإيقاف التذكيرات كلها.</i>"
    )
    rows = [
        _stepper("rmax", f"العدد: {ar_num(group.reminder_max_count)}"),
        _stepper("rfirst", f"الأول بعد: {ar_num(group.first_reminder_after_hours)} س"),
        _stepper("rstep", f"الفاصل: {ar_num(group.reminder_interval_hours)} س"),
        _stepper("mentions", f"منشن لكل رسالة: {ar_num(group.mentions_per_message)}"),
        _back(),
    ]
    return text, InlineKeyboardMarkup(rows)


def quiet(group: Group) -> tuple[str, InlineKeyboardMarkup]:
    enabled = group.quiet_hours_start is not None and group.quiet_hours_end is not None
    text = (
        "🌙 <b>ساعات الهدوء</b>\n\n"
        f"الحال: <b>{quiet_label(group)}</b>\n\n"
        "<i>أي تذكير يقع داخل هذه الساعات يُتخطّى ولا يُؤجَّل، فلا يُوقظ أحدًا.</i>"
    )
    if enabled:
        rows = [
            _stepper("qstart", f"من: {clock(group.quiet_hours_start)}", kind="t", step=30),
            _stepper("qend", f"إلى: {clock(group.quiet_hours_end)}", kind="t", step=30),
            [_btn("🔕 تعطيل ساعات الهدوء", "quiet:off")],
            _back(),
        ]
    else:
        rows = [[_btn("🔔 تفعيل ساعات الهدوء", "quiet:on")], _back()]
    return text, InlineKeyboardMarkup(rows)


def timezone(group: Group) -> tuple[str, InlineKeyboardMarkup]:
    text = (
        "🌍 <b>التوقيت</b>\n\n"
        f"التوقيت الآن: <b>{city(group.timezone)}</b> (<code>{group.timezone}</code>)\n\n"
        "<i>كل المواعيد — الإرسال والتذكير والإغلاق — بتوقيت المجموعة لا بتوقيت الخادم.</i>"
    )
    buttons = [
        _btn(f"{'✅ ' if tz == group.timezone else ''}{name}", f"tz:{tz}") for tz, name in TIMEZONES
    ]
    rows = [buttons[i : i + 3] for i in range(0, len(buttons), 3)]
    rows.append(_back())
    return text, InlineKeyboardMarkup(rows)


def advance(group: Group) -> tuple[str, InlineKeyboardMarkup]:
    text = (
        "📈 <b>متى تتقدّم الصفحات؟</b>\n\n"
        f"القاعدة الآن: <b>{ADVANCE_LABELS[group.advance_rule]}</b>\n"
        f"{ADVANCE_HINTS[group.advance_rule]}\n\n"
        "<i>إن لم تتحقّق القاعدة يُعاد ورد الأمس نفسه في اليوم التالي.</i>"
    )
    rows = [
        [_btn(f"{'✅ ' if rule is group.advance_rule else ''}{label}", f"a:{rule.value}")]
        for rule, label in ADVANCE_LABELS.items()
    ]
    rows.append(_back())
    return text, InlineKeyboardMarkup(rows)


def weekly(group: Group) -> tuple[str, InlineKeyboardMarkup]:
    text = (
        "🏅 <b>تقرير الأسبوع ولوحة الشرف</b>\n\n"
        f"الحال: <b>{'مفعّل' if group.weekly_report_enabled else 'معطّل'}</b>\n"
        f"موعده: <b>{WEEKDAY_NAMES[group.weekly_report_weekday]}"
        f" {clock(group.weekly_report_time)}</b>\n"
        f"الأسبوع يبدأ يوم: <b>{WEEKDAY_NAMES[group.week_start_weekday]}</b>\n\n"
        "<i>الأسماء في لوحة الشرف تُذكر نصًّا بلا منشن — احتفاء بلا إزعاج.</i>"
    )
    rows: list[list[InlineKeyboardButton]] = [
        [_btn("🔕 تعطيل التقرير" if group.weekly_report_enabled else "🔔 تفعيل التقرير", "we")]
    ]
    if group.weekly_report_enabled:
        marks = [
            _btn(
                f"{'✅' if d == group.weekly_report_weekday else '▫️'} {SHORT_WEEKDAYS[d]}", f"w:{d}"
            )
            for d in range(7)
        ]
        rows += [
            marks[:4],
            marks[4:],
            _stepper("wtime", f"الوقت: {clock(group.weekly_report_time)}", kind="t", step=30),
        ]
    rows.append(_back())
    return text, InlineKeyboardMarkup(rows)


def week_start(group: Group) -> tuple[str, InlineKeyboardMarkup]:
    text = (
        "🗓 <b>بداية الأسبوع</b>\n\n"
        f"الأسبوع يبدأ يوم: <b>{WEEKDAY_NAMES[group.week_start_weekday]}</b>\n\n"
        "<i>عليها يُبنى حساب لوحة الشرف: من أتمّ كل يوم مُفعّل في الأسبوع.</i>"
    )
    marks = [
        _btn(f"{'✅' if d == group.week_start_weekday else '▫️'} {SHORT_WEEKDAYS[d]}", f"s:{d}")
        for d in range(7)
    ]
    return text, InlineKeyboardMarkup([marks[:4], marks[4:], _back()])


def page(group: Group) -> tuple[str, InlineKeyboardMarkup]:
    """The page editor, which is also how a group joins an in-progress khatmah."""
    text = (
        "📄 <b>الصفحة الحالية</b>\n\n"
        f"ورد الغد يبدأ من الصفحة <b>{ar_num(group.current_page)}</b>"
        f" من {ar_num(TOTAL_PAGES)} (الختمة رقم {ar_num(group.khatmah_number)}).\n\n"
        "<b>كنتم في ختمة قبل البوت؟</b> عيّنوا صفحتكم مرّة واحدة بالأمر:\n"
        "<code>/setpage 350</code>\n"
        "وسيُكمل البوت من حيث وقفتم.\n\n"
        "<i>الأزرار للتصحيح اليسير، والأمر للقفزات الكبيرة.</i>"
    )
    rows = [
        [
            _btn("− ١٠", "n:page:-10"),
            _value(ar_num(group.current_page)),
            _btn("+ ١٠", "n:page:10"),
        ],
        [_btn("− ١", "n:page:-1"), _btn("+ ١", "n:page:1")],
        _back(),
    ]
    return text, InlineKeyboardMarkup(rows)


def pause(group: Group) -> tuple[str, InlineKeyboardMarkup]:
    if group.is_active:
        text = (
            "⏸️ <b>إيقاف الورد مؤقتًا</b>\n\n"
            "سيتوقّف الإرسال والتذكير حتى تستأنفوا.\n"
            "<i>التقدّم والسلاسل والاشتراكات كلها محفوظة كما هي.</i>"
        )
        rows = [[_btn("⏸️ نعم، أوقِف الورد", "pause:off")], _back()]
    else:
        text = (
            "▶️ <b>استئناف الورد</b>\n\n"
            f"سيعود الورد من الصفحة <b>{ar_num(group.current_page)}</b> في موعده"
            f" ({clock(group.send_time)})."
        )
        rows = [[_btn("▶️ استأنف الورد", "pause:on")], _back()]
    return text, InlineKeyboardMarkup(rows)


# Beyond this the list is summarised; a keyboard of a hundred names is unusable.
MAX_SUBSCRIBERS_SHOWN = 20


def khatmah(group: Group) -> tuple[str, InlineKeyboardMarkup]:
    """Starting a fresh khatmah by hand, without waiting to reach page 604."""
    started = group.khatmah_started_on
    text = (
        "🔄 <b>الختمة</b>\n\n"
        f"الختمة رقم <b>{ar_num(group.khatmah_number)}</b> ·"
        f" الصفحة <b>{ar_num(group.current_page)}</b> من {ar_num(TOTAL_PAGES)}\n"
        + (f"بدأت في: {ar_num(started.isoformat())}\n" if started else "")
        + "\n<b>بدء ختمة جديدة</b> يرفع رقم الختمة ويعود بالورد إلى الصفحة ١.\n"
        "<i>الإحصاءات والسلاسل والاشتراكات لا تتأثّر. "
        "ولتصحيح الصفحة فقط دون ختمة جديدة استعمل /setpage.</i>"
    )
    rows = [[_btn("🔄 ابدأ ختمة جديدة من الصفحة ١", "khatmah:new")], _back()]
    return text, InlineKeyboardMarkup(rows)


def subscribers(group: Group, members: list[Subscriber]) -> tuple[str, InlineKeyboardMarkup]:
    """The subscriber list, with a button to remove anyone from it."""
    if not members:
        text = (
            "👥 <b>المشتركون</b>\n\n"
            "لا مشترك بعد.\n"
            "<i>من أراد الاشتراك فليرسل /join — والاشتراك اختياري، "
            "ولا يُنادى في التذكير إلا المشتركون.</i>"
        )
        return text, InlineKeyboardMarkup([_back()])

    shown = members[:MAX_SUBSCRIBERS_SHOWN]
    rest = len(members) - len(shown)
    text = (
        "👥 <b>المشتركون</b>\n\n"
        f"عددهم: <b>{ar_num(len(members))}</b>\n"
        + (f"يُعرض منهم {ar_num(len(shown))}.\n" if rest > 0 else "")
        + "\n<i>اضغط على اسم لإلغاء اشتراكه — سجلّه محفوظ، وله أن يعود بـ /join متى شاء.</i>"
    )
    rows = [[_btn(f"❌ {safe_name(m.display_name)}", f"sub:{m.user_id}")] for m in shown]
    rows.append(_back())
    return text, InlineKeyboardMarkup(rows)
