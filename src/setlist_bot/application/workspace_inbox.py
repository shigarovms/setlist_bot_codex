"""Actionable personal invitations and administrator decisions."""

from collections import defaultdict
from typing import Any
from uuid import UUID

from setlist_bot.application.ports import Store
from setlist_bot.application.setlist_menus import REQUEST, SetlistMenus
from setlist_bot.application.staffing import OPEN, Staffing
from setlist_bot.application.workspace_gigs import REPLYABLE
from setlist_bot.application.workspace_setlists import WorkspaceSetlists
from setlist_bot.application.workspace_team import WorkspaceTeam, person_name
from setlist_bot.domain.concerts import today
from setlist_bot.domain.entities import (
    CatalogProposal,
    Concert,
    Confirmation,
    Group,
    Membership,
    StaffReplacement,
    Submission,
    User,
    now,
)

INBOX_PAGE = 20


class WorkspaceInbox:
    def __init__(self, store: Store, user: User) -> None:
        self.store, self.user = store, user

    async def personal(self) -> list[dict[str, Any]]:
        invitations = (await WorkspaceTeam(self.store, self.user).inbox())["invitations"]
        result = [
            {
                "kind": "roles" if row["already_member"] else "team_invite",
                "band": row["band"],
                "id": row["id"],
                "subject": " · ".join(row["roles"]),
            }
            for row in invitations
        ]
        rows = [
            row
            for row in await self.store.personal_invitations(self.user.id)
            if row.status in REPLYABLE and row.date >= today()
        ]
        groups = {g.id: g for g in await self.store.get_many(Group, {r.group_id for r in rows})}
        events = {e.id: e for e in await self.store.get_many(Concert, {r.concert_id for r in rows})}
        for row in sorted(rows, key=lambda row: (row.date, row.id)):
            group, event = groups.get(row.group_id), events.get(row.concert_id)
            if group and event and event.group_id == group.id and Staffing.valid(row, event, group):
                result.append(
                    self.event_item(
                        "date_change" if row.status == "reconfirm" else "gig", group, event, row.id
                    )
                )
        return result

    @staticmethod
    def event_item(kind: str, group: Group, event: Concert, identifier: UUID) -> dict[str, Any]:
        return {
            "kind": kind,
            "band": group.name,
            "band_id": str(group.id),
            "performance": str(event.id),
            "id": str(identifier),
            "title": event.title,
            "date": event.date.isoformat(),
            "past": event.date < today(),
        }

    async def admin(self) -> list[dict[str, Any]]:
        members = await self.store.find(
            Membership, user_id=self.user.id, status="active", role="admin"
        )
        groups = await self.store.get_many(Group, {row.group_id for row in members})
        confirmations: dict[UUID, set[UUID]] = defaultdict(set)
        for row in await self.store.find(
            Confirmation, user_id=self.user.id, chat_id=self.user.telegram_id
        ):
            value = SetlistMenus.data(row)
            if (
                row.expires_at > now()
                and value.get("kind") == REQUEST
                and value.get("status") == "pending"
            ):
                confirmations[UUID(value["group_id"])].add(UUID(value["concert_id"]))
        result: list[dict[str, Any]] = []
        shared = WorkspaceSetlists(self.store, self.user)
        for group in sorted(groups, key=lambda row: row.id):
            if group.deleted_at:
                continue
            team = [
                row
                for row in await self.store.find(Membership, group_id=group.id)
                if row.status != "left" or row.invitation
            ]
            team.sort(key=lambda row: (row.user_id != group.owner_id, row.id))
            asking = {
                row.user_id
                for row in team
                if row.status == "pending" or row.status == "active" and row.requested_roles
            }
            people = {person.id: person for person in await self.store.get_many(User, asking)}
            for index, member in enumerate(team):
                if (
                    member.status == "pending"
                    or member.status == "active"
                    and member.requested_roles
                ):
                    result.append(
                        {
                            "kind": "join" if member.status == "pending" else "role_request",
                            "band": group.name,
                            "band_id": str(group.id),
                            "id": str(member.id),
                            "page": index // INBOX_PAGE,
                            "subject": person_name(people[member.user_id], member),
                        }
                    )
            catalog = await self.store.find(CatalogProposal, group_id=group.id, status="pending")
            for index, proposal in enumerate(sorted(catalog, key=lambda row: row.id)):
                result.append(
                    {
                        "kind": "catalog",
                        "band": group.name,
                        "band_id": str(group.id),
                        "id": str(proposal.id),
                        "subject": proposal.payload.get("title")
                        or proposal.payload.get("alias", ""),
                        "page": index // INBOX_PAGE,
                    }
                )
            pending = await self.store.find(StaffReplacement, group_id=group.id, status="pending")
            by_event: dict[UUID, list[StaffReplacement]] = defaultdict(list)
            for item in pending:
                if item.expires_at > now():
                    by_event[item.concert_id].append(item)
            proposals = await self.store.find(
                Submission, group_id=group.id, applied=False, declined=False, pending_delivery=False
            )
            event_ids = (
                confirmations[group.id] | by_event.keys() | {row.concert_id for row in proposals}
            )
            for event in await self.store.get_many(Concert, set(event_ids)):
                if event.group_id != group.id or event.deleted_at:
                    continue
                if event.id in by_event and event.date >= today():
                    result.extend(await self.replacements(group, event, by_event[event.id]))
                if event.id in confirmations[group.id] or any(
                    row.concert_id == event.id for row in proposals
                ):
                    decision = await shared.requests(group.id, event.id)
                    result.extend(
                        {
                            **self.event_item(row["operation"], group, event, UUID(row["id"])),
                            "subject": row["requester"],
                        }
                        for row in decision["requests"]
                    )
                    result.extend(
                        {
                            **self.event_item("setlist", group, event, UUID(row["id"])),
                            "subject": row["author"],
                        }
                        for row in decision["proposals"]
                    )
        return sorted(
            result,
            key=lambda row: (
                row.get("date", ""),
                row["band"].casefold(),
                row["kind"],
                row["id"],
            ),
        )

    async def replacements(
        self, group: Group, event: Concert, pending: list[StaffReplacement]
    ) -> list[dict[str, Any]]:
        invitations = [
            row
            for row in await self.store.roster_invitations(group.id, event.id)
            if Staffing.valid(row, event, group)
        ]
        confirmed = {row.id for row in invitations if row.status == "confirmed"}
        result = []
        for exchange in pending:
            if exchange.source_id not in confirmed:
                continue
            candidates = [
                row
                for row in invitations
                if row.replacement_id == exchange.id and row.status in OPEN
            ]
            subject = f"@{exchange.candidate_ref}" if exchange.candidate_ref else ""
            if any(row.status == "accepted" for row in candidates):
                result.append(
                    {
                        **self.event_item("approve_replacement", group, event, exchange.id),
                        "subject": subject,
                    }
                )
            elif not candidates and exchange.candidate_link_by is None:
                result.append(
                    {
                        **self.event_item("replacement", group, event, exchange.id),
                        "subject": subject,
                    }
                )
        return result

    async def view(self) -> dict[str, Any]:
        personal, admin = await self.personal(), await self.admin()
        return {"language": self.user.language, "personal": personal, "admin": admin}
