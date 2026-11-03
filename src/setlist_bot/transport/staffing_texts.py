"""RU/EN copy for staffing; no external calendar authorization is requested."""

TEXTS = {
    "ru": {
        "staff_more": "ещё {count}",
        "staff_all_invites": "Все приглашения",
        "staff_invite_defaults": "Пригласить основной состав",
        "staff_team": "👥 Команда и составы",
        "staff_lineup": "👥 Состав выступления",
        "staff_templates": "Составы",
        "staff_contacts": "Знакомые исполнители",
        "staff_add_template": "＋ Создать состав",
        "staff_add_contact": "＋ Добавить исполнителя",
        "staff_add_role": "＋ Добавить роль",
        "staff_roles": "Роли и инструменты",
        "staff_role_prompt": (
            "Введите название роли: например, Гитара, Вокал, Звукорежиссёр.\n/cancel — отмена."
        ),
        "staff_contact_prompt": (
            "Введите роль и известный боту @ник или Telegram ID через пробел, дефис или двоеточие. "
            "Например:\nГитара  @musician\nЧеловек должен предварительно открыть "
            "бота и отправить /start.\n/cancel — отмена."
        ),
        "staff_template_prompt": (
            "Первая строка — название состава. Далее по одной роли на строку. "
            "Через пробел, дефис или двоеточие можно указать основного исполнителя:\n"
            "Большой\nВокал  "
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
        "staff_guest_list": "Открыть сет-лист",
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
        "staff_templates": "Lineups",
        "staff_contacts": "Known performers",
        "staff_add_template": "＋ Create lineup",
        "staff_add_contact": "＋ Add performer",
        "staff_add_role": "＋ Add role",
        "staff_roles": "Roles and instruments",
        "staff_role_prompt": (
            "Enter a role, for example Guitar, Vocals or Sound engineer.\n/cancel — cancel."
        ),
        "staff_contact_prompt": (
            "Enter a role and a known @username or Telegram ID, separated by a space, "
            "dash or colon:\nGuitar  @musician\nThe person must first send /start to the "
            "bot.\n/cancel — cancel."
        ),
        "staff_template_prompt": (
            "First line: lineup name. Then one role per line; optionally add a "
            "default performer after a space, dash or colon:\nFull band\nVocals  "
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
        "staff_guest_list": "Open setlist",
        "staff_guest_pdf": "Printable PDF",
        "staff_delivery_failed": (
            "Could not deliver the invitation. Ask the person to send /start and "
            "unblock the bot, then retry."
        ),
        "staff_retry": "Retry delivery",
        "staff_personal_calendar": "My confirmed performances",
    },
}

TEXTS["ru"].update(
    {
        "staff_main_name": "Основной",
        "staff_onboarding": (
            "Настройте основной состав: роли и основных исполнителей. При создании "
            "выступления единственный состав подставится автоматически, а из "
            "нескольких можно будет выбрать. Перед отправкой приглашений бот покажет "
            "занятость участников."
        ),
        "staff_setup_main": "Настроить основной состав",
        "staff_later": "Позже",
        "staff_main_prompt": (
            "Введите роли основного состава, по одной на строку. Можно добавить @ник "
            "или Telegram ID через пробел, дефис или двоеточие:\nВокал @singer\nБас-"
            "гитара: @bassist\nЗвукорежиссёр\n/cancel — отмена."
        ),
        "staff_unknown_person": (
            "Не удалось найти исполнителя: попросите его открыть бота и отправить "
            "/start. Перешлите приглашение ниже, затем повторите сохранение. Ваш ввод"
            " сохранён."
        ),
        "staff_forward_invite": (
            "Привет! Коллектив «{name}» использует setListBot для организации "
            "выступлений и подготовки сет-листов. Открой бота {link} и нажми /start, "
            "чтобы получать приглашения на выступления и подтверждать участие."
        ),
        "staff_selected": "Выбран состав: {name}",
        "staff_no_lineup": "Состав не выбран",
        "staff_change_lineup": "Изменить {name} состав",
        "staff_choose_lineup": "Выбрать состав",
        "staff_change_person": "поменять {name}",
        "staff_wait_person": "ждём {name}",
        "staff_invite_person": "пригласить",
        "staff_add_participant": "+ участник",
        "staff_check_send": "Проверить занятость и пригласить",
        "staff_group_lineups": "Составы {name}",
        "staff_rename": "Изменить название",
        "staff_copy": "Копировать состав",
        "staff_delete_lineup": "Удалить состав",
        "staff_role_options": "Основной исполнитель и роль в составе",
        "staff_change_default": "Изменить исполнителя",
        "staff_remove_role": "Удалить роль",
        "staff_delete_check": "Удалить этот состав? Составы уже созданных выступлений сохранятся.",
        "staff_replace_check": (
            "Заменить состав выступления? Текущие приглашения и участия будут "
            "отменены. Новый состав можно проверить перед отправкой приглашений."
        ),
        "staff_remove_check": (
            "Удалить роль из выступления? Приглашения и подтверждённое участие по "
            "этой роли будут отменены."
        ),
        "staff_remind": "Отправить напоминание в личку",
        "staff_invite_other": "Пригласить другого",
        "staff_slot_explanation": (
            "Здесь можно напомнить об ответе, пригласить другого исполнителя или "
            "убрать роль. При подмене подтверждённый участник остаётся в составе до "
            "согласования замены."
        ),
        "staff_reminder_queued": (
            "Отправка поставлена в очередь. Повторные нажатия не создают несколько напоминаний."
        ),
        "staff_event_role_prompt": (
            "Введите роль и, если хотите, основного исполнителя: Гитара @musician. "
            "Приглашение отправляется отдельно.\n/cancel — отмена."
        ),
        "staff_invite_person_prompt": (
            "Введите известный боту @ник или Telegram ID исполнителя.\n/cancel — отмена."
        ),
        "staff_template_add_prompt": (
            "Введите роль и, при необходимости, @ник исполнителя: Бэк-вокал: @singer."
            "\n/cancel — отмена."
        ),
        "staff_template_person_prompt": (
            "Введите @ник или Telegram ID нового основного исполнителя. Чтобы "
            "оставить роль свободной, введите дефис (-).\n/cancel — отмена."
        ),
        "staff_template_rename_prompt": "Введите новое название состава.\n/cancel — отмена.",
        "staff_template_copy_prompt": "Введите название копии состава.\n/cancel — отмена.",
        "staff_choose_invite": "Пригласить {name}? Перед отправкой проверим занятость.",
        "staff_creation_choose": (
            "Выберите состав для нового выступления. Перед созданием покажем все "
            "данные для подтверждения."
        ),
        "staff_without_lineup": "Продолжить без состава",
        "staff_creation_selected": "Состав: {name}",
        "staff_creation_none": (
            "Состав пока не настроен. Его можно задать сейчас или после создания выступления."
        ),
        "staff_creation_check": "Сейчас проверю занятость участников перед отправкой приглашений.",
    }
)

TEXTS["en"].update(
    {
        "staff_main_name": "Main",
        "staff_onboarding": (
            "Set up the main lineup with roles and default performers. A single "
            "lineup is applied automatically; choose between multiple lineups. Review"
            " availability before sending invitations."
        ),
        "staff_setup_main": "Set up main lineup",
        "staff_later": "Later",
        "staff_main_prompt": (
            "Enter main lineup roles, one per line. Optionally add @username or "
            "Telegram ID after a space, dash or colon:\nVocals @singer\nBass: @bassist\n"
            "Sound engineer\n/cancel — cancel."
        ),
        "staff_unknown_person": (
            "Performer not found. Ask them to open the bot and send /start. Forward "
            "the invitation below, then retry. Your input is saved."
        ),
        "staff_forward_invite": (
            "Hi! The band “{name}” uses setListBot to organize performances and "
            "prepare setlists. Open the bot {link} and send /start to receive "
            "performance invitations and confirm attendance."
        ),
        "staff_selected": "Selected lineup: {name}",
        "staff_no_lineup": "No lineup selected",
        "staff_change_lineup": "Change {name} lineup",
        "staff_choose_lineup": "Choose lineup",
        "staff_change_person": "replace {name}",
        "staff_wait_person": "waiting for {name}",
        "staff_invite_person": "invite",
        "staff_add_participant": "+ participant",
        "staff_check_send": "Check availability and invite",
        "staff_group_lineups": "Lineups · {name}",
        "staff_rename": "Rename",
        "staff_copy": "Copy lineup",
        "staff_delete_lineup": "Delete lineup",
        "staff_role_options": "Default performer and role",
        "staff_change_default": "Change performer",
        "staff_remove_role": "Remove role",
        "staff_delete_check": (
            "Delete this lineup? Existing performance lineups will remain unchanged."
        ),
        "staff_replace_check": (
            "Replace the performance lineup? Current invitations and assignments will"
            " be cancelled. Review the new lineup before inviting."
        ),
        "staff_remove_check": (
            "Remove this role? Its pending invitations and confirmed participation "
            "will be cancelled."
        ),
        "staff_remind": "Send a private reminder",
        "staff_invite_other": "Invite someone else",
        "staff_slot_explanation": (
            "Send a reminder, choose another performer, or remove the role. A "
            "confirmed performer remains committed until a substitution is approved."
        ),
        "staff_reminder_queued": (
            "Delivery queued. Repeated clicks do not create duplicate reminders."
        ),
        "staff_event_role_prompt": (
            "Enter a role and optionally a performer: Guitar @musician. Invitations "
            "are sent separately.\n/cancel — cancel."
        ),
        "staff_invite_person_prompt": "Enter a known @username or Telegram ID.\n/cancel — cancel.",
        "staff_template_add_prompt": (
            "Enter a role and optionally its performer: Backing vocals: @singer.\n/cancel — cancel."
        ),
        "staff_template_person_prompt": (
            "Enter @username or Telegram ID of the new default performer. Enter - to "
            "leave the role vacant.\n/cancel — cancel."
        ),
        "staff_template_rename_prompt": "Enter a new lineup name.\n/cancel — cancel.",
        "staff_template_copy_prompt": "Enter a name for the lineup copy.\n/cancel — cancel.",
        "staff_choose_invite": "Invite {name}? Availability will be checked before sending.",
        "staff_creation_choose": (
            "Choose a lineup. Review all details before creating the performance."
        ),
        "staff_without_lineup": "Continue without a lineup",
        "staff_creation_selected": "Lineup: {name}",
        "staff_creation_none": "No lineups yet. Set one up now or after creating the performance.",
        "staff_creation_check": "I will check participant availability before sending invitations.",
    }
)

TEXTS["ru"].update(
    {
        "staff_edit_role_name": "Изменить название роли",
        "staff_template_role_name_prompt": (
            "Введите новое название роли в этом составе.\n/cancel — отмена."
        ),
    }
)
TEXTS["en"].update(
    {
        "staff_edit_role_name": "Change role name",
        "staff_template_role_name_prompt": (
            "Enter the new role name for this lineup.\n/cancel — cancel."
        ),
    }
)

TEXTS["ru"].update(
    {
        "staff_open_lineup": "Открыть состав выступления",
        "staff_empty_lineup": "В составе пока нет ролей.",
    }
)
TEXTS["en"].update(
    {
        "staff_open_lineup": "Open performance lineup",
        "staff_empty_lineup": "There are no roles in this lineup yet.",
    }
)
