"""RU: Один обновляемый список на коллектив. EN: Durable, coalesced roster updates."""

from uuid import UUID

from setlist_bot.application.ports import Store
from setlist_bot.domain.entities import ChatBinding, Group, MemberBoard, Outbox


async def queue_member_board(
    store: Store, group_id: UUID, *, ensure: bool = False, coalesce: bool = True
) -> None:
    group = await store.get(Group, group_id)
    if group.deleted_at is not None:
        return
    bindings = await store.find(ChatBinding, group_id=group_id)
    if not bindings:
        return
    boards = await store.find(MemberBoard, group_id=group_id)
    if not boards:
        if not ensure:
            return
        board = MemberBoard(
            group_id=group_id, chat_id=bindings[0].chat_id, group_epoch=group.lifecycle_version
        )
        store.add(board)
    else:
        board = boards[0]
        if board.chat_id != bindings[0].chat_id or board.group_epoch != group.lifecycle_version:
            board.chat_id, board.group_epoch = bindings[0].chat_id, group.lifecycle_version
            board.message_ids = []
    # RU: Сохраняем задание вместе с изменениями. EN: No Telegram request inside a mutation.
    # RU: Объединять можно только под блокировкой коллектива.
    # EN: Profile edits hold User, not Group: always enqueue to avoid a lost refresh
    # when a worker has already read the old name but has not committed delivery.
    if not coalesce or not await store.find(
        Outbox, group_id=group_id, chat_id=board.chat_id, support_kind="members", sent=False
    ):
        store.add(
            Outbox(
                group_id=group_id,
                chat_id=board.chat_id,
                support_kind="members",
                text="Обновление участников коллектива",
            )
        )
