"""Repository layer: all database access lives behind these classes."""

from .groups import GroupRepo
from .media import MediaRepo
from .mushaf import MushafRepo
from .reports import BadgeRepo, WeeklyReportRepo
from .stats import StatsRepo
from .subscribers import NudgeRepo, SubscriberRepo
from .tasks import TaskRepo

__all__ = [
    "BadgeRepo",
    "GroupRepo",
    "MediaRepo",
    "MushafRepo",
    "NudgeRepo",
    "StatsRepo",
    "SubscriberRepo",
    "TaskRepo",
    "WeeklyReportRepo",
]
