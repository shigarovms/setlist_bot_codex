"""RU: Только SELECT для публичной службы. EN: No access to drafts, messages or tokens."""

from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncEngine

from setlist_bot.domain.calendar import CalendarEvent
from setlist_bot.domain.display import short_duration
from setlist_bot.infrastructure.database import metadata

MAX_EVENTS = 5000


class CalendarQueries:
    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine

    async def read(self, digest: str) -> tuple[dict[str, object], tuple[CalendarEvent, ...]] | None:
        subscriptions = metadata.tables["calendar_subscriptions"]
        concerts, groups, members = (
            metadata.tables[n] for n in ("concerts", "groups", "memberships")
        )
        async with self.engine.connect() as connection:
            subscription = (
                (
                    await connection.execute(
                        select(
                            *(
                                subscriptions.c[name]
                                for name in (
                                    "id",
                                    "owner_id",
                                    "group_id",
                                    "token_hash",
                                    "language",
                                    "enabled",
                                    "version",
                                    "created_at",
                                    "scope",
                                )
                            )
                        ).where(
                            subscriptions.c.token_hash == digest, subscriptions.c.enabled.is_(True)
                        )
                    )
                )
                .mappings()
                .one_or_none()
            )
            if subscription is None:
                return None
            conditions = [
                members.c.user_id == subscription["owner_id"],
                members.c.status == "active",
                groups.c.deleted_at.is_(None),
                concerts.c.deleted_at.is_(None),
            ]
            if subscription["group_id"] is not None:
                conditions.append(groups.c.id == subscription["group_id"])
            if subscription["scope"] == "personal":
                invitations = metadata.tables["gig_invitations"]
                conditions = [
                    groups.c.deleted_at.is_(None),
                    concerts.c.deleted_at.is_(None),
                    exists(
                        select(invitations.c.id).where(
                            invitations.c.user_id == subscription["owner_id"],
                            invitations.c.concert_id == concerts.c.id,
                            invitations.c.group_id == groups.c.id,
                            invitations.c.status == "confirmed",
                            invitations.c.date == concerts.c.date,
                            invitations.c.concert_epoch == concerts.c.lifecycle_version,
                        )
                    ),
                ]
            query = (
                select(concerts, groups.c.name.label("group_name"))
                .select_from(concerts)
                .join(groups, groups.c.id == concerts.c.group_id)
                .where(*conditions)
                .order_by(concerts.c.date, concerts.c.id)
                .limit(MAX_EVENTS + 1)
            )
            if subscription["scope"] != "personal":
                query = query.join(members, members.c.group_id == groups.c.id)
            else:
                invitation_id = (
                    select(invitations.c.id)
                    .where(
                        invitations.c.user_id == subscription["owner_id"],
                        invitations.c.concert_id == concerts.c.id,
                        invitations.c.status == "confirmed",
                        invitations.c.date == concerts.c.date,
                        invitations.c.concert_epoch == concerts.c.lifecycle_version,
                    )
                    .order_by(invitations.c.id)
                    .limit(1)
                    .scalar_subquery()
                )
                query = query.add_columns(invitation_id.label("invitation_id"))
            rows = (await connection.execute(query)).mappings().all()
        if len(rows) > MAX_EVENTS:
            raise OverflowError("Calendar event limit exceeded")
        events = []
        language = str(subscription["language"])
        labels = (
            ("Тип", "Плановая длительность", "Город")
            if language == "ru"
            else ("Type", "Planned duration", "City")
        )
        for row in rows:
            description = []
            for label, value in zip(
                labels,
                (
                    row["performance_type"],
                    short_duration(row["performance_duration_seconds"], language, minutes_only=True)
                    if row["performance_duration_seconds"]
                    else "",
                    row["city"],
                ),
                strict=True,
            ):
                if value:
                    description.append(label + ": " + value)
            if row["extra_info"]:
                description.append(row["extra_info"])
            events.append(
                CalendarEvent(
                    id=row["id"],
                    date=row["date"],
                    summary=" · ".join(part for part in (row["group_name"], row["title"]) if part),
                    city=row["city"],
                    description="\n".join(description),
                    sequence=row["metadata_version"] + row["lifecycle_version"],
                    route="gig_" + row["invitation_id"].hex
                    if subscription["scope"] == "personal"
                    else "",
                )
            )
        return dict(subscription), tuple(events)
