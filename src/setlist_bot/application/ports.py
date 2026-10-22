from datetime import datetime
from types import TracebackType
from typing import Any, Protocol
from uuid import UUID

from setlist_bot.domain.entities import Entity


class Store(Protocol):
    async def get[T: Entity](self, kind: type[T], entity_id: UUID, *, lock: bool = False) -> T: ...

    async def find[T: Entity](self, kind: type[T], **filters: Any) -> list[T]: ...

    async def expired[T: Entity](self, kind: type[T], field: str, before: datetime) -> list[T]: ...

    async def lock_chat(self, chat_id: int) -> None: ...

    def add(self, entity: Entity) -> None: ...

    async def delete(self, entity: Entity) -> None: ...

    async def flush(self) -> None: ...


class UnitOfWork(Protocol):
    store: Store

    async def __aenter__(self) -> "UnitOfWork": ...

    async def __aexit__(
        self,
        typ: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...
