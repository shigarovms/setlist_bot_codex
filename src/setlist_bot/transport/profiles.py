"""Private, versioned ForceReply questionnaire. Nothing is saved until confirmation."""

import json
from datetime import timedelta
from typing import Any
from uuid import UUID

from setlist_bot.application.profiles import Profiles
from setlist_bot.application.role_dictionary import RoleDictionary
from setlist_bot.domain.entities import Confirmation, now
from setlist_bot.domain.rules import AppError, require
from setlist_bot.transport.controller import Controller, Response

STEPS = ("name", "roles", "city", "availability", "about", "confirm")


def data(row: Confirmation) -> dict[str, Any]:
    return dict(json.loads(row.command))


def action(row: Confirmation, values: dict[str, Any], operation: str) -> str:
    return f"pf_go {row.id.hex} {values['version']} {operation}"


def summary(c: Controller, values: dict[str, Any]) -> str:
    lines = []
    for key in ("name", "roles", "city", "substitutions", "travel", "about"):
        if key == "roles":
            value = ", ".join(values.get(key, [])) or "—"
        elif key in ("substitutions", "travel") and key in values:
            value = c.t("profile_yes" if values[key] else "profile_no")
        else:
            value = str(values.get(key) or "—")
        lines.append(c.t("profile_label_" + key) + ": " + value)
    return "\n".join(lines)


async def show(c: Controller, row: Confirmation, values: dict[str, Any]) -> Response:
    step = STEPS[values["step"]]

    def command(op: str) -> str:
        return action(row, values, op)

    buttons = []
    if step == "confirm":
        text = c.t("profile_confirm") + "\n\n" + summary(c, values["values"])
        buttons.append((c.t("profile_save"), command("save")))
    elif step == "availability":
        text = c.t("profile_availability")
        for key in ("substitutions", "travel"):
            values["values"].setdefault(key, False)
        row.command = json.dumps(values)
        for key in ("substitutions", "travel"):
            label = (
                c.t("profile_label_" + key)
                + ": "
                + c.t("profile_yes" if values["values"].get(key, False) else "profile_no")
            )
            buttons.append((label, command(key)))
        buttons.append((c.t("profile_next"), command("skip")))
    else:
        text = c.t("profile_question_" + step)
        if step == "name":
            text += "\n\n" + c.t("profile_privacy")
        current = values["values"].get(step)
        if current:
            text += "\n\n" + c.t(
                "current_value", value=", ".join(current) if isinstance(current, list) else current
            )
        text += "\n\n" + c.t("profile_skip_hint")
    if values["step"] and step in ("confirm", "availability"):
        buttons.append((c.t("menu_back"), command("back")))
    if step in ("confirm", "availability"):
        buttons.append((c.t("cancel_button"), command("cancel")))
    return Response(
        text,
        buttons,
        force_reply=step not in ("confirm", "availability"),
        workflow_id=row.id,
    )


async def begin(c: Controller) -> Response:
    require(c.private, "private_only")
    for old in await c.store.find(Confirmation, user_id=c.user.id, chat_id=c.chat_id):
        if data(old).get("kind") == "profile_input":
            await c.store.delete(old)
    row = Confirmation(
        user_id=c.user.id,
        chat_id=c.chat_id,
        expires_at=now() + timedelta(minutes=30),
        command=json.dumps(
            {
                "kind": "profile_input",
                "step": 0,
                "version": 0,
                "expected": c.user.profile_version,
                "values": c.user.profile,
            }
        ),
    )
    c.store.add(row)
    return await show(c, row, data(row))


async def load(c: Controller, row: Confirmation) -> dict[str, Any]:
    require(
        c.private
        and row.user_id == c.user.id
        and row.chat_id == c.chat_id
        and row.expires_at > now()
        and data(row).get("kind") == "profile_input",
        "input_expired",
    )
    row = await c.store.get(Confirmation, row.id, lock=True)
    return data(row)


async def advance(c: Controller, row: Confirmation, values: dict[str, Any]) -> Response:
    values.pop("message_ids", None)
    values["version"] += 1
    row.command = json.dumps(values)
    return await show(c, row, values)


async def reply(c: Controller, row: Confirmation, value: str, prompt_id: int) -> Response:
    values = await load(c, row)
    require(prompt_id in values.get("message_ids", []), "input_expired")
    step = STEPS[values["step"]]
    require(step in ("name", "roles", "city", "about"), "input_expired")
    limit = 1000 if step == "about" else 5000 if step == "roles" else 200
    require(0 < len(value.strip()) <= limit, "input")
    try:
        if value.strip() != "=":
            values["values"][step] = (
                (
                    []
                    if value.strip() == "-"
                    else (await RoleDictionary(c.store).index()).parse(value)
                )
                if step == "roles"
                else ("" if value.strip() == "-" else value.strip())
            )
    except AppError as exc:
        response = await show(c, row, values)
        response.text = c.t(exc.code) + "\n\n" + response.text
        return response
    values["step"] += 1
    return await advance(c, row, values)


async def handle(c: Controller, args: str) -> Response:
    identifier, version, operation = args.split()
    row = await c.store.get(Confirmation, UUID(identifier))
    values = await load(c, row)
    require(values["version"] == int(version), "input_expired")
    if operation == "cancel":
        await c.store.delete(row)
        return Response(c.t("profile_cancelled"))
    if operation == "save":
        require(STEPS[values["step"]] == "confirm", "input_expired")
        await Profiles(c.store, c.user).save(values["expected"], values["values"])
        await c.store.delete(row)
        return Response(c.t("profile_saved") + "\n\n" + summary(c, c.user.profile))
    if operation in ("substitutions", "travel"):
        require(STEPS[values["step"]] == "availability", "input_expired")
        values["values"][operation] = not values["values"].get(operation, False)
    elif operation == "skip":
        require(values["step"] < len(STEPS) - 1, "input_expired")
        values["step"] += 1
    elif operation == "back":
        require(values["step"] > 0, "input_expired")
        values["step"] -= 1
    else:
        require(False, "input")
    return await advance(c, row, values)
