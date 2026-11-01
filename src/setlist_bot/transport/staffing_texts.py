"""RU/EN copy for staffing; no external calendar authorization is requested."""

TEXTS = {
    "ru": {
        "staff_more": "ещё {count}",
        "staff_all_invites": "Все приглашения",
        "staff_invite_defaults": "Пригласить основной состав",
        "staff_team": "👥 Команда и составы",
        "staff_lineup": "👥 Состав выступления",
        "staff_templates": "Шаблоны составов",
        "staff_contacts": "Знакомые исполнители",
        "staff_add_template": "＋ Создать / изменить шаблон",
        "staff_add_contact": "＋ Добавить исполнителя",
        "staff_add_role": "＋ Добавить роль",
        "staff_roles": "Роли и инструменты",
        "staff_role_prompt": (
            "Введите название роли: например, Гитара, Вокал, Звукорежиссёр.\n/cancel — отмена."
        ),
        "staff_contact_prompt": (
            "Введите роль и известный боту @ник или Telegram ID через два пробела. "
            "Например:\nГитара  @musician\nЧеловек должен предварительно открыть "
            "бота и отправить /start.\n/cancel — отмена."
        ),
        "staff_template_prompt": (
            "Первая строка — название состава. Далее по одной роли на строку. "
            "Через два пробела можно указать основного исполнителя:\nБольшой\nВокал  "
            "@singer\nГитара\nБас\n\nПриглашения отправляются отдельно после выбора "
            "состава.\n/cancel — отмена."
        ),
        "staff_busy_prompt": (
            "Введите дату или диапазон включительно, например:\n22.12\n22.12 — "
            "25.12\nПричины не нужны: другие видят только занятость.\n/cancel — "
            "отмена."
        ),
        "staff_my": "🎸 Мои выступления и приглашения",
        "staff_busy_menu": "📅 Моя занятость",
        "staff_busy_add": "＋ Отметить занятость",
        "staff_busy_remove": "Снять отметку",
        "staff_candidates": "Выбрать исполнителя",
        "staff_not_needed": "Инструмент не нужен",
        "staff_needed": "Нужен — искать исполнителя",
        "staff_vacant": "не назначен",
        "staff_skipped": "не требуется",
        "staff_unknown": "пересечений не найдено",
        "staff_conflicts": "возможное пересечение",
        "staff_tentative": "есть приглашения без ответа",
        "staff_busy": (
            "На эту дату есть занятость. Это возможное пересечение: события "
            "учитываются на весь день."
        ),
        "staff_force_invite": "Всё равно пригласить",
        "staff_force_yes": "Подтвердить несмотря на пересечение",
        "staff_confirm": "✅ Подтверждаю",
        "staff_decline": "Не могу",
        "staff_conditional": "Могу, если найду подмену",
        "staff_pending": "ожидаем ответа",
        "staff_confirmed": "подтверждено",
        "staff_conditional_status": "ищет подмену",
        "staff_accepted": "кандидат согласен — нужно одобрение",
        "staff_declined": "отказ",
        "staff_cancelled": "отменено",
        "staff_replaced": "заменён",
        "staff_reconfirm": "нужно подтвердить новую дату",
        "staff_details": "Открыть приглашение",
        "staff_apply": "Применить этот состав",
        "staff_lineup_exists": (
            "У выступления уже есть состав. Изменяйте его роли и исполнителей по отдельности."
        ),
        "staff_filled": (
            "На эту роль уже подтверждён человек. Для замены нужен запрос подмены "
            "или отмена участия."
        ),
        "staff_hidden": (
            "Администратор отметил исполнителя «Не предлагать» для этой роли. "
            "Измените отметку в списке контактов."
        ),
        "staff_preferred": "Предлагать первым",
        "staff_normal": "Можно приглашать",
        "staff_hidden_label": "Не предлагать",
        "staff_worked": "Отметить: участвовал в выступлении",
        "staff_experience": "Отмечено участий: {count}",
        "staff_request": "Запросить подмену",
        "staff_find": "Подобрать подмену",
        "staff_approve_swap": "✅ Утвердить подмену",
        "staff_cancel_request": "Отменить запрос подмены",
        "staff_waiting": "Исходное участие сохраняется, пока администратор не утвердит замену.",
        "staff_private": "Приглашения и занятость доступны в личном чате с ботом.",
        "staff_invitation_notice": "Приглашение на выступление",
        "staff_reply_notice": "Получен ответ на приглашение",
        "staff_replacement_notice": "Запрошена подмена",
        "staff_swapped_notice": "Подмена согласована",
        "staff_busy_notice": "У участника появилась отметка занятости на дату выступления",
        "staff_date_changed_notice": "Дата изменилась. Подтвердите участие заново.",
        "staff_cancelled_notice": "Участие отменено",
        "staff_confirmed_notice": "Ваша подмена утверждена: участие подтверждено",
        "staff_released_notice": (
            "Ваша подмена согласована. Исходное обязательство снято. Новое "
            "приглашение нужно подтвердить отдельно."
        ),
        "staff_replacement_cancelled_notice": (
            "Запрос подмены отменён. Исходное участие сохраняется."
        ),
        "staff_cancel_invite": "Отменить участие / приглашение",
        "staff_cancel_check": "Отменить участие или приглашение этого человека?",
        "staff_guest_list": "Посмотреть утверждённый сет-лист",
        "staff_guest_pdf": "PDF для печати",
        "staff_delivery_failed": (
            "Не удалось доставить приглашение. Попросите человека открыть бота, "
            "отправить /start и проверить блокировку. Затем отправьте приглашение "
            "повторно."
        ),
        "staff_retry": "Повторить отправку",
        "staff_personal_calendar": "Мои подтверждённые участия",
    },
    "en": {
        "staff_more": "{count} more",
        "staff_all_invites": "All invitations",
        "staff_invite_defaults": "Invite default performers",
        "staff_team": "👥 Team and lineups",
        "staff_lineup": "👥 Performance lineup",
        "staff_templates": "Lineup templates",
        "staff_contacts": "Known performers",
        "staff_add_template": "＋ Create / update template",
        "staff_add_contact": "＋ Add performer",
        "staff_add_role": "＋ Add role",
        "staff_roles": "Roles and instruments",
        "staff_role_prompt": (
            "Enter a role, for example Guitar, Vocals or Sound engineer.\n/cancel — cancel."
        ),
        "staff_contact_prompt": (
            "Enter a role and a known @username or Telegram ID, separated by two "
            "spaces:\nGuitar  @musician\nThe person must first send /start to the "
            "bot.\n/cancel — cancel."
        ),
        "staff_template_prompt": (
            "First line: lineup name. Then one role per line; optionally add a "
            "default performer after two spaces:\nFull band\nVocals  "
            "@singer\nGuitar\nBass\nInvitations are sent separately.\n/cancel — cancel."
        ),
        "staff_busy_prompt": (
            "Enter a date or inclusive range:\n22.12\n22.12 — 25.12\nNo reason is "
            "required; others only see that you are busy.\n/cancel — cancel."
        ),
        "staff_my": "🎸 My performances and invitations",
        "staff_busy_menu": "📅 My availability",
        "staff_busy_add": "＋ Mark busy",
        "staff_busy_remove": "Remove busy period",
        "staff_candidates": "Choose performer",
        "staff_not_needed": "Instrument not required",
        "staff_needed": "Required — find a performer",
        "staff_vacant": "unassigned",
        "staff_skipped": "not required",
        "staff_unknown": "no known conflicts",
        "staff_conflicts": "possible conflict",
        "staff_tentative": "pending invitations",
        "staff_busy": (
            "There is a booking or busy period on this date. Events are all-day, "
            "so this is a possible conflict."
        ),
        "staff_force_invite": "Invite anyway",
        "staff_force_yes": "Confirm despite conflict",
        "staff_confirm": "✅ Confirm",
        "staff_decline": "Cannot attend",
        "staff_conditional": "Can attend if I find a substitute",
        "staff_pending": "awaiting reply",
        "staff_confirmed": "confirmed",
        "staff_conditional_status": "seeking substitute",
        "staff_accepted": "candidate accepted — needs approval",
        "staff_declined": "declined",
        "staff_cancelled": "cancelled",
        "staff_replaced": "replaced",
        "staff_reconfirm": "confirm new date",
        "staff_details": "Open invitation",
        "staff_apply": "Apply lineup",
        "staff_lineup_exists": (
            "A lineup already exists. Edit its roles and performers individually."
        ),
        "staff_filled": (
            "This role is already filled. Request a substitution or cancel the "
            "current assignment first."
        ),
        "staff_hidden": (
            "The administrator marked this performer as Do not suggest for this "
            "role. Change the contact preference first."
        ),
        "staff_preferred": "Suggest first",
        "staff_normal": "Can invite",
        "staff_hidden_label": "Do not suggest",
        "staff_worked": "Mark as performed",
        "staff_experience": "Recorded performances: {count}",
        "staff_request": "Request substitute",
        "staff_find": "Find substitute",
        "staff_approve_swap": "✅ Approve substitute",
        "staff_cancel_request": "Cancel substitution request",
        "staff_waiting": (
            "Your original commitment remains until the administrator approves a substitute."
        ),
        "staff_private": "Open invitations and availability in the bot's private chat.",
        "staff_invitation_notice": "Performance invitation",
        "staff_reply_notice": "Invitation reply received",
        "staff_replacement_notice": "Substitute requested",
        "staff_swapped_notice": "Substitution approved",
        "staff_busy_notice": "A participant marked this performance date as busy",
        "staff_date_changed_notice": "Date changed. Please confirm again.",
        "staff_cancelled_notice": "Participation cancelled",
        "staff_confirmed_notice": "Your substitution was approved: participation confirmed",
        "staff_released_notice": (
            "Your substitute was approved. The original commitment is released. "
            "Confirm any new invitation separately."
        ),
        "staff_replacement_cancelled_notice": (
            "Substitution request cancelled. The original commitment remains."
        ),
        "staff_cancel_invite": "Cancel participation / invitation",
        "staff_cancel_check": "Cancel this person's participation or invitation?",
        "staff_guest_list": "View approved setlist",
        "staff_guest_pdf": "Printable PDF",
        "staff_delivery_failed": (
            "Could not deliver the invitation. Ask the person to send /start and "
            "unblock the bot, then retry."
        ),
        "staff_retry": "Retry delivery",
        "staff_personal_calendar": "My confirmed performances",
    },
}
