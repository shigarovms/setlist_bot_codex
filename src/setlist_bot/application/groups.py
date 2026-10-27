import json
from datetime import timedelta
from uuid import UUID

from setlist_bot.application.ports import Store
from setlist_bot.application.support import Support
from setlist_bot.domain.entities import (
    ChatBinding,
    Confirmation,
    Context,
    Group,
    Membership,
    User,
    now,
)
from setlist_bot.domain.rules import pause_seconds, require


class Groups:
    def __init__(self, store: Store, user: User) -> None:
        self.store, self.user = store, user

    async def access(self, group_id: UUID, *, admin: bool = False, deleted: bool = False) -> Group:
        memberships = await self.store.find(Membership, group_id=group_id, user_id=self.user.id)
        require(bool(memberships) and memberships[0].status == "active", "forbidden")
        require(not admin or memberships[0].role == "admin", "admin_only")
        # RU: Блокировка коллектива сериализует правки из разных чатов. Права проверены выше.
        # EN: The tenant row lock serializes mutations across group and private chats.
        group = await self.store.get(Group, group_id, lock=True)
        require(deleted or group.deleted_at is None, "in_trash")
        return group

    async def check_creation_chat(self, chat_id: int) -> None:
        await self.store.lock_chat(chat_id)
        require(not await self.store.find(ChatBinding, chat_id=chat_id), "already_bound")

    async def create(self, name: str, chat_id: int) -> Group:
        await self.check_creation_chat(chat_id)
        require(0 < len(name.strip()) <= 200, "group_name")
        group = Group(name=name.strip(), language=self.user.language, owner_id=self.user.id)
        self.store.add(group)
        await self.store.flush()
        self.store.add(
            Membership(group_id=group.id, user_id=self.user.id, role="admin", status="active")
        )
        self.store.add(ChatBinding(group_id=group.id, chat_id=chat_id))
        await self.store.flush()
        support = Support(self.store, self.user)
        await support.notify(
            f"Создан коллектив: {group.name}\nАвтор: {support.actor}\nЧат: {chat_id}"
        )
        return group

    async def clear_creation(self, chat_id: int) -> None:
        for session in await self.store.find(Confirmation, user_id=self.user.id, chat_id=chat_id):
            if json.loads(session.command).get("kind") == "create_group":
                await self.store.delete(session)

    async def begin_creation(self, chat_id: int, name: str | None = None) -> Confirmation:
        await self.check_creation_chat(chat_id)
        if name is not None:
            require(0 < len(name.strip()) <= 200, "group_name")
        await self.clear_creation(chat_id)
        session = Confirmation(
            user_id=self.user.id,
            chat_id=chat_id,
            command=json.dumps(
                {
                    "kind": "create_group",
                    "stage": "name" if name is None else "review",
                    "name": name.strip() if name is not None else None,
                    "prompt_id": None,
                },
                ensure_ascii=False,
            ),
            expires_at=now() + timedelta(minutes=10),
        )
        self.store.add(session)
        return session

    async def creation_session(self, chat_id: int, identifier: UUID) -> Confirmation:
        sessions = await self.store.find(
            Confirmation, id=identifier, user_id=self.user.id, chat_id=chat_id
        )
        require(bool(sessions), "group_creation_expired")
        session = sessions[0]
        require(
            session.expires_at > now()
            and json.loads(session.command).get("kind") == "create_group",
            "group_creation_expired",
        )
        return session

    async def creation_reply(self, chat_id: int, prompt_id: int) -> Confirmation | None:
        for session in await self.store.find(Confirmation, user_id=self.user.id, chat_id=chat_id):
            data = json.loads(session.command)
            if (
                data.get("kind") == "create_group"
                and data.get("stage") == "name"
                and data.get("prompt_id") == prompt_id
                and session.expires_at > now()
            ):
                return session
        return None

    async def set_default_pause(self, group_id: UUID, value: str) -> Group:
        group = await self.access(group_id, admin=True)
        group.default_pause_seconds = pause_seconds(value)
        return group

    async def register(self, group_id: UUID, musical_role: str) -> Membership:
        require(0 < len(musical_role.strip()) <= 200, "musical_role")
        group = await self.store.get(Group, group_id, lock=True)
        require(group.deleted_at is None, "in_trash")
        existing = await self.store.find(Membership, group_id=group_id, user_id=self.user.id)
        if existing:
            existing[0].musical_role = musical_role.strip()
            return existing[0]
        membership = Membership(
            group_id=group_id, user_id=self.user.id, musical_role=musical_role.strip()
        )
        self.store.add(membership)
        return membership

    async def decide_member(self, group_id: UUID, membership_id: UUID, approve: bool) -> None:
        await self.access(group_id, admin=True)
        membership = await self.store.get(Membership, membership_id)
        require(membership.group_id == group_id, "not_found")
        require(membership.status == "pending", "already_done")
        if approve:
            membership.status = "active"
        else:
            await self.store.delete(membership)

    async def context(self, chat_id: int, private: bool) -> Context:
        matches = await self.store.find(Context, user_id=self.user.id, chat_id=chat_id)
        context = matches[0] if matches else Context(user_id=self.user.id, chat_id=chat_id)
        if not matches:
            self.store.add(context)
        if not private:
            bindings = await self.store.find(ChatBinding, chat_id=chat_id)
            require(bool(bindings), "bind_first")
            if context.group_id != bindings[0].group_id:
                context.group_id = bindings[0].group_id
                context.concert_id = None
                context.observed_version = None
                context.selected_song_id = None
        return context

    async def name(self, group_id: UUID, user_id: UUID | None = None) -> str:
        target = user_id or self.user.id
        rows = await self.store.find(Membership, group_id=group_id, user_id=target)
        user = await self.store.get(User, target)
        return (rows[0].musical_role if rows else "") or user.display_name or str(user.telegram_id)

    async def administrator(self, group_id: UUID, member_id: UUID, grant: bool) -> None:
        # RU: Владелец — отдельное право, оно не передаётся вместе с ролью admin.
        # EN: Ownership is independent of the administrative role and cannot be granted here.
        group = await self.access(group_id, admin=True)
        require(group.owner_id == self.user.id, "owner_only")
        member = await self.store.get(Membership, member_id, lock=True)
        require(member.group_id == group_id and member.status == "active", "not_found")
        require(member.user_id != group.owner_id, "owner_only")
        require(member.role != ("admin" if grant else "member"), "already_done")
        member.role = "admin" if grant else "member"
