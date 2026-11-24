"""Browser adapter for the same discovery job and catalog importer used by the bot."""

from typing import Any
from uuid import UUID

from setlist_bot.application.groups import Groups
from setlist_bot.application.music_search import ACTIVE, MusicSearches, notify_search
from setlist_bot.application.ports import Store
from setlist_bot.domain.entities import MusicSearch, User, now
from setlist_bot.domain.music_discovery import query_target
from setlist_bot.domain.rules import duration, require


class WorkspaceMusic:
    def __init__(self, store: Store, user: User) -> None:
        self.store, self.user = store, user
        self.searches = MusicSearches(store, user, user.telegram_id)

    async def view(self, group_id: UUID) -> dict[str, Any]:
        group = await Groups(self.store, self.user).access(group_id, admin=True)
        rows = await self.store.find(MusicSearch, group_id=group_id, owner_id=self.user.id)
        jobs = [
            row
            for row in rows
            if row.chat_id == self.user.telegram_id
            and row.expires_at > now()
            and row.group_epoch == group.lifecycle_version
        ]
        job = max(
            jobs, key=lambda item: (item.status in ACTIVE, item.created_at, item.id), default=None
        )
        return {
            "language": self.user.language,
            "band": group.name,
            "search": self._view(job) if job else None,
        }

    def _view(self, job: MusicSearch) -> dict[str, Any]:
        data = job.data
        result: dict[str, Any] = {
            "id": str(job.id),
            "version": job.version,
            "status": job.status,
            "query": job.query,
        }
        if job.status == "artists":
            result["artists"] = [
                {"name": row["name"], "examples": row.get("examples", [])[:3]}
                for row in data["artists"]
            ]
        elif job.status == "choosing":
            excluded = set(data.get("excluded", []))
            durations = data.get("durations", {})
            result.update(
                tracks=[
                    {
                        "index": index,
                        "title": row["title"],
                        "selected": index not in excluded,
                        "seconds": durations.get(str(index), row["seconds"]),
                        "bpm": row["bpm"],
                    }
                    for index, row in enumerate(data["tracks"])
                ],
                has_partial_results=bool(data.get("warnings")),
            )
        elif job.status == "preview":
            selected = [row for _, row in self.searches.selected(job)]
            found = sum(row["bpm"] is not None for row in selected)
            conflicts = sum(len(row.get("bpms", [])) > 1 for row in selected)
            result.update(
                songs=data["preview_rows"],
                duplicates=data["duplicate_count"],
                bpm={
                    "found": found,
                    "conflicts": conflicts,
                    "missing": len(selected) - found - conflicts,
                },
            )
        elif job.status == "done":
            result["imported"] = data["imported_count"]
        return result

    @staticmethod
    def command(payload: dict[str, Any]) -> dict[str, Any]:
        action = payload.get("action")
        require(
            action
            in (
                "start",
                "artist",
                "toggle",
                "all",
                "duration",
                "preview",
                "back",
                "commit",
                "cancel",
            ),
            "input",
        )
        if action == "start":
            query = payload.get("query")
            require(isinstance(query, str) and 0 < len(query.strip()) <= 300, "input")
            assert isinstance(query, str)
            query_target(query.strip())
            return {"action": action, "query": query.strip()}
        version = payload.get("version")
        require(type(version) is int and version > 0, "input")
        data = {"action": action, "job": str(UUID(payload["job"])), "version": version}
        if action in ("artist", "toggle", "duration"):
            index = payload.get("index")
            require(type(index) is int and index >= 0, "input")
            data["index"] = index
        if action == "duration":
            value = payload.get("value")
            require(isinstance(value, str) and len(value) <= 80, "input")
            assert isinstance(value, str)
            duration(value)
            data["value"] = value
        if action == "all":
            require(type(payload.get("include")) is bool, "input")
            data["include"] = payload["include"]
        return data

    async def apply(self, group_id: UUID, command: dict[str, Any]) -> dict[str, Any]:
        action = command["action"]
        if action == "start":
            job = await self.searches.create(group_id, command["query"])
            return {"job": str(job.id)}
        identifier = UUID(command["job"])
        # Check the tenant before load, which locks the job's actual group.
        current = await self.store.get(MusicSearch, identifier)
        require(current.group_id == group_id, "not_found")
        service = self.searches
        job = await service.load(identifier)
        # The background worker advances the version while searching. Cancelling the
        # same job is safe even if that worker has just finished one provider step.
        require(action == "cancel" or job.version == command["version"], "music_conflict")
        match action:
            case "artist":
                await service.choose_artist(job, command["index"])
            case "toggle":
                service.toggle(job, command["index"])
            case "all":
                service.select_all(job, command["include"])
            case "duration":
                service.set_duration(job, command["index"], command["value"])
            case "preview":
                await service.preview(job)
            case "back":
                await service.back(job)
            case "commit":
                await service.commit(job, job.data.get("preview_token", ""))
            case "cancel":
                service.cancel(job)
            case _:
                require(False, "input")
        if job.data.get("panel_ids") or job.data.get("group_panel_ids"):
            await notify_search(self.store, job, debounce=True)
        return {"job": str(job.id)}
