"""Bounded, audited system-operator actions, separate from group-member permissions."""

from uuid import UUID

from setlist_bot.application.activity import web_activity
from setlist_bot.application.groups import Groups
from setlist_bot.application.message_refresh import queue_card_refreshes
from setlist_bot.application.ports import Store
from setlist_bot.application.support import Support
from setlist_bot.application.trash import Trash
from setlist_bot.domain.entities import (
    AdminAudit,
    Concert,
    Group,
    Membership,
    Outbox,
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
        with web_activity():
            await self._execute(action, identifier, values, request_key)

    async def _execute(
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
            user = await self.store.get(User, routes[0].user_id)
            support = Support(self.store, user)
            case = await support.case(identifier, int(values["expected_version"]))
            if action == "support_reply":
                event = await support.reply_to_request(
                    identifier,
                    values.get("message", ""),
                    expected_version=case.version,
                    source="web",
                    actor=self.actor,
                )
                audit.details = {"reply_outbox_id": str(event.id)}
                case.status = "closed" if values.get("close") == "yes" else "open"
            else:
                case.status = "closed" if action == "support_close" else "open"
                case.version += 1
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
            await queue_card_refreshes(self.store, group.id, concert.id if concert else None)
        elif action in ("group_reminders", "group_owner", "group_administrator"):
            group = await self.store.get(Group, identifier, lock=True)
            require(group.deleted_at is None, "in_trash")
            require(group.owner_id is not None, "not_found")
            assert group.owner_id is not None
            owner = await self.store.get(User, group.owner_id)
            service_groups = Groups(self.store, owner)
            if action == "group_reminders":
                require(values.get("enabled") in ("yes", "no"), "input")
                await service_groups.set_reminders(group.id, values["enabled"] == "yes")
                audit.details = {"enabled": group.reminders_enabled}
            elif action == "group_administrator":
                from setlist_bot.application.user_lookup import checked_target

                require(values.get("confirmed") == "yes", "confirmation")
                require(values.get("operation") in ("grant", "revoke"), "input")
                member = await self.store.get(Membership, UUID(values["member_id"]), lock=True)
                require(member.group_id == group.id and member.status == "active", "forbidden")
                require(
                    values.get("expected_owner") == str(group.owner_id)
                    and values.get("expected_role") == member.role,
                    "conflict",
                )
                if values.get("reference"):
                    await checked_target(self.store, member.user_id, values["reference"])
                await service_groups.administrator(
                    group.id, member.id, values["operation"] == "grant"
                )
                audit.details = {"user_id": str(member.user_id), "operation": values["operation"]}
            else:
                if values.get("reference"):
                    from setlist_bot.application.user_lookup import checked_target

                    member = await self.store.get(Membership, UUID(values["member_id"]))
                    require(member.group_id == group.id and member.status == "active", "forbidden")
                    await checked_target(self.store, member.user_id, values["reference"])
                previous = group.owner_id
                await service_groups.transfer_ownership(
                    group.id,
                    UUID(values["member_id"]),
                    values.get("expected_owner", ""),
                    values.get("confirmation", ""),
                )
                audit.details = {"old_owner": str(previous), "new_owner": str(group.owner_id)}
        else:
            require(False, "input")
