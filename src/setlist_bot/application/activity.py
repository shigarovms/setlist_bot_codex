"""RU: Общий журнал важных действий. EN: Audit in the same transaction as the change."""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any
from uuid import UUID, uuid4

from setlist_bot.application.ports import Store
from setlist_bot.domain.entities import AdminAudit, User

_web_action: ContextVar[bool] = ContextVar("web_action", default=False)


@contextmanager
def web_activity() -> Iterator[None]:
    """The outer Administration transaction already records the real web actor."""
    token = _web_action.set(True)
    try:
        yield
    finally:
        _web_action.reset(token)


def record_activity(
    store: Store,
    user: User,
    action: str,
    target: UUID,
    **details: Any,
) -> None:
    # RU: Текст сообщений и секреты сюда не передаются; источник хранится отдельно.
    # EN: Store structural metadata only, never private message bodies or credentials.
    if _web_action.get():
        return
    store.add(
        AdminAudit(
            actor=f"telegram:{user.telegram_id}",
            source="telegram",
            action=action,
            target=str(target),
            request_key=uuid4().hex,
            details=details,
        )
    )
