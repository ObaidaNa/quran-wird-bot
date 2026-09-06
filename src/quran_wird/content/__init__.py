"""Content providers: what the bot sends each day."""

from .base import BuildContext, ContentBlock, ContentProvider
from .quran_pages import QuranPagesProvider

__all__ = ["BuildContext", "ContentBlock", "ContentProvider", "QuranPagesProvider"]
