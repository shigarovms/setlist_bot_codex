"""RU: Участие в выступлении не даёт членства в коллективе.

EN: Person locks precede tenant locks. Reserve and swap atomically, notify via outbox;
never release an existing commitment just because another band sent an invitation.
"""

from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from uuid import UUID

from setlist_bot.application.activity import record_activity
from setlist_bot.application.groups import Groups
from setlist_bot.application.ports import Store
from setlist_bot.application.role_dictionary import RoleDictionary
from setlist_bot.domain.concerts import today
from setlist_bot.domain.entities import (
    BusyPeriod,
    Concert,
    GigInvitation,
    GigSlot,
    Group,
    LineupTemplate,
    Membership,
    Outbox,
    StaffContact,
    StaffReplacement,
    StaffRole,
    User,
    now,
)
from setlist_bot.domain.participation import OPEN_INVITATIONS
from setlist_bot.domain.rules import normalize, require
from setlist_bot.domain.staff_roles import RoleIndex

REMINDER_COOLDOWN = timedelta(minutes=1)

OPEN = OPEN_INVITATIONS


class Staffing:
    def __init__(self, store: Store, user: User):
        self.store, self.user = store, user
        self.groups = Groups(store, user)
        self._vocabulary: RoleIndex | None = None

    async def vocabulary(self) -> RoleIndex:
        if self._vocabulary is None:
            self._vocabulary = await RoleDictionary(self.store).index()
        return self._vocabulary

    async def event(self, identifier: UUID, *, admin: bool = True) -> Concert:
        event = await self.store.get(Concert, identifier)
        group = await self.groups.access(event.group_id, admin=admin)
        require(event.deleted_at is None, "in_trash")
        if not admin and group.owner_id != self.user.id:
            await self.groups.check_performance_visibility(event.group_id, event.id)
        return event

    async def role(self, group_id: UUID, name: str) -> StaffRole:
        await self.groups.access(group_id, admin=True)
        require(0 < len(name.strip()) <= 100 and not any(ord(c) < 32 for c in name), "input")
        key = normalize(name)
        found = await self.store.find(StaffRole, group_id=group_id, name_key=key)
        if found:
            # RU: Ключ нужен для поиска, а введённый регистр — для отображения.
            # EN: Reusing a normalized role must not discard the administrator's spelling.
            if found[0].name != name.strip():
                found[0].name = name.strip()
                from setlist_bot.application.member_boards import queue_member_board

                await self.store.flush()
                await queue_member_board(self.store, group_id)
            return found[0]
        vocabulary = await self.vocabulary()
        canonical = vocabulary.key(name)
        if not canonical.startswith("custom:"):
            synonyms = [
                r
                for r in await self.store.find(StaffRole, group_id=group_id)
                if vocabulary.key(r.name) == canonical
            ]
            if synonyms:
                return min(synonyms, key=lambda r: r.id)
        row = StaffRole(group_id=group_id, name=name.strip(), name_key=key)
        self.store.add(row)
        await self.store.flush()
        return row

    async def contact(
        self, group_id: UUID, role_id: UUID, user_id: UUID, preference: str | None = None
    ) -> StaffContact:
        await self.groups.access(group_id, admin=True)
        role = await self.store.get(StaffRole, role_id)
        require(
            role.group_id == group_id and preference in (None, "normal", "preferred", "hidden"),
            "input",
        )
        await self.store.get(User, user_id)
        rows = await self.store.find(
            StaffContact, group_id=group_id, role_id=role_id, user_id=user_id
        )
        row = rows[0] if rows else StaffContact(group_id=group_id, role_id=role_id, user_id=user_id)
        if not rows:
            self.store.add(row)
        if preference is not None:
            row.preference = preference
        row.version += 1
        record_activity(self.store, self.user, "staff_contact", row.id, preference=preference)
        return row

    async def set_permanent(
        self,
        group_id: UUID,
        role_id: UUID,
        user_id: UUID,
    ) -> StaffContact:
        row = await self.contact(group_id, role_id, user_id)
        if not row.permanent:
            vocabulary = await self.vocabulary()
            roles = {r.id: r for r in await self.store.find(StaffRole, group_id=group_id)}
            previous = [
                r.priority
                for r in await self.store.find(StaffContact, group_id=group_id)
                if r.permanent
                and vocabulary.key(roles[r.role_id].name) == vocabulary.key(roles[role_id].name)
            ]
            row.priority = max(previous, default=-1) + 1
        row.permanent = True
        from setlist_bot.application.member_boards import queue_member_board

        await self.store.flush()
        await queue_member_board(self.store, group_id)
        return row

    async def change_contact(
        self,
        identifier: UUID,
        expected: int,
        *,
        remove: bool = False,
        priority: int | None = None,
    ) -> StaffContact:
        row = await self.store.get(StaffContact, identifier)
        await self.groups.access(row.group_id, admin=True)
        row = await self.store.get(StaffContact, identifier, lock=True)
        require(row.version == expected, "input_expired")
        if remove:
            # RU: Убираем из постоянных, сохраняя знакомства и историю выступлений.
            # EN: Removing a permanent assignment never cancels existing bookings.
            row.permanent = False
        if priority is not None:
            require(type(priority) is int and 0 <= priority <= 9999, "input")
            row.priority = priority
        row.version += 1
        record_activity(self.store, self.user, "permanent_performer", row.id, removed=remove)
        from setlist_bot.application.member_boards import queue_member_board

        await self.store.flush()
        await queue_member_board(self.store, row.group_id)
        return row

    async def permanent_order(self, group_id: UUID, role_id: UUID) -> list[StaffContact]:
        await self.groups.access(group_id, admin=True)
        vocabulary = await self.vocabulary()
        roles = {
            r.id: vocabulary.key(r.name)
            for r in await self.store.find(StaffRole, group_id=group_id)
        }
        require(role_id in roles, "forbidden")
        return sorted(
            (
                contact
                for contact in await self.store.find(StaffContact, group_id=group_id)
                if contact.permanent and roles[contact.role_id] == roles[role_id]
            ),
            key=lambda contact: (
                contact.preference == "hidden",
                contact.priority,
                contact.preference != "preferred",
                -contact.worked_count,
                contact.id,
            ),
        )

    async def move_permanent(self, identifier: UUID, expected: int, step: int) -> StaffContact:
        row = await self.store.get(StaffContact, identifier)
        await self.groups.access(row.group_id, admin=True)
        row = await self.store.get(StaffContact, identifier, lock=True)
        order = await self.permanent_order(row.group_id, row.role_id)
        require(step in (-1, 1) and row.version == expected and row.permanent, "input_expired")
        visible = [item for item in order if item.preference != "hidden"]
        require(row in visible, "input_expired")
        index = visible.index(row)
        require(0 <= index + step < len(visible), "input_expired")
        visible[index], visible[index + step] = visible[index + step], visible[index]
        for priority, performer in enumerate(visible):
            performer.priority = priority
            performer.version += 1
        record_activity(self.store, self.user, "permanent_performer", row.id, reordered=True)
        from setlist_bot.application.member_boards import queue_member_board

        await self.store.flush()
        await queue_member_board(self.store, row.group_id)
        return row

    async def lineup_people(self, group_id: UUID, identifiers: list[UUID]) -> list[UUID | None]:
        await self.groups.access(group_id, admin=True)
        roles = {r.id: r for r in await self.store.find(StaffRole, group_id=group_id)}
        return self.select_defaults(
            roles,
            await self.store.find(StaffContact, group_id=group_id),
            identifiers,
            await self.vocabulary(),
        )

    @staticmethod
    def select_defaults(
        roles: dict[UUID, StaffRole],
        contacts: list[StaffContact],
        identifiers: list[UUID],
        vocabulary: RoleIndex | None = None,
    ) -> list[UUID | None]:
        vocabulary = vocabulary or RoleIndex()
        contacts = sorted(
            (c for c in contacts if c.permanent and c.preference != "hidden"),
            key=lambda c: (c.priority, c.preference != "preferred", -c.worked_count, c.id),
        )
        result: list[UUID | None] = []
        used: dict[str, set[UUID]] = {}
        # RU: Два вокала — два человека. Повтор роли не дублирует первого кандидата.
        # EN: Consume a distinct default per occurrence of the same professional role.
        for identifier in identifiers:
            key = vocabulary.key(roles[identifier].name)
            occupied = used.setdefault(key, set())
            person = next(
                (
                    c.user_id
                    for c in contacts
                    if c.user_id not in occupied
                    and vocabulary.matches(roles[identifier].name, roles[c.role_id].name)
                ),
                None,
            )
            result.append(person)
            if person:
                occupied.add(person)
        return result

    async def template(
        self, group_id: UUID, name: str, values: list[dict[str, Any]]
    ) -> LineupTemplate:
        await self.groups.access(group_id, admin=True)
        require(0 < len(name.strip()) <= 100 and 1 <= len(values) <= 40, "input")
        for item in values:
            role = await self.store.get(StaffRole, UUID(item["role_id"]))
            require(role.group_id == group_id, "forbidden")
            if item.get("user_id"):
                person = await self.store.get(User, UUID(item["user_id"]))
                await self.set_permanent(group_id, role.id, person.id)
        matches = await self.store.find(LineupTemplate, group_id=group_id, name_key=normalize(name))
        row = (
            matches[0]
            if matches
            else LineupTemplate(group_id=group_id, name=name.strip(), name_key=normalize(name))
        )
        if not matches:
            self.store.add(row)
        else:
            row.version += 1
        row.slots = values
        record_activity(self.store, self.user, "lineup_template", row.id)
        return row

    async def edit_template(
        self,
        identifier: UUID,
        expected: int,
        *,
        name: str | None = None,
        values: list[dict[str, Any]] | None = None,
        delete: bool = False,
    ) -> LineupTemplate:
        row = await self.store.get(LineupTemplate, identifier)
        await self.groups.access(row.group_id, admin=True)
        row = await self.store.get(LineupTemplate, identifier, lock=True)
        require(row.version == expected, "input_expired")
        if delete:
            await self.store.delete(row)
        else:
            if name is not None:
                require(0 < len(name.strip()) <= 100, "input")
                others = await self.store.find(
                    LineupTemplate, group_id=row.group_id, name_key=normalize(name)
                )
                require(not others or others[0].id == row.id, "input")
                row.name, row.name_key = name.strip(), normalize(name)
            if values is not None:
                require(len(values) <= 40, "input")
                for item in values:
                    role = await self.store.get(StaffRole, UUID(item["role_id"]))
                    require(role.group_id == row.group_id, "forbidden")
                    if item.get("user_id"):
                        await self.set_permanent(row.group_id, role.id, UUID(item["user_id"]))
                row.slots = [dict(item) for item in values]
            row.version += 1
        record_activity(self.store, self.user, "lineup_edited", row.id, deleted=delete)
        return row

    async def remove_slot(self, identifier: UUID, expected: int) -> GigSlot:
        slot = await self.store.get(GigSlot, identifier)
        event = await self.event(slot.concert_id)
        slot = await self.store.get(GigSlot, identifier, lock=True)
        require(not slot.removed and slot.version == expected, "input_expired")
        # RU: Не удаляем историю приглашений; исключаем роль из действующего состава.
        # EN: Soft removal preserves consent and substitution history for support/audit.
        await self.needed(slot.id, expected, False)
        slot.removed = True
        event.lineup_version += 1
        record_activity(self.store, self.user, "lineup_role_removed", slot.id)
        return slot

    async def cancel_invitation(self, identifier: UUID, expected: int) -> GigInvitation:
        row = await self.store.get(GigInvitation, identifier)
        event = await self.event(row.concert_id)
        row = await self.store.get(GigInvitation, identifier, lock=True)
        require(row.status in OPEN | {"confirmed"} and row.version == expected, "input_expired")
        row.status, row.version = "cancelled", row.version + 1
        await self.cancel_replacements(row.id)
        await self.notify(row.user_id, "cancelled", row)
        event.lineup_version += 1
        return row

    async def remind(self, identifier: UUID, expected: int) -> GigInvitation:
        row = await self.store.get(GigInvitation, identifier)
        event = await self.event(row.concert_id)
        row = await self.store.get(GigInvitation, identifier, lock=True)
        require(
            row.status in ("pending", "reconfirm", "conditional")
            and row.version == expected
            and self.valid(row, event, await self.store.get(Group, row.group_id)),
            "input_expired",
        )
        if row.last_reminded_at and row.last_reminded_at + REMINDER_COOLDOWN > now():
            return row
        # RU: Повторное нажатие не плодит неотправленные письма.
        # EN: Retry shares the durable outbox; repeated clicks do not duplicate pending delivery.
        row.last_reminded_at = now()
        await self.notify(row.user_id, "invitation", row)
        return row

    async def replace_uninvited_lineup(
        self, event_id: UUID, expected: int, name: str, values: list[dict[str, Any]]
    ) -> Concert:
        """RU: Принимаем подготовленный состав без приглашений. EN: Atomic pre-invite save."""
        event = await self.event(event_id)
        require(
            event.lineup_version == expected and len(values) <= 40 and len(name) <= 100,
            "input_expired",
        )
        require(
            not any(
                row.status in OPEN | {"confirmed"}
                for row in await self.store.find(GigInvitation, concert_id=event.id)
            ),
            "input_expired",
        )
        # RU: Сначала проверяем все ссылки; временный ввод не меняет живой состав.
        # EN: Validate the whole plan before mutation. No notification is generated here.
        for item in values:
            if item.get("role_id"):
                role = await self.store.get(StaffRole, UUID(item["role_id"]))
                require(role.group_id == event.group_id, "forbidden")
            else:
                text = item.get("role_name", "")
                require(
                    0 < len(text.strip()) <= 100 and not any(ord(ch) < 32 for ch in text), "input"
                )
            if item.get("user_id"):
                await self.store.get(User, UUID(item["user_id"]))
        previous = sorted(
            await self.store.find(GigSlot, concert_id=event.id, removed=False),
            key=lambda slot: slot.position,
        )
        unchanged = [
            {
                "role_id": str(slot.role_id),
                "user_id": str(slot.preferred_user_id) if slot.preferred_user_id else None,
            }
            for slot in previous
        ]
        if event.lineup_name == name and values == unchanged:
            return event
        for slot in previous:
            await self.remove_slot(slot.id, slot.version)
        await self.store.flush()
        for item in values:
            role = (
                await self.store.get(StaffRole, UUID(item["role_id"]))
                if item.get("role_id")
                else await self.role(event.group_id, item["role_name"])
            )
            slot = await self.add_slot(event.id, role.id)
            if item.get("user_id"):
                slot.preferred_user_id = UUID(item["user_id"])
                await self.contact(event.group_id, role.id, slot.preferred_user_id)
        event.lineup_name = name
        event.lineup_version += 1
        record_activity(self.store, self.user, "lineup_prepared", event.id)
        await self.store.flush()
        return event

    async def apply_template(
        self,
        event_id: UUID,
        template_id: UUID,
        expected: int,
        *,
        replace: bool = False,
        event_version: int | None = None,
    ) -> list[GigSlot]:
        event = await self.event(event_id)
        template = await self.store.get(LineupTemplate, template_id)
        require(
            template.group_id == event.group_id and template.version == expected, "input_expired"
        )
        old = await self.store.find(GigSlot, group_id=event.group_id, concert_id=event.id)
        require(not any(not s.removed for s in old) or replace, "staff_lineup_exists")
        require(event_version is None or event.lineup_version == event_version, "input_expired")
        for slot in old:
            if not slot.removed:
                await self.remove_slot(slot.id, slot.version)
        event.lineup_name = template.name
        event.lineup_version += 1
        start = max((s.position for s in old), default=0)
        defaults = await self.lineup_people(
            event.group_id, [UUID(i["role_id"]) for i in template.slots]
        )
        rows = []
        for position, item in enumerate(template.slots, 1):
            row = GigSlot(
                group_id=event.group_id,
                concert_id=event.id,
                role_id=UUID(item["role_id"]),
                preferred_user_id=defaults[position - 1],
                position=start + position,
            )
            self.store.add(row)
            rows.append(row)
        event.metadata_version += 1
        record_activity(self.store, self.user, "lineup_applied", event.id)
        return rows

    async def add_slot(self, event_id: UUID, role_id: UUID) -> GigSlot:
        event = await self.event(event_id)
        role = await self.store.get(StaffRole, role_id)
        require(role.group_id == event.group_id, "forbidden")
        slots = await self.store.find(GigSlot, concert_id=event.id)
        require(sum(not s.removed for s in slots) < 40, "input")
        event.lineup_version += 1
        row = GigSlot(
            group_id=event.group_id,
            concert_id=event.id,
            role_id=role.id,
            position=max((s.position for s in slots), default=0) + 1,
        )
        self.store.add(row)
        return row

    async def needed(self, slot_id: UUID, expected: int, value: bool) -> GigSlot:
        slot = await self.store.get(GigSlot, slot_id)
        event = await self.event(slot.concert_id)
        slot = await self.store.get(GigSlot, slot_id, lock=True)
        require(not slot.removed and slot.version == expected, "input_expired")
        slot.required, slot.version = value, slot.version + 1
        if not value:
            for invite in await self.store.find(GigInvitation, slot_id=slot.id):
                if invite.status in OPEN | {"confirmed"}:
                    invite.status, invite.version = "cancelled", invite.version + 1
                    await self.notify(invite.user_id, "cancelled", invite)
                    await self.cancel_replacements(invite.id)
        event.metadata_version += 1
        event.lineup_version += 1
        return slot

    async def conflicts(
        self, user_id: UUID, when: date, *, exclude: UUID | None = None
    ) -> tuple[int, int]:
        # RU: Только счётчики; названия групп и причины занятости не выходят из хранилища.
        return (await self.store.staffing_conflicts({user_id}, when, exclude))[user_id]

    @staticmethod
    def valid(row: GigInvitation, event: Concert | None, group: Group | None) -> bool:
        return bool(
            event
            and group
            and not event.deleted_at
            and not group.deleted_at
            and row.concert_epoch == event.lifecycle_version
            and row.date == event.date
            and (row.status == "confirmed" or row.expires_at > now())
        )

    async def invite(
        self,
        slot_id: UUID,
        user_id: UUID,
        expected: int,
        *,
        force: bool = False,
        replacement_id: UUID | None = None,
    ) -> GigInvitation:
        await self.store.lock_people({user_id})
        slot = await self.store.get(GigSlot, slot_id)
        event = await self.event(slot.concert_id)
        slot = await self.store.get(GigSlot, slot_id, lock=True)
        require(
            not slot.removed
            and slot.required
            and slot.version == expected
            and event.date >= today(),
            "input_expired",
        )
        person = await self.store.get(User, user_id)
        contacts = await self.store.find(
            StaffContact, group_id=event.group_id, role_id=slot.role_id, user_id=user_id
        )
        require(not contacts or contacts[0].preference != "hidden", "staff_hidden")
        if replacement_id:
            request = await self.store.get(StaffReplacement, replacement_id)
            source = await self.store.get(GigInvitation, request.source_id)
            require(
                request.group_id == event.group_id
                and request.concert_id == event.id
                and request.status == "pending"
                and request.expires_at > now()
                and source.slot_id == slot.id
                and source.status == "confirmed"
                and source.user_id != user_id,
                "input_expired",
            )
        else:
            filled = await self.store.find(GigInvitation, slot_id=slot.id, status="confirmed")
            if filled and user_id == self.user.id and filled[0].user_id == user_id:
                return filled[0]
            require(not filled, "staff_filled")
        for old in await self.store.find(
            GigInvitation, slot_id=slot.id, user_id=user_id, replacement_id=replacement_id
        ):
            if (
                old.status in OPEN
                and old.expires_at > now()
                and old.date == event.date
                and old.concert_epoch == event.lifecycle_version
            ):
                if user_id == self.user.id and old.status in (
                    "pending",
                    "reconfirm",
                    "conditional",
                ):
                    return await self.respond(
                        old.id, old.version, "yes", force=force, notify_admins=False
                    )
                return old
        busy, _ = await self.conflicts(user_id, event.date, exclude=event.id)
        require(force or not busy, "staff_busy")
        expiry = min(
            now() + timedelta(days=7), datetime.combine(event.date + timedelta(days=1), time(), UTC)
        )
        row = GigInvitation(
            group_id=event.group_id,
            concert_id=event.id,
            slot_id=slot.id,
            user_id=person.id,
            date=event.date,
            concert_epoch=event.lifecycle_version,
            expires_at=expiry,
            replacement_id=replacement_id,
        )
        self.store.add(row)
        event.lineup_version += 1
        await self.contact(event.group_id, slot.role_id, person.id)
        record_activity(self.store, self.user, "staff_invited", row.id)
        if user_id == self.user.id:
            # RU: Назначая себя, администратор уже выразил согласие. Проверки занятости
            # и процедура подмены остаются теми же, повторное личное приглашение не нужно.
            # EN: Self-assignment is consent; reuse transitions without another DM.
            await self.store.flush()
            return await self.respond(row.id, row.version, "yes", force=force, notify_admins=False)
        await self.notify(person.id, "invitation", row)
        return row

    async def invite_defaults(
        self, event_id: UUID, versions: dict[str, int], *, force: bool = False
    ) -> int:
        slots = await self.store.find(GigSlot, concert_id=event_id)
        targets = [s for s in slots if str(s.id) in versions and s.preferred_user_id is not None]
        require(bool(targets) and len(targets) == len(versions), "input_expired")
        # RU: Всех людей блокируем до коллектива; пакет не создаёт инверсии блокировок.
        # EN: Take the complete sorted reservation lock set before entering any tenant.
        await self.store.lock_people({s.preferred_user_id for s in targets if s.preferred_user_id})
        await self.event(event_id)
        previous = {row.id for row in await self.store.find(GigInvitation, concert_id=event_id)}
        recipients = set()
        for slot in targets:
            assert slot.preferred_user_id is not None
            row = await self.invite(
                slot.id, slot.preferred_user_id, versions[str(slot.id)], force=force
            )
            if row.id not in previous and row.user_id != self.user.id:
                recipients.add(row.user_id)
        return len(recipients)

    async def respond(
        self,
        identifier: UUID,
        expected: int,
        action: str,
        *,
        force: bool = False,
        notify_admins: bool = True,
    ) -> GigInvitation:
        await self.store.lock_people({self.user.id})
        row = await self.store.get(GigInvitation, identifier)
        group = await self.store.get(Group, row.group_id, lock=True)
        row = await self.store.get(GigInvitation, identifier, lock=True)
        event = await self.store.get(Concert, row.concert_id)
        require(row.user_id == self.user.id, "forbidden")
        require(self.valid(row, event, group) and event.date >= today(), "input_expired")
        require(action in ("yes", "no", "conditional"), "input")
        replacing = False
        if row.replacement_id:
            request = await self.store.get(StaffReplacement, row.replacement_id)
            replacing = request.status == "pending"
            require(request.status in ("pending", "approved"), "input_expired")
            if replacing:
                source = await self.store.get(GigInvitation, request.source_id)
                require(
                    request.expires_at > now() and source.status == "confirmed", "input_expired"
                )
        target = (
            "declined"
            if action == "no"
            else "conditional"
            if action == "conditional"
            else "accepted"
            if replacing
            else "confirmed"
        )
        if row.status == target and row.version == expected + 1:
            return row
        require(
            row.version == expected
            and (
                row.status in ("pending", "reconfirm", "conditional")
                or row.status == "accepted"
                and action == "no"
            ),
            "input_expired",
        )
        if action == "yes":
            busy, _ = await self.conflicts(row.user_id, event.date, exclude=event.id)
            require(force or not busy, "staff_busy")
            if not replacing:
                require(
                    not await self.store.find(
                        GigInvitation, slot_id=row.slot_id, status="confirmed"
                    ),
                    "staff_filled",
                )
        require(not replacing or action != "conditional", "input")
        row.notified_at = row.notified_at or now()
        row.status, row.version = target, row.version + 1
        if target == "confirmed":
            for other in await self.store.find(GigInvitation, slot_id=row.slot_id):
                if other.id != row.id and other.status in OPEN and other.replacement_id is None:
                    other.status, other.version = "cancelled", other.version + 1
                    await self.notify(other.user_id, "cancelled", other)
        event.metadata_version += 1
        event.lineup_version += 1
        if notify_admins:
            await self.admin_notice(row, "reply")
        record_activity(self.store, self.user, "staff_response", row.id, status=target)
        return row

    async def request_replacement(
        self, source_id: UUID, target_id: UUID | None = None, *, by_admin: bool = False
    ) -> StaffReplacement:
        source = await self.store.get(GigInvitation, source_id)
        await self.store.lock_people({source.user_id})
        if by_admin:
            await self.event(source.concert_id)
        group = await self.store.get(Group, source.group_id, lock=True)
        source = await self.store.get(GigInvitation, source_id, lock=True)
        event = await self.store.get(Concert, source.concert_id)
        require(
            (by_admin or source.user_id == self.user.id) and source.status == "confirmed",
            "forbidden",
        )
        require(self.valid(source, event, group) and event.date >= today(), "input_expired")
        if target_id:
            require(not by_admin, "forbidden")
            target = await self.store.get(GigInvitation, target_id)
            target_event = await self.store.get(Concert, target.concert_id)
            target_group = await self.store.get(Group, target.group_id)
            require(
                target.user_id == self.user.id
                and target.status == "conditional"
                and self.valid(target, target_event, target_group)
                and target.concert_id != source.concert_id,
                "input_expired",
            )
        existing = await self.store.find(StaffReplacement, source_id=source.id, status="pending")
        if existing and existing[0].expires_at > now():
            return existing[0]
        for expired in existing:
            expired.status = "cancelled"
        await self.store.flush()
        row = StaffReplacement(
            group_id=source.group_id,
            concert_id=source.concert_id,
            source_id=source.id,
            target_id=target_id,
            expires_at=min(
                now() + timedelta(days=7),
                datetime.combine(event.date + timedelta(days=1), time(), UTC),
            ),
        )
        self.store.add(row)
        await self.admin_notice(source, "replacement", replacement=row)
        return row

    async def approve_replacement(self, invitation_id: UUID, expected: int) -> GigInvitation:
        candidate = await self.store.get(GigInvitation, invitation_id)
        require(candidate.replacement_id is not None, "input")
        assert candidate.replacement_id is not None
        request = await self.store.get(StaffReplacement, candidate.replacement_id)
        source = await self.store.get(GigInvitation, request.source_id)
        await self.store.lock_people({source.user_id, candidate.user_id})
        event = await self.event(candidate.concert_id)
        # Reload after acquiring person/tenant locks, not from the pre-lock identity map.
        candidate = await self.store.get(GigInvitation, invitation_id, lock=True)
        source = await self.store.get(GigInvitation, source.id, lock=True)
        request = await self.store.get(StaffReplacement, request.id, lock=True)
        require(
            request.status == "pending"
            and request.expires_at > now()
            and candidate.status == "accepted"
            and candidate.version == expected
            and source.status == "confirmed"
            and candidate.slot_id == source.slot_id
            and self.valid(candidate, event, await self.store.get(Group, event.group_id)),
            "input_expired",
        )
        busy, _ = await self.conflicts(candidate.user_id, event.date, exclude=event.id)
        require(not busy, "staff_busy")
        source.status, source.version = "replaced", source.version + 1
        await (
            self.store.flush()
        )  # RU/EN: Release the unique slot before confirming its replacement.
        candidate.status, candidate.version = "confirmed", candidate.version + 1
        request.status, request.version = "approved", request.version + 1
        event.metadata_version += 1
        event.lineup_version += 1
        for other in await self.store.find(GigInvitation, replacement_id=request.id):
            if other.id != candidate.id and other.status in OPEN:
                other.status, other.version = "cancelled", other.version + 1
                await self.notify(other.user_id, "cancelled", other)
        await self.notify(source.user_id, "released", source, target_id=request.target_id)
        await self.notify(candidate.user_id, "confirmed", candidate)
        await self.admin_notice(candidate, "swapped")
        record_activity(self.store, self.user, "staff_replaced", request.id)
        return candidate

    async def cancel_replacements(self, source_id: UUID) -> None:
        for row in await self.store.find(StaffReplacement, source_id=source_id, status="pending"):
            row.status, row.version = "cancelled", row.version + 1
            for invite in await self.store.find(GigInvitation, replacement_id=row.id):
                if invite.status in OPEN:
                    invite.status, invite.version = "cancelled", invite.version + 1
                    await self.notify(invite.user_id, "cancelled", invite)

    async def cancel_request(self, identifier: UUID, expected: int) -> StaffReplacement:
        row = await self.store.get(StaffReplacement, identifier)
        source = await self.store.get(GigInvitation, row.source_id)
        await self.store.lock_people({source.user_id})
        if source.user_id != self.user.id:
            await self.event(row.concert_id)
        else:
            await self.store.get(Group, row.group_id, lock=True)
        row = await self.store.get(StaffReplacement, identifier, lock=True)
        require(row.status == "pending" and row.version == expected, "input_expired")
        await self.cancel_replacements(source.id)
        await self.notify(source.user_id, "replacement_cancelled", source)
        return row

    async def candidates(self, slot_id: UUID) -> list[tuple[StaffContact, User, int, int]]:
        slot = await self.store.get(GigSlot, slot_id)
        event = await self.event(slot.concert_id)
        require(not slot.removed, "input_expired")
        return await self.role_candidates(event, slot.role_id, slot.preferred_user_id)

    async def role_candidates(
        self,
        event: Concert,
        role_id: UUID,
        preferred: UUID | None = None,
    ) -> list[tuple[StaffContact, User, int, int]]:
        await self.event(event.id)
        contacts = await self.store.find(StaffContact, group_id=event.group_id)
        roles = {r.id: r for r in await self.store.find(StaffRole, group_id=event.group_id)}
        vocabulary = await self.vocabulary()
        required = roles[role_id].name
        people = {p.id: p for p in await self.store.get_many(User, {c.user_id for c in contacts})}
        by_person: dict[UUID, list[StaffContact]] = {}
        for contact in contacts:
            by_person.setdefault(contact.user_id, []).append(contact)
        visible = []
        for person in people.values():
            local = by_person[person.id]
            matching = [c for c in local if vocabulary.matches(required, roles[c.role_id].name)]
            if any(c.preference == "hidden" for c in matching):
                continue
            eligible = [c for c in matching if c.preference != "hidden"]
            if eligible:
                contact = min(
                    eligible,
                    key=lambda c: (
                        not c.permanent,
                        c.priority,
                        c.preference != "preferred",
                        -c.worked_count,
                    ),
                )
            elif any(c.preference != "hidden" for c in local) and any(
                vocabulary.matches(required, skill) for skill in person.profile.get("roles", [])
            ):
                # RU: Чужие коллективы/контакты не ищем; навыки расширяют только местных знакомых.
                # EN: A declared skill broadens matching, never the contact visibility boundary.
                contact = StaffContact(group_id=event.group_id, role_id=role_id, user_id=person.id)
            else:
                continue
            if person.profile.get("substitutions") is False and not contact.permanent:
                continue
            visible.append(contact)
        conflicts = await self.store.staffing_conflicts(set(people), event.date, event.id)
        result = [
            (contact, people[contact.user_id], *conflicts[contact.user_id])
            for contact in visible
            if contact.user_id in people
        ]
        result.sort(
            key=lambda row: (
                not row[0].permanent,
                row[0].priority,
                row[2] > 0,
                row[1].id != preferred,
                row[0].preference != "preferred",
                -row[0].worked_count,
                next(
                    (
                        i
                        for i, skill in enumerate(row[1].profile.get("roles", []))
                        if vocabulary.matches(required, skill)
                    ),
                    100,
                ),
                row[1].display_name,
                row[1].id,
            )
        )
        return result

    async def mark_worked(self, identifier: UUID, expected: int) -> None:
        row = await self.store.get(GigInvitation, identifier)
        event = await self.event(row.concert_id)
        row = await self.store.get(GigInvitation, identifier, lock=True)
        require(
            event.date < today() and row.status == "confirmed" and row.version == expected,
            "input_expired",
        )
        if not row.worked:
            contact = (
                await self.store.find(
                    StaffContact,
                    group_id=row.group_id,
                    user_id=row.user_id,
                    role_id=(await self.store.get(GigSlot, row.slot_id)).role_id,
                )
            )[0]
            row.worked = True
            contact.worked_count += 1

    async def guest_setlist(self, identifier: UUID) -> tuple[dict[str, Any], int, bool]:
        from setlist_bot.application.setlists import Setlists
        from setlist_bot.domain.entities import Setlist

        row = await self.store.get(GigInvitation, identifier)
        require(row.user_id == self.user.id, "forbidden")
        group = await self.store.get(Group, row.group_id, lock=True)
        row = await self.store.get(GigInvitation, identifier, lock=True)
        event = await self.store.get(Concert, row.concert_id)
        require(row.status == "confirmed" and self.valid(row, event, group), "forbidden")
        lists = await self.store.find(Setlist, group_id=group.id, concert_id=event.id)
        require(bool(lists), "not_found")
        # RU: Только общий список своего подтверждённого выступления, без членства
        # и доступа к чужим черновикам. EN: Invitation access is strictly read-only.
        return await Setlists(self.store, self.user)._read_shared(lists[0])

    async def busy(self, starts: date, ends: date) -> BusyPeriod:
        await self.store.lock_people({self.user.id})
        require(today() <= starts <= ends and (ends - starts).days <= 366, "input")
        rows = await self.store.find(
            BusyPeriod, user_id=self.user.id, starts_on=starts, ends_on=ends
        )
        if rows:
            return rows[0]
        row = BusyPeriod(user_id=self.user.id, starts_on=starts, ends_on=ends)
        self.store.add(row)
        # Notify only affected bands, without exposing the private reason or other engagements.
        for invite in await self.store.find(
            GigInvitation, user_id=self.user.id, status="confirmed"
        ):
            event = await self.store.get(Concert, invite.concert_id)
            if starts <= event.date <= ends and self.valid(
                invite, event, await self.store.get(Group, invite.group_id)
            ):
                await self.admin_notice(invite, "busy")
        return row

    async def unbusy(self, identifier: UUID) -> None:
        await self.store.lock_people({self.user.id})
        row = await self.store.get(BusyPeriod, identifier)
        require(row.user_id == self.user.id, "forbidden")
        await self.store.delete(row)

    async def notify(
        self, user_id: UUID, kind: str, invite: GigInvitation, *, target_id: UUID | None = None
    ) -> None:
        if kind in ("cancelled", "released"):
            for pending in await self.store.find(
                Outbox,
                group_id=invite.group_id,
                concert_id=invite.concert_id,
                support_kind="staffing",
                sent=False,
            ):
                if pending.payload.get("invite_id") == str(invite.id) and not pending.payload.get(
                    "admin"
                ):
                    pending.sent, pending.discarded_at = True, now()
            if invite.notified_at is None:
                return
        person = await self.store.get(User, user_id)
        payload = {
            "kind": kind,
            "invite_id": str(invite.id),
            "user_id": str(user_id),
            "version": invite.version,
            "target_id": str(target_id) if target_id else None,
        }
        if await self.store.find(
            Outbox,
            group_id=invite.group_id,
            concert_id=invite.concert_id,
            support_kind="staffing",
            chat_id=person.telegram_id,
            sent=False,
            payload=payload,
        ):
            return
        self.store.add(
            Outbox(
                chat_id=person.telegram_id,
                text="",
                support_kind="staffing",
                group_id=invite.group_id,
                concert_id=invite.concert_id,
                payload=payload,
                next_attempt_at=now(),
            )
        )

    async def admin_notice(
        self, invite: GigInvitation, kind: str, *, replacement: StaffReplacement | None = None
    ) -> None:
        for member in await self.store.find(
            Membership, group_id=invite.group_id, role="admin", status="active"
        ):
            person = await self.store.get(User, member.user_id)
            self.store.add(
                Outbox(
                    chat_id=person.telegram_id,
                    text="",
                    support_kind="staffing",
                    group_id=invite.group_id,
                    concert_id=invite.concert_id,
                    payload={
                        "kind": kind,
                        "invite_id": str(invite.id),
                        "user_id": str(person.id),
                        "admin": True,
                        "replacement_id": str(replacement.id) if replacement else None,
                    },
                )
            )


async def invalidate_staffing(store: Store, event: Concert, *, deleted: bool = False) -> None:
    """RU: Под блокировкой коллектива, только снятие броней. EN: No person-lock inversion."""
    event.lineup_version += 1
    # RU: Старый выбор кандидата нельзя применять к уже другой дате.
    # EN: Invalidate pre-change invite/defaults callbacks as well as recipient replies.
    for slot in await store.find(GigSlot, concert_id=event.id, removed=False):
        slot.version += 1
    for row in await store.find(GigInvitation, group_id=event.group_id, concert_id=event.id):
        if row.status not in OPEN | {"confirmed"}:
            continue
        was_confirmed = row.status == "confirmed"
        row.status, row.version = ("cancelled" if deleted else "reconfirm"), row.version + 1
        row.date, row.concert_epoch = event.date, event.lifecycle_version
        row.expires_at = now() + timedelta(days=7)
        actor = await store.get(User, row.user_id)
        service = Staffing(store, actor)
        await service.cancel_replacements(row.id)
        await service.notify(row.user_id, "cancelled" if deleted else "date_changed", row)
        if was_confirmed:
            event.metadata_version += 1
