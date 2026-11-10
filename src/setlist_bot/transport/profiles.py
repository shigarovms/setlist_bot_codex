"""Private profile card and versioned inline questionnaire."""

import json
from datetime import timedelta
from difflib import get_close_matches
from typing import Any
from uuid import UUID

from setlist_bot.application.profiles import Profiles
from setlist_bot.application.role_dictionary import RoleDictionary
from setlist_bot.domain.entities import Confirmation, now
from setlist_bot.domain.rules import AppError, normalize, require
from setlist_bot.transport.controller import Controller, Response

STEPS = ("name", "roles", "city", "availability", "about", "confirm")
TEXT_STEPS = {"name", "roles", "city", "about"}


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
    step = "card" if values["step"] == -1 else STEPS[values["step"]]
    pending = values.get("role_choice") if step == "roles" else None
    values["waiting"] = step in TEXT_STEPS and (not pending or pending.get("rename", False))
    values["active"] = True
    row.command = json.dumps(values)

    def button(key: str, op: str) -> tuple[str, str]:
        return c.t(key), action(row, values, op)

    if step == "card":
        return Response(
            c.t("profile_button") + "\n\n" + summary(c, c.user.profile),
            [
                button("profile_edit_" + field, f"edit{index}")
                for index, field in enumerate(STEPS[:-1])
            ]
            + [(c.t("menu_back"), "groups")],
            workflow_id=row.id,
        )

    buttons: list[tuple[str, str]] = []
    if pending and not pending.get("rename"):
        text = c.t(
            "profile_role_sound" if pending["sound"] else "profile_role_unknown",
            name=pending["name"],
        )
        buttons.extend(
            (label, action(row, values, f"pick{index}"))
            for index, label in enumerate(pending["labels"])
        )
        buttons.append(button("profile_role_rename", "rename"))
    elif pending:
        text = c.t("profile_role_rename_prompt", name=pending["name"])
    elif step == "confirm":
        text = c.t("profile_confirm") + "\n\n" + summary(c, values["values"])
        buttons.append(button("profile_save", "save"))
    elif step == "availability":
        text = c.t("profile_availability")
        for key in ("substitutions", "travel"):
            choice = values["values"].get(key)
            label = c.t("profile_yes" if choice else "profile_no") if choice is not None else "—"
            buttons.append((c.t("profile_label_" + key) + " · " + label, action(row, values, key)))
        buttons += [
            button("profile_reset", "clear"),
            button("profile_save" if values.get("editing") else "profile_next", "skip"),
        ]
    else:
        text = c.t("profile_question_" + step)
        if step == "name":
            text += "\n\n" + c.t("profile_privacy")
        current = values["values"].get(step)
        if current:
            text += "\n\n" + c.t(
                "current_value", value=", ".join(current) if isinstance(current, list) else current
            )
        buttons.append(button("profile_clear_" + step, "clear"))
    if not pending and not values.get("editing") and step != "confirm":
        text = (
            c.t("profile_progress", field=c.t("profile_label_" + step), number=values["step"] + 1)
            + "\n\n"
            + text
        )
    if not values.get("editing"):
        buttons.append(button("profile_later", "later"))
    buttons.append(button("menu_back", "back"))
    text += "\n\n" + c.t("profile_cancel_hint")
    return Response(text, buttons, workflow_id=row.id)


async def begin(c: Controller) -> Response:
    require(c.private, "private_only")
    resumed = None
    for old in await c.store.find(Confirmation, user_id=c.user.id, chat_id=c.chat_id):
        values = data(old)
        if values.get("kind") != "profile_input":
            continue
        if (
            c.user.profile_version == 0
            and old.expires_at > now()
            and values.get("expected") == 0
            and "active" in values
            and resumed is None
        ):
            resumed = old
        else:
            await c.store.delete(old)
    if resumed is not None:
        return await advance(c, resumed, data(resumed))
    row = Confirmation(
        user_id=c.user.id,
        chat_id=c.chat_id,
        expires_at=now() + timedelta(minutes=30),
        command=json.dumps(
            {
                "kind": "profile_input",
                "step": -1 if c.user.profile_version else 0,
                "version": 0,
                "expected": c.user.profile_version,
                "values": c.user.profile,
            }
        ),
    )
    c.store.add(row)
    return await show(c, row, data(row))


async def load(c: Controller, row: Confirmation) -> dict[str, Any]:
    require(c.private and row.user_id == c.user.id and row.chat_id == c.chat_id, "input_expired")
    row = await c.store.get(Confirmation, row.id, lock=True)
    values = data(row)
    require(
        row.expires_at > now()
        and values.get("kind") == "profile_input"
        and values.get("active", False)
        and values["expected"] == c.user.profile_version,
        "input_expired",
    )
    return values


async def advance(c: Controller, row: Confirmation, values: dict[str, Any]) -> Response:
    previous = values.pop("message_ids", [])
    values["version"] += 1
    row.expires_at = now() + timedelta(minutes=30)
    response = await show(c, row, values)
    response.delete_message_ids.extend(previous)
    return response


async def save(c: Controller, row: Confirmation, values: dict[str, Any]) -> Response:
    await Profiles(c.store, c.user).save(values["expected"], values["values"])
    if values.get("editing"):
        values.update(step=-1, editing=False, expected=c.user.profile_version)
        return await advance(c, row, values)
    await c.store.delete(row)
    return Response(
        c.t("profile_saved") + "\n\n" + summary(c, c.user.profile),
        [(c.t("profile_edit_button"), "profile"), (c.t("my_groups"), "groups")],
    )


async def next_step(c: Controller, row: Confirmation, values: dict[str, Any]) -> Response:
    if values.get("editing"):
        return await save(c, row, values)
    values["step"] += 1
    return await advance(c, row, values)


async def resolve_roles(
    c: Controller, row: Confirmation, values: dict[str, Any], names: list[str], start: int
) -> Response:
    index = await RoleDictionary(c.store).index()
    for position in range(start, len(names)):
        name = names[position]
        key = index.key(name)
        if key != "sound" and key in index.titles:
            names[position] = index.titles[key]
            continue
        if key == "sound":
            choices = [index.titles["foh"], index.titles["mon"], c.t("profile_sound_general")]
            labels = ["🎚 " + choices[0], "🎧 " + choices[1], c.t("profile_keep_sound")]
        else:
            close = get_close_matches(normalize(name), index.aliases, n=10, cutoff=0.55)
            nearby = list(dict.fromkeys(index.aliases[alias] for alias in close))
            choices = [index.titles[match] for match in nearby if match != "sound"][:3]
            labels = [*choices, c.t("profile_add_role", name=name)]
            choices.append(name)
        values["role_choice"] = {
            "name": name,
            "names": names,
            "position": position,
            "sound": key == "sound",
            "choices": choices,
            "labels": labels,
        }
        return await advance(c, row, values)
    values.pop("role_choice", None)
    values["values"]["roles"] = list(dict.fromkeys(names))
    return await next_step(c, row, values)


async def reply(c: Controller, row: Confirmation, value: str, prompt_id: int | None) -> Response:
    values = await load(c, row)
    require(
        values["waiting"] and (prompt_id is None or prompt_id in values.get("message_ids", [])),
        "input_expired",
    )
    step = STEPS[values["step"]]
    pending = values.get("role_choice") if step == "roles" else None
    limit = 1000 if step == "about" else 5000 if step == "roles" else 200
    try:
        require(0 < len(value.strip()) <= limit, "input")
        if step == "roles" and value.strip() not in ("-", "="):
            index = await RoleDictionary(c.store).index()
            names = index.local_names(value)
            if pending:
                require(len(names) == 1, "input")
                pending["names"][pending["position"]] = names[0]
                return await resolve_roles(c, row, values, pending["names"], pending["position"])
            return await resolve_roles(c, row, values, names, 0)
        require(not pending, "input")
        if value.strip() != "=":
            values["values"][step] = (
                ([] if step == "roles" else "") if value.strip() == "-" else value.strip()
            )
        return await next_step(c, row, values)
    except AppError as exc:
        response = await show(c, row, data(row))
        response.text = c.t(exc.code) + "\n\n" + response.text
        return response


async def handle(c: Controller, args: str) -> Response:
    identifier, version, operation = args.split()
    row = await c.store.get(Confirmation, UUID(identifier))
    values = await load(c, row)
    require(values["version"] == int(version), "input_expired")
    step = "card" if values["step"] == -1 else STEPS[values["step"]]
    pending = values.get("role_choice") if step == "roles" else None
    if operation == "cancel":
        await c.store.delete(row)
        return Response(c.t("profile_cancelled"))
    if operation == "later" or operation == "back" and values["step"] == 0:
        previous = values.pop("message_ids", [])
        values.update(active=False, waiting=False, version=values["version"] + 1)
        row.command = json.dumps(values)
        return Response(
            c.t("profile_paused"),
            [(c.t("profile_button"), "profile"), (c.t("my_groups"), "groups")],
            delete_message_ids=previous,
        )
    if step == "card" and operation in ("edit0", "edit1", "edit2", "edit3", "edit4"):
        values.update(step=int(operation[-1]), editing=True, values=dict(c.user.profile))
    elif pending and operation.startswith("pick") and not pending.get("rename"):
        index = int(operation[4:])
        require(0 <= index < len(pending["choices"]), "input_expired")
        pending["names"][pending["position"]] = pending["choices"][index]
        return await resolve_roles(c, row, values, pending["names"], pending["position"] + 1)
    elif pending and operation == "rename" and not pending.get("rename"):
        pending["rename"] = True
    elif operation == "save" and step == "confirm":
        return await save(c, row, values)
    elif operation in ("substitutions", "travel") and step == "availability":
        values["values"][operation] = not values["values"].get(operation, False)
    elif operation == "skip" and step == "availability":
        return await next_step(c, row, values)
    elif operation == "clear" and step == "availability":
        for key in ("substitutions", "travel"):
            values["values"].pop(key, None)
    elif operation == "clear" and step in TEXT_STEPS and not pending:
        values["values"][step] = [] if step == "roles" else ""
        return await next_step(c, row, values)
    elif operation == "back":
        if pending and pending.pop("rename", False):
            pass
        elif pending and not values.get("editing"):
            values["step"] -= 1
        elif values.get("editing"):
            values.pop("role_choice", None)
            values.update(step=-1, editing=False, values=dict(c.user.profile))
        else:
            require(values["step"] > 0, "input_expired")
            values["step"] -= 1
    else:
        require(False, "input_expired")
    return await advance(c, row, values)
