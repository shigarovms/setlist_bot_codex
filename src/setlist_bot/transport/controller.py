import json
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any
from uuid import UUID

from setlist_bot.application.backup import Backup
from setlist_bot.application.catalog import SONG_FIELDS, Catalog
from setlist_bot.application.exports import Exports
from setlist_bot.application.groups import Groups
from setlist_bot.application.importing import Importer
from setlist_bot.application.ports import Store
from setlist_bot.application.setlist_menus import CARD, REQUEST, SetlistMenus
from setlist_bot.application.setlists import Setlists
from setlist_bot.application.song_selection import PAGE_SIZE, SongSelection
from setlist_bot.application.templates import Templates
from setlist_bot.application.trash import RETENTION, Trash
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
    Song,
    Submission,
    User,
    now,
)
from setlist_bot.domain.importing import FIELDS, SPACED_SEPARATOR, TIME_CELL, read_table
from setlist_bot.domain.rules import (
    AppError,
    SongListError,
    clock,
    duration,
    normalize,
    require,
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
    suggestion_template_id: UUID | None = None
    force_reply: bool = False
    selection_id: UUID | None = None
    reply_keyboard: list[list[str]] | None = None
    keyboard_text: str = ""
    remove_keyboard: bool = False
    keyboard_message_id: int | None = None
    previous_keyboard_id: int | None = None
    edit_message_ids: list[int] = field(default_factory=list)
    delete_input: bool = False
    button_columns: int = 1
    group_creation_id: UUID | None = None
    menu_card_id: UUID | None = None
    markup_only: bool = False
    callback_notice: str = ""
    deliveries: list[tuple[int, "Response"]] = field(default_factory=list)


class Controller:
    def __init__(
        self, store: Store, user: User, chat_id: int, *, private: bool, chat_admin: bool = False
    ) -> None:
        self.store, self.user, self.chat_id = store, user, chat_id
        self.private, self.chat_admin = private, chat_admin
        self.language = user.language
        self.groups = Groups(store, user)
        self.catalog = Catalog(store, user)
        self.setlists = Setlists(store, user)
        self.selection = SongSelection(store, user, chat_id)
        self.importer = Importer(store, user, chat_id)
        self.trash = Trash(store, user)
        self.templates = Templates(store, user, chat_id)
        self.exports = Exports(store, user, chat_id)
        self.menus = SetlistMenus(store, user, chat_id)
        self.source_message_id: int | None = None
        self.reply_target: tuple[UUID, UUID] | None = None

    def t(self, key: str, **values: Any) -> str:
        return tr(self.language, key, **values)

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
            (self.t("cancel_button"), "cancel"),
        ]
        return Response(
            self.t("confirm") + "\n" + preview,
            buttons,
            monospace=monospace,
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
        if command in ("slmenu", "slact", "slopt", "slrev", "slreq", "slreturn"):
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
        if command in ("start", "help"):
            return Response(self.t("welcome" if command == "start" else "help"))
        if command == "language":
            require(args in ("ru", "en"), "input")
            self.user.language = args
            self.language = args if self.private else self.language
            return Response(self.t("ok"))
        if command == "copy_list":
            return await self._suggestion_prompt(UUID(args))
        if command in ("export_menu", "export_file", "export_back"):
            values = args.split()
            export = await self.exports.load(UUID(values[0]))
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
            return Response(self.t("concert_restored", name=concert_label(restored_concert)))
        if command == "cancel":
            for item in await self.store.find(
                Confirmation, user_id=self.user.id, chat_id=self.chat_id
            ):
                if json.loads(item.command).get("kind") in (CARD, REQUEST):
                    continue
                await self.store.delete(item)
            for job in await self.store.find(
                CatalogImport, owner_id=self.user.id, chat_id=self.chat_id, status="pending"
            ):
                job.status = "cancelled"
            return Response(self.t("ok"))
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
            await self.groups.register(group_id, args)
            return Response(self.t("registered"))
        group = await self.groups.access(group_id)
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
            require("command" in data, "confirmation")
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
                label = user.display_name or str(user.telegram_id)
                lines.append(f"{label} · {member.musical_role} · {self.t(member.status)}")
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
                group.name + "\n\n" + ("\n\n".join(song_card(s) for s in songs) or self.t("empty")),
                [(self.t("song_open", name=s.title), f"show_song {s.id}") for s in songs],
            )
        if command in ("show_song", "edit_hint", "copy_hint", "alias_hint"):
            selected = await self.catalog.song(group_id, UUID(args))
            context.selected_song_id = selected.id
            if command != "show_song":
                return Response(
                    self.t(
                        command,
                        title=selected.title,
                        time=clock(selected.duration_seconds),
                        bpm=selected.bpm or "-",
                        lights=selected.lights or "-",
                        comment=selected.comment or "-",
                    )
                )
            buttons = [
                (self.t("song_edit"), f"edit_hint {selected.id}"),
                (self.t("song_copy"), f"copy_hint {selected.id}"),
                (self.t("song_alias"), f"alias_hint {selected.id}"),
            ]
            if not selected.archived:
                buttons.insert(0, (self.t("song_add"), f"offer_add {selected.id}"))
            member = (await self.store.find(Membership, group_id=group_id, user_id=self.user.id))[0]
            if member.role == "admin":
                action = "unarchive" if selected.archived else "archive"
                buttons.append(
                    (
                        self.t("song_unarchive" if selected.archived else "song_archive"),
                        f"{action} {selected.id}",
                    )
                )
            return Response(song_card(selected), buttons)
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
            return await self.confirm(context, "save_song " + json.dumps(payload), song_card(data))
        if command == "save_song":
            require(confirmed, "confirmation")
            payload = json.loads(args)
            result = await self.catalog.save(
                group_id,
                payload["data"],
                UUID(payload["song_id"]) if payload["song_id"] else None,
                payload["expected"],
            )
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
                context, f"set_archive {song.id} {song.version} {command}", song_card(song)
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
                preview = proposal.payload.get("alias") or song_card(proposal.payload)
                author = await self.store.get(User, proposal.author_id)
                lines.append(f"{author.display_name or author.telegram_id}\n{preview}")
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
            parts = command_fields(args, maxsplit=1)
            concert = await self.setlists.create(
                group_id, date.fromisoformat(parts[0]), parts[1] if len(parts) > 1 else ""
            )
            context.concert_id = concert.id
            return await self._show(context)
        if command == "concerts":
            concerts = sorted(
                await self.store.find(Concert, group_id=group_id, deleted_at=None),
                key=lambda c: c.date,
            )
            return Response(
                self.t("choose_concert") if concerts else self.t("empty"),
                [(concert_label(c), f"use_concert {c.id}") for c in concerts],
            )
        if command == "use_concert":
            await self.setlists.load(group_id, UUID(args))
            context.concert_id = UUID(args)
            context.observed_version = None
            return await self._show(context)
        if command == "view_submission":
            submission = await self.store.get(Submission, UUID(args))
            require(submission.group_id == group_id, "not_found")
            context.concert_id = submission.concert_id
            await self._show(context)
            export_buttons = await self._export_buttons(
                group_id,
                submission.concert_id,
                submission.snapshot,
                0,
                approved=False,
                back=f"view_submission {submission.id}",
            )
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
                )
                + export_buttons,
                monospace=True,
            )
        require(context.concert_id is not None, "select_concert")
        concert_id = context.concert_id
        assert concert_id is not None
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
                return Response(self.t("concert_deleted", name=concert_label(deleted_concert)))
            target_concert = await self.store.get(Concert, concert_id)
            return await self.confirm(
                context,
                f"delete_concert_final {fingerprint}",
                self.t("concert_delete_preview", name=concert_label(target_concert)),
                concert=True,
            )
        if command in ("setlist", "shared"):
            return await self._show(context, shared=command == "shared")
        if command == "export":
            snapshot, version, approved = await self.setlists.read(
                group_id, concert_id, private=self.private
            )
            export = await self.exports.create(
                group_id,
                concert_id,
                snapshot,
                version,
                approved=approved,
                private=self.private,
                back="setlist",
            )
            return self._export_menu(export.id, export.payload, export.payload["language"], False)
        if command == "pause" and not args:
            response = await self._show(context)
            response.text += "\n\n" + self.t("pause_usage")
            return response
        require(context.observed_version is not None, "view_first")
        expected = context.observed_version
        assert expected is not None
        if command in ("add_encore", "remove_encore_song", "remove_encore", "manage_encore"):
            if command == "manage_encore":
                identifier, raw_version, action = args.split()
                require(UUID(identifier) == concert_id, "confirmation")
                require(int(raw_version) == expected, "conflict")
                require(action in ("add", "remove", "drop"), "input")
                command = {
                    "add": "add_encore",
                    "remove": "remove_encore_song",
                    "drop": "remove_encore",
                }[action]
                args = ""
            snapshot, version, approved = await self.setlists.read(
                group_id, concert_id, private=self.private
            )
            require(version == expected, "conflict")
            require(not approved, "approved")
            enc_blocks = [
                i
                for i, block_data in enumerate(snapshot["blocks"], 1)
                if block_data["kind"] == "encore"
            ]
            require(len(enc_blocks) == 1, "encore_missing")
            enc_block = enc_blocks[0]
            if command == "add_encore":
                if args:
                    try:
                        await self.setlists.edit_songs(
                            group_id,
                            concert_id,
                            expected,
                            [line.strip() for line in args.splitlines() if line.strip()],
                            block=enc_block,
                            private=self.private,
                        )
                    except SongListError as exc:
                        return self._song_list_errors(exc)
                    return await self._show(context)
                return Response(
                    self.t("encore_add_prompt"),
                    [
                        (song_card(song).splitlines()[0], f"offer_add {song.id.hex} {enc_block}")
                        for song in await self.catalog.list_songs(group_id)
                    ],
                )
            if command == "remove_encore_song" and not args:
                return Response(
                    self.t("encore_remove_prompt"),
                    [
                        (
                            f"{pos}. {item['title']}",
                            f"encore_pick {concert_id.hex} {expected} {pos}",
                        )
                        for pos, item in enumerate(snapshot["blocks"][enc_block - 1]["items"], 1)
                    ],
                )
            if command == "remove_encore_song":
                pos = int(args)
                require(
                    1 <= pos <= len(snapshot["blocks"][enc_block - 1]["items"]), "setlist_input"
                )
                operation, encore_values = "remove", f"{enc_block} {pos}"
            else:
                operation, encore_values = "remove_block", str(enc_block)
            return await self.confirm(
                context,
                f"edit_list {operation} {expected} {encore_values}",
                self.t(
                    "encore_private_drop_preview"
                    if self.private
                    else (
                        "encore_drop_preview"
                        if command == "remove_encore"
                        else "encore_song_drop_preview"
                    )
                )
                + "\n"
                + render_setlist(
                    snapshot,
                    await self.store.get(Concert, concert_id),
                    self.language,
                    version,
                    private=self.private,
                ),
                concert=True,
                monospace=True,
            )
        if command == "encore_pick":
            identifier, raw_version, position = args.split()
            require(UUID(identifier) == concert_id and int(raw_version) == expected, "confirmation")
            return await self.execute(f"remove_encore_song {position}")
        if command == "suggestion" and not args:
            shown = await self._show(context)
            require(shown.suggestion_template_id is not None, "approved")
            assert shown.suggestion_template_id is not None
            return await self._suggestion_prompt(shown.suggestion_template_id)
        if command == "suggestion" or (command == "add" and "\n" in message[len(head) :]):
            body = args
            batch_block = 1
            inline = message[len(head) :].split("\n", 1)[0].strip()
            if command == "add" and inline.isdecimal():
                batch_block = int(inline)
                body = message[len(head) :].split("\n", 1)[1]
            titles = [line.strip() for line in body.splitlines() if line.strip()]
            try:
                await self.setlists.edit_songs(
                    group_id,
                    concert_id,
                    expected,
                    titles,
                    replace=command == "suggestion",
                    block=batch_block,
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
                return Response("\n\n".join(lines))
            previous = await self.selection.active() if self.private else None
            if self.private:
                await self.selection.clear()
            response = await self._show(context)
            response.remove_keyboard = (
                previous is not None and self.selection.data(previous)["stage"] == "choosing"
            )
            response.keyboard_text = self.t("selection_keyboard_closed")
            return response
        if command == "encore":
            if args:
                identifier, raw_version = args.split()
                require(UUID(identifier) == concert_id, "confirmation")
                expected = int(raw_version)
            await self.setlists.edit(
                group_id, concert_id, expected, "encore", [self.t("encore")], private=self.private
            )
            return await self._show(context)
        if command == "offer_add":
            values = args.split()
            target = await self.catalog.song(group_id, UUID(values[0]))
            block = values[1] if len(values) > 1 else "1"
            preview = (await self._show(context)).text + "\n\n" + song_card(target)
            assert context.observed_version is not None
            return await self.confirm(
                context,
                f"edit_list add {context.observed_version} {block} {target.id}",
                preview,
                concert=True,
            )
        if command in ("add", "move", "block", "pause", "break", "remove"):
            values = args.split()
            operation = command
            if command == "pause" and len(values) == 1:
                operation = "default_pause"
            elif command == "pause" and len(values) == 2:
                values.insert(0, "1")
            if command == "add":
                require("|" not in args, "field_separator")
                fields = command_fields(args, maxsplit=1)
                selector = fields[0]
                block = fields[1] if len(fields) == 2 else "1"
                require(block.isdigit() and 1 <= int(block) <= 20, "setlist_input")
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
                                (song_card(s).splitlines()[0], f"offer_add {s.id.hex} {block}")
                                for s in songs
                            ],
                        )
                    values = [block, str(songs[0].id)]
            lengths = {"add": 2, "move": 3, "pause": 3, "default_pause": 1, "break": 2, "remove": 2}
            require(command == "block" or len(values) == lengths[operation], "setlist_input")
            if command == "remove" and not confirmed:
                snapshot, version, approved = await self.setlists.read(
                    group_id, concert_id, private=self.private
                )
                return await self.confirm(
                    context,
                    f"edit_list {command} {expected} {args}",
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
                preview = render_setlist(
                    submission.snapshot, await self.store.get(Concert, concert_id), self.language, 0
                )
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
            lines = [concert_label(await self.store.get(Concert, concert_id))]
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
                        author=author.display_name or str(author.telegram_id),
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
        if command == "publish":
            require(self.private, "private_only")
            return await self.confirm(
                context,
                f"publish_draft {expected}",
                (await self._show(context)).text,
                concert=True,
                monospace=True,
            )
        if command == "publish_draft":
            require(confirmed and self.private, "confirmation")
            snapshot, version, _ = await self.setlists.read(group_id, concert_id, private=True)
            require(version == int(args), "conflict")
            bindings = await self.store.find(ChatBinding, group_id=group_id)
            require(bool(bindings), "no_chat")
            submission = await self.setlists.publish(group_id, concert_id)
            public_text = (
                tr(group.language, "submission")
                + "\n"
                + render_setlist(
                    snapshot,
                    await self.store.get(Concert, concert_id),
                    group.language,
                    version,
                    private=True,
                )
            )
            export_buttons = await self._export_buttons(
                group_id,
                concert_id,
                snapshot,
                version,
                approved=False,
                back=f"view_submission {submission.id}",
                chat_id=bindings[0].chat_id,
                private=False,
                language=group.language,
            )
            self.store.add(
                Outbox(
                    chat_id=bindings[0].chat_id,
                    text=public_text,
                    group_id=group_id,
                    concert_id=concert_id,
                    monospace=True,
                    buttons=[
                        [tr(group.language, "submission_open"), f"view_submission {submission.id}"]
                    ]
                    + [list(button) for button in export_buttons],
                )
            )
            return Response(self.t("published"))
        require(False, "input")
        raise AssertionError("unreachable")

    async def _revision_label(self, revision: Revision) -> str:
        author = await self.store.get(User, revision.proposer_id or revision.author_id)
        return self.t(
            "variant_label" if revision.suggestion_number is not None else "revision_label",
            name=author.display_name or self.t("member_name"),
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
        if not any(block["items"] for block in snapshot["blocks"]):
            return []
        service = self.exports if chat_id is None else Exports(self.store, self.user, chat_id)
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
        status = self.t("pdf_approved" if payload["approved"] else "pdf_draft")
        return Response(
            f"{payload['group_name']}\n{payload['concert_date']} {payload['concert_title']}\n"
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
        return Response("\n\n".join(lines))

    async def _template_context(self, template_id: UUID) -> Context:
        template = await self.templates.load(template_id)
        self.reply_target = template.group_id, template.concert_id
        context = await self.groups.context(self.chat_id, self.private)
        context.group_id, context.concert_id = template.group_id, template.concert_id
        context.observed_version = template.version
        return context

    async def _suggestion_prompt(self, template_id: UUID) -> Response:
        context = await self._template_context(template_id)
        assert context.group_id is not None and context.concert_id is not None
        _, version, approved = await self.setlists.read(
            context.group_id, context.concert_id, private=self.private
        )
        require(version == context.observed_version, "conflict")
        require(not approved, "approved")
        template = await self.templates.load(template_id)
        text = self.t("suggestion_prompt")
        if self.private:
            text += "\n" + self.t("suggestion_private")
        if template.titles:
            text += "\n\n" + "\n".join(template.titles)
        if self.private:
            previous = await self.selection.active()
            previous_data = self.selection.data(previous) if previous else {}
            session = await self.selection.create(
                context.group_id, context.concert_id, version, template_id
            )
            return Response(
                text + "\n\n" + self.t("selection_method"),
                [
                    (self.t("selection_text_button"), f"pick_text {session.id.hex}"),
                    (self.t("selection_pick_button"), f"pick_start {session.id.hex}"),
                ],
                suggestion_template_id=template_id,
                selection_id=session.id,
                remove_keyboard=previous_data.get("stage") == "choosing",
                keyboard_text=self.t("selection_keyboard_closed"),
                previous_keyboard_id=previous_data.get("keyboard_message_id"),
            )
        return Response(text, suggestion_template_id=template_id, force_reply=True)

    def _group_name_prompt(self, session: Confirmation, *, invalid: bool = False) -> Response:
        return Response(
            (self.t("group_name") + "\n\n" if invalid else "") + self.t("group_creation_prompt"),
            force_reply=True,
            group_creation_id=session.id,
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
        titles = [roster[i]["title"] for i in data["selected"]]
        reviewing = data["stage"] == "review"
        body = "\n".join(
            titles if reviewing else [f"{i}. {title}" for i, title in enumerate(titles, 1)]
        )
        response = Response(
            self.t("selection_title")
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
            rows = [[self.t("selection_finish")]]
            rows += [labels[i : i + 2] for i in range(0, len(labels), 2)]
            navigation = []
            if data["page"]:
                navigation.append(self.t("selection_prev"))
            if start + PAGE_SIZE < len(data["songs"]):
                navigation.append(self.t("selection_next"))
            if navigation:
                rows.append(navigation)
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
            context = await self.groups.context(self.chat_id, True)
            was_choosing = data["stage"] == "choosing"
            response = Response(self.t("selection_cancelled"), remove_keyboard=was_choosing)
            try:
                if context.group_id == UUID(data["group_id"]) and context.concert_id == UUID(
                    data["concert_id"]
                ):
                    # Fresh prompt with the current version, not the cancelled snapshot.
                    shown = await self._show(context)
                    assert shown.suggestion_template_id is not None
                    response = await self._suggestion_prompt(shown.suggestion_template_id)
                    response.text = self.t("selection_cancelled") + "\n\n" + response.text
                    response.remove_keyboard = was_choosing
            except AppError:
                pass
            response.previous_keyboard_id = data["keyboard_message_id"]
            response.keyboard_text = self.t("selection_keyboard_closed")
            return response
        if action == "pick_start":
            session, data = await self.selection.start(identifier)
            return self._selection_view(session, data, keyboard=True)
        session, data = await self.selection.load(identifier)
        if action == "pick_text":
            require(data["stage"] == "method", "selection_expired")
            data["stage"] = "text"
            self.selection.write(session, data)
            template_id = UUID(data["template_id"])
            return Response(
                self.t("suggestion_prompt") + "\n" + self.t("suggestion_private"),
                suggestion_template_id=template_id,
                force_reply=True,
                selection_id=session.id,
            )
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
        if value == self.t("selection_finish"):
            require(bool(data["selected"]), "empty_setlist")
            data["stage"] = "review"
        elif value in (self.t("selection_prev"), self.t("selection_next")):
            page = data["page"] + (-1 if value == self.t("selection_prev") else 1)
            require(0 <= page * PAGE_SIZE < len(data["songs"]), "selection_expired")
            data["page"] = page
            keyboard = True
        else:
            start = data["page"] * PAGE_SIZE
            matches = [s for s in data["songs"][start : start + PAGE_SIZE] if s["label"] == value]
            if not matches:
                return Response(self.t("selection_unknown"))
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
                                name=concert_label(concert),
                                until=(concert.deleted_at + RETENTION).date().isoformat(),
                            )
                        )
                        buttons.append(
                            (
                                self.t("restore_concert_button", name=concert_label(concert)),
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
            lines.append(self.t("import_new") + ": " + song_card(data))
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
            response = Response(preview, [(self.t("cancel_button"), "cancel")])
        response.buttons.insert(-1, (self.t("import_map"), f"map_start {job.id.hex}"))
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
            buttons = [
                (label, f"slopt {key} {i}")
                for i, (label, _) in enumerate(await self._card_options(data))
            ]
            if variants:
                buttons.append((self.t("menu_variants"), f"slmenu {key} variants"))
            return buttons + [(self.t("menu_back"), f"slmenu {key} main")]
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
                source = Controller(self.store, self.user, data["source_chat_id"], private=False)
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
                name = self.user.display_name or self.t("member_name")
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
                            concert=concert_label(concert),
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
                callback_notice=self.t("menu_reopen_prompt"),
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
        if not shared and not approved:
            if any(block["kind"] == "encore" for block in snapshot["blocks"]):
                for label, action in (
                    ("encore_add_button", "add"),
                    ("encore_remove_song_button", "remove"),
                    ("encore_drop_button", "drop"),
                ):
                    buttons.append(
                        (self.t(label), f"manage_encore {concert.id.hex} {version} {action}")
                    )
            else:
                buttons.append((self.t("add_encore"), f"encore {concert.id.hex} {version}"))
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
            suggestion_template_id=template_id,
            menu_card_id=card.id,
        )
