from textwrap import wrap
from typing import Any

from setlist_bot.domain.concerts import compact_duration, date_label
from setlist_bot.domain.entities import Concert, Song
from setlist_bot.domain.rules import clock, flat_content, totals
from setlist_bot.transport.i18n import tr


def song_card(song: Song | dict[str, Any]) -> str:
    data = song if isinstance(song, dict) else vars(song)
    result = f"{data['title']} · {clock(data['duration_seconds'])}"
    if data.get("bpm"):
        result += f" · {data['bpm']} BPM"
    for key in ("lights", "comment"):
        if data.get(key):
            result += f"\n{data[key]}"
    return result


def concert_label(concert: Concert, language: str = "ru", *, button: bool = False) -> str:
    title = concert.title[:50] if button else concert.title
    text = f"{date_label(concert.date, language)} {title}".strip()
    if button and concert.performance_duration_seconds:
        text += " · " + compact_duration(concert.performance_duration_seconds, language)
    return text


def render_setlist(
    snapshot: dict[str, Any],
    concert: Concert,
    language: str,
    version: int,
    *,
    private: bool = False,
    approved: bool = False,
) -> str:
    status = "draft" if private else "approved_title" if approved else "working"
    snapshot = flat_content(snapshot)
    lines = [
        concert_label(concert, language),
        tr(language, status),
        tr(language, "version", version=version),
    ]
    if concert.performance_duration_seconds:
        lines.append(
            tr(language, "concert_duration_label")
            + ": "
            + compact_duration(concert.performance_duration_seconds, language)
        )
    if concert.city:
        lines.append(tr(language, "concert_city_label") + ": " + concert.city)
    if concert.extra_info:
        lines.append(concert.extra_info)
    lines.append(tr(language, "average_pause", time=clock(snapshot["default_pause_seconds"])))
    _, _, total = totals(snapshot)
    items = snapshot["blocks"][0]["items"]
    labels = [f"{pos}. {' '.join(item['title'].split())}" for pos, item in enumerate(items, 1)]
    width = min(27, max((len(label) for label in labels), default=20))
    separator = "─" * (width + 7)
    lines.append(separator)
    for pos, item in enumerate(items, 1):
        label = f"{pos}. {' '.join(item['title'].split())}"
        title_lines = wrap(label, width=width, subsequent_indent=" " * (len(str(pos)) + 2))
        lines.append(f"{title_lines[0]:<{width}}  {clock(item['duration_seconds'])}")
        lines.extend(title_lines[1:])
    lines += [separator, tr(language, "timing_total", time=clock(total))]
    return "\n".join(lines)
