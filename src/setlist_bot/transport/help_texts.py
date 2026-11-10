"""Help topics in the same languages as the command menus."""

HELP_TEXTS = {
    "ru": {
        "help_intro": (
            "Справка по setListBot. Выберите тему.\n\nПоддержка — /support. Отмена текущего "
            "ввода — /cancel."
        ),
        "help_back": "◀️ К темам помощи",
        "help_title_start": "Начать работу",
        "help_title_events": "Выступления",
        "help_title_catalog": "Репертуар",
        "help_title_setlists": "Сет-листы и PDF",
        "help_title_team": "Команда и приглашения",
        "help_title_calendar": "Календарь",
        "help_start": (
            "Начать работу\n\nДобавьте бота в общий чат и отправьте /new_band Название. "
            "Начать может любой участник. Владелец Telegram-чата подтверждает подключение.\n"
            "Для вступления отправьте /register и укажите роли. Заявку одобрит администратор "
            "коллектива.\n"
            "В личке /groups открывает ваши коллективы, /profile — профиль.\n"
            "Язык интерфейса — /language ru, язык коллектива — /group_language ru.\n"
            "Владелец может перенести коллектив в другой чат из /groups.\n\n"
            "Бот собирает обезличенную статистику действий без содержания переписки и хранит "
            "подробные события 90 дней."
        ),
        "help_events": (
            "Выступления\n\n/concerts открывает выступления коллектива. ✅ означает, что сет-лист "
            "утверждён.\n"
            "Создание администратором — /concert 23.04 Клуб Космос. 45мин. Сольник. Москва. "
            "Дату и другие данные можно изменить в опциях выступления.\n"
            "В личке /my_gigs открывает расписание, /all_my_concerts — сводку выступлений.\n"
            "Напоминания — /reminders. Удаление — /delete_concert и /delete_group. "
            "Удалённые записи можно восстановить через /trash в течение 30 дней. ZIP общих данных "
            "— /backup."
        ),
        "help_catalog": (
            "Репертуар\n\n/catalog открывает каталог, /catalog Комета ищет песню.\n"
            "Добавление — /add_new_songs Комета  03:38  свет  110  комментарий. "
            "Между полями два пробела или табуляция. Последние три поля необязательны, - оставляет "
            "поле пустым. "
            "Можно отправить таблицу ответом на запрос команды. Поиск в интернете доступен "
            "администратору из каталога.\n"
            "Правка — /edit Комета  Новое название  03:38  110  свет  комментарий.\n"
            "Копия — /copy Комета  Комета Live  05:00. Алиас — /alias Комета  Comet.\n"
            "Архив — /archived, /archive Комета, /unarchive Комета. "
            "Предложения участников администратор рассматривает через /proposals."
        ),
        "help_setlists": (
            "Сет-листы и PDF\n\n/setlist открывает список, /shared — общий вариант. "
            "В редакторе перетаскивайте песни, добавляйте паузы и открывайте историю.\n"
            "В личке правки сохраняются в вашем черновике. «Предложить группе» или /publish "
            "публикует вариант в общем чате для согласования.\n"
            "Совместимые команды — /add Комета, /suggestion, /remove 2, /move 2  3, /pause 30. "
            "Для /add и /suggestion вводите по одной песне или паузе на строку.\n"
            "Средняя пауза для новых списков — /group_pause 5 (администратор). "
            "Оформление Telegram-списков и ограничения предложений относятся ко всему коллективу.\n"
            "История и версии — /history, /versions. Утверждение и возврат в работу — /approve, "
            "/reopen. "
            "Участник может запросить утверждение у администратора. "
            "PDF утверждённой версии — /export, для музыкантов и световика."
        ),
        "help_team": (
            "Команда и приглашения\n\nАдминистратор управляет командой и составами через /team, "
            "участниками и правами через /members. /lineup открывает состав выбранного "
            "выступления.\n"
            "Приглашения и запросы подмены приходят в личку. "
            "Через /my_gigs можно открыть свои приглашения и участия, "
            "через /set_no_concert_days — отметить недоступные даты. Совпадение дат — возможное "
            "пересечение."
        ),
        "help_calendar": (
            "Календарь\n\n/calendar выдаёт ссылку для подписки в Google Calendar или Apple "
            "Calendar. "
            "Есть отдельная подписка на подтверждённые участия. "
            "Храните ссылку как пароль. Бот публикует расписание по ссылке и не читает внешние "
            "календари."
        ),
    },
    "en": {
        "help_intro": (
            "Help with setListBot. Choose a topic.\n\nContact /support for help. Use /cancel "
            "to cancel the current input."
        ),
        "help_back": "◀️ Help topics",
        "help_title_start": "Getting started",
        "help_title_events": "Performances",
        "help_title_catalog": "Repertoire",
        "help_title_setlists": "Setlists and PDFs",
        "help_title_team": "Team and invitations",
        "help_title_calendar": "Calendar",
        "help_start": (
            "Getting started\n\nAdd the bot to the group chat and send /new_band Name. "
            "Any member can start registration. The Telegram chat owner confirms it.\n"
            "Send /register and enter your roles to join. A band administrator approves the "
            "request.\n"
            "In private chat, /groups opens your bands and /profile opens your profile.\n"
            "Set your language with /language en or the band language with /group_language en.\n"
            "The band owner can move to another chat from /groups.\n\n"
            "The bot collects pseudonymous usage statistics without message content and retains "
            "detailed events for 90 days."
        ),
        "help_events": (
            "Performances\n\n/concerts opens the band's performances. ✅ marks approved setlists.\n"
            "An administrator can create a performance with /concert 23.04 Cosmos Club. 45min. "
            "Solo. Moscow. "
            "Change the date and other details in performance options.\n"
            "In private chat, /my_gigs opens your schedule and /all_my_concerts opens a "
            "performance overview.\n"
            "Use /reminders for reminders, /delete_concert and /delete_group to delete, "
            "/trash to restore within 30 days, and /backup for a ZIP of shared data."
        ),
        "help_catalog": (
            "Repertoire\n\nUse /catalog to open the catalog or /catalog Comet to search.\n"
            "Add songs with /add_new_songs Comet  03:38  lights  110  comment. "
            "Separate fields with two spaces or a tab. The last three fields are optional and - "
            "leaves a field empty. "
            "You can reply to the command prompt with a table. Administrators can also import "
            "songs online from the catalog.\n"
            "Edit with /edit Comet  New title  03:38  110  lights  comment.\n"
            "Copy with /copy Comet  Comet Live  05:00, or add an alias with /alias Comet  "
            "Alternative title.\n"
            "Archive commands are /archived, /archive Comet, and /unarchive Comet. "
            "Administrators review member suggestions with /proposals."
        ),
        "help_setlists": (
            "Setlists and PDFs\n\n/setlist opens the setlist and /shared opens the shared version. "
            "Drag songs, add pauses and browse history in the editor.\n"
            "Private edits stay in your draft. Suggest to the band or use /publish to post your "
            "variant for discussion.\n"
            "Compatible commands include /add Comet, /suggestion, /remove 2, /move 2  3 and /pause "
            "30. "
            "For /add and /suggestion, enter one song or pause per line.\n"
            "Administrators set the average pause for new setlists with /group_pause 5. "
            "Telegram formatting and proposal rules apply to the whole band.\n"
            "Browse /history and /versions. Use /approve to approve and /reopen to return to "
            "editing. "
            "Members can request approval from an administrator. "
            "Use /export to get approved PDFs for performers and lighting."
        ),
        "help_team": (
            "Team and invitations\n\nAdministrators use /team for team and lineups and "
            "/members for membership and permissions. /lineup opens the selected performance "
            "lineup.\n"
            "Invitations and substitutions arrive in private chat. Use /my_gigs for invitations "
            "and "
            "assignments and /set_no_concert_days to mark unavailable dates. Same-day bookings can "
            "overlap."
        ),
        "help_calendar": (
            "Calendar\n\n/calendar provides a subscription link for Google Calendar or Apple "
            "Calendar. "
            "You can subscribe to confirmed assignments separately. "
            "Keep the link private. The bot publishes the schedule and does not read external "
            "calendars."
        ),
    },
}
