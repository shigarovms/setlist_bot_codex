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
    last_seen_at: datetime | None = None
    telegram_id: int
    language: str = "ru"
    display_name: str = ""
    export_settings: dict[str, Any] = field(default_factory=dict)


@dataclass(kw_only=True)
class Group(Entity):
    owner_id: UUID | None = None
    reminders_enabled: bool = True
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
    created_at: datetime = field(default_factory=now)
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
    performance_duration_seconds: int | None = None
    city: str = ""
    extra_info: str = ""
    metadata_version: int = 0
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
    song_id: UUID | None
    position: int
    pause_before: int | None = None
    pause_duration_seconds: int | None = None


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
    actor: str = ""
    source: str = "system"
    payload: dict[str, Any] = field(default_factory=dict)
    created_at: datetime | None = field(default_factory=now)
    delivered_at: datetime | None = None
    discarded_at: datetime | None = None
    attempts: int = 0
    last_error: str = ""
    next_attempt_at: datetime | None = None
    support_request_id: UUID | None = None
    support_kind: str = ""
    chat_id: int
    text: str
    sent: bool = False
    buttons: list[list[str]] = field(default_factory=list)
    group_id: UUID | None = None
    concert_id: UUID | None = None
    monospace: bool = False
    monospace_offset: int = 0


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
class ExportSnapshot(Entity):
    group_id: UUID
    concert_id: UUID
    chat_id: int
    payload: dict[str, Any]
    expires_at: datetime
    owner_id: UUID | None = None
    group_epoch: int = 0
    concert_epoch: int = 0


@dataclass(kw_only=True)
class ExportFile(Entity):
    export_id: UUID
    audience: str
    language: str
    transliterate: bool
    filename: str
    content: bytes


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
    created_at: datetime | None = field(default_factory=now)
    update_id: int


@dataclass(kw_only=True)
class Confirmation(Entity):
    user_id: UUID
    chat_id: int
    command: str
    expires_at: datetime


@dataclass(kw_only=True)
class SupportSettings(Entity):
    key: str = "main"
    owner_telegram_id: int
    chat_id: int


@dataclass(kw_only=True)
class SupportMessage(Entity):
    outbox_id: UUID
    user_id: UUID
    chat_id: int
    message_id: int | None = None


@dataclass(kw_only=True)
class Reminder(Entity):
    group_id: UUID
    concert_id: UUID
    day: date
    outbox_id: UUID


@dataclass(kw_only=True)
class AdminSession(Entity):
    token_hash: str
    actor: str
    created_at: datetime
    expires_at: datetime


@dataclass(kw_only=True)
class AdminLoginLimit(Entity):
    key: str
    window_start: datetime
    attempts: int = 0


@dataclass(kw_only=True)
class AdminAudit(Entity):
    source: str = "web"
    actor: str
    action: str
    target: str
    request_key: str
    created_at: datetime = field(default_factory=now)
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(kw_only=True)
class SupportCase(Entity):
    version: int = 0
    last_reply_by: str = ""
    last_reply_source: str = ""
    request_id: UUID
    status: str = "open"
    updated_at: datetime = field(default_factory=now)


@dataclass(kw_only=True)
class RuntimeStatus(Entity):
    last_success_at: datetime | None = None
    last_error_at: datetime | None = None
    details: dict[str, Any] = field(default_factory=dict)
    last_activity_at: datetime | None = None
    component: str
    updated_at: datetime
    state: str
    errors: int = 0
    last_error: str = ""
    next_run: datetime | None = None


@dataclass(kw_only=True)
class DeploymentIdentity(Entity):
    key: str = "main"
    bot_id: int
    environment: str
