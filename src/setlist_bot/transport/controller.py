import json
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any
from uuid import UUID

from setlist_bot.application.backup import Backup
from setlist_bot.application.catalog import SONG_FIELDS, Catalog
from setlist_bot.application.chat_cleanup import collect as cleanup_messages
from setlist_bot.application.command_inputs import (
    GROUP_INPUTS,
    SETLIST_INPUTS,
    VERSION_INPUTS,
    CommandInputs,
)
from setlist_bot.application.concert_details import FIELDS as CONCERT_INPUTS
from setlist_bot.application.concert_details import ConcertDetails
from setlist_bot.application.exports import Exports
from setlist_bot.application.groups import Groups
from setlist_bot.application.importing import Importer
from setlist_bot.application.ports import Store
from setlist_bot.application.recommendations import suggested_titles
from setlist_bot.application.setlist_menus import CARD, REQUEST, SetlistMenus
from setlist_bot.application.setlists import Setlists
from setlist_bot.application.song_selection import PAGE_SIZE, SongSelection
from setlist_bot.application.support import Support
from setlist_bot.application.templates import Templates
from setlist_bot.application.trash import RETENTION, Trash
from setlist_bot.domain.concerts import compact_duration, date_label, parse_concert, today
from setlist_bot.domain.display import entry_labels, short_duration
from setlist_bot.domain.entities import (
    CatalogImport,
    CatalogProposal,
    ChatBinding,
    Concert,
    Confirmation,
    Context,
    Group,
    Membership,
    Outbox,
    Revision,
    Setlist,
    Song,
    Submission,
    SupportSettings,
    User,
    now,
)
from setlist_bot.domain.export_settings import FONTS, settings
from setlist_bot.domain.importing import FIELDS, SPACED_SEPARATOR, TIME_CELL, read_table
from setlist_bot.domain.rules import (
    AppError,
    SongListError,
    clock,
    duration,
    is_pause,
    normalize,
    parse_pause,
    require,
    totals,
    validate_song,
)
from setlist_bot.infrastructure.pdf import PdfDocuments
from setlist_bot.transport.i18n import tr
from setlist_bot.transport.render import concert_label, render_setlist, song_card

COMMAND_SEPARATOR = re.compile(r"\t+|" + SPACED_SEPARATOR.pattern)


def command_fields(value: str, *, maxsplit: int = 0) -> list[str]:
    return [part.strip() for part in COMMAND_SEPARATOR.split(value.strip(), maxsplit=maxsplit)]


@dataclass
class Response:
    text: str
    buttons: list[tuple[str, str]] = field(default_factory=list)
    files: list[tuple[str, bytes]] = field(default_factory=list)
    monospace: bool = False
    monospace_body: str | None = None
    suggestion_template_id: UUID | None = None
    cancel_action: str = ""
    cancel_label: str = "❌ Отмена"
    force_reply: bool = False
    selection_id: UUID | None = None
    reply_keyboard: list[list[str]] | None = None
    keyboard_text: str = ""
    remove_keyboard: bool = False
    keyboard_message_id: int | None = None
    previous_keyboard_id: int | None = None
    edit_message_ids: list[int] = field(default_factory=list)
    delete_input: bool = False
    delete_message_ids: list[int] = field(default_factory=list)
    auxiliary_message_ids: list[int] = field(default_factory=list)
    button_columns: int = 1
    group_creation_id: UUID | None = None
    navigation_owner_id: UUID | None = None
    support_prompt_id: UUID | None = None
    command_input_id: UUID | None = None
    input_rejected: bool = False
    menu_card_id: UUID | None = None
    markup_only: bool = False
    callback_notice: str = ""
    deliveries: list[tuple[int, "Response"]] = field(default_factory=list)


class Controller:
    def __init__(
        self,
        store: Store,
        user: User,
        chat_id: int,
        *,
        private: bool,
        chat_admin: bool = False,
        bot_username: str = "",
    ) -> None:
        self.store, self.user, self.chat_id = store, user, chat_id
        self.sender_username = ""
        self.bot_username = bot_username
        self.private, self.chat_admin = private, chat_admin
        self.language = user.language
        self.groups = Groups(store, user)
        self.catalog = Catalog(store, user)
        self.setlists = Setlists(store, user)
        self.selection = SongSelection(store, user, chat_id)
        self.importer = Importer(store, user, chat_id)
        self.inputs = CommandInputs(store, user, chat_id, private)
        self.trash = Trash(store, user)
        self.templates = Templates(store, user, chat_id)
        self.exports = Exports(store, user, chat_id, bot_username=bot_username)
        self.menus = SetlistMenus(store, user, chat_id)
        self.source_message_id: int | None = None
        self.reply_target: tuple[UUID, UUID] | None = None

    def t(self, key: str, **values: Any) -> str:
        return tr(self.language, key, **values)

    def _song_card(self, song: Song | dict[str, Any]) -> str:
        return song_card(song, self.language)

    async def confirm(
        self,
        context: Context,
        command: str,
        preview: str,
        *,
        concert: bool = False,
        monospace: bool = False,
    ) -> Response:
        payload: dict[str, Any] = {
            "command": command,
            "group_id": str(context.group_id),
            "concert_id": str(context.concert_id) if concert else None,
        }
        assert context.group_id is not None
        payload["group_epoch"] = (await self.store.get(Group, context.group_id)).lifecycle_version
        if concert:
            assert context.concert_id is not None
            payload["concert_epoch"] = (
                await self.store.get(Concert, context.concert_id)
            ).lifecycle_version
        if command.startswith(("change_shared approve", "publish_draft")):
            assert context.group_id is not None and context.concert_id is not None
            snapshot, _, _ = await self.setlists.read(
                context.group_id, context.concert_id, private=self.private
            )
            payload["song_versions"] = {
                item["song_id"]: item["song_version"]
                for block in snapshot["blocks"]
                for item in block["items"]
                if not is_pause(item)
            }
        confirmation = Confirmation(
            user_id=self.user.id,
            chat_id=self.chat_id,
            expires_at=now() + timedelta(minutes=10),
            command=json.dumps(payload),
        )
        self.store.add(confirmation)
        buttons = [
            (self.t("confirm_button"), f"confirm {confirmation.id}"),
            (self.t("cancel_button"), f"confirm_cancel {confirmation.id.hex}"),
        ]
        return Response(
            self.t("confirm") + "\n\n" + preview,
            buttons,
            monospace=monospace,
            monospace_body=(
                preview.removeprefix(self.t("restore_notice") + "\n") if monospace else None
            ),
        )

    async def execute(
        self, text: str, *, confirmed: bool = False, reply_template_id: UUID | None = None
    ) -> Response:
        message = text.lstrip()
        head = message.split(maxsplit=1)[0] if message else ""
        raw_args = message[len(head) :].lstrip(" \t\r")
        body_on_new_line = raw_args.startswith("\n")
        if body_on_new_line:
            raw_args = raw_args[1:]
        args = raw_args.strip()
        command = head.lstrip("/").split("@")[0].lower()
        args = args.strip()
        if not self.private:
            bindings = await self.store.find(ChatBinding, chat_id=self.chat_id)
            if bindings:
                group = await self.store.get(Group, bindings[0].group_id)
                self.language = group.language
        if command in (
            "block",
            "break",
            "encore",
            "add_encore",
            "remove_encore_song",
            "remove_encore",
            "manage_encore",
            "encore_pick",
        ):
            return Response(self.t("flat_list_only"))
        if command in ("concert_settings", "concert_field"):
            values = args.split()
            metadata_concert = await self.store.get(Concert, UUID(values[0]))
            await self.groups.access(metadata_concert.group_id, admin=True)
            require(metadata_concert.deleted_at is None, "in_trash")
            if not self.private:
                require(
                    bool(
                        await self.store.find(
                            ChatBinding, group_id=metadata_concert.group_id, chat_id=self.chat_id
                        )
                    ),
                    "not_found",
                )
            context = await self.groups.context(self.chat_id, self.private)
            context.group_id, context.concert_id = metadata_concert.group_id, metadata_concert.id
            if command == "concert_field":
                require(len(values) == 2 and values[1] in CONCERT_INPUTS, "input")
                return await self._request_input(values[1], context)
            return self._concert_settings(metadata_concert)
        if command == "import_cancel":
            import_rows = await self.store.find(
                CatalogImport, id=UUID(args), owner_id=self.user.id, chat_id=self.chat_id
            )
            require(bool(import_rows) and import_rows[0].status == "pending", "input_expired")
            import_rows[0].status = "cancelled"
            return Response(self.t("ok"))
        if command in ("input_cancel", "confirm_cancel"):
            rows = await self.store.find(
                Confirmation, id=UUID(args), user_id=self.user.id, chat_id=self.chat_id
            )
            require(bool(rows), "input_expired")
            row = rows[0]
            data = json.loads(row.command)
            require(row.expires_at > now(), "input_expired")
            require(
                data.get("kind") == "command_input"
                if command == "input_cancel"
                else data.get("kind") is None,
                "input_expired",
            )
            await self.store.delete(row)
            if data.get("command") == "picker_pause":
                session = await self.selection.active()
                require(session is not None, "selection_expired")
                assert session is not None
                session, picked = await self.selection.load(session.id)
                require(picked["stage"] == "pause", "selection_expired")
                picked["stage"] = "choosing"
                self.selection.write(session, picked)
                response = self._selection_view(session, picked, keyboard=True)
                response.delete_message_ids = list(data.get("transient_message_ids", []))
                return response
            if data.get("command") == "suggestion" and self.private:
                await self.selection.clear()
                return await self._show(await self.groups.context(self.chat_id, True))
            return Response(self.t("ok"))
        if command == "picker_pause":
            require(self.private, "private_only")
            session = await self.selection.active()
            require(session is not None, "selection_expired")
            assert session is not None
            session, picked = await self.selection.load(session.id)
            require(picked["stage"] == "pause", "selection_expired")
            seconds = parse_pause(
                args + " мин" if args.strip().isdigit() else args, bare_seconds=True
            )
            require(seconds is not None, "pause_duration")
            require(len(picked["selected"]) < 200, "song_list_input")
            picked["selected"].append({"pause_seconds": seconds})
            picked["stage"] = "choosing"
            self.selection.write(session, picked)
            response = self._selection_view(session, picked, keyboard=True)
            response.delete_input = True
            return response
        if command in (
            "slmenu",
            "slact",
            "slopt",
            "slrev",
            "slreq",
            "slreturn",
            "sldraft",
            "slformat",
        ):
            return await self._menu_action(command, args)
        if command in ("pick_start", "pick_text", "pick_confirm", "pick_cancel"):
            require(self.private, "private_only")
            return await self._selection_action(command, UUID(args))
        if command in ("group_create_yes", "group_create_edit", "group_create_cancel"):
            require(not self.private, "group_only")
            session = await self.groups.creation_session(self.chat_id, UUID(args))
            data = json.loads(session.command)
            if command == "group_create_cancel":
                await self.store.delete(session)
                return Response(self.t("group_creation_cancelled"))
            await self.groups.check_creation_chat(self.chat_id)
            require(self.chat_admin, "chat_admin")
            require(data["stage"] == "review", "group_creation_expired")
            if command == "group_create_edit":
                return self._group_name_prompt(await self.groups.begin_creation(self.chat_id))
            group = await self.groups.create(data["name"], self.chat_id)
            await self.groups.clear_creation(self.chat_id)
            return Response(self.t("created_group", name=group.name))
        if reply_template_id is not None and (
            not message.startswith("/") or command == "suggestion"
        ):
            await self._template_context(reply_template_id)
            if not message.startswith("/"):
                command, args = "suggestion", message.strip()
        if command == "set_support_chat":
            await Support(self.store, self.user).configure(self.chat_id, self.sender_username)
            return Response(self.t("support_configured"))
        if command == "support_cancel":
            rows = await self.store.find(
                Confirmation, id=UUID(args), user_id=self.user.id, chat_id=self.chat_id
            )
            require(
                bool(rows) and json.loads(rows[0].command).get("kind") == "support", "input_expired"
            )
            await self.store.delete(rows[0])
            return Response(self.t("ok"))
        if command == "support":
            if not self.private:
                require(bool(self.bot_username), "private_only")
                return Response(
                    self.t("support_private") + f" https://t.me/{self.bot_username}?start=support"
                )
            if not await self.store.find(SupportSettings, key="main"):
                return Response(self.t("support_unavailable"))
            for row in await self.store.find(
                Confirmation, user_id=self.user.id, chat_id=self.chat_id
            ):
                if json.loads(row.command).get("kind") == "support":
                    await self.store.delete(row)
            session = Confirmation(
                user_id=self.user.id,
                chat_id=self.chat_id,
                command=json.dumps({"kind": "support"}),
                expires_at=now() + timedelta(minutes=30),
            )
            self.store.add(session)
            return Response(
                self.t("support_prompt"),
                force_reply=True,
                support_prompt_id=session.id,
                cancel_action=f"support_cancel {session.id.hex}",
                cancel_label=self.t("cancel_button"),
            )
        if command == "start" and args == "support":
            return await self.execute("/support")
        if command == "position_cancel":
            rows = await self.store.find(
                Confirmation, id=UUID(args), user_id=self.user.id, chat_id=self.chat_id
            )
            require(
                bool(rows) and json.loads(rows[0].command).get("kind") == "position_choice",
                "input_expired",
            )
            await self.store.delete(rows[0])
            return Response(self.t("ok"))
        if command == "position":
            identifier, raw = args.split()
            rows = await self.store.find(
                Confirmation, id=UUID(identifier), user_id=self.user.id, chat_id=self.chat_id
            )
            require(bool(rows) and rows[0].expires_at > now(), "input_expired")
            row = rows[0]
            data = json.loads(row.command)
            require(
                data.get("kind") == "position_choice" and data["private"] == self.private,
                "input_expired",
            )
            context = await self.groups.context(self.chat_id, self.private)
            position_group_id, position_concert_id = (
                UUID(data["group_id"]),
                UUID(data["concert_id"]),
            )
            await self.groups.access(position_group_id)
            require(
                context.group_id == position_group_id and context.concert_id == position_concert_id,
                "input_expired",
            )
            group = await self.store.get(Group, position_group_id)
            concert = await self.store.get(Concert, position_concert_id)
            require(
                group.lifecycle_version == data["group_epoch"]
                and concert.lifecycle_version == data["concert_epoch"],
                "input_expired",
            )
            snapshot, version, approved = await self.setlists.read(
                position_group_id, position_concert_id, private=self.private
            )
            require(version == data["version"] and not approved, "conflict")
            items = snapshot["blocks"][0]["items"]
            index = int(raw)
            require(0 <= index < len(items), "input")
            if data["operation"] == "move" and data.get("source") is None:
                data["source"] = index
                row.command = json.dumps(data)
                return self._position_buttons(row, data, items)
            await self.store.delete(row)
            if data["operation"] == "remove":
                return await self.confirm(
                    context,
                    f"edit_list remove {version} 1 {index + 1}",
                    self.t("remove_entry", title=items[index]["title"]),
                    concert=True,
                )
            await self.setlists.edit(
                position_group_id,
                position_concert_id,
                version,
                "move",
                ["1", str(data["source"] + 1), str(index + 1)],
                private=self.private,
            )
            return await self._show(context)
        if command in ("start", "help"):
            return Response(self.t("welcome" if command == "start" else "help"))
        if command == "language":
            if not args:
                return await self._request_input(command)
            require(args in ("ru", "en"), "input")
            self.user.language = args
            self.language = args if self.private else self.language
            return Response(self.t("ok"))
        if command == "export_settings":
            export = await self.exports.load(UUID(args))
            context = await self.groups.context(self.chat_id, self.private)
            context.group_id, context.concert_id = export.group_id, export.concert_id
            shown = await self._show(context, shared=self.private)
            assert shown.menu_card_id is not None
            row, data = await self.menus.load(shown.menu_card_id)
            if self.source_message_id is not None:
                data["message_ids"] = [self.source_message_id]
                self.menus.write(row, data)
            return await self._menu_action("slformat", f"{row.id.hex} main")
        if command == "copy_list":
            return await self._suggestion_prompt(UUID(args))
        if command in ("export_menu", "export_file", "export_back"):
            values = args.split()
            export = await self.exports.load(UUID(values[0]))
            if command in ("export_menu", "export_file"):
                export = await self.exports.configured(export)
            if command == "export_back":
                if export.payload.get("menu_card_id"):
                    return await self.execute(export.payload["back"])
                context = await self.groups.context(self.chat_id, self.private)
                context.group_id, context.concert_id = export.group_id, export.concert_id
                await self._show(context)
                return await self.execute(export.payload["back"])
            language = values[-2] if len(values) > 1 else export.payload["language"]
            latin = values[-1] if len(values) > 1 else "0"
            require(language in ("ru", "en") and latin in ("0", "1"), "input")
            response = self._export_menu(export.id, export.payload, language, latin == "1")
            if command == "export_file":
                require(len(values) == 4, "input")
                response.files = await self.exports.files(
                    export.id, values[1], language, latin == "1", PdfDocuments()
                )
                response.text = self.t("export_ready") + "\n\n" + response.text
            if export.payload.get("menu_card_id"):
                response.markup_only = True
                cards = await self.store.find(Confirmation, id=UUID(export.payload["menu_card_id"]))
                if command == "export_file" and cards and self.menus.data(cards[0]).get("closed"):
                    # A cached PDF stays downloadable after its source card was refreshed.
                    response.buttons = []
                else:
                    _, card = await self.menus.load(
                        UUID(export.payload["menu_card_id"]), self.source_message_id
                    )
                    response.edit_message_ids = list(card["message_ids"])
            return response
        if command == "trash":
            return await self._trash_list()
        if command == "restore_group":
            identifier, epoch = args.split()
            restored_group = await self.trash.restore_group(UUID(identifier), int(epoch))
            return Response(self.t("group_restored", name=restored_group.name))
        if command == "restore_concert":
            identifier, epoch = args.split()
            restored_target = await self.store.get(Concert, UUID(identifier))
            restored_concert = await self.trash.restore_concert(
                restored_target.group_id, restored_target.id, int(epoch)
            )
            return Response(
                self.t("concert_restored", name=concert_label(restored_concert, self.language))
            )
        if command == "cancel":
            cleanup = []
            for item in await self.store.find(
                Confirmation, user_id=self.user.id, chat_id=self.chat_id
            ):
                pending = json.loads(item.command)
                if pending.get("kind") in (CARD, REQUEST):
                    continue
                if (
                    self.private
                    and pending.get("kind") == "command_input"
                    and pending.get("command") == "picker_pause"
                ):
                    cleanup.extend(pending.get("transient_message_ids", []))
                await self.store.delete(item)
            for job in await self.store.find(
                CatalogImport, owner_id=self.user.id, chat_id=self.chat_id, status="pending"
            ):
                job.status = "cancelled"
            return Response(self.t("ok"), delete_message_ids=cleanup)
        if command == "group":
            require(not self.private, "group_only")
            await self.groups.check_creation_chat(self.chat_id)
            require(self.chat_admin, "chat_admin")
            if not args:
                return self._group_name_prompt(await self.groups.begin_creation(self.chat_id))
            group = await self.groups.create(args, self.chat_id)
            await self.groups.clear_creation(self.chat_id)
            return Response(self.t("created_group", name=group.name))
        if command == "groups":
            require(self.private, "private_only")
            memberships = await self.store.find(Membership, user_id=self.user.id, status="active")
            groups = [await self.store.get(Group, m.group_id) for m in memberships]
            groups = [g for g in groups if g.deleted_at is None]
            return Response(
                self.t("choose_group") if groups else self.t("empty"),
                [(g.name, f"use_group {g.id}") for g in groups],
            )
        context = await self.groups.context(self.chat_id, self.private)
        if command == "use_group":
            require(self.private, "private_only")
            group = await self.groups.access(UUID(args))
            context.group_id, context.concert_id, context.observed_version = group.id, None, None
            context.selected_song_id = None
            return Response(self.t("selected", name=group.name))
        require(context.group_id is not None, "select_group")
        group_id = context.group_id
        assert group_id is not None
        if command == "register":
            require(not self.private, "group_only")
            if not args:
                return await self._request_input(command, context)
            membership = await self.groups.register(group_id, args)
            return Response(
                self.t("nickname_saved" if membership.status == "active" else "registered")
            )
        group = await self.groups.access(group_id)
        if command in GROUP_INPUTS and not args:
            if command in (
                "concert",
                "group_language",
                "group_pause",
                "import",
                "archive",
                "unarchive",
            ):
                await self.groups.access(group_id, admin=True)
            if command not in ("archive", "unarchive") or context.selected_song_id is None:
                return await self._request_input(command, context)
        if command == "reminders":
            await self.groups.access(group_id, admin=True)
            reminder_args = args.split()
            require(len(reminder_args) <= 2, "input")
            if len(reminder_args) == 2:
                require(UUID(reminder_args[1]) == group_id, "input_expired")
            reminder_action = reminder_args[0] if reminder_args else ""
            require(reminder_action in ("", "on", "off"), "input")
            if reminder_action:
                group = await self.groups.set_reminders(group_id, reminder_action == "on")
            return Response(
                self.t("reminders_on" if group.reminders_enabled else "reminders_off"),
                [
                    (
                        self.t(
                            "reminder_disable" if group.reminders_enabled else "reminder_enable"
                        ),
                        f"reminders {'off' if group.reminders_enabled else 'on'} {group_id}",
                    )
                ],
            )
        if command == "cleanup_chat":
            if not self.private:
                await self.groups.access(group_id, admin=True)
            if not confirmed:
                cleanup_confirmation = await self.confirm(
                    context, "cleanup_chat", self.t("cleanup_prompt")
                )
                cleanup_confirmation.buttons[0] = (
                    self.t("cleanup_yes"),
                    cleanup_confirmation.buttons[0][1],
                )
                return cleanup_confirmation
            return Response(
                self.t("cleanup_done"),
                delete_message_ids=await cleanup_messages(self.store, self.chat_id),
            )
        if command == "administrator":
            operation, member_id = args.split()
            require(operation in ("grant", "revoke"), "input")
            require(group.owner_id == self.user.id, "owner_only")
            if not confirmed:
                member = await self.store.get(Membership, UUID(member_id))
                require(member.group_id == group_id and member.status == "active", "not_found")
                name = await self.groups.name(group_id, member.user_id)
                return await self.confirm(
                    context,
                    f"administrator {args}",
                    self.t("administrator_prompt", name=name, action=self.t(operation)),
                )
            await self.groups.administrator(group_id, UUID(member_id), operation == "grant")
            return await self.execute("members")
        if command in ("backup", "backup_group"):
            require(not args or UUID(args) == group_id, "not_found")
            return Response(
                self.t("backup_ready"),
                files=[
                    ("setlist-shared-data.zip", await Backup(self.store, self.user).build(group_id))
                ],
            )
        if command in ("delete_group", "delete_group_step", "delete_group_final"):
            await self.groups.access(group_id, admin=True)
            fingerprint = await self.trash.group_fingerprint(group_id)
            if command != "delete_group":
                require(confirmed and args == fingerprint, "delete_changed")
            if command == "delete_group_final":
                await self.trash.delete_group(group_id, args)
                return Response(self.t("group_deleted", name=group.name))
            next_step = "delete_group_step" if command == "delete_group" else "delete_group_final"
            response = await self.confirm(
                context,
                f"{next_step} {fingerprint}",
                self.t(
                    "group_delete_preview" if command == "delete_group" else "group_delete_final",
                    name=group.name,
                ),
            )
            if command == "delete_group":
                response.buttons.insert(
                    0, (self.t("backup_button"), f"backup_group {group_id.hex}")
                )
            return response
        if command == "group_pause":
            if args:
                group = await self.groups.set_default_pause(group_id, args)
            return Response(
                self.t(
                    "group_pause_setting", name=group.name, time=clock(group.default_pause_seconds)
                )
            )
        table_input = "\t" in raw_args or (
            bool(raw_args) and (body_on_new_line or "\n" in raw_args)
        )
        if command == "import" or (command == "song" and table_input):
            job = await self.importer.create(group_id, raw_args)
            return await self._import_preview(context, job)
        if command == "commit_import":
            require(confirmed, "confirmation")
            identifier, fingerprint = args.split()
            count = await self.importer.commit(group_id, UUID(identifier), fingerprint)
            return Response(self.t("import_done", count=count))
        if command in ("map_start", "map_header", "map_col"):
            values = args.split()
            job = await self.importer.job(group_id, UUID(values[0]))
            if command == "map_start":
                return Response(
                    self.t("import_header_prompt"),
                    [
                        (self.t("import_with_header"), f"map_header {job.id.hex} 1"),
                        (self.t("import_without_header"), f"map_header {job.id.hex} 0"),
                    ],
                )
            if command == "map_header":
                require(len(values) == 2 and values[1] in ("0", "1"), "input")
                job.has_header, job.mapping = values[1] == "1", {}
            else:
                require(len(values) == 3 and job.mapping is not None, "input")
                step, index = int(values[1]), int(values[2])
                assert job.mapping is not None
                require(0 <= step < len(FIELDS), "input")
                require(step == len(job.mapping), "confirmation")
                table = read_table(job.source, job.has_header)
                require(0 <= index < len(table.headers) or (step > 1 and index == -1), "input")
                require(index == -1 or index not in job.mapping.values(), "input")
                job.mapping = {**job.mapping, FIELDS[step]: index}
            assert job.mapping is not None
            if len(job.mapping) == len(FIELDS):
                return await self._import_preview(context, job)
            return self._mapping_step(job)
        if command == "confirm":
            confirmation = await self.store.get(Confirmation, UUID(args))
            require(
                confirmation.user_id == self.user.id
                and confirmation.chat_id == self.chat_id
                and confirmation.expires_at > now(),
                "confirmation",
            )
            data = json.loads(confirmation.command)
            require("command" in data and data.get("kind") is None, "confirmation")
            require(data["group_id"] == str(context.group_id), "confirmation")
            require(data.get("group_epoch", 0) == group.lifecycle_version, "confirmation")
            require(
                data["concert_id"] is None or data["concert_id"] == str(context.concert_id),
                "confirmation",
            )
            if data["concert_id"] is not None:
                target_concert = await self.store.get(Concert, UUID(data["concert_id"]))
                require(
                    target_concert.deleted_at is None
                    and data.get("concert_epoch", 0) == target_concert.lifecycle_version,
                    "confirmation",
                )
            for identifier, song_version in data.get("song_versions", {}).items():
                song = await self.catalog.song(group_id, UUID(identifier))
                require(song.version == song_version, "conflict")
            await self.store.delete(confirmation)
            return await self.execute(data["command"], confirmed=True)
        if command == "group_language":
            await self.groups.access(group_id, admin=True)
            require(args in ("ru", "en"), "input")
            group.language = args
            if not self.private:
                self.language = args
            return Response(self.t("ok"))
        if command == "members":
            await self.groups.access(group_id, admin=True)
            lines = [self.t("members")]
            buttons = []
            for member in await self.store.find(Membership, group_id=group_id):
                user = await self.store.get(User, member.user_id)
                label = await self.groups.name(group_id, user.id)
                role = "owner" if group.owner_id == user.id else member.role
                lines.append(f"{label} · {self.t(role)} · {self.t(member.status)}")
                if (
                    group.owner_id == self.user.id
                    and user.id != group.owner_id
                    and member.status == "active"
                ):
                    operation = "revoke" if member.role == "admin" else "grant"
                    buttons.append(
                        (
                            self.t(operation) + " · " + label,
                            f"administrator {operation} {member.id}",
                        )
                    )
                if member.status == "pending":
                    for action, key in (("yes", "approve_button"), ("no", "reject_button")):
                        buttons.append((f"{self.t(key)} {label}", f"member {action} {member.id}"))
            return Response("\n".join(lines), buttons)
        if command == "member":
            action, identifier = args.split()
            require(action in ("yes", "no"), "input")
            await self.groups.decide_member(group_id, UUID(identifier), action == "yes")
            return Response(self.t("ok"))
        if command in ("catalog", "archived"):
            songs = (
                await self.catalog.search(group_id, args)
                if args and command == "catalog"
                else await self.catalog.list_songs(group_id, archived=command == "archived")
            )
            return Response(
                group.name
                + "\n\n"
                + ("\n\n".join(self._song_card(s) for s in songs) or self.t("empty")),
                [(self.t("song_open", name=s.title), f"show_song {s.id}") for s in songs],
            )
        if command in (
            "show_song",
            "edit_hint",
            "edit_full_hint",
            "copy_hint",
            "alias_hint",
            "song_edit_menu",
            "song_field",
        ):
            values = args.split()
            selected = await self.catalog.song(group_id, UUID(values[0]))
            context.selected_song_id = selected.id
            if command == "edit_hint":
                command = "song_edit_menu"
            if command == "edit_full_hint":
                return await self._request_input("edit", context)
            if command == "song_field":
                require(len(values) == 2 and values[1] in ("bpm", "lights", "comment"), "input")
                return await self._request_input("edit_" + values[1], context)
            if command == "song_edit_menu":
                return Response(
                    self._song_card(selected),
                    [
                        (
                            self.t(
                                "song_field_change"
                                if getattr(selected, field)
                                else "song_field_add",
                                field=self.t("song_field_" + field),
                            ),
                            f"song_field {selected.id.hex} {field}",
                        )
                        for field in ("bpm", "lights", "comment")
                    ]
                    + [
                        (self.t("song_edit_title_duration"), f"edit_full_hint {selected.id}"),
                        (self.t("menu_back"), f"show_song {selected.id}"),
                    ],
                )
            if command != "show_song":
                return await self._request_input(command.removesuffix("_hint"), context)
            buttons = [
                (self.t("song_edit"), f"song_edit_menu {selected.id}"),
                (self.t("song_alias"), f"alias_hint {selected.id}"),
            ]
            member = (await self.store.find(Membership, group_id=group_id, user_id=self.user.id))[0]
            if member.role == "admin":
                action = "unarchive" if selected.archived else "archive"
                buttons.append(
                    (
                        self.t("song_unarchive" if selected.archived else "song_archive"),
                        f"{action} {selected.id}",
                    )
                )
            return Response(self._song_card(selected), buttons)
        if command in ("edit_bpm", "edit_lights", "edit_comment"):
            require(context.selected_song_id is not None, "song_input")
            assert context.selected_song_id is not None
            selected_field_song = await self.catalog.song(group_id, context.selected_song_id)
            field = command.removeprefix("edit_")
            data = {key: getattr(selected_field_song, key) for key in SONG_FIELDS}
            data[field] = (
                (None if args == "-" else int(args))
                if field == "bpm"
                else ("" if args == "-" else args)
            )
            validate_song(data)
            payload = {
                "data": data,
                "song_id": str(selected_field_song.id),
                "expected": selected_field_song.version,
                "return_to_edit": True,
            }
            return await self.confirm(
                context, "save_song " + json.dumps(payload), self._song_card(data)
            )
        if command in ("song", "edit", "copy"):
            parts = command_fields(args, maxsplit=2 if command == "copy" else 4)
            song_id: UUID | None = None
            expected: int | None = None
            if command == "copy":
                if len(parts) == 2:
                    parts.insert(0, "")
                require(len(parts) == 3, "song_input")
                original = await self._resolve_song(group_id, parts[0], context)
                data = {key: getattr(original, key) for key in SONG_FIELDS}
                data.update(title=parts[1], duration_seconds=duration(parts[2]))
            else:
                original = None
                if command == "edit":
                    prefix = command_fields(args, maxsplit=2)
                    using_selected_song = (
                        len(prefix) >= 2 and TIME_CELL.fullmatch(prefix[1]) is not None
                    )
                    parts = command_fields(args, maxsplit=4 if using_selected_song else 5)
                    if using_selected_song:
                        parts.insert(0, "")
                    original = await self._resolve_song(group_id, parts.pop(0), context)
                    song_id = original.id
                    expected = original.version
                require(2 <= len(parts) <= 5, "song_input")
                provided = len(parts)
                parts += [""] * (5 - len(parts))
                parts[2:] = ["" if part == "-" else part for part in parts[2:]]
                data = {
                    "title": parts[0],
                    "duration_seconds": duration(parts[1]),
                    "bpm": int(parts[2]) if parts[2] else None,
                    "lights": parts[3],
                    "comment": parts[4],
                }
                if original:
                    for key in SONG_FIELDS[provided:]:
                        data[key] = getattr(original, key)
            validate_song(data)
            payload = {
                "data": data,
                "song_id": str(song_id) if song_id else None,
                "expected": expected,
            }
            return await self.confirm(
                context, "save_song " + json.dumps(payload), self._song_card(data)
            )
        if command == "save_song":
            require(confirmed, "confirmation")
            payload = json.loads(args)
            result = await self.catalog.save(
                group_id,
                payload["data"],
                UUID(payload["song_id"]) if payload["song_id"] else None,
                payload["expected"],
            )
            if payload.get("return_to_edit"):
                response = await self.execute(f"song_edit_menu {payload['song_id']}")
                response.text = (
                    self.t(
                        "proposed" if isinstance(result, CatalogProposal) else "saved_song",
                        name=payload["data"]["title"],
                    )
                    + "\n\n"
                    + response.text
                )
                return response
            return Response(
                self.t(
                    "proposed" if isinstance(result, CatalogProposal) else "saved_song",
                    name=payload["data"]["title"],
                )
            )
        if command == "alias":
            require("|" not in args, "field_separator")
            parts = command_fields(args, maxsplit=1)
            if len(parts) == 1:
                parts.insert(0, "")
            identifier, title = parts
            target = await self._resolve_song(group_id, identifier, context)
            alias_result = await self.catalog.alias(group_id, target.id, title)
            return Response(
                self.t("proposed", id=alias_result.id)
                if isinstance(alias_result, CatalogProposal)
                else self.t("ok")
            )
        if command in ("archive", "unarchive"):
            song = await self._resolve_song(
                group_id, args, context, archived=command == "unarchive"
            )
            await self.groups.access(group_id, admin=True)
            return await self.confirm(
                context, f"set_archive {song.id} {song.version} {command}", self._song_card(song)
            )
        if command == "set_archive":
            require(confirmed, "confirmation")
            identifier, raw_version, action = args.split()
            await self.catalog.archive(
                group_id, UUID(identifier), int(raw_version), action == "archive"
            )
            return Response(self.t("ok"))
        if command == "proposals":
            await self.groups.access(group_id, admin=True)
            proposals = await self.store.find(CatalogProposal, group_id=group_id, status="pending")
            lines, buttons = [], []
            for proposal in proposals:
                preview = proposal.payload.get("alias") or self._song_card(proposal.payload)
                author = await self.store.get(User, proposal.author_id)
                lines.append(f"{await self.groups.name(group_id, author.id)}\n{preview}")
                for action, key in (("yes", "approve_button"), ("no", "reject_button")):
                    buttons.append(
                        (
                            f"{self.t(key)}: {preview.splitlines()[0][:60]}",
                            f"decide {action} {proposal.id}",
                        )
                    )
            return Response("\n\n".join(lines) or self.t("empty"), buttons)
        if command == "decide":
            action, identifier = args.split()
            require(action in ("yes", "no"), "input")
            await self.catalog.decide(group_id, UUID(identifier), action == "yes")
            return Response(self.t("ok"))
        if command == "concert":
            await self.groups.access(group_id, admin=True)
            when, title = parse_concert(args)
            require(len(title) <= 200, "concert_input")
            return await self.confirm(
                context,
                f"create_concert {when.isoformat()} {title}",
                self.t("concert_create_preview", date=date_label(when, self.language), title=title),
            )
        if command == "create_concert":
            require(confirmed, "confirmation")
            raw_date, _, title = args.partition(" ")
            concert = await self.setlists.create(group_id, date.fromisoformat(raw_date), title)
            context.concert_id = concert.id
            _, context.observed_version, _ = await self.setlists.read(
                group_id, concert.id, private=self.private
            )
            response = self._concert_settings(concert, created=True)
            return response
        if command in CONCERT_INPUTS:
            await self.groups.access(group_id, admin=True)
            require(context.concert_id is not None, "select_concert")
            if not args:
                return await self._request_input(command, context)
            assert context.concert_id is not None
            concert = await ConcertDetails(self.store, self.user).update(
                group_id, context.concert_id, command, args
            )
            return self._concert_settings(concert)
        if command in ("concerts", "past_concerts"):
            past = command == "past_concerts"
            concerts = sorted(
                [
                    c
                    for c in await self.store.find(Concert, group_id=group_id, deleted_at=None)
                    if (c.date < today()) == past
                ],
                key=lambda c: (c.date, str(c.id)),
                reverse=past,
            )
            approved_concerts = {
                row.concert_id
                for row in await self.store.find(Setlist, group_id=group_id, approved=True)
            }
            buttons = [
                (
                    ("✅ " if c.id in approved_concerts else "")
                    + concert_label(c, self.language, button=True),
                    f"use_concert {c.id}",
                )
                for c in concerts
            ]
            if past:
                buttons.append((self.t("menu_back"), "concerts"))
            else:
                buttons.insert(0, (self.t("past_concerts"), "past_concerts"))
            buttons.append((self.t("new_concert"), "concert"))
            return Response(self.t("past_concerts" if past else "choose_concert"), buttons)
        if command == "use_concert":
            await self.setlists.load(group_id, UUID(args))
            context.concert_id = UUID(args)
            context.observed_version = None
            return await self._show(context)
        if command == "apply_submission":
            require(not self.private, "group_only")
            submitted = await self.store.get(Submission, UUID(args))
            require(submitted.group_id == group_id, "not_found")
            require(not submitted.applied, "already_done")
            context.concert_id = submitted.concert_id
            _, context.observed_version, approved = await self.setlists.read(
                group_id, submitted.concert_id
            )
            require(not approved, "approved")
            expected_shared = submitted.snapshot.get("shared_version")
            require(
                expected_shared is None or expected_shared == context.observed_version, "conflict"
            )
            response = await self.execute(f"apply {submitted.id}")
            if self.source_message_id is not None:
                response.edit_message_ids = [self.source_message_id]
                events = await self.store.find(
                    Outbox, group_id=group_id, concert_id=submitted.concert_id
                )
                for event in events:
                    if any(
                        button[1] == f"apply_submission {submitted.id}" for button in event.buttons
                    ):
                        refs = await self.store.find(Confirmation, chat_id=self.chat_id)
                        for ref in refs:
                            payload = json.loads(ref.command)
                            if payload.get("submission_id") == str(submitted.id):
                                response.edit_message_ids = payload.get(
                                    "message_ids", response.edit_message_ids
                                )
                                await self.store.delete(ref)
            return response
        if command == "view_submission":
            submission = await self.store.get(Submission, UUID(args))
            require(submission.group_id == group_id, "not_found")
            context.concert_id = submission.concert_id
            await self._show(context)
            return Response(
                render_setlist(
                    submission.snapshot,
                    await self.store.get(Concert, submission.concert_id),
                    self.language,
                    0,
                ),
                (
                    [(self.t("submission_apply"), f"apply {submission.id}")]
                    if not self.private
                    else []
                ),
                monospace=True,
            )
        require(context.concert_id is not None, "select_concert")
        concert_id = context.concert_id
        assert concert_id is not None
        if command in ("move", "remove") and not args:
            snapshot, version, approved = await self.setlists.read(
                group_id, concert_id, private=self.private
            )
            require(not approved, "approved")
            require(bool(snapshot["blocks"][0]["items"]), "empty_setlist")
            context.observed_version = version
            row = Confirmation(
                user_id=self.user.id,
                chat_id=self.chat_id,
                expires_at=now() + timedelta(minutes=10),
                command=json.dumps(
                    {
                        "kind": "position_choice",
                        "group_id": str(group_id),
                        "concert_id": str(concert_id),
                        "version": version,
                        "private": self.private,
                        "operation": command,
                        "group_epoch": group.lifecycle_version,
                        "concert_epoch": (
                            await self.store.get(Concert, concert_id)
                        ).lifecycle_version,
                    }
                ),
            )
            self.store.add(row)
            return self._position_buttons(
                row, json.loads(row.command), snapshot["blocks"][0]["items"]
            )
        if command in SETLIST_INPUTS and not args:
            return await self._request_input(command, context)
        if command in ("delete_concert", "delete_concert_final"):
            await self.groups.access(group_id, admin=True)
            require(
                not args or command == "delete_concert_final" or UUID(args) == concert_id,
                "confirmation",
            )
            fingerprint = await self.trash.concert_fingerprint(group_id, concert_id)
            if command == "delete_concert_final":
                require(confirmed and args == fingerprint, "delete_changed")
                deleted_concert = await self.trash.delete_concert(group_id, concert_id, fingerprint)
                return Response(
                    self.t("concert_deleted", name=concert_label(deleted_concert, self.language))
                )
            target_concert = await self.store.get(Concert, concert_id)
            return await self.confirm(
                context,
                f"delete_concert_final {fingerprint}",
                self.t("concert_delete_preview", name=concert_label(target_concert, self.language)),
                concert=True,
            )
        if command in ("setlist", "shared"):
            return await self._show(context, shared=command == "shared")
        if command == "export":
            snapshot, version, approved = await self.setlists.read(
                group_id, concert_id, private=False
            )
            require(approved, "export_approved_only")
            export = await self.exports.create(
                group_id,
                concert_id,
                snapshot,
                version,
                approved=approved,
                private=self.private,
                back="shared" if self.private else "setlist",
            )
            return self._export_menu(export.id, export.payload, export.payload["language"], False)
        if command == "pause" and not args:
            response = await self._show(context)
            response.text += "\n\n" + self.t("pause_usage")
            return response
        require(context.observed_version is not None, "view_first")
        expected = context.observed_version
        assert expected is not None
        if command == "suggestion" and not args:
            shown = await self._show(context)
            require(shown.suggestion_template_id is not None, "approved")
            assert shown.suggestion_template_id is not None
            return await self._suggestion_prompt(shown.suggestion_template_id)
        if command == "suggestion" or (
            command == "add" and ("\n" in message[len(head) :] or parse_pause(args) is not None)
        ):
            body = args
            titles = [line.strip() for line in body.splitlines() if line.strip()]
            try:
                await self.setlists.edit_songs(
                    group_id,
                    concert_id,
                    expected,
                    titles,
                    replace=command == "suggestion",
                    private=self.private,
                )
            except SongListError as exc:
                lines = [self.t("song_list_errors")]
                for number, title, candidates in exc.issues:
                    reason = (
                        self.t("song_list_ambiguous", names=", ".join(candidates))
                        if candidates
                        else self.t("song_list_missing")
                    )
                    lines.append(
                        self.t("song_list_line", number=number, title=title, reason=reason)
                    )
                return Response("\n\n".join(lines), input_rejected=True)
            previous = await self.selection.active() if self.private else None
            if self.private:
                await self.selection.clear()
            response = await self._show(context)
            response.remove_keyboard = (
                previous is not None and self.selection.data(previous)["stage"] == "choosing"
            )
            response.keyboard_text = self.t("selection_keyboard_closed")
            return response
        if command == "offer_add":
            values = args.split()
            target = await self.catalog.song(group_id, UUID(values[0]))
            block = values[1] if len(values) > 1 else "1"
            preview = (await self._show(context)).text + "\n\n" + self._song_card(target)
            assert context.observed_version is not None
            return await self.confirm(
                context,
                f"edit_list add {context.observed_version} {block} {target.id}",
                preview,
                concert=True,
            )
        if command in ("add", "move", "pause", "remove"):
            values = args.split()
            operation = command
            if command == "pause" and len(values) == 1:
                operation = "default_pause"
            elif command in ("move", "remove", "pause"):
                values.insert(0, "1")
            if command == "add":
                require("|" not in args, "field_separator")
                fields = command_fields(args, maxsplit=1)
                selector = fields[0]
                require(len(fields) == 1, "setlist_input")
                block = "1"
                try:
                    legacy_song_id = UUID(values[0]) if values else None
                except ValueError:
                    legacy_song_id = None
                if legacy_song_id is not None:
                    require(len(values) in (1, 2), "setlist_input")
                    values = [values[1] if len(values) == 2 else "1", str(legacy_song_id)]
                else:
                    songs = (
                        await self.catalog.search(group_id, selector.strip())
                        if selector.strip()
                        else await self.catalog.list_songs(group_id)
                    )
                    if len(songs) != 1 or not selector.strip():
                        return Response(
                            self.t("add_choose") if songs else self.t("empty"),
                            [
                                (
                                    self._song_card(s).splitlines()[0],
                                    f"offer_add {s.id.hex} {block}",
                                )
                                for s in songs
                            ],
                            input_rejected=bool(selector.strip()) and not songs,
                        )
                    values = [block, str(songs[0].id)]
            if command in ("move", "remove"):
                snapshot, _, _ = await self.setlists.read(
                    group_id, concert_id, private=self.private
                )
                song_rows = [
                    index + 1
                    for index, item in enumerate(snapshot["blocks"][0]["items"])
                    if not is_pause(item)
                ]
                for slot in [1, 2] if command == "move" else [1]:
                    require(slot < len(values), "setlist_input")
                    number = int(values[slot])
                    require(1 <= number <= len(song_rows), "setlist_input")
                    values[slot] = str(song_rows[number - 1])
            lengths = {"add": 2, "move": 3, "pause": 3, "default_pause": 1, "break": 2, "remove": 2}
            require(len(values) == lengths[operation], "setlist_input")
            if command == "remove" and not confirmed:
                snapshot, version, approved = await self.setlists.read(
                    group_id, concert_id, private=self.private
                )
                return await self.confirm(
                    context,
                    f"edit_list {command} {expected} {' '.join(values)}",
                    render_setlist(
                        snapshot,
                        await self.store.get(Concert, concert_id),
                        self.language,
                        version,
                        private=self.private,
                        approved=approved,
                    )
                    + f"\n/remove {args}",
                    concert=True,
                    monospace=True,
                )
            await self.setlists.edit(
                group_id, concert_id, expected, operation, values, private=self.private
            )
            return await self._show(context)
        if command == "edit_list":
            require(confirmed, "confirmation")
            operation, raw_version, rest = args.split(" ", 2)
            await self.setlists.edit(
                group_id,
                concert_id,
                int(raw_version),
                operation,
                rest.split(),
                private=self.private,
            )
            return await self._show(context)
        if command in ("approve", "reopen", "restore", "apply"):
            require(not self.private, "group_only")
            if command in ("approve", "reopen"):
                await self.groups.access(group_id, admin=True)
            preview = (await self._show(context)).text
            if command == "restore":
                revision = await self.store.get(Revision, UUID(args))
                setlist = await self.setlists.load(group_id, concert_id)
                require(
                    revision.group_id == group_id and revision.setlist_id == setlist.id, "not_found"
                )
                preview = (
                    self.t("restore_notice")
                    + "\n"
                    + render_setlist(
                        revision.snapshot,
                        await self.store.get(Concert, concert_id),
                        self.language,
                        revision.number,
                    )
                )
            if command == "apply":
                submission = await self.store.get(Submission, UUID(args))
                require(
                    submission.group_id == group_id and submission.concert_id == concert_id,
                    "not_found",
                )
                await self.setlists.apply(group_id, concert_id, submission.id, expected)
                return await self._show(context)
            return await self.confirm(
                context,
                f"change_shared {command} {expected} {args}".strip(),
                preview,
                concert=True,
                monospace=True,
            )
        if command == "change_shared":
            require(confirmed and not self.private, "confirmation")
            values = args.split()
            operation, raw_version = values[:2]
            if operation == "approve":
                await self.setlists.approve(group_id, concert_id, int(raw_version))
            elif operation == "reopen":
                await self.setlists.reopen(group_id, concert_id, int(raw_version))
            elif operation == "restore":
                await self.setlists.restore(group_id, concert_id, UUID(values[2]), int(raw_version))
            elif operation == "apply":
                await self.setlists.apply(group_id, concert_id, UUID(values[2]), int(raw_version))
            else:
                require(False, "input")
            return await self._show(context)
        if command in ("history", "versions"):
            history = await self.setlists.history(group_id, concert_id)
            lines = [concert_label(await self.store.get(Concert, concert_id), self.language)]
            for revision in history:
                author = await self.store.get(User, revision.author_id)
                lines.append(
                    self.t(
                        "history_line",
                        number=revision.number,
                        action=self.t(
                            "approved_action" if revision.action == "approved" else revision.action
                        ),
                        time=revision.created_at.isoformat(timespec="seconds"),
                        author=await self.groups.name(group_id, author.id),
                    )
                )
            return Response(
                "\n\n".join(lines),
                [(await self._revision_label(r), f"revision {r.id}") for r in history],
            )
        if command == "revision":
            revision = await self.store.get(Revision, UUID(args))
            setlist = await self.setlists.load(group_id, concert_id)
            require(
                revision.group_id == group_id and revision.setlist_id == setlist.id, "not_found"
            )
            return Response(
                render_setlist(
                    revision.snapshot,
                    await self.store.get(Concert, concert_id),
                    self.language,
                    revision.number,
                    approved=revision.action == "approved",
                ),
                (
                    [(self.t("revision_restore"), f"restore {revision.id}")]
                    if not self.private
                    else []
                )
                + await self._export_buttons(
                    group_id,
                    concert_id,
                    revision.snapshot,
                    revision.number,
                    approved=revision.action == "approved",
                    back=f"revision {revision.id}",
                ),
                monospace=True,
            )
        if command in ("publish", "publish_draft"):
            require(self.private, "private_only")
            if command == "publish_draft":
                require(confirmed, "confirmation")
            snapshot, version, _ = await self.setlists.read(group_id, concert_id, private=True)
            require(version == (int(args) if command == "publish_draft" else expected), "conflict")
            bindings = await self.store.find(ChatBinding, group_id=group_id)
            require(bool(bindings), "no_chat")
            require(any(block["items"] for block in snapshot["blocks"]), "empty_setlist")
            submission = await self.setlists.publish(group_id, concert_id)
            _, shared_version, _ = await self.setlists.read(group_id, concert_id)
            submission.snapshot = {**submission.snapshot, "shared_version": shared_version}
            public_body = (
                group.name
                + "\n"
                + render_setlist(
                    snapshot,
                    await self.store.get(Concert, concert_id),
                    group.language,
                    version,
                    private=True,
                )
            )
            public_prefix = (
                tr(group.language, "submission", name=await self.groups.name(group_id)) + "\n\n"
            )
            public_text = public_prefix + public_body
            self.store.add(
                Outbox(
                    chat_id=bindings[0].chat_id,
                    text=public_text,
                    group_id=group_id,
                    concert_id=concert_id,
                    monospace=True,
                    monospace_offset=len(public_prefix),
                    buttons=[
                        [
                            tr(group.language, "submission_apply"),
                            f"apply_submission {submission.id}",
                        ]
                    ],
                )
            )
            return Response(self.t("published"))
        require(False, "input")
        raise AssertionError("unreachable")

    async def _revision_label(self, revision: Revision) -> str:
        author = await self.store.get(User, revision.proposer_id or revision.author_id)
        return self.t(
            "variant_label" if revision.suggestion_number is not None else "revision_label",
            name=await self.groups.name(revision.group_id, author.id),
            number=revision.suggestion_number
            if revision.suggestion_number is not None
            else revision.number,
        )

    async def _export_buttons(
        self,
        group_id: UUID,
        concert_id: UUID,
        snapshot: dict[str, Any],
        version: int,
        *,
        approved: bool,
        back: str,
        chat_id: int | None = None,
        private: bool | None = None,
        language: str | None = None,
    ) -> list[tuple[str, str]]:
        if not approved or not any(block["items"] for block in snapshot["blocks"]):
            return []
        service = (
            self.exports
            if chat_id is None
            else Exports(self.store, self.user, chat_id, bot_username=self.bot_username)
        )
        export = await service.create(
            group_id,
            concert_id,
            snapshot,
            version,
            approved=approved,
            private=self.private if private is None else private,
            back=back,
        )
        return [(tr(language or self.language, "export_button"), f"export_menu {export.id.hex}")]

    def _export_menu(
        self, export_id: UUID, payload: dict[str, Any], language: str, latin: bool
    ) -> Response:
        buttons = [
            (self.t(label), f"export_file {export_id.hex} {audience} {language} {int(latin)}")
            for label, audience in (
                ("export_musicians", "musicians"),
                ("export_lights", "lights"),
                ("export_all", "all"),
            )
        ]
        buttons += [
            (
                self.t("export_language", code=language.upper()),
                f"export_menu {export_id.hex} {'en' if language == 'ru' else 'ru'} {int(latin)}",
            ),
            (
                self.t("export_latin_on" if latin else "export_latin_off"),
                f"export_menu {export_id.hex} {language} {int(not latin)}",
            ),
            (self.t("back_button"), f"export_back {export_id.hex}"),
        ]
        if payload.get("menu_card_id"):
            buttons.insert(
                0, (self.t("export_settings"), f"slformat {UUID(payload['menu_card_id']).hex} main")
            )
        else:
            buttons.insert(0, (self.t("export_settings"), f"export_settings {export_id.hex}"))
        status = self.t("pdf_approved" if payload["approved"] else "pdf_draft")
        label = date_label(date.fromisoformat(payload["concert_date"]), self.language)
        return Response(
            f"{payload['group_name']}\n" + f"{label} {payload['concert_title']}\n"
            f"{self.t('version', version=payload['version'])} · {status}\n\n"
            + self.t("export_choose"),
            buttons,
        )

    def _song_list_errors(self, exc: SongListError) -> Response:
        lines = [self.t("song_list_errors")]
        for number, title, candidates in exc.issues:
            reason = (
                self.t("song_list_ambiguous", names=", ".join(candidates))
                if candidates
                else self.t("song_list_missing")
            )
            lines.append(self.t("song_list_line", number=number, title=title, reason=reason))
        return Response("\n\n".join(lines), input_rejected=True)

    def _concert_settings(self, concert: Concert, *, created: bool = False) -> Response:
        text = concert_label(concert, self.language) + "\n\n" + self.t("concert_details_intro")
        buttons = []
        for command, value, label in (
            (
                "concert_duration",
                compact_duration(concert.performance_duration_seconds, self.language)
                if concert.performance_duration_seconds
                else "",
                "concert_duration_label",
            ),
            ("concert_city", concert.city, "concert_city_label"),
            ("concert_info", concert.extra_info, "concert_info_label"),
        ):
            if value:
                text += "\n" + self.t(label) + ": " + value
            buttons.append(
                (
                    self.t("change_field" if value else "set_field", field=self.t(label)),
                    f"concert_field {concert.id.hex} {command}",
                )
            )
        buttons.append((self.t("open_setlist"), f"use_concert {concert.id}"))
        return Response((self.t("concert_created") + "\n\n" if created else "") + text, buttons)

    async def _request_input(self, command: str, context: Context | None = None) -> Response:
        version = None
        prefix = ""
        detail = self.t("input_" + command)
        if command in CONCERT_INPUTS:
            assert context is not None and context.group_id is not None
            await self.groups.access(context.group_id, admin=True)
        if context is not None and context.group_id is not None:
            group = await self.store.get(Group, context.group_id)
            prefix = group.name + "\n"
            if command in VERSION_INPUTS:
                require(context.concert_id is not None, "select_concert")
                assert context.concert_id is not None
                snapshot, version, approved = await self.setlists.read(
                    context.group_id, context.concert_id, private=self.private
                )
                require(not approved, "approved")
                if command in ("move", "remove"):
                    require(any(block["items"] for block in snapshot["blocks"]), "empty_setlist")
                concert = await self.store.get(Concert, context.concert_id)
                prefix += concert_label(concert, self.language) + "\n"
                context.observed_version = version
            if command in ("edit", "copy", "alias") and context.selected_song_id is not None:
                song = await self.catalog.song(context.group_id, context.selected_song_id)
                detail = self.t(
                    command + "_hint",
                    title=song.title,
                    time=clock(song.duration_seconds),
                    bpm=song.bpm or "-",
                    lights=song.lights or "-",
                    comment=song.comment or "-",
                )
        if command in ("edit_bpm", "edit_lights", "edit_comment"):
            assert context is not None and context.group_id is not None
            require(context.selected_song_id is not None, "song_input")
            assert context.selected_song_id is not None
            editing_song = await self.catalog.song(context.group_id, context.selected_song_id)
            prefix += self._song_card(editing_song) + "\n"
        if command in CONCERT_INPUTS:
            assert context is not None and context.concert_id is not None
            prefix += (
                concert_label(await self.store.get(Concert, context.concert_id), self.language)
                + "\n"
            )
        prompt = (
            prefix
            + (
                ""
                if command in CONCERT_INPUTS
                or command in ("concert", "edit_bpm", "edit_lights", "edit_comment")
                else self.t("input_prompt", command=command)
            )
            + "\n\n"
            + detail
            + "\n\n"
            + self.t("input_cancel")
        )
        row = await self.inputs.create(command, context, prompt, version=version)
        return Response(
            prompt,
            force_reply=True,
            command_input_id=row.id,
            cancel_action=f"input_cancel {row.id.hex}",
            cancel_label=self.t("cancel_button"),
        )

    async def command_input(self, row: Confirmation, value: str) -> Response:
        target = self.inputs.data(row)
        if target.get("group_id") and target.get("concert_id"):
            self.reply_target = UUID(target["group_id"]), UUID(target["concert_id"])
        data = await self.inputs.restore(row)
        require(bool(value.strip()), "input")
        if value.lstrip().startswith("/"):
            response = await self.execute(value)
        else:
            join = "\n" if data["command"] in ("song", "import", "add") and "\n" in value else " "
            response = await self.execute(f"/{data['command']}" + join + value.strip())
        if data["command"] == "add" and any(
            action.startswith("offer_add ") for _, action in response.buttons
        ):
            response.text = (
                self.t("input_add_ambiguous")
                + "\n"
                + "\n".join(label for label, _ in response.buttons)
            )
            response.buttons = []
            response.input_rejected = True
        if response.input_rejected:
            row.expires_at = now() + timedelta(minutes=10)
            if response.buttons:
                response.deliveries.append(
                    (
                        self.chat_id,
                        Response(
                            data["prompt"],
                            monospace=bool(data.get("monospace_body")),
                            monospace_body=data.get("monospace_body"),
                            force_reply=True,
                            command_input_id=row.id,
                            cancel_action=f"input_cancel {row.id.hex}",
                            cancel_label=self.t("cancel_button"),
                        ),
                    )
                )
            else:
                response.text += "\n\n" + data["prompt"]
                response.monospace = bool(data.get("monospace_body"))
                response.monospace_body = data.get("monospace_body")
                response.force_reply = True
                response.command_input_id = row.id
                response.cancel_action = f"input_cancel {row.id.hex}"
                response.cancel_label = self.t("cancel_button")
        else:
            if self.private and data["command"] == "picker_pause":
                response.delete_message_ids.extend(data.get("transient_message_ids", []))
            await self.store.delete(row)
        return response

    async def _template_context(self, template_id: UUID) -> Context:
        template = await self.templates.load(template_id)
        self.reply_target = template.group_id, template.concert_id
        context = await self.groups.context(self.chat_id, self.private)
        context.group_id, context.concert_id = template.group_id, template.concert_id
        context.observed_version = template.version
        return context

    async def _suggestion_parts(self, template_id: UUID) -> tuple[str, Response, Context, int]:
        template = await self.templates.load(template_id)
        context = await self._template_context(template_id)
        assert context.group_id is not None and context.concert_id is not None
        snapshot, version, approved = await self.setlists.read(
            context.group_id, context.concert_id, private=self.private
        )
        require(version == context.observed_version, "conflict")
        require(not approved, "approved")
        comment = self.t("suggestion_prompt")
        if self.private:
            comment += "\n" + self.t("suggestion_private")
        titles = list(template.titles)
        if not titles:
            concert = await self.store.get(Concert, context.concert_id)
            suggestion = await suggested_titles(self.store, self.user, concert)
            titles = suggestion.titles
            for label, names in (
                ("suggested_new", suggestion.new),
                ("suggested_popular", suggestion.popular),
            ):
                if names:
                    comment += "\n\n" + self.t(label) + "\n" + "\n".join(names)
        catalog = {
            normalize(song.title): song
            for song in await self.store.find(Song, group_id=context.group_id)
        }
        entries = []
        for title in titles:
            pause = parse_pause(title)
            song = catalog.get(normalize(title))
            if pause is not None:
                entries.append({"kind": "pause", "duration_seconds": pause, "pause_before": 0})
            elif song is not None:
                entries.append({"duration_seconds": song.duration_seconds, "pause_before": None})
        duration = totals(
            {
                "default_pause_seconds": snapshot["default_pause_seconds"],
                "blocks": [{"items": entries, "break_seconds": 0}],
            }
        )[2]
        body = "\n".join(titles)
        heading = self.t("suggested_duration", time=clock(duration))
        copied = Response(
            heading + "\n\n" + (body or self.t("selection_no_songs")),
            monospace=bool(body),
            monospace_body=body or None,
        )
        copied.suggestion_template_id = template_id
        return comment, copied, context, version

    async def _suggestion_prompt(self, template_id: UUID) -> Response:
        comment, copied, context, version = await self._suggestion_parts(template_id)
        assert context.group_id is not None and context.concert_id is not None
        if self.private:
            await self.inputs.clear()
            previous = await self.selection.active()
            previous_data = self.selection.data(previous) if previous else {}
            session = await self.selection.create(
                context.group_id, context.concert_id, version, template_id
            )
            copied.suggestion_template_id = template_id
            response = Response(
                comment + "\n\n" + self.t("selection_method"),
                [
                    (self.t("selection_text_button"), f"pick_text {session.id.hex}"),
                    (self.t("selection_pick_button"), f"pick_start {session.id.hex}"),
                    (self.t("cancel_button"), f"pick_cancel {session.id.hex}"),
                ],
                selection_id=session.id,
                remove_keyboard=previous_data.get("stage") == "choosing",
                keyboard_text=self.t("selection_keyboard_closed"),
                previous_keyboard_id=previous_data.get("keyboard_message_id"),
            )
        else:
            row = await self.inputs.create(
                "suggestion",
                context,
                copied.text,
                version=version,
                monospace_body=copied.monospace_body,
            )
            copied.force_reply = True
            copied.command_input_id = row.id
            copied.cancel_action = f"input_cancel {row.id.hex}"
            copied.cancel_label = self.t("cancel_button")
            response = Response(comment)
        response.deliveries.append((self.chat_id, copied))
        return response

    def _group_name_prompt(self, session: Confirmation, *, invalid: bool = False) -> Response:
        return Response(
            (self.t("group_name") + "\n\n" if invalid else "") + self.t("group_creation_prompt"),
            force_reply=True,
            group_creation_id=session.id,
            cancel_action=f"group_create_cancel {session.id.hex}",
            cancel_label=self.t("cancel_button"),
        )

    async def group_name_input(self, session: Confirmation, value: str) -> Response:
        session = await self.groups.creation_session(self.chat_id, session.id)
        require(json.loads(session.command)["stage"] == "name", "group_creation_expired")
        await self.groups.check_creation_chat(self.chat_id)
        if not 0 < len(value.strip()) <= 200:
            return self._group_name_prompt(
                await self.groups.begin_creation(self.chat_id), invalid=True
            )
        session = await self.groups.begin_creation(self.chat_id, value)
        return Response(
            self.t("group_creation_review", name=value.strip()),
            [
                (self.t("group_creation_yes"), f"group_create_yes {session.id.hex}"),
                (self.t("group_creation_edit"), f"group_create_edit {session.id.hex}"),
                (self.t("group_creation_cancel"), f"group_create_cancel {session.id.hex}"),
            ],
        )

    def _selection_view(
        self, session: Confirmation, data: dict[str, Any], *, keyboard: bool = False
    ) -> Response:
        roster = {s["id"]: s for s in data["songs"]}
        titles = [
            clock(i["pause_seconds"]) if isinstance(i, dict) else roster[i]["title"]
            for i in data["selected"]
        ]
        reviewing = data["stage"] == "review"
        live_titles = [
            ("пауза " if self.language == "ru" else "pause ") + title
            if isinstance(entry, dict)
            else title
            for entry, title in zip(data["selected"], titles, strict=True)
        ]
        numbers = entry_labels(
            [{"kind": "pause"} if isinstance(entry, dict) else {} for entry in data["selected"]]
        )
        body = "\n".join(
            titles
            if reviewing
            else [f"{number}. {title}" for number, title in zip(numbers, live_titles, strict=True)]
        )
        entries = [
            {"kind": "pause", "duration_seconds": entry["pause_seconds"], "pause_before": 0}
            if isinstance(entry, dict)
            else {"duration_seconds": roster[entry]["duration_seconds"], "pause_before": None}
            for entry in data["selected"]
        ]
        duration = totals(
            {
                "default_pause_seconds": data.get("default_pause_seconds", 5),
                "blocks": [{"items": entries, "break_seconds": 0}],
            }
        )[2]
        planned = data.get("planned_duration_seconds")
        response = Response(
            self.t("selection_title")
            + " · "
            + clock(duration)
            + "\n"
            + self.t(
                "selection_planned",
                time=short_duration(planned, self.language, minutes_only=True)
                if planned is not None
                else self.t("unknown_duration"),
            )
            + "\n\n"
            + (body or self.t("selection_no_songs"))
            + "\n\n"
            + self.t("selection_count", count=len(titles)),
            selection_id=session.id,
            edit_message_ids=list(data["panel_ids"]),
            previous_keyboard_id=data["keyboard_message_id"],
        )
        if reviewing:
            response.buttons = [
                (self.t("selection_confirm"), f"pick_confirm {session.id.hex}"),
                (self.t("selection_cancel"), f"pick_cancel {session.id.hex}"),
            ]
            response.button_columns = 2
            response.remove_keyboard = True
            response.keyboard_text = self.t("selection_review")
        elif keyboard:
            start = data["page"] * PAGE_SIZE
            labels = [s["label"] for s in data["songs"][start : start + PAGE_SIZE]]
            rows = [
                [self.t("selection_finish"), self.t("selection_pause")],
                [self.t("selection_remove_last")],
            ]
            rows += [labels[i : i + 2] for i in range(0, len(labels), 2)]
            navigation = []
            if data["page"]:
                navigation.append(self.t("selection_prev"))
            if start + PAGE_SIZE < len(data["songs"]):
                navigation.append(self.t("selection_next"))
            if navigation:
                rows.append(navigation)
            rows.append([self.t("selection_cancel")])
            response.reply_keyboard = rows
            response.keyboard_text = self.t(
                "selection_page",
                page=data["page"] + 1,
                pages=(len(data["songs"]) + PAGE_SIZE - 1) // PAGE_SIZE,
            )
        return response

    async def _selection_action(self, action: str, identifier: UUID) -> Response:
        if action == "pick_cancel":
            # Cancellation must still work after the draft/catalog/context has changed.
            sessions = await self.store.find(
                Confirmation, id=identifier, user_id=self.user.id, chat_id=self.chat_id
            )
            require(bool(sessions), "selection_expired")
            session = sessions[0]
            data = self.selection.data(session)
            active = await self.selection.active()
            require(
                data.get("kind") == "song_selection"
                and active is not None
                and active.id == session.id,
                "selection_expired",
            )
            await self.store.delete(session)
            pending_cleanup = []
            for pending_input in await self.store.find(
                Confirmation, user_id=self.user.id, chat_id=self.chat_id
            ):
                input_data = self.inputs.data(pending_input)
                if (
                    input_data.get("kind") == "command_input"
                    and input_data.get("command") == "picker_pause"
                ):
                    pending_cleanup.extend(input_data.get("transient_message_ids", []))
            await self.inputs.clear()
            context = await self.groups.context(self.chat_id, True)
            was_choosing = data["stage"] == "choosing"
            response = Response(self.t("selection_cancelled"), remove_keyboard=was_choosing)
            try:
                if context.group_id == UUID(data["group_id"]) and context.concert_id == UUID(
                    data["concert_id"]
                ):
                    response = await self._show(context)
                    response.text = self.t("selection_cancelled") + "\n\n" + response.text
                    response.remove_keyboard = was_choosing
            except AppError:
                pass
            response.previous_keyboard_id = data["keyboard_message_id"]
            response.keyboard_text = self.t("selection_keyboard_closed")
            response.delete_message_ids.extend(pending_cleanup)
            return response
        if action == "pick_start":
            session, data = await self.selection.start(identifier)
            return self._selection_view(session, data, keyboard=True)
        session, data = await self.selection.load(identifier)
        if action == "pick_text":
            require(data["stage"] == "method", "selection_expired")
            data["stage"] = "text"
            self.selection.write(session, data)
            comment, response, context, version = await self._suggestion_parts(
                UUID(data["template_id"])
            )
            row = await self.inputs.create(
                "suggestion",
                context,
                response.text,
                version=version,
                monospace_body=response.monospace_body,
            )
            response.force_reply = True
            response.command_input_id = row.id
            response.suggestion_template_id = UUID(data["template_id"])
            response.selection_id = session.id
            response.cancel_action = f"input_cancel {row.id.hex}"
            response.cancel_label = self.t("cancel_button")
            return response
        await self.selection.save(identifier)
        context = await self.groups.context(self.chat_id, True)
        response = await self._show(context)
        response.text = self.t("selection_saved") + "\n\n" + response.text
        return response

    async def selection_input(self, value: str, message_id: int | None) -> Response | None:
        session = await self.selection.active()
        if session is None:
            return None
        session, data = await self.selection.load(session.id)
        if data["stage"] in ("method", "text"):
            return await self.execute(
                "/suggestion\n" + value, reply_template_id=UUID(data["template_id"])
            )
        if data["stage"] != "choosing":
            return None
        if (
            message_id is not None
            and data["keyboard_message_id"] is not None
            and message_id <= data["keyboard_message_id"]
        ):
            return None
        keyboard = False
        if value == self.t("selection_cancel"):
            response = await self._selection_action("pick_cancel", session.id)
            response.delete_input = True
            return response
        if value == self.t("selection_pause"):
            require(len(data["selected"]) < 200, "song_list_input")
            data["stage"] = "pause"
            self.selection.write(session, data)
            context = await self.groups.context(self.chat_id, True)
            response = await self._request_input("picker_pause", context)
            response.selection_id = session.id
            response.remove_keyboard = True
            response.previous_keyboard_id = data["keyboard_message_id"]
            response.keyboard_text = self.t("selection_pause_prompt")
            response.delete_input = True
            return response
        if value == self.t("selection_finish"):
            require(bool(data["selected"]), "empty_setlist")
            data["stage"] = "review"
        elif value == self.t("selection_remove_last"):
            if data["selected"]:
                data["selected"].pop()
        elif value in (self.t("selection_prev"), self.t("selection_next")):
            page = data["page"] + (-1 if value == self.t("selection_prev") else 1)
            require(0 <= page * PAGE_SIZE < len(data["songs"]), "selection_expired")
            data["page"] = page
            keyboard = True
        else:
            start = data["page"] * PAGE_SIZE
            matches = [
                s
                for s in data["songs"][start : start + PAGE_SIZE]
                if s["label"] == value or s.get("previous_label") == value
            ]
            if not matches:
                return Response(self.t("selection_unknown"))
            require(len(matches) == 1, "selection_ambiguous")
            require(len(data["selected"]) < 200, "song_list_input")
            data["selected"] = [*data["selected"], matches[0]["id"]]
        self.selection.write(session, data)
        response = self._selection_view(session, data, keyboard=keyboard)
        response.delete_input = True
        return response

    async def _trash_list(self) -> Response:
        memberships = await self.store.find(
            Membership, user_id=self.user.id, role="admin", status="active"
        )
        buttons, lines = [], [self.t("trash_title")]
        for membership in memberships:
            group = await self.store.get(Group, membership.group_id)
            if group.deleted_at is not None:
                if not self.private and group.deleted_chat_id != self.chat_id:
                    continue
                if group.deleted_at + RETENTION > now():
                    lines.append(
                        self.t(
                            "trash_group",
                            name=group.name,
                            until=(group.deleted_at + RETENTION).date().isoformat(),
                        )
                    )
                    buttons.append(
                        (
                            self.t("restore_group_button", name=group.name),
                            f"restore_group {group.id.hex} {group.lifecycle_version}",
                        )
                    )
            else:
                bindings = await self.store.find(ChatBinding, group_id=group.id)
                if not self.private and not any(b.chat_id == self.chat_id for b in bindings):
                    continue
                for concert in await self.store.find(Concert, group_id=group.id):
                    if concert.deleted_at is not None and concert.deleted_at + RETENTION > now():
                        lines.append(
                            self.t(
                                "trash_concert",
                                name=concert_label(concert, self.language),
                                until=(concert.deleted_at + RETENTION).date().isoformat(),
                            )
                        )
                        buttons.append(
                            (
                                self.t(
                                    "restore_concert_button",
                                    name=concert_label(concert, self.language),
                                ),
                                f"restore_concert {concert.id.hex} {concert.lifecycle_version}",
                            )
                        )
        return Response("\n\n".join(lines) if buttons else self.t("trash_empty"), buttons)

    async def _resolve_song(
        self, group_id: UUID, selector: str, context: Context, *, archived: bool = False
    ) -> Song:
        if not selector and context.selected_song_id:
            return await self.catalog.song(group_id, context.selected_song_id)
        try:
            identifier = UUID(selector)
        except ValueError:
            songs = await self.catalog.list_songs(group_id, archived=archived)
            matches = [song for song in songs if normalize(song.title) == normalize(selector)]
            require(bool(matches), "not_found")
            require(len(matches) == 1, "song_choose")
            return matches[0]
        return await self.catalog.song(group_id, identifier)

    async def _import_preview(self, context: Context, job: CatalogImport) -> Response:
        plan = await self.importer.preview(job.group_id, job.id)
        group = await self.store.get(Group, job.group_id)
        lines = [
            group.name,
            self.t(
                "import_summary",
                new=len(plan.new),
                duplicates=len(plan.duplicates),
                errors=len(plan.errors),
            ),
        ]
        if plan.parsed.error:
            lines.append(self.t(plan.parsed.error))
        for data in plan.new:
            lines.append(self.t("import_new") + ": " + self._song_card(data))
        for row in plan.duplicates:
            lines.append(f"{row.title}: {self.t('import_duplicate')}")
        for row in plan.errors:
            lines.append(
                self.t(
                    "import_row_error", number=row.number, title=row.title, error=self.t(row.error)
                )
            )
        lines.append(self.t("import_note"))
        preview = "\n\n".join(lines)
        if plan.new and not plan.errors and not plan.parsed.error:
            response = await self.confirm(
                context, f"commit_import {job.id} {plan.fingerprint}", preview
            )
        else:
            if not plan.new and not plan.errors and not plan.parsed.error:
                preview += "\n" + self.t("import_none")
            response = Response(preview, [(self.t("cancel_button"), f"import_cancel {job.id.hex}")])
        response.buttons.insert(-1, (self.t("import_map"), f"map_start {job.id.hex}"))
        response.input_rejected = bool(plan.errors or plan.parsed.error)
        return response

    def _mapping_step(self, job: CatalogImport) -> Response:
        assert job.mapping is not None
        step = len(job.mapping)
        table = read_table(job.source, job.has_header)
        buttons = []
        for index, header in enumerate(table.headers):
            if index in job.mapping.values():
                continue
            label = (
                header or table.rows[1 if table.has_header and len(table.rows) > 1 else 0][index]
            )
            buttons.append((f"{index + 1}. {label[:60]}", f"map_col {job.id.hex} {step} {index}"))
        if step > 1:
            buttons.append((self.t("import_skip"), f"map_col {job.id.hex} {step} -1"))
        buttons.append((self.t("cancel_button"), f"import_cancel {job.id.hex}"))
        return Response(
            self.t("import_column_prompt", field=self.t("import_field_" + FIELDS[step])), buttons
        )

    async def _card_options(self, data: dict[str, Any]) -> list[tuple[str, str]]:
        options = [(label, command) for label, command in data["options"]]
        membership = (
            await self.store.find(
                Membership, group_id=UUID(data["group_id"]), user_id=self.user.id, status="active"
            )
        )[0]
        if membership.role == "admin":
            options.append(
                (self.t("delete_concert_button"), f"delete_concert {data['concert_id']}")
            )
        return options

    async def _menu_buttons(
        self, card: Confirmation, data: dict[str, Any], level: str, page: int = 0
    ) -> list[tuple[str, str]]:
        require(level in ("main", "options", "variants"), "input")
        key = card.id.hex
        if level == "main":
            proposal = (
                f"copy_list {UUID(data['template_id']).hex}"
                if data["template_id"]
                else f"slact {key} reopen"
            )
            buttons = [
                (self.t("copy_variant"), proposal),
                (self.t("menu_options"), f"slmenu {key} options"),
            ]
            if not data["private_chat"]:
                buttons.append(
                    (
                        self.t("menu_edit" if data["approved"] else "menu_approve"),
                        f"slact {key} {'reopen' if data['approved'] else 'approve'}",
                    )
                )
            if data["approved"]:
                exports = [
                    (label, action)
                    for label, action in data["options"]
                    if action.startswith("export_menu ")
                ]
                buttons = exports + buttons[1:]
            if data["personal"]:
                buttons = [
                    (self.t("draft_publish"), f"sldraft {key} publish"),
                    (self.t("draft_edit"), proposal),
                    (self.t("menu_options"), f"slmenu {key} options"),
                ]
            return buttons
        variants = (
            []
            if data["personal"]
            else [
                r
                for r in await self.setlists.history(
                    UUID(data["group_id"]), UUID(data["concert_id"])
                )
                if r.suggestion_number is not None
            ]
        )
        if level == "options":
            buttons = [(self.t("export_settings"), f"slformat {key} main")]
            member = (
                await self.store.find(
                    Membership,
                    group_id=UUID(data["group_id"]),
                    user_id=self.user.id,
                    status="active",
                )
            )[0]
            if member.role == "admin":
                buttons.append(
                    (self.t("concert_settings"), f"concert_settings {data['concert_id']}")
                )
            if variants:
                buttons.append((self.t("menu_variants"), f"slmenu {key} variants"))
            for index, (label, action) in enumerate(await self._card_options(data)):
                if action.startswith("delete_concert "):
                    buttons.append((label, f"slopt {key} {index}"))
            if self.private or member.role == "admin":
                buttons.append((self.t("cleanup_button"), "cleanup_chat"))
            buttons.extend(
                [(self.t("support_button"), "support"), (self.t("menu_back"), f"slmenu {key} main")]
            )
            return buttons
        require(page >= 0, "input")
        page = min(page, max(0, (len(variants) - 1) // 20))
        buttons = [
            (await self._revision_label(r), f"slrev {key} {r.number}")
            for r in variants[page * 20 : (page + 1) * 20]
        ]
        if page:
            buttons.append(("◀️", f"slmenu {key} variants {page - 1}"))
        if (page + 1) * 20 < len(variants):
            buttons.append(("▶️", f"slmenu {key} variants {page + 1}"))
        return buttons + [(self.t("menu_back"), f"slmenu {key} options")]

    async def _card_context(self, data: dict[str, Any]) -> Context:
        context = await self.groups.context(self.chat_id, self.private)
        context.group_id, context.concert_id = UUID(data["group_id"]), UUID(data["concert_id"])
        context.observed_version = data["version"]
        return context

    async def _refresh_card(self, row: Confirmation, data: dict[str, Any]) -> Response:
        context = await self._card_context(data)
        response = await self._show(context, shared=data["shared"])
        response.edit_message_ids = list(data["message_ids"])
        self.menus.write(row, {**data, "closed": True})
        return response

    async def refresh_shared_card(self, row: Confirmation) -> Response:
        """RU: Фоновое обновление не переключает выбранный пользователем концерт.

        EN: Reuse normal rendering with a transient context; never read private drafts.
        """
        data = self.menus.data(row)
        require(not self.private and not data["personal"] and data["owner_id"] is None, "forbidden")
        await self.menus.validate(data)
        context = Context(
            user_id=self.user.id,
            chat_id=self.chat_id,
            group_id=UUID(data["group_id"]),
            concert_id=UUID(data["concert_id"]),
        )
        response = await self._show(context, shared=data["shared"])
        response.edit_message_ids = list(data["message_ids"])
        self.menus.write(row, {**data, "closed": True})
        return response

    async def _menu_action(self, command: str, args: str, *, confirmed: bool = False) -> Response:
        values = args.split()
        require(len(values) >= 2, "input")
        identifier = UUID(values[0])
        if command == "slreturn":
            rows = await self.store.find(
                Confirmation, id=identifier, user_id=self.user.id, chat_id=self.chat_id
            )
            require(bool(rows), "menu_expired")
            challenge = rows[0]
            challenge_data = self.menus.data(challenge)
            require(
                challenge_data.get("kind") == "menu_reopen"
                and challenge.expires_at > now()
                and values[1] in ("yes", "no"),
                "menu_expired",
            )
            await self.store.delete(challenge)
            return await self._menu_action(
                "slact" if values[1] == "yes" else "slmenu",
                f"{challenge_data['card_id']} {'return' if values[1] == 'yes' else 'main'}",
                confirmed=True,
            )
        if command == "slreq":
            require(self.private and values[1] in ("yes", "no"), "confirmation")
            data = await self.menus.decide(identifier, values[1] == "yes")
            response = Response(
                self.t("request_accepted" if values[1] == "yes" else "request_rejected")
            )
            if values[1] == "yes":
                source = Controller(
                    self.store,
                    self.user,
                    data["source_chat_id"],
                    private=False,
                    bot_username=self.bot_username,
                )
                source.language = (await self.store.get(Group, UUID(data["group_id"]))).language
                rows = await self.store.find(Confirmation, id=UUID(data["card_id"]))
                if rows:
                    refreshed = await source._refresh_card(rows[0], source.menus.data(rows[0]))
                    response.deliveries.append((data["source_chat_id"], refreshed))
            group = await self.store.get(Group, UUID(data["group_id"]))
            self.store.add(
                Outbox(
                    group_id=group.id,
                    concert_id=UUID(data["concert_id"]),
                    chat_id=data["source_chat_id"],
                    text=tr(
                        group.language,
                        "request_result",
                        action=tr(
                            group.language,
                            "request_approve" if data["operation"] == "approve" else "request_edit",
                        ),
                        result=tr(
                            group.language,
                            "request_accepted" if values[1] == "yes" else "request_rejected",
                        ),
                    ),
                )
            )
            return response
        row, data = await self.menus.load(identifier, self.source_message_id)
        if command == "slformat":
            config = settings(self.user.export_settings)
            if values[1] in ("font", "size", "align"):
                require(len(values) == 3, "input")
                config[values[1]] = int(values[2]) if values[1] == "size" else values[2]
                self.user.export_settings = settings(config)
            buttons = [
                (
                    self.t("export_font", value=FONTS[config["font"]]),
                    f"slformat {identifier.hex} fonts",
                ),
                (self.t("export_size", value=config["size"]), f"slformat {identifier.hex} sizes"),
                (
                    self.t("export_align", value=self.t("align_" + config["align"])),
                    f"slformat {identifier.hex} alignment",
                ),
            ]
            if data["approved"]:
                export_actions = [
                    (label, action)
                    for label, action in data["options"]
                    if action.startswith("export_menu ")
                ]
                buttons = export_actions + buttons
            if values[1] == "fonts":
                buttons = [
                    (name, f"slformat {identifier.hex} font {key}") for key, name in FONTS.items()
                ]
            elif values[1] == "sizes":
                buttons = [
                    (f"{size} pt", f"slformat {identifier.hex} size {size}")
                    for size in range(24, 41)
                ]
            elif values[1] == "alignment":
                buttons = [
                    (self.t("align_" + align), f"slformat {identifier.hex} align {align}")
                    for align in ("left", "center")
                ]
            buttons.append(
                (
                    self.t("menu_back"),
                    f"slmenu {identifier.hex} options"
                    if values[1] not in ("fonts", "sizes", "alignment")
                    else f"slformat {identifier.hex} main",
                )
            )
            return Response(
                "", buttons, markup_only=True, edit_message_ids=list(data["message_ids"])
            )
        if command == "sldraft":
            require(data["personal"] and self.private and values[1] == "publish", "private_only")
            await self.menus.validate(data, fresh=True)
            await self._card_context(data)
            # RU: Двойной клик не должен дважды рассылать один личный вариант.
            # EN: Consume this card's publish action once; other card actions stay usable.
            require(not data.get("published"), "already_done")
            response = await self.execute("publish")
            self.menus.write(row, {**data, "published": True})
            return response
        if command == "slmenu":
            return Response(
                "",
                await self._menu_buttons(
                    row, data, values[1], int(values[2]) if len(values) > 2 else 0
                ),
                markup_only=True,
                edit_message_ids=list(data["message_ids"]),
            )
        if command == "slrev":
            context = await self._card_context(data)
            _, context.observed_version, _ = await self.setlists.read(
                UUID(data["group_id"]), UUID(data["concert_id"]), private=self.private
            )
            history = await self.setlists.history(UUID(data["group_id"]), UUID(data["concert_id"]))
            revisions = [
                r for r in history if r.number == int(values[1]) and r.suggestion_number is not None
            ]
            require(bool(revisions), "menu_expired")
            return await self.execute(f"revision {revisions[0].id}")
        if command == "slopt":
            options = await self._card_options(data)
            index = int(values[1])
            require(0 <= index < len(options), "input")
            action = options[index][1]
            if not action.startswith("export_menu "):
                await self.menus.validate(data, fresh=True)
            await self._card_context(data)
            return await self.execute(action)
        require(
            command == "slact" and values[1] in ("approve", "reopen", "return", "cancel"), "input"
        )
        await self.menus.validate(data, fresh=True)
        require(not data["private_chat"], "group_only")
        if values[1] == "cancel":
            return Response(
                "",
                await self._menu_buttons(row, data, "main"),
                markup_only=True,
                edit_message_ids=list(data["message_ids"]),
            )
        operation = "approve" if values[1] == "approve" else "reopen"
        require(values[1] != "return" or confirmed, "confirmation")
        require(data["approved"] == (operation == "reopen"), "menu_expired")
        membership = (
            await self.store.find(
                Membership, group_id=UUID(data["group_id"]), user_id=self.user.id, status="active"
            )
        )[0]
        if membership.role != "admin":
            require(values[1] != "return", "admin_only")
            request, admin, created = await self.menus.request(row, data, operation)
            if created:
                group = await self.store.get(Group, UUID(data["group_id"]))
                concert = await self.store.get(Concert, UUID(data["concert_id"]))
                name = await self.groups.name(UUID(data["group_id"]))
                header = tr(
                    admin.language,
                    "request_admin",
                    name=name,
                    group=group.name,
                    action=tr(
                        admin.language,
                        "request_approve" if operation == "approve" else "request_edit",
                    ),
                )
                self.store.add(
                    Outbox(
                        group_id=group.id,
                        concert_id=concert.id,
                        chat_id=admin.telegram_id,
                        text=header
                        + "\n\n"
                        + render_setlist(
                            data["snapshot"],
                            concert,
                            admin.language,
                            data["version"],
                            approved=data["approved"],
                        ),
                        buttons=[
                            [tr(admin.language, "request_yes"), f"slreq {request.id.hex} yes"],
                            [tr(admin.language, "request_no"), f"slreq {request.id.hex} no"],
                        ],
                    )
                )
                self.store.add(
                    Outbox(
                        group_id=group.id,
                        concert_id=concert.id,
                        chat_id=self.chat_id,
                        text=self.t(
                            "request_group",
                            name=name,
                            concert=concert_label(concert, self.language),
                            action=self.t(
                                "request_approve" if operation == "approve" else "request_edit"
                            ),
                            version=data["version"],
                        ),
                    )
                )
            return Response(
                "", callback_notice=self.t("request_sent" if created else "request_duplicate")
            )
        if operation == "reopen" and values[1] != "return":
            for previous in await self.store.find(
                Confirmation, user_id=self.user.id, chat_id=self.chat_id
            ):
                previous_data = self.menus.data(previous)
                if previous_data.get("kind") == "menu_reopen" and previous_data.get(
                    "card_id"
                ) == str(row.id):
                    await self.store.delete(previous)
            challenge = Confirmation(
                user_id=self.user.id,
                chat_id=self.chat_id,
                command=json.dumps({"kind": "menu_reopen", "card_id": str(row.id)}),
                expires_at=now() + timedelta(minutes=10),
            )
            self.store.add(challenge)
            return Response(
                self.t("menu_reopen_prompt"),
                [
                    (self.t("menu_return_yes"), f"slreturn {challenge.id.hex} yes"),
                    (self.t("menu_cancel"), f"slreturn {challenge.id.hex} no"),
                ],
                markup_only=True,
                edit_message_ids=list(data["message_ids"]),
            )
        service = self.setlists.approve if operation == "approve" else self.setlists.reopen
        await service(UUID(data["group_id"]), UUID(data["concert_id"]), data["version"])
        return await self._refresh_card(row, data)

    async def _show(self, context: Context, *, shared: bool = False) -> Response:
        assert context.group_id is not None and context.concert_id is not None
        personal = self.private and not shared
        snapshot, version, approved = await self.setlists.read(
            context.group_id, context.concert_id, private=personal
        )
        if not shared:
            context.observed_version = version
        concert = await self.store.get(Concert, context.concert_id)
        group = await self.store.get(Group, context.group_id)
        buttons = []
        template_id = None
        if not approved or self.private:
            edit_version = version
            if shared and self.private:
                _, edit_version, _ = await self.setlists.read(
                    context.group_id, context.concert_id, private=True
                )
            template = self.templates.create(
                context.group_id,
                context.concert_id,
                snapshot,
                private=self.private,
                version=edit_version,
                group_epoch=group.lifecycle_version,
                concert_epoch=concert.lifecycle_version,
            )
            template_id = template.id
        buttons += await self._export_buttons(
            context.group_id,
            context.concert_id,
            snapshot,
            version,
            approved=approved,
            back="shared" if shared else "setlist",
        )
        text = (
            group.name
            + "\n"
            + render_setlist(
                snapshot, concert, self.language, version, private=personal, approved=approved
            )
        )
        card = self.menus.create(
            {
                "group_id": str(group.id),
                "concert_id": str(concert.id),
                "group_epoch": group.lifecycle_version,
                "concert_epoch": concert.lifecycle_version,
                "owner_id": str(self.user.id) if self.private else None,
                "personal": personal,
                "private_chat": self.private,
                "shared": shared,
                "version": version,
                "approved": approved,
                "snapshot": snapshot,
                "template_id": str(template_id) if template_id else None,
                "options": buttons,
            }
        )
        for _, callback in buttons:
            if callback.startswith("export_menu "):
                export = await self.exports.load(UUID(callback.split()[1]))
                export.payload = {
                    **export.payload,
                    "menu_card_id": str(card.id),
                    "back": f"slmenu {card.id.hex} options",
                }
        return Response(
            text,
            await self._menu_buttons(card, self.menus.data(card), "main"),
            monospace=True,
            monospace_body=text,
            suggestion_template_id=template_id,
            menu_card_id=card.id,
        )

    def _position_buttons(
        self, row: Confirmation, data: dict[str, Any], items: list[dict[str, Any]]
    ) -> Response:
        labels = entry_labels(items)
        return Response(
            self.t("choose_position"),
            [
                (
                    f"{labels[index]}. {item['title']} · {clock(item['duration_seconds'])}",
                    f"position {row.id.hex} {index}",
                )
                for index, item in enumerate(items)
            ]
            + [(self.t("cancel_button"), f"position_cancel {row.id.hex}")],
        )
