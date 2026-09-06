"""نماذج SQLAlchemy — راجع docs/PLAN.md القسم 4.

اصطلاحات:
  * كل الطوابع الزمنية (`DateTime`) تُخزَّن بتوقيت UTC.
  * التواريخ التقويمية (`Date`) واﻷوقات (`Time`) محليّة بتوقيت المجموعة —
    فيوم الورد هو اليوم كما يراه أعضاء المجموعة لا كما يراه الخادم.
  * القوائم تُخزَّن كـ JSON بدل نصوص مفصولة بفواصل.
"""

from __future__ import annotations

import datetime as dt
import enum

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    Time,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.UTC).replace(tzinfo=None)


class AdvanceRule(enum.StrEnum):
    """متى تتقدّم صفحات المجموعة إلى الورد التالي."""

    ANYONE = "anyone"  # يكفي أن يُنجز واحد (الافتراضي)
    MAJORITY = "majority"  # أكثر من نصف المشتركين
    ALL = "all"  # الجميع
    ALWAYS = "always"  # دائمًا، أنجز أحد أم لا


class TaskStatus(enum.StrEnum):
    OPEN = "open"
    CLOSED = "closed"


class CompletionSource(enum.StrEnum):
    BUTTON = "button"
    COMMAND = "command"


class BadgeKind(enum.StrEnum):
    PERFECT_WEEK = "perfect_week"
    STREAK_30 = "streak_30"
    KHATMAH = "khatmah"


# ============================ المجموعات ============================


class Group(Base):
    """مجموعة تيليجرام وإعداداتها وتقدّمها في الختمة."""

    __tablename__ = "groups"

    chat_id: Mapped[int] = mapped_column(primary_key=True, autoincrement=False)
    title: Mapped[str | None] = mapped_column(String(256))
    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Damascus")

    # --- الورد ---
    pages_per_day: Mapped[int] = mapped_column(default=2)
    active_weekdays: Mapped[list[int]] = mapped_column(
        JSON, default=lambda: [0, 1, 2, 3, 4, 5, 6]
    )  # 0=الاثنين … 6=الأحد (مطابق لـ date.weekday())
    send_time: Mapped[dt.time] = mapped_column(Time, default=dt.time(5, 0))

    # --- التذكيرات ---
    first_reminder_after_hours: Mapped[int] = mapped_column(default=8)
    reminder_interval_hours: Mapped[int] = mapped_column(default=3)
    reminder_max_count: Mapped[int] = mapped_column(default=3)
    quiet_hours_start: Mapped[dt.time | None] = mapped_column(Time, default=dt.time(23, 0))
    quiet_hours_end: Mapped[dt.time | None] = mapped_column(Time, default=dt.time(7, 0))
    mentions_per_message: Mapped[int] = mapped_column(default=4)

    # --- إغلاق اليوم ---
    day_close_time: Mapped[dt.time] = mapped_column(Time, default=dt.time(23, 59))
    advance_rule: Mapped[AdvanceRule] = mapped_column(String(16), default=AdvanceRule.ANYONE)

    # --- التقرير الأسبوعي ---
    weekly_report_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    weekly_report_weekday: Mapped[int] = mapped_column(default=4)  # 4=الجمعة
    weekly_report_time: Mapped[dt.time] = mapped_column(Time, default=dt.time(20, 0))
    week_start_weekday: Mapped[int] = mapped_column(default=5)  # 5=السبت

    # --- التقدّم ---
    current_page: Mapped[int] = mapped_column(default=1)
    khatmah_number: Mapped[int] = mapped_column(default=1)
    khatmah_started_on: Mapped[dt.date | None] = mapped_column(Date)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)

    subscribers: Mapped[list[Subscriber]] = relationship(
        back_populates="group", cascade="all, delete-orphan"
    )
    tasks: Mapped[list[DailyTask]] = relationship(
        back_populates="group", cascade="all, delete-orphan"
    )


class Subscriber(Base):
    """عضو اشترك في الورد — الاشتراك اختياري، ولا يُنادى إلا المشتركون."""

    __tablename__ = "subscribers"

    chat_id: Mapped[int] = mapped_column(
        ForeignKey("groups.chat_id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[int] = mapped_column(primary_key=True, autoincrement=False)
    display_name: Mapped[str] = mapped_column(String(256))
    username: Mapped[str | None] = mapped_column(String(64))
    joined_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    left_at: Mapped[dt.datetime | None] = mapped_column(DateTime)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    group: Mapped[Group] = relationship(back_populates="subscribers")

    __table_args__ = (Index("ix_subscribers_active", "chat_id", "is_active"),)


# ============================ الورد اليومي ============================


class DailyTask(Base):
    """ورد يوم واحد في مجموعة واحدة."""

    __tablename__ = "daily_tasks"

    id: Mapped[int] = mapped_column(primary_key=True)
    chat_id: Mapped[int] = mapped_column(
        ForeignKey("groups.chat_id", ondelete="CASCADE"), index=True
    )
    task_date: Mapped[dt.date] = mapped_column(Date)  # محلي بتوقيت المجموعة
    page_start: Mapped[int]
    page_end: Mapped[int]

    message_id: Mapped[int | None]
    album_message_ids: Mapped[list[int] | None] = mapped_column(JSON)

    status: Mapped[TaskStatus] = mapped_column(String(16), default=TaskStatus.OPEN)
    is_repeat_of: Mapped[int | None] = mapped_column(ForeignKey("daily_tasks.id"))
    sent_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    closed_at: Mapped[dt.datetime | None] = mapped_column(DateTime)

    group: Mapped[Group] = relationship(back_populates="tasks")
    completions: Mapped[list[Completion]] = relationship(
        back_populates="task", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("chat_id", "task_date", name="uq_daily_tasks_chat_id"),
        Index("ix_daily_tasks_status", "chat_id", "status"),
    )

    @property
    def page_count(self) -> int:
        return self.page_end - self.page_start + 1


class Completion(Base):
    """تسجيل إنجاز عضو لورد يوم."""

    __tablename__ = "completions"

    task_id: Mapped[int] = mapped_column(
        ForeignKey("daily_tasks.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[int] = mapped_column(primary_key=True, autoincrement=False)
    done_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    source: Mapped[CompletionSource] = mapped_column(String(16), default=CompletionSource.BUTTON)
    # هل كان مشتركًا لحظة الإنجاز؟ يفرّق بين المشترك ومن ضغط الزر عرضًا
    was_subscriber: Mapped[bool] = mapped_column(Boolean)

    task: Mapped[DailyTask] = relationship(back_populates="completions")


class UserStats(Base):
    """إحصاءات العضو داخل مجموعة بعينها (العضوية في عدّة مجموعات مستقلّة)."""

    __tablename__ = "user_stats"

    chat_id: Mapped[int] = mapped_column(primary_key=True, autoincrement=False)
    user_id: Mapped[int] = mapped_column(primary_key=True, autoincrement=False)
    total_done: Mapped[int] = mapped_column(default=0)
    total_missed: Mapped[int] = mapped_column(default=0)
    current_streak: Mapped[int] = mapped_column(default=0)
    best_streak: Mapped[int] = mapped_column(default=0)
    consecutive_missed: Mapped[int] = mapped_column(default=0)
    last_done_date: Mapped[dt.date | None] = mapped_column(Date)


class ReminderLog(Base):
    """سجل التذكيرات المرسلة — يمنع تكرار تذكيرة بعد إعادة تشغيل البوت."""

    __tablename__ = "reminder_log"

    task_id: Mapped[int] = mapped_column(
        ForeignKey("daily_tasks.id", ondelete="CASCADE"), primary_key=True
    )
    seq: Mapped[int] = mapped_column(primary_key=True, autoincrement=False)
    sent_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    message_ids: Mapped[list[int] | None] = mapped_column(JSON)


# ============================ التقارير والأوسمة ============================


class WeeklyReport(Base):
    """لوحة الشرف الأسبوعية — التخزين يجعل الإرسال idempotent."""

    __tablename__ = "weekly_reports"

    chat_id: Mapped[int] = mapped_column(
        ForeignKey("groups.chat_id", ondelete="CASCADE"), primary_key=True
    )
    week_start: Mapped[dt.date] = mapped_column(Date, primary_key=True)
    week_end: Mapped[dt.date] = mapped_column(Date)
    sent_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    message_id: Mapped[int | None]
    pages_read: Mapped[int] = mapped_column(default=0)
    active_days: Mapped[int] = mapped_column(default=0)
    perfect_user_ids: Mapped[list[int] | None] = mapped_column(JSON)


class Badge(Base):
    __tablename__ = "badges"

    chat_id: Mapped[int] = mapped_column(primary_key=True, autoincrement=False)
    user_id: Mapped[int] = mapped_column(primary_key=True, autoincrement=False)
    badge: Mapped[BadgeKind] = mapped_column(String(32), primary_key=True)
    # مرجع يميّز الوسام الواحد عن تكراره: تاريخ بدء الأسبوع أو رقم الختمة
    ref: Mapped[str] = mapped_column(String(32), primary_key=True, default="")
    earned_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)


class Nudge(Base):
    """آخر مرة دُعي فيها غير مشترك للاشتراك — بحدّ أقصى مرة كل أسبوع."""

    __tablename__ = "nudges"

    chat_id: Mapped[int] = mapped_column(primary_key=True, autoincrement=False)
    user_id: Mapped[int] = mapped_column(primary_key=True, autoincrement=False)
    last_nudged_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)


class PageMedia(Base):
    """كاش file_id لصور الصفحات — ترفع الصورة مرة واحدة ثم تُرسل بمعرّفها."""

    __tablename__ = "page_media"

    page_no: Mapped[int] = mapped_column(primary_key=True, autoincrement=False)
    file_id: Mapped[str] = mapped_column(String(256))
    file_unique_id: Mapped[str | None] = mapped_column(String(128))
    uploaded_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)


# ============================ فهرس المصحف (مرجعي) ============================


class Surah(Base):
    __tablename__ = "surahs"

    number: Mapped[int] = mapped_column(primary_key=True, autoincrement=False)
    name: Mapped[str] = mapped_column(String(64))  # «سورة البقرة»
    short_name: Mapped[str] = mapped_column(String(64))  # «البقرة»
    ayah_count: Mapped[int]
    first_page: Mapped[int]
    last_page: Mapped[int]


class PageIndex(Base):
    """ما تحتويه كل صفحة — تُبنى منه ترويسة رسالة الورد."""

    __tablename__ = "page_index"

    page_no: Mapped[int] = mapped_column(primary_key=True, autoincrement=False)
    juz: Mapped[int]
    hizb: Mapped[int]
    first_surah: Mapped[int]
    first_ayah: Mapped[int]
    last_surah: Mapped[int]
    last_ayah: Mapped[int]
    surah_names: Mapped[str] = mapped_column(String(256))  # «البقرة، آل عمران»
    ayah_count: Mapped[int]


class Ayah(Base):
    """نصّ الآيات — أساس ميزتي التفسير والبحث مستقبلًا."""

    __tablename__ = "ayahs"

    surah: Mapped[int] = mapped_column(primary_key=True, autoincrement=False)
    number: Mapped[int] = mapped_column(primary_key=True, autoincrement=False)
    page_no: Mapped[int] = mapped_column(Integer, index=True)
    juz: Mapped[int]
    hizb: Mapped[int]
    text: Mapped[str] = mapped_column(Text)
