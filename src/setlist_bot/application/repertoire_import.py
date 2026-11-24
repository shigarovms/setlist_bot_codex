from dataclasses import dataclass
from typing import Any
from uuid import UUID

from setlist_bot.application.importing import Importer, ImportPlan
from setlist_bot.domain.importing import FIELDS
from setlist_bot.domain.rules import require


@dataclass(frozen=True)
class RepertoireImport:
    source: str
    mapping: dict[str, int] | None
    has_header: bool | None

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "RepertoireImport":
        source, mapping, has_header = (
            payload.get(key) for key in ("source", "mapping", "has_header")
        )
        require(isinstance(source, str), "import_size")
        assert isinstance(source, str)
        require(has_header is None or type(has_header) is bool, "input")
        if mapping is not None:
            require(
                isinstance(mapping, dict)
                and set(mapping) == set(FIELDS)
                and all(type(value) is int and -1 <= value < 50 for value in mapping.values()),
                "import_columns",
            )
        return cls(source, mapping, has_header)

    async def preview(self, importer: Importer, group_id: UUID) -> ImportPlan:
        return await importer.preview_source(group_id, self.source, self.mapping, self.has_header)

    def data(self) -> dict[str, Any]:
        return {"source": self.source, "mapping": self.mapping, "has_header": self.has_header}
