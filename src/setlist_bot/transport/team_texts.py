"""Explicit permanent membership invitations and outcomes."""

TEXTS = {
    "ru": {
        "team_roles_invitation": (
            "Новые роли в коллективе «{group}»\n\nПредлагаемые роли: {roles}\n"
            "Действующие роли и права сохраняются. Примите или отклоните новые роли."
        ),
        "team_invitation": (
            "Приглашение в постоянную команду коллектива «{group}»\n\nРоли: {roles}\n"
            "После принятия коллектив появится в /groups. Участие в отдельных "
            "выступлениях подтверждается отдельно."
        ),
        "team_accept": ("Вступить в коллектив"),
        "team_accept_roles": ("Принять новые роли"),
        "team_decline": ("Отклонить приглашение"),
        "team_cancel": ("Отозвать приглашение"),
        "team_retry": ("Отправить приглашение повторно"),
        "team_wait": ("ожидает принятия приглашения"),
        "team_joined": ("{name} принят в постоянную команду с согласованными ролями."),
        "team_declined": ("{name} отклонил приглашение в постоянную команду."),
        "team_withdrawn": ("Приглашение в постоянную команду отозвано."),
        "team_removed": (
            "Членство в коллективе прекращено. Согласованные будущие выступления "
            "сохраняются; отмена участия выполняется отдельно."
        ),
        "team_role_removed": (
            "Администратор снял одну из ваших ролей. Членство и согласованные "
            "выступления сохранены."
        ),
        "team_remove": ("Исключить из коллектива"),
        "team_leave": ("Выйти из коллектива"),
        "team_remove_check": (
            "Прекратить членство: {name}? Доступ к данным коллектива будет закрыт. "
            "История сохраняется. Будущие подтверждённые участия ниже сохранятся; "
            "отменить их можно отдельно."
        ),
        "team_transfer_first": ("Владелец должен сначала передать владение коллективом."),
        "team_failed": (
            "Не удалось доставить приглашение: {name}. Попросите участника "
            "запустить бота и повторите отправку из постоянной команды."
        ),
        "team_pending": ("Приглашения и заявки"),
        "team_no_gigs": ("Подтверждённых будущих выступлений нет."),
        "team_sent": (
            "Приглашение в постоянную команду поставлено в очередь. Роли появятся "
            "после принятия участником."
        ),
        "invited": ("приглашён"),
        "left": ("вышел из коллектива"),
        "staff_swap_pending": ("запросил подмену"),
        "staff_swap_candidate": ("{name} — согласился, ожидает утверждения"),
        "staff_swap_invited": ("{name} — приглашён на подмену"),
        "context_proposals": ("Предложения по репертуару"),
        "song_archive_question": ("Перенести песню в архив?"),
        "song_restore_question": ("Восстановить песню из архива?"),
    },
    "en": {
        "team_roles_invitation": (
            "New roles in “{group}”\n\nProposed roles: {roles}\n"
            "Your existing roles and permissions remain. Accept or decline the new roles."
        ),
        "team_invitation": (
            "Invitation to the permanent team of “{group}”\n\nRoles: {roles}\nAfter "
            "acceptance the band will appear in /groups. Each performance requires "
            "separate confirmation."
        ),
        "team_accept": ("Join the band"),
        "team_accept_roles": ("Accept new roles"),
        "team_decline": ("Decline invitation"),
        "team_cancel": ("Withdraw invitation"),
        "team_retry": ("Resend invitation"),
        "team_wait": ("awaiting invitation acceptance"),
        "team_joined": ("{name} joined the permanent team with the agreed roles."),
        "team_declined": ("{name} declined the permanent team invitation."),
        "team_withdrawn": ("The permanent team invitation was withdrawn."),
        "team_removed": (
            "Band membership ended. Confirmed future performances remain; cancel "
            "participation separately."
        ),
        "team_role_removed": (
            "An administrator removed one of your roles. Membership and agreed performances remain."
        ),
        "team_remove": ("Remove from band"),
        "team_leave": ("Leave band"),
        "team_remove_check": (
            "End membership for {name}? Access to band data will end. History is "
            "preserved. Confirmed future performances below remain; cancel them "
            "separately."
        ),
        "team_transfer_first": ("The owner must transfer ownership before leaving the band."),
        "team_failed": (
            "Could not deliver the invitation to {name}. Ask them to start the bot "
            "and resend from the permanent team."
        ),
        "team_pending": ("Invitations and applications"),
        "team_no_gigs": ("No confirmed future performances."),
        "team_sent": (
            "The permanent team invitation is queued. Roles will appear after acceptance."
        ),
        "invited": ("invited"),
        "left": ("left the band"),
        "staff_swap_pending": ("requested a substitute"),
        "staff_swap_candidate": ("{name} — accepted, awaiting approval"),
        "staff_swap_invited": ("{name} — invited as a substitute"),
        "context_proposals": ("Repertoire proposals"),
        "song_archive_question": ("Move this song to the archive?"),
        "song_restore_question": ("Restore this song from the archive?"),
    },
}
