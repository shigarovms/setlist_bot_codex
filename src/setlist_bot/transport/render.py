from textwrap import wrap
from typing import Any

from setlist_bot.domain.concerts import compact_duration, date_label
from setlist_bot.domain.display import english_colors, entry_labels, pause_title, short_duration
from setlist_bot.domain.entities import Concert, Song
from setlist_bot.domain.rules import clock, flat_content, is_pause, totals
from setlist_bot.transport.i18n import tr


def song_card(song: Song | dict[str, Any], language: str = "ru") -> str:
    data = song if isinstance(song, dict) else vars(song)
    result = f"{data['title']} · {clock(data['duration_seconds'])}"
    if data.get("bpm"):
        result += f" · {data['bpm']} BPM"
    for key in ("lights", "comment"):
        if data.get(key):
            result += "\n" + english_colors(data[key], language)
    return result


def concert_label(concert: Concert, language: str = "ru", *, button: bool = False) -> str:
    title = concert.title[:50] if button else concert.title
    text = " · ".join(
        part for part in (date_label(concert.date, language), title, concert.city) if part
    )
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
    header_only: bool = False,
) -> str:
    status = "draft" if private else "approved_title" if approved else "working"
    snapshot = flat_content(snapshot)
    lines = [
        concert_label(concert, language),
        tr(language, status) + " " + tr(language, "version", version=version),
    ]
    if approved and snapshot.get("approved_by"):
        lines[1] = tr(language, "approved_by", name=snapshot["approved_by"], version=version)
    lines.append(
        tr(
            language,
            "compact_header",
            pause=short_duration(snapshot["default_pause_seconds"], language),
            planned=short_duration(
                concert.performance_duration_seconds, language, minutes_only=True
            )
            if concert.performance_duration_seconds
            else tr(language, "unknown_duration"),
        )
    )
    if concert.extra_info:
        lines.append(concert.extra_info)
    if header_only:
        return "\n".join(lines)
    _, _, total = totals(snapshot)
    items = snapshot["blocks"][0]["items"]
    numbers = entry_labels(items)
    labels = []
    for pos, item in enumerate(items, 1):
        title = pause_title(language) if is_pause(item) else item["title"]
        prefix = numbers[pos - 1] + ("  " if is_pause(item) else ". ")
        labels.append(prefix + " ".join(title.split()))
    total_label = tr(language, "timing_total", time="").rstrip()
    time_width = max([len(clock(total)), *[len(clock(item["duration_seconds"])) for item in items]])
    # RU: Telegram не сообщает ширину экрана. Узкая таблица оставляет место аватару;
    # переносим только название, сохраняя номер и время на первой строке.
    # EN: Telegram exposes no viewport width. Keep rows narrow and wrap titles only.
    width = max(
        len(total_label),
        min(27 - 2 - time_width, max((len(label) for label in labels), default=20)),
    )
    separator = "─" * (width + 2 + time_width)
    lines.append(separator)
    for pos, item in enumerate(items, 1):
        number = numbers[pos - 1]
        indent = " " * (len(number) + 2)
        title = pause_title(language) if is_pause(item) else item["title"]
        title_lines = wrap(" ".join(title.split()), width=max(1, width - len(indent))) or [""]
        label = number + ("  " if is_pause(item) else ". ") + title_lines[0]
        lines.append(f"{label:<{width}}  {clock(item['duration_seconds']):>{time_width}}")
        lines.extend(indent + part for part in title_lines[1:])
    lines += [separator, f"{total_label:<{width}}  {clock(total):>{time_width}}"]
    return "\n".join(lines)
