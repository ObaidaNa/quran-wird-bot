"""Repository layer: all database access lives behind these classes."""

from .groups import GroupRepo
from .media import MediaRepo
from .mushaf import MushafRepo
from .owner import OwnerStatsRepo
from .reports import BadgeRepo, WeeklyReportRepo
from .stats import StatsRepo
from .subscribers import NudgeRepo, SubscriberRepo
from .tasks import TaskRepo

__all__ = [
    "BadgeRepo",
    "GroupRepo",
    "MediaRepo",
    "MushafRepo",
    "OwnerStatsRepo",
    "NudgeRepo",
    "StatsRepo",
    "SubscriberRepo",
    "TaskRepo",
    "WeeklyReportRepo",
]
