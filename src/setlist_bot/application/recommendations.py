"""A copyable starting list, using only the group's approved past performances."""

from collections import Counter

from setlist_bot.application.catalog import Catalog
from setlist_bot.application.ports import Store
from setlist_bot.domain.concerts import today
from setlist_bot.domain.entities import Concert, Revision, Setlist, User
from setlist_bot.domain.rules import clock, flat_content, is_pause, normalize, totals


async def suggested_titles(store: Store, user: User, concert: Concert) -> list[str]:
    songs = await Catalog(store, user).list_songs(concert.group_id)
    active = {str(song.id): song for song in songs}
    concerts = await store.find(Concert, group_id=concert.group_id, deleted_at=None)
    active_concerts = {c.id for c in concerts}
    past = {c.id: c for c in concerts if c.date < today() and c.id != concert.id}
    counts: Counter[str] = Counter()
    candidates = []
    for setlist in await store.find(Setlist, group_id=concert.group_id):
        if setlist.approved_revision_id is None or setlist.concert_id not in active_concerts:
            continue
        revision = await store.get(Revision, setlist.approved_revision_id)
        snapshot = flat_content(revision.snapshot)
        counts.update(
            item["song_id"]
            for block in snapshot["blocks"]
            for item in block["items"]
            if not is_pause(item)
        )
        if setlist.concert_id in past:
            candidates.append((past[setlist.concert_id], snapshot, totals(snapshot)[2]))
    target = concert.performance_duration_seconds
    if target and candidates:
        similar = [entry for entry in candidates if abs(entry[2] - target) <= target * 0.2]
        candidates = (
            sorted(similar, key=lambda entry: entry[0].date, reverse=True)
            if similar
            else sorted(
                candidates, key=lambda entry: (abs(entry[2] - target), -entry[0].date.toordinal())
            )
        )
    else:
        candidates.sort(key=lambda entry: entry[0].date, reverse=True)
    base: list[str] = []
    used: set[str] = set()
    if candidates:
        for entry in candidates[0][1]["blocks"][0]["items"]:
            if len(base) >= 194:
                break
            if is_pause(entry):
                base.append(clock(entry["duration_seconds"]))
            elif entry["song_id"] in active:
                base.append(active[entry["song_id"]].title)
                used.add(entry["song_id"])
    newest = sorted(songs, key=lambda song: (song.created_at, str(song.id)), reverse=True)
    new = [song for song in newest if str(song.id) not in used][:3]
    used.update(str(song.id) for song in new)
    popular = sorted(songs, key=lambda song: (-counts[str(song.id)], normalize(song.title)))
    top = [song for song in popular if counts[str(song.id)] and str(song.id) not in used][:3]
    return [
        "\n".join(section)
        for section in (base, [s.title for s in new], [s.title for s in top])
        if section
    ]
