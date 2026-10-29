"""RU: Нормализация внешнего каталога. EN: Pure, conservative metadata merging."""

import re
import unicodedata
from collections import Counter
from collections.abc import Iterable
from typing import Any

from setlist_bot.domain.rules import normalize

MAX_TRACKS = 500
PAGE_SIZE = 20
FEATURED = re.compile(r"\(\s*feat\b[^)]*\)|\[\s*feat\b[^\]]*\]", re.IGNORECASE)
QUALIFIER = re.compile(r"\([^()]*\)|\[[^\[\]]*\]")


def without_featured(title: str) -> str:
    """RU: Кредит остаётся в исходной таблице. EN: Clean only the catalog title."""
    return " ".join(FEATURED.sub("", title).split()) or title.strip()


def without_version(title: str, version: str) -> str:
    pattern = re.compile(r"\b" + re.escape(version) + r"\b", re.IGNORECASE)
    changed = QUALIFIER.sub(
        lambda match: (
            "" if pattern.search(match[0]) and not FEATURED.fullmatch(match[0]) else match[0]
        ),
        title,
    )
    return (" ".join(changed.split()) or title.strip()) if changed != title else title


def default_exclusions(rows: list[dict[str, Any]]) -> list[int]:
    """RU: Только первоначальный выбор, не фильтр. EN: Every exclusion is reversible.

    Предпочитаем концертную версию студийной, ремиксы пользователь включает сам.
    Apply this once per new selection, including a public-cache hit; never reset clicks.
    """
    live = {
        title_key(without_version(row["title"], "live"))
        for row in rows
        if without_version(row["title"], "live") != row["title"]
        and without_version(row["title"], "remix") == row["title"]
    }
    return [
        index
        for index, row in enumerate(rows)
        if without_version(row["title"], "remix") != row["title"]
        or (
            without_version(row["title"], "live") == row["title"]
            and title_key(row["title"]) in live
        )
    ]


def catalog_titles(rows: list[dict[str, Any]], occupied: Iterable[str]) -> list[str]:
    """RU: Не сливаем разные записи при очистке Live. EN: Preview exactly what is saved.

    Проверяем одновременно весь выбранный пакет, каталог и алиасы. При коллизии
    оставляем Live; обычный Importer затем проверит повторы и конфликтующие строки.
    Existing records and aliases remain untouched, as do the full source titles.
    """
    cleaned = [without_featured(row["title"]) for row in rows]
    candidates = [without_version(title, "live") for title in cleaned]
    counts = Counter(normalize(title) for title in candidates)
    known = {normalize(title) for title in occupied}
    return [
        title
        if candidate != title
        and (counts[normalize(candidate)] > 1 or normalize(candidate) in known)
        else candidate
        for title, candidate in zip(cleaned, candidates, strict=True)
    ]


def title_key(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).casefold().replace("ё", "е")
    # RU: Имена приглашённых музыкантов бывают в названии или в исполнителях.
    # Пометки remix/live НЕ удаляем: это самостоятельные версии.
    # EN: Normalize featured credits, but retain actual version qualifiers.
    value = without_featured(value)
    return " ".join(re.sub(r"[^\w]+", " ", value).split())


def same_recording(left: dict[str, Any], right: dict[str, Any]) -> bool:
    return (
        title_key(left["title"]) == title_key(right["title"])
        and left.get("seconds") is not None
        and right.get("seconds") is not None
        and abs(left["seconds"] - right["seconds"]) <= 2
    )


def recording_index(rows: list[dict[str, Any]]) -> dict[str, set[int]]:
    index: dict[str, set[int]] = {}
    for row in rows:
        if row.get("seconds") is not None:
            index.setdefault(title_key(row["title"]), set()).add(row["seconds"])
    return index


def recording_known(row: dict[str, Any], index: dict[str, set[int]]) -> bool:
    seconds = row.get("seconds")
    return seconds is not None and any(
        abs(seconds - known) <= 2 for known in index.get(title_key(row["title"]), set())
    )


def merged_tracks(raw: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Never invent a missing duration/BPM or average contradictory BPM estimates."""
    groups: list[dict[str, Any]] = []
    by_title: dict[str, list[dict[str, Any]]] = {}
    for track in raw[:4000]:
        title = str(track.get("title", "")).strip()
        if not title or len(title) > 200:
            continue
        track = {**track, "title": title}
        key = title_key(title)
        candidates = by_title.get(key, [])
        matches = [
            row
            for row in candidates
            if row.get("seconds") is not None
            and track.get("seconds") is not None
            and abs(row["seconds"] - track["seconds"]) <= 2
        ]
        if not matches and track.get("source") == "MusicBrainz":
            # RU/EN: A timing disagreement alone does not prove a new, unnamed version.
            matches = candidates
        if matches:
            row = matches[0]
            if without_featured(title) != title and without_featured(row["title"]) == row["title"]:
                row["title"] = title
        elif len(groups) < MAX_TRACKS:
            row = {"title": title, "seconds": track.get("seconds"), "sources": [], "bpms": []}
            groups.append(row)
            by_title.setdefault(key, []).append(row)
        else:
            continue
        if track.get("seconds") and row["seconds"] is None:
            row["seconds"] = track["seconds"]
        source = {"name": track.get("source", ""), "url": track.get("url", "")}
        if source not in row["sources"]:
            row["sources"].append(source)
        bpm = track.get("bpm")
        if isinstance(bpm, (float, int)) and 0 < bpm <= 1000 and same_recording(row, track):
            value = int(bpm + 0.5)
            if value not in row["bpms"]:
                row["bpms"].append(value)
    repeated: dict[str, int] = {}
    for row in groups:
        key = title_key(row["title"])
        repeated[key] = repeated.get(key, 0) + 1
    for row in groups:
        row["bpms"].sort()
        row["bpm"] = row["bpms"][0] if len(row["bpms"]) == 1 else None
        if repeated[title_key(row["title"])] > 1:
            seconds = row["seconds"]
            suffix = f" ({seconds // 60:02d}:{seconds % 60:02d})" if seconds else " (?)"
            row["title"] = row["title"][: 200 - len(suffix)] + suffix
    return sorted(groups, key=lambda row: title_key(row["title"]))
