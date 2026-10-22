from textwrap import wrap
from typing import Any

from setlist_bot.domain.entities import Concert, Song
from setlist_bot.domain.rules import clock, pause_content, totals
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


def concert_label(concert: Concert) -> str:
    return f"{concert.date.isoformat()} {concert.title}".strip()


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
    snapshot = pause_content(snapshot)
    lines = [concert_label(concert), tr(language, status), tr(language, "version", version=version)]
    if "default_pause_seconds" in snapshot:
        lines.append(tr(language, "average_pause", time=clock(snapshot["default_pause_seconds"])))
    times, net, total = totals(snapshot)
    blocks = snapshot["blocks"]
    multiple = len(blocks) > 1
    labels = [
        f"{str(n) + '.' if multiple else ''}{pos}. {' '.join(item['title'].split())}"
        for n, block in enumerate(blocks, 1)
        for pos, item in enumerate(block["items"], 1)
    ]
    width = min(27, max((len(label) for label in labels), default=20))
    separator = "─" * (width + 7)
    lines.append(separator)
    for n, block in enumerate(blocks, 1):
        if multiple or block["title"]:
            lines.append("\n" + (block["title"] or tr(language, "block_title", number=n)))
        for pos, item in enumerate(block["items"], 1):
            pause = item.get("pause_before")
            if pause:
                lines.append(tr(language, "lead_in", time=clock(pause)))
            number = f"{n}.{pos}" if multiple else str(pos)
            label = f"{number}. {' '.join(item['title'].split())}"
            title_lines = wrap(label, width=width, subsequent_indent=" " * (len(number) + 2))
            lines.append(f"{title_lines[0]:<{width}}  {clock(item['duration_seconds'])}")
            lines.extend(title_lines[1:])
        if multiple:
            lines.append(tr(language, "block_duration", time=clock(times[n - 1])))
        if n < len(blocks) and block["break_seconds"]:
            lines.append(tr(language, "block_break", time=clock(block["break_seconds"])))
    lines += [separator, tr(language, "timing_net", time=clock(net))]
    if total > net:
        lines.append(tr(language, "timing_breaks", time=clock(total - net)))
    lines.append(tr(language, "timing_total", time=clock(total)))
    return "\n".join(lines)
