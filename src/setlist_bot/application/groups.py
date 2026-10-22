from uuid import UUID

from setlist_bot.application.ports import Store
from setlist_bot.domain.entities import (
    ChatBinding,
    Context,
    Group,
    Membership,
    User,
)
from setlist_bot.domain.rules import pause_seconds, require


class Groups:
    def __init__(self, store: Store, user: User) -> None:
        self.store, self.user = store, user

    async def access(self, group_id: UUID, *, admin: bool = False, deleted: bool = False) -> Group:
        memberships = await self.store.find(Membership, group_id=group_id, user_id=self.user.id)
        require(bool(memberships) and memberships[0].status == "active", "forbidden")
        require(not admin or memberships[0].role == "admin", "admin_only")
        group = await self.store.get(Group, group_id, lock=True)
        require(deleted or group.deleted_at is None, "in_trash")
        return group

    async def create(self, name: str, chat_id: int) -> Group:
        await self.store.lock_chat(chat_id)
        require(0 < len(name.strip()) <= 200, "group_name")
        require(not await self.store.find(ChatBinding, chat_id=chat_id), "already_bound")
        group = Group(name=name.strip(), language=self.user.language)
        self.store.add(group)
        await self.store.flush()
        self.store.add(
            Membership(group_id=group.id, user_id=self.user.id, role="admin", status="active")
        )
        self.store.add(ChatBinding(group_id=group.id, chat_id=chat_id))
        await self.store.flush()
        return group

    async def set_default_pause(self, group_id: UUID, value: str) -> Group:
        group = await self.access(group_id, admin=True)
        group.default_pause_seconds = pause_seconds(value)
        return group

    async def register(self, group_id: UUID, musical_role: str) -> Membership:
        require(0 < len(musical_role.strip()) <= 200, "musical_role")
        group = await self.store.get(Group, group_id, lock=True)
        require(group.deleted_at is None, "in_trash")
        require(
            not await self.store.find(Membership, group_id=group_id, user_id=self.user.id),
            "already_member",
        )
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
