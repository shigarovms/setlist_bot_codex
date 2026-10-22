from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID, uuid4


def now() -> datetime:
    return datetime.now(UTC)


@dataclass(kw_only=True)
class Entity:
    id: UUID = field(default_factory=uuid4)


@dataclass(kw_only=True)
class User(Entity):
    telegram_id: int
    language: str = "ru"
    display_name: str = ""


@dataclass(kw_only=True)
class Group(Entity):
    name: str
    language: str = "ru"
    default_pause_seconds: int = 5
    deleted_at: datetime | None = None
    deleted_chat_id: int | None = None
    lifecycle_version: int = 0


@dataclass(kw_only=True)
class Membership(Entity):
    group_id: UUID
    user_id: UUID
    role: str = "member"
    musical_role: str = ""
    status: str = "pending"


@dataclass(kw_only=True)
class ChatBinding(Entity):
    group_id: UUID
    chat_id: int


@dataclass(kw_only=True)
class Context(Entity):
    user_id: UUID
    chat_id: int
    group_id: UUID | None = None
    concert_id: UUID | None = None
    observed_version: int | None = None
    selected_song_id: UUID | None = None


@dataclass(kw_only=True)
class Song(Entity):
    group_id: UUID
    title: str
    duration_seconds: int
    bpm: int | None = None
    lights: str = ""
    comment: str = ""
    archived: bool = False
    version: int = 1


@dataclass(kw_only=True)
class SongAlias(Entity):
    group_id: UUID
    song_id: UUID
    title: str


@dataclass(kw_only=True)
class CatalogProposal(Entity):
    group_id: UUID
    author_id: UUID
    payload: dict[str, Any]
    song_id: UUID | None = None
    expected_version: int | None = None
    status: str = "pending"


@dataclass(kw_only=True)
class Concert(Entity):
    group_id: UUID
    date: date
    title: str = ""
    deleted_at: datetime | None = None
    lifecycle_version: int = 0


@dataclass(kw_only=True)
class Setlist(Entity):
    group_id: UUID
    concert_id: UUID
    default_pause_seconds: int = 5
    version: int = 0
    approved_revision_id: UUID | None = None
    approved: bool = False


@dataclass(kw_only=True)
class Block(Entity):
    group_id: UUID
    setlist_id: UUID
    position: int
    title: str = ""
    break_seconds: int = 0
    kind: str = "regular"


@dataclass(kw_only=True)
class Item(Entity):
    group_id: UUID
    block_id: UUID
    song_id: UUID
    position: int
    pause_before: int | None = None


@dataclass(kw_only=True)
class Revision(Entity):
    group_id: UUID
    setlist_id: UUID
    author_id: UUID
    number: int
    action: str
    snapshot: dict[str, Any]
    suggestion_number: int | None = None
    proposer_id: UUID | None = None
    created_at: datetime = field(default_factory=now)


@dataclass(kw_only=True)
class PersonalDraft(Entity):
    group_id: UUID
    concert_id: UUID
    owner_id: UUID
    content: dict[str, Any]
    version: int = 0


@dataclass(kw_only=True)
class Submission(Entity):
    group_id: UUID
    concert_id: UUID
    author_id: UUID
    snapshot: dict[str, Any]
    applied: bool = False


@dataclass(kw_only=True)
class Outbox(Entity):
    chat_id: int
    text: str
    sent: bool = False
    buttons: list[list[str]] = field(default_factory=list)
    group_id: UUID | None = None
    concert_id: UUID | None = None
    monospace: bool = False


@dataclass(kw_only=True)
class SongListTemplate(Entity):
    group_id: UUID
    concert_id: UUID
    chat_id: int
    titles: list[str]
    expires_at: datetime
    owner_id: UUID | None = None
    version: int | None = None
    group_epoch: int = 0
    concert_epoch: int = 0


@dataclass(kw_only=True)
class SetlistMessage(Entity):
    template_id: UUID
    chat_id: int
    message_id: int


@dataclass(kw_only=True)
class CatalogImport(Entity):
    group_id: UUID
    owner_id: UUID
    chat_id: int
    source: str
    expires_at: datetime
    mapping: dict[str, int] | None = None
    has_header: bool | None = None
    status: str = "pending"


@dataclass(kw_only=True)
class ProcessedUpdate(Entity):
    update_id: int


@dataclass(kw_only=True)
class Confirmation(Entity):
    user_id: UUID
    chat_id: int
    command: str
    expires_at: datetime
