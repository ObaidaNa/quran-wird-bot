"""Stand-ins for Telegram and the session factory, shared by the tests."""

from __future__ import annotations

from dataclasses import dataclass, field

from telegram.error import BadRequest


@dataclass
class FakePhotoSize:
    file_id: str


@dataclass
class FakeMessage:
    message_id: int
    photo: tuple[FakePhotoSize, ...] = ()
    text: str | None = None
    caption: str | None = None
    reply_markup: object = None
    # Whatever else the caller passed, so a test can assert on parse_mode.
    kwargs: dict = field(default_factory=dict)


class FakeBot:
    """Records what would have been sent, and hands back plausible file_ids.

    `fail_on_file_id` simulates Telegram rejecting a stale cached file_id;
    `edit_error` simulates an edit_message_text failure.
    """

    def __init__(self, fail_on_file_id: bool = False, edit_error: str | None = None) -> None:
        self.albums: list[list] = []
        self.photos: list[dict] = []
        self.messages: list[FakeMessage] = []
        self.edits: list[dict] = []
        self.pinned: list[int] = []
        self.unpinned: list[int] = []
        self.fail_on_file_id = fail_on_file_id
        self.edit_error = edit_error
        self._next_id = 1000

    @property
    def sent_texts(self) -> list[str]:
        return [m.text for m in self.messages if m.text]

    def _id(self) -> int:
        self._next_id += 1
        return self._next_id

    async def send_media_group(self, chat_id, media, **kw):
        if self.fail_on_file_id and any(isinstance(m.media, str) for m in media):
            raise BadRequest("wrong file identifier/HTTP URL specified")
        self.albums.append(list(media))
        return tuple(
            FakeMessage(self._id(), photo=(FakePhotoSize(f"FID_{i}"),)) for i, _ in enumerate(media)
        )

    async def send_photo(self, chat_id, photo, caption=None, **kw):
        if self.fail_on_file_id and isinstance(photo, str):
            raise BadRequest("wrong file identifier/HTTP URL specified")
        self.photos.append({"photo": photo, "caption": caption})
        return FakeMessage(self._id(), photo=(FakePhotoSize("FID_SOLO"),))

    async def send_message(self, chat_id, text, reply_markup=None, **kw):
        msg = FakeMessage(self._id(), text=text, reply_markup=reply_markup, kwargs=dict(kw))
        self.messages.append(msg)
        return msg

    async def pin_chat_message(self, chat_id, message_id, **kw):
        self.pinned.append(message_id)
        return True

    async def unpin_chat_message(self, chat_id, message_id=None, **kw):
        self.unpinned.append(message_id)
        return True

    async def edit_message_text(self, chat_id, message_id, text, reply_markup=None, **kw):
        if self.edit_error:
            raise BadRequest(self.edit_error)
        self.edits.append({"chat_id": chat_id, "message_id": message_id, "text": text})
        return FakeMessage(message_id, text=text)


@dataclass
class FakeSessions:
    """Hands the same test session to every session_scope() call."""

    session: object
    committed: int = field(default=0)

    def __call__(self):
        return self

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, *exc):
        return False
