"""RU: Запросы каталога из Mini App. EN: Reuse Catalog through the bot's durable queue.

HTTP has no catalog write privilege. A request and its retry receipt live in existing
Confirmation/outbox records; the bot revalidates permissions before calling Catalog.
"""

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID, uuid5

from setlist_bot.application.catalog import SONG_FIELDS, Catalog, song_data
from setlist_bot.application.concert_details import ConcertDetails
from setlist_bot.application.exports import PdfRenderer
from setlist_bot.application.importing import Importer
from setlist_bot.application.miniapp import MiniDrafts
from setlist_bot.application.repertoire_import import RepertoireImport
from setlist_bot.application.support import WORKSPACE_SUPPORT_KIND, Support, workspace_support_text
from setlist_bot.application.workspace_band_settings import (
    BAND_SETTINGS_KIND,
    WorkspaceBandSettings,
)
from setlist_bot.application.workspace_band_trash import BAND_TRASH_KIND, WorkspaceBandTrash
from setlist_bot.application.workspace_exports import WorkspaceExports
from setlist_bot.application.workspace_gigs import (
    GIG_REPLACEMENT,
    GIG_REPLY,
    GIG_SESSION,
    WorkspaceGigs,
)
from setlist_bot.application.workspace_music import WorkspaceMusic
from setlist_bot.application.workspace_performances import WorkspacePerformances
from setlist_bot.application.workspace_profile import PROFILE_KIND, WorkspaceProfile
from setlist_bot.application.workspace_roster import ROSTER_KIND, WorkspaceRoster
from setlist_bot.application.workspace_schedule import (
    CALENDAR_TTL,
    SCHEDULE_KIND,
    SCHEDULE_SESSION,
    CalendarHandoff,
    CalendarRuntime,
    WorkspaceSchedule,
)
from setlist_bot.application.workspace_setlists import SETLIST_SESSION, WorkspaceSetlists
from setlist_bot.application.workspace_staffing import WorkspaceStaffing
from setlist_bot.application.workspace_team import TEAM_SESSION, WorkspaceTeam
from setlist_bot.domain.entities import (
    CatalogProposal,
    Concert,
    Confirmation,
    Outbox,
    Song,
    SongAlias,
    now,
)
from setlist_bot.domain.performance_input import performance_seconds
from setlist_bot.domain.rules import duration, require, validate_song

CATALOG_OPERATIONS = {
    "catalog_save": "miniapp_catalog",
    "catalog_archive": "miniapp_catalog_archive",
    "catalog_restore": "miniapp_catalog_restore",
    "catalog_decide": "miniapp_catalog_decide",
    "catalog_import": "miniapp_catalog_import",
    "music_action": "miniapp_music",
    "shared_action": "miniapp_shared",
    "export_action": "miniapp_export",
    "performance_action": "miniapp_performance",
    "band_settings_save": BAND_SETTINGS_KIND,
    "team_action": "miniapp_team",
    "staff_action": "miniapp_staff",
    "roster_action": ROSTER_KIND,
    "gig_reply": GIG_REPLY,
    "gig_replacement": GIG_REPLACEMENT,
    "schedule_action": SCHEDULE_KIND,
    "profile_action": PROFILE_KIND,
    "support_submit": WORKSPACE_SUPPORT_KIND,
    "band_trash_action": BAND_TRASH_KIND,
    "duration_save": "miniapp_duration",
}


@dataclass(frozen=True)
class CatalogTarget:
    parent: Confirmation
    group_id: UUID | None
    concert: Concert | None = None
    shared: bool = False
    membership_id: UUID | None = None
    invitation_id: UUID | None = None


class MiniCatalog:
    def __init__(self, drafts: MiniDrafts) -> None:
        self.drafts = drafts
        self.store, self.user = drafts.store, drafts.user
        self.catalog = Catalog(self.store, self.user)

    async def open_repertoire(self, group_id: UUID) -> Confirmation:
        group = await self.drafts.groups.access(group_id)
        parent = Confirmation(
            user_id=self.user.id,
            chat_id=self.user.telegram_id,
            expires_at=now() + timedelta(hours=24),
            command=json.dumps(
                {
                    "kind": "miniapp_repertoire",
                    "group_id": str(group_id),
                    "group_epoch": group.lifecycle_version,
                }
            ),
        )
        self.store.add(parent)
        return parent

    async def open_team_membership(self, identifier: UUID) -> Confirmation:
        member, group = await WorkspaceTeam(self.store, self.user).own_membership(
            identifier, opening=True
        )
        parent = Confirmation(
            user_id=self.user.id,
            chat_id=self.user.telegram_id,
            expires_at=now() + timedelta(hours=1),
            command=json.dumps(
                {
                    "kind": TEAM_SESSION,
                    "group_id": str(group.id),
                    "group_epoch": group.lifecycle_version,
                    "membership_id": str(member.id),
                }
            ),
        )
        self.store.add(parent)
        return parent

    async def target(self, session: UUID) -> CatalogTarget:
        parent = await self.store.get(Confirmation, session)
        data = json.loads(parent.command)
        kind = data.get("kind")
        if kind not in (
            "miniapp_repertoire",
            SETLIST_SESSION,
            TEAM_SESSION,
            GIG_SESSION,
            SCHEDULE_SESSION,
        ):
            parent, _, concert = await self.drafts.load(session)
            return CatalogTarget(parent, concert.group_id, concert)
        require(
            parent.user_id == self.user.id
            and parent.chat_id == self.user.telegram_id
            and parent.expires_at > now(),
            "miniapp_expired",
        )
        if kind == SCHEDULE_SESSION:
            return CatalogTarget(parent, None)
        if kind == GIG_SESSION:
            scope = await WorkspaceGigs(self.store, self.user).scope(
                UUID(data["invitation_id"]), active=False
            )
            require(
                scope.group.deleted_at is None
                and scope.event.deleted_at is None
                and data["group_id"] == str(scope.group.id)
                and data["concert_id"] == str(scope.event.id)
                and data["group_epoch"] == scope.group.lifecycle_version
                and data["concert_epoch"] == scope.event.lifecycle_version,
                "miniapp_expired",
            )
            return CatalogTarget(
                parent, scope.group.id, scope.event, invitation_id=scope.invitation.id
            )
        if kind == TEAM_SESSION:
            member, group = await WorkspaceTeam(self.store, self.user).own_membership(
                UUID(data["membership_id"])
            )
            require(
                str(group.id) == data["group_id"]
                and group.lifecycle_version == data["group_epoch"],
                "miniapp_expired",
            )
            return CatalogTarget(parent, group.id, membership_id=member.id)
        group = await self.drafts.groups.access(UUID(data["group_id"]))
        require(group.lifecycle_version == data["group_epoch"], "miniapp_expired")
        if kind == SETLIST_SESSION:
            concert = await self.store.get(Concert, UUID(data["concert_id"]))
            require(
                concert.group_id == group.id
                and concert.deleted_at is None
                and concert.lifecycle_version == data["concert_epoch"],
                "miniapp_expired",
            )
            await self.drafts.groups.check_performance_visibility(group.id, concert.id)
            return CatalogTarget(parent, group.id, concert, shared=True)
        return CatalogTarget(parent, group.id)

    async def open_gig(self, identifier: UUID) -> Confirmation:
        scope = await WorkspaceGigs(self.store, self.user).scope(identifier)
        parent = Confirmation(
            user_id=self.user.id,
            chat_id=self.user.telegram_id,
            expires_at=now() + timedelta(hours=1),
            command=json.dumps(
                {
                    "kind": GIG_SESSION,
                    "invitation_id": str(scope.invitation.id),
                    "group_id": str(scope.group.id),
                    "group_epoch": scope.group.lifecycle_version,
                    "concert_id": str(scope.event.id),
                    "concert_epoch": scope.event.lifecycle_version,
                }
            ),
        )
        self.store.add(parent)
        return parent

    def open_account(self) -> Confirmation:
        parent = Confirmation(
            user_id=self.user.id,
            chat_id=self.user.telegram_id,
            expires_at=now() + timedelta(hours=1),
            command=json.dumps({"kind": SCHEDULE_SESSION}),
        )
        self.store.add(parent)
        return parent

    async def read(self, session: UUID, song_id: UUID) -> dict[str, Any]:
        target = await self.target(session)
        require(
            target.group_id is not None
            and target.membership_id is None
            and target.invitation_id is None,
            "forbidden",
        )
        assert target.group_id is not None
        song = await self.catalog.song(target.group_id, song_id)
        require(target.concert is None or not song.archived, "not_found")
        aliases = await self.store.find(SongAlias, group_id=target.group_id, song_id=song.id)
        return {**song_data(song, aliases), "archived": song.archived}

    async def queue(
        self,
        session: UUID,
        payload: dict[str, Any],
        *,
        operation: str = "catalog_save",
        bot_username: str = "",
        miniapp_key: bytes = b"",
        browser: bool = False,
    ) -> dict[str, Any]:
        target = await self.target(session)
        group_id, concert = target.group_id, target.concert
        kind = CATALOG_OPERATIONS[operation]
        account = kind in (PROFILE_KIND, SCHEDULE_KIND, WORKSPACE_SUPPORT_KIND, BAND_TRASH_KIND)
        if group_id is None:
            require(account, "forbidden")
            return await self.queue_account(target, kind, payload, miniapp_key, browser=browser)
        require(not account, "forbidden")
        workspace_operations = ("shared_action", "export_action", "roster_action")
        gig_operations = ("gig_reply", "gig_replacement")
        if target.invitation_id is not None:
            require(operation in (*gig_operations, "export_action"), "forbidden")
        else:
            require(
                operation not in gig_operations
                and target.shared == (operation in workspace_operations),
                "input",
            )
        personal = target.membership_id is not None
        require(not personal or operation == "team_action", "forbidden")
        team_command = None
        if operation == "team_action":
            require(concert is None, "input")
            team_command = WorkspaceTeam.command(payload, personal=personal)
            if not personal and team_command["action"] != "request_roles":
                await self.drafts.groups.access(group_id, admin=True)
        elif operation not in ("catalog_save", "shared_action", "export_action", *gig_operations):
            await self.drafts.groups.access(group_id, admin=True)
        identifier, fingerprint = self.request_identity(session, payload)
        previous = await self.previous(session, identifier, kind, fingerprint)
        if previous is not None:
            return previous
        expires = target.parent.expires_at
        if operation == "duration_save":
            require(concert is not None, "not_found")
            assert concert is not None
            expected = payload.get("expected")
            require(type(expected) is int and concert.metadata_version == expected, "conflict")
            value = payload.get("value")
            require(isinstance(value, str) and len(value) <= 80, "pause_duration")
            assert isinstance(value, str)
            require(value == "-" or performance_seconds(value) is not None, "pause_duration")
            details = {"value": value, "expected": expected}
        elif operation in ("catalog_archive", "catalog_restore"):
            song_id = payload.get("song_id")
            require(isinstance(song_id, str), "input")
            assert isinstance(song_id, str)
            song = await self.catalog.song(group_id, UUID(song_id))
            expected = payload.get("expected")
            require(type(expected) is int and song.version == expected, "conflict")
            require(song.archived == (operation == "catalog_restore"), "conflict")
            details = {"song_id": str(song.id), "expected": expected}
        elif operation == "catalog_decide":
            proposal = await self.store.get(CatalogProposal, UUID(payload["proposal_id"]))
            require(proposal.group_id == group_id, "not_found")
            require(proposal.status == "pending", "already_done")
            require(type(payload.get("approve")) is bool, "input")
            details = {"proposal_id": str(proposal.id), "approve": payload["approve"]}
        elif operation == "catalog_import":
            source = RepertoireImport.from_payload(payload)
            importer = Importer(self.store, self.user, self.user.telegram_id)
            plan = await source.preview(importer, group_id)
            Importer.check_plan(plan, payload["fingerprint"])
            details = {**source.data(), "import_fingerprint": plan.fingerprint}
            expires = min(expires, now() + timedelta(minutes=30))
        elif operation == "music_action":
            require(concert is None, "input")
            details = {"music": WorkspaceMusic.command(payload)}
        elif operation == "shared_action":
            details = {"shared": WorkspaceSetlists.command(payload)}
        elif operation == "export_action":
            details = {"export": WorkspaceExports.command(payload), "bot_username": bot_username}
        elif operation == "performance_action":
            require(concert is None, "input")
            details = {"performance": WorkspacePerformances.command(payload)}
            expires = min(expires, now() + timedelta(minutes=15))
        elif operation == "band_settings_save":
            require(concert is None, "input")
            details = {"settings": WorkspaceBandSettings.command(payload)}
            expires = min(expires, now() + timedelta(minutes=15))
        elif team_command is not None:
            details = {"team": team_command}
        elif operation == "staff_action":
            require(concert is None, "input")
            details = {"staff": WorkspaceStaffing.command(payload)}
        elif operation == "roster_action":
            details = {"roster": WorkspaceRoster.command(payload)}
            expires = min(expires, now() + timedelta(minutes=15))
        elif operation == "gig_reply":
            details = {"gig_reply": WorkspaceGigs.command(payload)}
            expires = min(expires, now() + timedelta(minutes=15))
        elif operation == "gig_replacement":
            details = {"gig_replacement": WorkspaceGigs.replacement_command(payload)}
            expires = min(expires, now() + timedelta(minutes=15))
        else:
            details = await self._catalog_details(group_id, payload)
        return self.enqueue(target, kind, identifier, fingerprint, expires, details)

    @staticmethod
    def request_identity(session: UUID, payload: dict[str, Any]) -> tuple[UUID, str]:
        request = payload.get("request_id")
        require(isinstance(request, str) and str(UUID(request)) == request, "input")
        assert isinstance(request, str)
        identifier = uuid5(session, request)
        # RU: Один request_id всегда означает одни данные. EN: Lost responses are safe to retry.
        fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        return identifier, fingerprint

    async def previous(
        self,
        session: UUID,
        identifier: UUID,
        kind: str,
        fingerprint: str,
        *,
        miniapp_key: bytes = b"",
    ) -> dict[str, Any] | None:
        previous = await self.store.find(Confirmation, id=identifier)
        if previous:
            data = json.loads(previous[0].command)
            require(data.get("kind") == kind and data.get("fingerprint") == fingerprint, "input")
            return await self.status(session, identifier, miniapp_key=miniapp_key)
        return None

    async def queue_account(
        self,
        target: CatalogTarget,
        kind: str,
        payload: dict[str, Any],
        miniapp_key: bytes,
        *,
        browser: bool = False,
    ) -> dict[str, Any]:
        session = await self.store.get(Confirmation, target.parent.id, lock=True)
        require(session.expires_at > now(), "miniapp_expired")
        identifier, fingerprint = self.request_identity(session.id, payload)
        previous = await self.previous(
            session.id, identifier, kind, fingerprint, miniapp_key=miniapp_key
        )
        if previous is not None:
            return previous
        if kind == PROFILE_KIND:
            details = {"profile": WorkspaceProfile.command(payload)}
        elif kind == BAND_TRASH_KIND:
            details = {"trash": WorkspaceBandTrash.command(payload)}
        elif kind == WORKSPACE_SUPPORT_KIND:
            details = {
                "support": {
                    "text": workspace_support_text(payload.get("text")),
                    "source": "browser" if browser else "miniapp",
                },
            }
        else:
            require(bool(miniapp_key), "forbidden")
            handoff = CalendarHandoff(miniapp_key, self.user.id, identifier)
            details = {"schedule": WorkspaceSchedule.command(payload, handoff)}
        expires = min(session.expires_at, now() + timedelta(seconds=CALENDAR_TTL))
        return self.enqueue(target, kind, identifier, fingerprint, expires, details)

    def enqueue(
        self,
        target: CatalogTarget,
        kind: str,
        identifier: UUID,
        fingerprint: str,
        expires: datetime,
        details: dict[str, Any],
    ) -> dict[str, Any]:
        session, group_id, concert = target.parent.id, target.group_id, target.concert
        self.store.add(
            Confirmation(
                id=identifier,
                user_id=self.user.id,
                chat_id=self.user.telegram_id,
                expires_at=expires,
                command=json.dumps(
                    {
                        "kind": kind,
                        "session": str(session),
                        "group_id": str(group_id) if group_id else None,
                        "concert_id": str(concert.id) if concert else None,
                        "fingerprint": fingerprint,
                        "status": "pending",
                        **details,
                    }
                ),
            )
        )
        self.store.add(
            Outbox(
                chat_id=self.user.telegram_id,
                text="",
                group_id=group_id,
                concert_id=concert.id if concert else None,
                support_kind="catalog",
                actor=f"telegram:{self.user.telegram_id}",
                source="miniapp",
                payload={"request_id": str(identifier)},
            )
        )
        return {"status": "pending", "request": str(identifier)}

    async def _catalog_details(self, group_id: UUID, payload: dict[str, Any]) -> dict[str, Any]:
        song_id = UUID(payload["song_id"]) if payload.get("song_id") else None
        fields = payload.get("fields")
        require(isinstance(fields, dict) and set(fields) == {*SONG_FIELDS, "aliases"}, "song_input")
        assert isinstance(fields, dict)
        data = {key: fields[key] for key in SONG_FIELDS}
        # UI sends MM:SS; never interpret a song duration as an event duration.
        data["duration_seconds"] = duration(fields["duration_seconds"])
        require(isinstance(data["title"], str), "song_input")
        data["title"] = data["title"].strip()
        validate_song(data)
        require(type(data["bpm"]) is int or data["bpm"] is None, "song_input")
        expected = payload.get("expected")
        if song_id:
            song = await self.catalog.song(group_id, song_id)
            require(not song.archived, "not_found")
            require(type(expected) is int and song.version == expected, "conflict")
        else:
            require(expected is None, "input")
        match = await self.catalog.exact_song(group_id, data["title"])
        require(match is None or match.id == song_id, "alias_collision")
        aliases = await self.catalog.validate_aliases(
            group_id, song_id, data["title"], fields["aliases"]
        )
        return {
            "fields": data,
            "aliases": aliases,
            "song_id": str(song_id) if song_id else None,
            "expected": expected,
        }

    async def status(
        self, session: UUID, identifier: UUID, *, miniapp_key: bytes = b""
    ) -> dict[str, Any]:
        await self.target(session)
        row = await self.store.get(Confirmation, identifier)
        data = json.loads(row.command)
        require(
            row.user_id == self.user.id
            and data.get("kind") in CATALOG_OPERATIONS.values()
            and data.get("session") == str(session),
            "miniapp_expired",
        )
        result = dict(data.get("result", {}))
        if data["kind"] in (SCHEDULE_KIND, PROFILE_KIND, WORKSPACE_SUPPORT_KIND, BAND_TRASH_KIND):
            require(row.expires_at > now(), "input_expired")
        if capsule := result.pop("calendar_capsule", None):
            require(data["kind"] == SCHEDULE_KIND and bool(miniapp_key), "input_expired")
            result["url"] = CalendarHandoff(miniapp_key, self.user.id, identifier).open(
                "reveal", capsule
            )
        return {"request": str(identifier), "status": data["status"], **result}

    async def execute(
        self,
        target: CatalogTarget,
        identifier: UUID,
        *,
        pdf: PdfRenderer | None = None,
        calendar: CalendarRuntime | None = None,
    ) -> dict[str, Any]:
        """Called only by the bot worker with normal database privileges."""
        group_id, concert = target.group_id, target.concert
        row = await self.store.get(Confirmation, identifier, lock=True)
        data = json.loads(row.command)
        require(
            row.user_id == self.user.id
            and data.get("kind") in CATALOG_OPERATIONS.values()
            and data.get("session") == str(target.parent.id)
            and data.get("group_id") == (str(group_id) if group_id else None)
            and data.get("concert_id") == (str(concert.id) if concert else None)
            and row.expires_at > now(),
            "miniapp_expired",
        )
        require(target.membership_id is None or data["kind"] == "miniapp_team", "forbidden")
        require(
            target.invitation_id is None
            or data["kind"] in (GIG_REPLY, GIG_REPLACEMENT, "miniapp_export"),
            "forbidden",
        )
        if data["status"] != "pending":
            return dict(data.get("result", {}))
        if data["kind"] == SCHEDULE_KIND:
            require(group_id is None and concert is None, "forbidden")
            result_schedule = await WorkspaceSchedule(self.store, self.user).apply(
                data["schedule"], row.id, calendar
            )
            row.command = json.dumps({**data, "status": "saved", "result": result_schedule})
            return result_schedule
        if data["kind"] == PROFILE_KIND:
            require(group_id is None and concert is None, "forbidden")
            result_profile = await WorkspaceProfile(self.store, self.user).apply(
                data.pop("profile")
            )
            row.command = json.dumps({**data, "status": "saved", "result": result_profile})
            return result_profile
        if data["kind"] == WORKSPACE_SUPPORT_KIND:
            require(group_id is None and concert is None, "forbidden")
            support = data.pop("support")
            delivered = await Support(self.store, self.user).submit(
                support["text"], source=support["source"]
            )
            require(delivered, "support_unavailable")
            row.command = json.dumps({**data, "status": "saved"})
            return {}
        if data["kind"] == BAND_TRASH_KIND:
            require(group_id is None and concert is None, "forbidden")
            result_trash, change = await WorkspaceBandTrash(self.store, self.user).apply(
                target.parent.id, data["trash"]
            )
            if change is not None:
                data["change"] = change
            row.command = json.dumps({**data, "status": "saved", "result": result_trash})
            return result_trash
        require(group_id is not None, "forbidden")
        assert group_id is not None
        if data["kind"] == BAND_SETTINGS_KIND:
            require(concert is None and not target.shared, "miniapp_expired")
            await WorkspaceBandSettings(self.store, self.user).apply(group_id, data["settings"])
            row.command = json.dumps({**data, "status": "saved"})
            return {}
        if data["kind"] == "miniapp_duration":
            require(concert is not None, "not_found")
            assert concert is not None
            await self.drafts.groups.access(group_id, admin=True)
            require(concert.metadata_version == data["expected"], "conflict")
            saved_event = await ConcertDetails(self.store, self.user).update(
                group_id, concert.id, "concert_duration", data["value"]
            )
            duration_result = {
                "planned_seconds": saved_event.performance_duration_seconds,
                "metadata_version": saved_event.metadata_version,
            }
            row.command = json.dumps({**data, "status": "saved", "result": duration_result})
            return duration_result
        if data["kind"] in ("miniapp_catalog_archive", "miniapp_catalog_restore"):
            archived = data["kind"] == "miniapp_catalog_archive"
            await self.catalog.archive(
                group_id, UUID(data["song_id"]), data["expected"], archived=archived
            )
            archive_result = {"archived": archived}
            row.command = json.dumps({**data, "status": "saved", "result": archive_result})
            return archive_result
        if data["kind"] == "miniapp_catalog_decide":
            await self.catalog.decide(group_id, UUID(data["proposal_id"]), data["approve"])
            decision = {"approved": data["approve"]}
            row.command = json.dumps({**data, "status": "saved", "result": decision})
            return decision
        if data["kind"] == "miniapp_catalog_import":
            importer = Importer(self.store, self.user, self.user.telegram_id)
            imported = await importer.commit_source(
                group_id,
                data["source"],
                data["mapping"],
                data["has_header"],
                data["import_fingerprint"],
            )
            summary = {"added": imported.added, "supplemented": imported.supplemented}
            # The source is no longer needed after import. Keep the idempotency receipt.
            receipt = {key: value for key, value in data.items() if key != "source"}
            row.command = json.dumps({**receipt, "status": "saved", "result": summary})
            return summary
        if data["kind"] == "miniapp_music":
            music_result = await WorkspaceMusic(self.store, self.user).apply(
                group_id, data["music"]
            )
            row.command = json.dumps({**data, "status": "saved", "result": music_result})
            return music_result
        if data["kind"] == "miniapp_shared":
            require(target.shared and concert is not None, "miniapp_expired")
            assert concert is not None
            shared_result = await WorkspaceSetlists(self.store, self.user).apply(
                group_id, concert.id, data["shared"]
            )
            public = {
                key: value
                for key, value in shared_result.items()
                if key not in ("notify", "resolved")
            }
            row.command = json.dumps({**data, "status": "saved", "result": public})
            return shared_result
        if data["kind"] == "miniapp_export":
            require(
                (target.shared or target.invitation_id is not None) and concert is not None,
                "miniapp_expired",
            )
            assert concert is not None
            result_export = await WorkspaceExports(
                self.store, self.user, data["bot_username"], invitation_id=target.invitation_id
            ).apply(group_id, concert.id, data["export"], pdf)
            row.command = json.dumps({**data, "status": "saved", "result": result_export})
            return result_export
        if data["kind"] == "miniapp_performance":
            require(concert is None, "miniapp_expired")
            manager = WorkspacePerformances(self.store, self.user)
            command = data["performance"]
            if command["action"] == "confirm":
                result_performance = await manager.confirm(
                    group_id, target.parent.id, UUID(command["preview"])
                )
            else:
                result_performance, change = await manager.preview(group_id, command)
                data["change"] = change
            row.command = json.dumps({**data, "status": "saved", "result": result_performance})
            return result_performance
        if data["kind"] == "miniapp_team":
            require(concert is None and not target.shared, "miniapp_expired")
            result_team = await WorkspaceTeam(self.store, self.user).apply(
                group_id, data["team"], target.membership_id
            )
            row.command = json.dumps({**data, "status": "saved", "result": result_team})
            return result_team
        if data["kind"] == "miniapp_staff":
            require(concert is None and not target.shared, "miniapp_expired")
            result_staff = await WorkspaceStaffing(self.store, self.user).apply(
                group_id, data["staff"]
            )
            row.command = json.dumps({**data, "status": "saved", "result": result_staff})
            return result_staff
        if data["kind"] == ROSTER_KIND:
            require(target.shared and concert is not None, "miniapp_expired")
            assert concert is not None
            result_roster = await WorkspaceRoster(self.store, self.user).apply(
                group_id, concert.id, data
            )
            if delivery := result_roster.pop("delivery", None):
                data["delivery"] = delivery
            row.command = json.dumps({**data, "status": "saved", "result": result_roster})
            return result_roster
        if data["kind"] in (GIG_REPLY, GIG_REPLACEMENT):
            require(target.invitation_id is not None, "miniapp_expired")
            assert target.invitation_id is not None
            gigs = WorkspaceGigs(self.store, self.user)
            result_reply = (
                await gigs.reply(target.invitation_id, data)
                if data["kind"] == GIG_REPLY
                else await gigs.replace(target.invitation_id, data["gig_replacement"])
            )
            row.command = json.dumps({**data, "status": "saved", "result": result_reply})
            return result_reply
        song_id = UUID(data["song_id"]) if data["song_id"] else None
        if song_id:
            require(not (await self.catalog.song(group_id, song_id)).archived, "not_found")
        match = await self.catalog.exact_song(group_id, data["fields"]["title"])
        require(match is None or match.id == song_id, "alias_collision")
        saved = await self.catalog.save(
            group_id, data["fields"], song_id, data["expected"], aliases=data["aliases"]
        )
        proposed = isinstance(saved, CatalogProposal)
        result: dict[str, Any] = {"proposed": proposed}
        if not proposed:
            assert isinstance(saved, Song)
            result["song"] = song_data(
                saved, await self.store.find(SongAlias, group_id=group_id, song_id=saved.id)
            )
        row.command = json.dumps({**data, "status": "saved", "result": result})
        return result
