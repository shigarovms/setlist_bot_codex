"""Telegram Main Mini App shortcuts shared by group cards and queued messages."""

from uuid import UUID

LABELS = {
    "event": ("К выступлению в Mini App", "Open performance in Mini App"),
    "variant": ("К варианту сет-листа в Mini App", "Open setlist proposal in Mini App"),
    "request": ("К запросу участника в Mini App", "Open participant request in Mini App"),
    "approved": ("К утверждённому сет-листу в Mini App", "Open approved setlist in Mini App"),
    "catalog": ("К репертуару в Mini App", "Open repertoire in Mini App"),
    "songs": ("К предложениям по репертуару в Mini App", "Open repertoire suggestions in Mini App"),
}


def workspace_button(
    base_url: str, bot_username: str, language: str, section: str, *identifiers: UUID
) -> list[tuple[str, str]]:
    if not base_url or not bot_username:
        return []
    start = "_".join(("ws", section, *(value.hex for value in identifiers)))
    return [(LABELS[section][language == "en"], f"https://t.me/{bot_username}?startapp={start}")]
