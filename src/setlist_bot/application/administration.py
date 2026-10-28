"""Bounded, audited system-operator actions, separate from group-member permissions."""

from uuid import UUID

from setlist_bot.application.ports import Store
from setlist_bot.application.support import Support
from setlist_bot.application.trash import Trash
from setlist_bot.domain.entities import (
    AdminAudit,
    Concert,
    Group,
    Membership,
    Outbox,
    SupportCase,
    SupportMessage,
    User,
    now,
)
from setlist_bot.domain.rules import require


class Administration:
    """Call only after server-side web authentication and signed-form verification."""

    def __init__(self, store: Store, actor: str) -> None:
        self.store, self.actor = store, actor

    async def execute(
        self, action: str, identifier: UUID, values: dict[str, str], request_key: str
    ) -> None:
        # RU: Ключ формы коммитится вместе с действием; двойной клик не повторяет отправку.
        # EN: Audit nonce and mutation share a transaction; duplicate POSTs roll back atomically.
        audit = AdminAudit(
            actor=self.actor, action=action, target=str(identifier), request_key=request_key
        )
        self.store.add(audit)
        await self.store.flush()
        if action in ("support_reply", "support_close", "support_reopen"):
            request = await self.store.get(Outbox, identifier, lock=True)
            routes = await self.store.find(SupportMessage, outbox_id=identifier)
            require(request.support_kind == "request" and bool(routes), "not_found")
            cases = await self.store.find(SupportCase, request_id=identifier)
            case = cases[0] if cases else SupportCase(request_id=identifier)
            if not cases:
                self.store.add(case)
            if action == "support_reply":
                user = await self.store.get(User, routes[0].user_id)
                event = await Support(self.store, user).reply_to_request(
                    identifier, values.get("message", "")
                )
                audit.details = {"reply_outbox_id": str(event.id)}
                case.status = "closed" if values.get("close") == "yes" else "open"
            else:
                case.status = "closed" if action == "support_close" else "open"
            case.updated_at = now()
        elif action in ("queue_retry", "queue_discard"):
            event = await self.store.get(Outbox, identifier, lock=True)
            require(not event.sent, "already_done")
            require(values.get("confirmed") == "yes", "confirmation")
            if action == "queue_retry":
                event.next_attempt_at = now()
            else:
                event.sent, event.discarded_at = True, now()
            audit.details = {"attempts": event.attempts}
        elif action in ("restore_group", "restore_concert"):
            require(values.get("confirmed") == "yes", "confirmation")
            concert = (
                await self.store.get(Concert, identifier) if action == "restore_concert" else None
            )
            group = await self.store.get(Group, concert.group_id if concert else identifier)
            require(group.owner_id is not None, "not_found")
            assert group.owner_id is not None
            owner = await self.store.get(User, group.owner_id)
            # Reuse lifecycle/retention checks; the audit actor remains the web operator.
            service = Trash(self.store, owner)
            epoch = int(values["epoch"])
            if concert:
                await service.restore_concert(group.id, concert.id, epoch)
            else:
                await service.restore_group(group.id, epoch)
        elif action in ("group_reminders", "group_owner"):
            group = await self.store.get(Group, identifier, lock=True)
            require(group.deleted_at is None, "in_trash")
            if action == "group_reminders":
                require(values.get("enabled") in ("yes", "no"), "input")
                group.reminders_enabled = values["enabled"] == "yes"
                audit.details = {"enabled": group.reminders_enabled}
            else:
                require(values.get("confirmation", "").strip() == group.name, "confirmation")
                require(values.get("expected_owner") == str(group.owner_id), "conflict")
                member = await self.store.get(Membership, UUID(values["member_id"]), lock=True)
                require(member.group_id == group.id and member.status == "active", "forbidden")
                require(member.user_id != group.owner_id, "already_done")
                audit.details = {"old_owner": str(group.owner_id), "new_owner": str(member.user_id)}
                member.role = "admin"
                group.owner_id = member.user_id
        else:
            require(False, "input")
