"""Telegram presentation for owner-controlled collective workflows."""

from uuid import UUID

from setlist_bot.application.collectives import Collectives
from setlist_bot.domain.entities import ChatBinding, Group, Outbox, User
from setlist_bot.domain.rules import require
from setlist_bot.transport.controller import Controller, Response
from setlist_bot.transport.i18n import tr


async def restricted(controller: Controller, group_id: UUID) -> Response:
    await controller.groups.access(group_id)
    service = Collectives(controller.store, controller.user, controller.chat_id)
    row = service.session("restricted_notice", {"group_id": str(group_id)}, minutes=10)
    return Response(
        controller.t("admin_suggestions_only"),
        [
            (controller.t("request_admin_access"), f"cr_request {row.id.hex}"),
            (controller.t("cancel_button"), f"cr_cancel {row.id.hex}"),
        ],
        workflow_id=row.id,
    )


async def handle_collective(c: Controller, command: str, args: str) -> Response:
    service = Collectives(c.store, c.user, c.chat_id)
    t = c.t
    if command == "on_added":
        rows = await c.store.find(ChatBinding, chat_id=c.chat_id)
        if rows:
            group = await c.store.get(Group, rows[0].group_id)
            return Response(
                tr(group.language, "added_existing", name=group.name),
                [(tr(group.language, "open_concerts"), "concerts")],
            )
        return Response(t("added_welcome"))
    if command == "group_manage":
        require(c.private, "private_only")
        group = await c.groups.access(UUID(args))
        buttons = (
            [(t("transfer_button"), f"ct_begin {group.id.hex}")]
            if group.owner_id == c.user.id
            else []
        )
        buttons.append((t("open_concerts"), f"group_concerts {group.id.hex}"))
        return Response(group.name, buttons)
    if command == "group_concerts":
        require(c.private, "private_only")
        group = await c.groups.access(UUID(args))
        context = await c.groups.context(c.chat_id, True)
        context.group_id, context.concert_id = group.id, None
        return await c.execute("concerts")
    if command in ("cr_request", "cr_cancel"):
        row, data = await service.load(UUID(args), "restricted_notice")
        require(row.user_id == c.user.id and row.chat_id == c.chat_id, "input_expired")
        response = Response("", delete_message_ids=data.get("message_ids", []))
        await c.store.delete(row)
        if command == "cr_cancel":
            return response
        group = await c.groups.access(UUID(data["group_id"]))
        request, fresh = await service.role_request(group.id)
        if fresh:
            assert group.owner_id is not None
            owner = await c.store.get(User, group.owner_id)
            c.store.add(
                Outbox(
                    chat_id=owner.telegram_id,
                    support_kind="collective",
                    payload={"request_id": str(request.id)},
                    text=tr(
                        owner.language,
                        "admin_request",
                        name=await c.groups.name(group.id),
                        group=group.name,
                    ),
                    buttons=[
                        [tr(owner.language, "admin_grant"), f"cr_decide {request.id.hex} yes"],
                        [tr(owner.language, "admin_refuse"), f"cr_decide {request.id.hex} no"],
                    ],
                )
            )
        response.callback_notice = t("admin_requested")
        return response
    if command == "cr_decide":
        identifier, decision = args.split()
        require(decision in ("yes", "no"), "input")
        user, group = await service.decide_role(UUID(identifier), decision == "yes")
        c.store.add(
            Outbox(
                chat_id=user.telegram_id,
                support_kind="role_done",
                text=tr(user.language, "admin_result_" + decision, group=group.name),
            )
        )
        return Response(
            t("ok"), delete_message_ids=[c.source_message_id] if c.source_message_id else []
        )
    if command == "ct_begin":
        require(c.private, "private_only")
        group = await c.groups.access(UUID(args), admin=True)
        require(group.owner_id == c.user.id, "owner_only")
        return Response(
            t("transfer_intro", name=group.name),
            [
                (t("confirm_button"), f"ct_code {group.id.hex}"),
                (t("cancel_button"), "groups"),
            ],
        )
    if command == "ct_code":
        row = await service.begin_transfer(UUID(args))
        return Response(t("transfer_code", token=row.id.hex), workflow_id=row.id)
    if command == "chat_transfer":
        row, group = await service.claim_transfer(UUID(args))
        return Response(
            t(
                "transfer_review",
                name=group.name,
                owner=await c.groups.name(group.id, group.owner_id),
            ),
            [
                (t("transfer_yes"), f"ct_yes {row.id.hex}"),
                (t("cancel_button"), f"ct_cancel {row.id.hex}"),
            ],
            workflow_id=row.id,
        )
    if command == "ct_cancel":
        row, data = await service.load(UUID(args), "chat_transfer")
        require(
            row.user_id == c.user.id
            or (data.get("new_chat_id") == c.chat_id and c.chat_owner_id == c.user.telegram_id),
            "owner_only",
        )
        service.finish(row, data, "cancelled")
        return Response(t("transfer_cancelled"))
    require(command == "ct_yes", "input")
    group = await service.finish_transfer(UUID(args), c.chat_owner_id)
    return Response(t("transfer_done", name=group.name))
