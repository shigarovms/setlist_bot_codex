"""RU: Ограниченные публичные запросы. EN: Checkpointed, replaceable metadata adapters.

No Telegram objects, credentials, arbitrary URLs, audio downloads or generated BPMs.
One background step is committed before the next one starts.
"""

import asyncio
import html
import json
import re
from typing import Any
from urllib.parse import urlencode, urlparse
from uuid import UUID

import aiohttp
from unidecode import unidecode

from setlist_bot.domain.music_discovery import recording_index, recording_known, title_key
from setlist_bot.domain.rules import AppError

MAX_BYTES = 3_000_000
HOSTS = frozenset(
    {
        "itunes.apple.com",
        "musicbrainz.org",
        "songbpm.com",
        "tunebat.com",
        "open.spotify.com",
        "music.yandex.ru",
        "music.yandex.com",
    }
)


class SourceError(Exception):
    def __init__(self, status: int = 0, retry_after: int = 0) -> None:
        self.status, self.retry_after = status, min(max(retry_after, 0), 600)
        super().__init__(f"Source request failed ({status})")


def artist_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", unidecode(value).lower()).strip("-")


def query_target(value: str) -> tuple[str, str]:
    """Validate URLs before scheduling. Never let user input become an HTTP host."""
    if not re.match(r"https?://", value, re.I):
        if "://" in value:
            raise AppError("music_bad_link")
        return "name", value
    parsed = urlparse(value)
    if parsed.scheme != "https" or parsed.username or parsed.password or parsed.port:
        raise AppError("music_bad_link")
    host = parsed.hostname
    path = parsed.path.rstrip("/")
    if host == "music.apple.com" and re.search(r"/artist/(?:[^/]+/)?\d+$", path):
        return "apple", path.rsplit("/", 1)[-1]
    if host == "musicbrainz.org" and re.fullmatch(r"/artist/[a-fA-F0-9-]{36}", path):
        return "mb", str(UUID(path.rsplit("/", 1)[-1]))
    if host == "open.spotify.com" and re.fullmatch(
        r"/(?:intl-[a-z]+/)?artist/[A-Za-z0-9]{22}", path
    ):
        return "web", "https://open.spotify.com/artist/" + path.rsplit("/", 1)[-1]
    if host in ("music.yandex.ru", "music.yandex.com") and re.fullmatch(r"/artist/\d+", path):
        return "web", f"https://{host}{path}"
    raise AppError("music_bad_link")


class MusicSources:
    def __init__(self, session: aiohttp.ClientSession, *, interval: float = 2.0) -> None:
        self.session, self.interval = session, interval
        self.last_request = 0.0

    async def get(self, url: str) -> str:
        if urlparse(url).hostname not in HOSTS or urlparse(url).scheme != "https":
            raise SourceError(400)
        loop = asyncio.get_running_loop()
        await asyncio.sleep(max(0.0, self.interval - (loop.time() - self.last_request)))
        self.last_request = loop.time()
        try:
            async with self.session.get(url, allow_redirects=False) as response:
                if response.status != 200:
                    retry = response.headers.get("Retry-After", "0")
                    raise SourceError(response.status, int(retry) if retry.isdigit() else 0)
                if response.content_length and response.content_length > MAX_BYTES:
                    raise SourceError(413)
                chunks = bytearray()
                async for chunk in response.content.iter_chunked(65536):
                    chunks.extend(chunk)
                    if len(chunks) > MAX_BYTES:
                        raise SourceError(413)
                return chunks.decode("utf-8")
        except (aiohttp.ClientError, TimeoutError, UnicodeError) as error:
            raise SourceError() from error

    async def api(self, base: str, **params: Any) -> dict[str, Any]:
        try:
            data = json.loads(await self.get(base + "?" + urlencode(params)))
            if not isinstance(data, dict):
                raise ValueError("not an object")
            return data
        except (ValueError, TypeError) as error:
            raise SourceError(502) from error

    async def resolve(self, query: str) -> list[dict[str, Any]]:
        kind, value = query_target(query)
        if kind == "web":
            page = await self.get(value)
            match = re.search(r"<title[^>]*>(.*?)</title>", page, re.S | re.I)
            if not match:
                raise SourceError(422)
            value = html.unescape(match.group(1)).split("|")[0].split("—")[0].strip()
            kind = "name"
        if kind == "mb":
            artist = await self.api(f"https://musicbrainz.org/ws/2/artist/{value}", fmt="json")
            return [
                {
                    "name": artist["name"],
                    "mb_id": value,
                    "id": "mb:" + value,
                    "examples": [],
                    "url": f"https://musicbrainz.org/artist/{value}",
                }
            ]
        try:
            if kind == "apple":
                data = await self.api("https://itunes.apple.com/lookup", id=value)
            else:
                data = await self.api(
                    "https://itunes.apple.com/search", term=value, entity="musicArtist", limit=25
                )
        except SourceError as error:
            if kind == "apple" or error.status == 429:
                raise
            data = {}  # A second public catalog can still identify the artist.
        rows = [row for row in data.get("results", []) if row.get("wrapperType") == "artist"]
        exact = [row for row in rows if artist_key(row["artistName"]) == artist_key(value)]
        if exact:
            rows = exact
        artists = [
            {
                "name": row["artistName"],
                "apple_id": row["artistId"],
                "id": str(row["artistId"]),
                "genre": row.get("primaryGenreName", ""),
                "url": row.get("artistLinkUrl", ""),
                "examples": [],
            }
            for row in rows[:5]
        ]
        if artists:
            return artists
        data = await self.api(
            "https://musicbrainz.org/ws/2/artist/", query=f'artist:"{value}"', fmt="json", limit=5
        )
        return [
            {
                "name": row["name"],
                "mb_id": row["id"],
                "id": "mb:" + row["id"],
                "examples": [],
                "url": "https://musicbrainz.org/artist/" + row["id"],
            }
            for row in data.get("artists", [])[:5]
        ]

    @staticmethod
    def apple_tracks(
        data: dict[str, Any], artist: dict[str, Any], *, related: bool = False
    ) -> list[dict[str, Any]]:
        records = []
        for row in data.get("results", []):
            if row.get("kind") != "song" or not isinstance(row.get("trackTimeMillis"), int):
                continue
            if (
                not related
                and row.get("artistId") != artist.get("apple_id")
                and artist_key(artist["name"])
                not in artist_key(row.get("artistName", "") + " " + row.get("trackName", ""))
            ):
                continue
            seconds = (row["trackTimeMillis"] + 500) // 1000
            if seconds <= 0:
                continue
            records.append(
                {
                    "title": row["trackName"],
                    "seconds": seconds,
                    "apple_id": row["trackId"],
                    "source": "Apple Music",
                    "url": row.get("trackViewUrl", ""),
                    "bpm": None,
                }
            )
        return records

    async def step(self, data: dict[str, Any], query: str) -> tuple[dict[str, Any], str | None]:
        phase = data["phase"]
        artist = data.get("artist", {})
        if phase == "resolve":
            data["artists"] = await self.resolve(query)
            data["phase"], data["example_index"] = "examples", 0
        elif phase == "examples":
            index = data.get("example_index", 0)
            if index >= len(data["artists"]):
                return data, "artists" if data["artists"] else "failed"
            candidate = data["artists"][index]
            if candidate.get("apple_id"):
                result = await self.api(
                    "https://itunes.apple.com/lookup",
                    id=candidate["apple_id"],
                    entity="song",
                    limit=5,
                )
                tracks = self.apple_tracks(result, candidate, related=True)
                candidate["examples"] = list(dict.fromkeys(t["title"] for t in tracks))[:3]
            else:
                result = await self.api(
                    "https://musicbrainz.org/ws/2/recording",
                    artist=candidate["mb_id"],
                    limit=3,
                    fmt="json",
                )
                candidate["examples"] = [t["title"] for t in result.get("recordings", [])[:3]]
            data["example_index"] = index + 1
        elif phase in ("songs", "songs_ru"):
            if artist.get("apple_id"):
                result = await self.api(
                    "https://itunes.apple.com/lookup",
                    id=artist["apple_id"],
                    entity="song",
                    limit=200,
                    country="ru" if phase == "songs_ru" else "us",
                )
                data["raw"].extend(self.apple_tracks(result, artist, related=True))
            data["phase"] = "albums" if phase == "songs" else "mb_find"
        elif phase == "albums":
            result = (
                await self.api(
                    "https://itunes.apple.com/lookup",
                    id=artist["apple_id"],
                    entity="album",
                    limit=200,
                )
                if artist.get("apple_id")
                else {}
            )
            data["albums"] = [
                r["collectionId"]
                for r in result.get("results", [])
                if r.get("collectionType") == "Album"
            ][:100]
            data["album_index"], data["phase"] = 0, "album_tracks"
        elif phase == "album_tracks":
            index = data["album_index"]
            batch = data["albums"][index : index + 10]
            if not batch:
                data["phase"] = "songs_ru"
            else:
                result = await self.api(
                    "https://itunes.apple.com/lookup",
                    id=",".join(map(str, batch)),
                    entity="song",
                    limit=200,
                )
                data["raw"].extend(self.apple_tracks(result, artist))
                data["album_index"] = index + 10
        elif phase == "mb_find":
            if artist.get("mb_id"):
                data["mb_id"], data["mb_verified"] = artist["mb_id"], True
            else:
                result = await self.api(
                    "https://musicbrainz.org/ws/2/artist/",
                    query=f'artist:"{artist["name"]}"',
                    fmt="json",
                    limit=10,
                )
                matches = [
                    row
                    for row in result.get("artists", [])
                    if artist_key(row["name"]) == artist_key(artist["name"])
                ]
                if len(matches) == 1:
                    data["mb_id"] = matches[0]["id"]
                    data["mb_begin"] = matches[0].get("life-span", {}).get("begin", "") or ""
            data["mb_offset"], data["phase"] = 0, "mb_tracks" if data.get("mb_id") else "songbpm"
        elif phase == "mb_tracks":
            result = await self.api(
                "https://musicbrainz.org/ws/2/recording",
                artist=data["mb_id"],
                limit=100,
                offset=data["mb_offset"],
                fmt="json",
            )
            records = [
                {
                    "title": r["title"],
                    "seconds": (r["length"] + 500) // 1000 if r.get("length") else None,
                    "source": "MusicBrainz",
                    "url": "https://musicbrainz.org/recording/" + r["id"],
                    "bpm": None,
                }
                for r in result.get("recordings", [])
                if not r.get("video")
                and not (
                    data.get("mb_begin")
                    and r.get("first-release-date")
                    and r["first-release-date"][:4] < data["mb_begin"][:4]
                )
            ]
            known = recording_index(data["raw"])
            overlap = sum(recording_known(track, known) for track in records)
            if data.get("mb_verified") or overlap >= 3:
                data["mb_verified"] = True
                data["raw"].extend(records)
                data["mb_offset"] += 100
                if data["mb_offset"] >= min(result.get("recording-count", 0), 1000):
                    data["phase"] = "songbpm"
            else:
                data["warnings"].append("MusicBrainz: artist identity could not be verified")
                data["phase"] = "songbpm"
        elif phase == "songbpm":
            path = data.get("bpm_next") or "/@" + artist_key(artist["name"])
            page = await self.get("https://songbpm.com" + path)
            records, next_path = songbpm_page(page, artist_key(artist["name"]))
            data.setdefault("bpm_pending", []).extend(records)
            if not data.get("bpm_verified"):
                known = recording_index(data["raw"])
                overlaps = {
                    title_key(track["title"])
                    for track in data["bpm_pending"]
                    if recording_known(track, known)
                }
                data["bpm_verified"] = len(overlaps) >= 2
            data["bpm_pages"] = data.get("bpm_pages", 0) + 1
            if data["bpm_verified"]:
                data["raw"].extend(data["bpm_pending"])
                data["bpm_pending"] = []
            if (
                next_path
                and next_path != path
                and data["bpm_pages"] < 100
                and len(data["bpm_pending"]) < 1000
            ):
                data["bpm_next"] = next_path
            else:
                data["phase"] = "tunebat"
        elif phase == "tunebat":
            page = await self.get("https://tunebat.com/Search?" + urlencode({"q": artist["name"]}))
            records = tunebat_page(page, artist["name"])
            known = recording_index(data["raw"])
            if sum(recording_known(track, known) for track in records) >= 2:
                data["raw"].extend(records)
            data["phase"] = "finish"
        # RU: Не переписываем в БД одинаковые треки из сингла, альбома и другой страны.
        # EN: Compact each checkpoint while retaining conflicting BPM evidence.
        data["raw"] = list(
            {
                (r["source"], r["title"], r.get("seconds"), r.get("bpm")): r
                for r in data.get("raw", [])
            }.values()
        )
        if len(data.get("raw", [])) > 4000:
            data["raw"] = data["raw"][:4000]
            data["warnings"].append("Source result limit reached")
            data["phase"] = "finish"
        return data, "choosing" if data["phase"] == "finish" else None


def skip_phase(data: dict[str, Any]) -> dict[str, Any]:
    phase = data["phase"]
    if phase == "examples":
        data["example_index"] = data.get("example_index", 0) + 1
    else:
        data["phase"] = {
            "songs": "albums",
            "albums": "songs_ru",
            "album_tracks": "songs_ru",
            "songs_ru": "mb_find",
            "mb_find": "songbpm",
            "mb_tracks": "songbpm",
            "songbpm": "tunebat",
            "tunebat": "finish",
        }.get(phase, "finish")
    return data


def plain(value: str) -> str:
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", value)).split())


def songbpm_page(page: str, slug: str) -> tuple[list[dict[str, Any]], str | None]:
    escaped = re.escape(slug)
    matches = list(
        re.finditer(r'<a\b[^>]*href="(/@' + escaped + r'/[^/"?]+)"[^>]*>(.*?)</a>', page, re.S)
    )
    records = []
    for match in matches:
        names = re.findall(r"<p\b[^>]*>(.*?)</p>", match[2], re.S)
        duration = re.search(r"Duration\s+(\d+):(\d+)", plain(match[2]))
        bpm = re.search(r"BPM\s+(\d+(?:\.\d+)?)", plain(match[2]))
        if len(names) < 2 or not duration or not bpm or artist_key(plain(names[0])) != slug:
            continue
        records.append(
            {
                "title": plain(names[1]),
                "seconds": int(duration[1]) * 60 + int(duration[2]),
                "bpm": float(bpm[1]),
                "source": "SongBPM",
                "url": "https://songbpm.com" + html.unescape(match[1]),
            }
        )
    more = re.search(r'href="(/@' + escaped + r'\?after=[^"]+)"', page)
    return records, html.unescape(more[1]) if more else None


def tunebat_page(page: str, artist: str) -> list[dict[str, Any]]:
    marker = page.find('{"pageModel"')
    if marker < 0:
        return []
    properties, _ = json.JSONDecoder().raw_decode(page[marker:])
    items = json.loads(properties["pageModel"]).get("searchResult", {}).get("items", [])
    links = {
        path.rsplit("/", 1)[-1]: "https://tunebat.com" + html.unescape(path)
        for path in re.findall(r'href="(/Info/[^"<>]+)"', page)
    }
    return [
        {
            "title": r["n"],
            "seconds": (r["d"] + 500) // 1000,
            "bpm": r.get("b"),
            "source": "Tunebat",
            "url": links.get(r["id"], "https://tunebat.com/Search?" + urlencode({"q": artist})),
        }
        for r in items
        if r.get("d") and any(artist_key(name) == artist_key(artist) for name in r.get("as", []))
    ]
