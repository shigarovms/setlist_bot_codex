# Тексты с контекстом коллектива — согласовано

Дата подготовки и согласования — 11 ноября 2026. Статус — формулировки согласованы с исключением для карточки песни непосредственно под каталогом.

В документе 96 шаблонов ответов. Для каждого приведены текущий и предлагаемый варианты RU и EN. Фигурные скобки обозначают реальные данные, которые подставит бот.

## Общее оформление

- Ответ о коллективе начинается с его названия отдельной строкой.
- Ответ о выступлении начинается с названия коллектива и строки даты, названия выступления и города. Пустые части строки пропускаются.
- Внутри состава при необходимости добавляются название состава, роль и исполнитель.
- Пустые списки получают такой же контекст, как заполненные.
- В календарных ответах первая строка называет область подписки. Для одного коллектива это его название, для общего календаря — «Сводный календарь всех коллективов», для личного — «Мои подтверждённые участия». Английские варианты — Combined calendar for all groups и My confirmed assignments.
- Название коллектива берётся из объекта или сохранённой кнопки после проверки доступа. Переключение текущего коллектива не меняет подпись старой кнопки.
- Исключение для шаблонов 12–13 — карточка песни и меню её редактирования, открытые нажатием на название в таблице каталога. Если сообщение находится непосредственно под исходной таблицей того же коллектива, название коллектива опускается. В остальных случаях оно показывается. Самого факта открытия из каталога недостаточно для исключения.

Список участников, названия песен, значения полей, предупреждения о последствиях и действующие подписи кнопок сохранены в шаблонах. В подтверждениях архивации и восстановления песни предложены дополнительные явные вопросы о действии.

Общие экраны выбора коллективов, личный профиль, личная занятость, сводное расписание и общая справка не относятся к одному выбранному коллективу. Для них такой заголовок не предлагается. Экраны, где коллектив уже показан, повторный заголовок не получают.

## Разделы

1. Участники, права и настройки коллектива
2. Песни, редактирование и импорт репертуара
3. Выступления и история
4. Составы и исполнители
5. Предварительный состав выступления
6. Запросы ввода в составах
7. Календарные подписки
8. Перенос коллектива в другой чат

## Участники, права и настройки коллектива

### RU

| № | Экран или действие | Сейчас | Предлагается |
|---|---|---|---|
| 01 | /members — список и заявки | Участники и права · заявки<br><br>{Имена · права · статусы · заявки} | {Коллектив}<br><br>Участники и права · заявки<br><br>{Имена · права · статусы · заявки} |
| 02 | /proposals — предложения | {Автор}<br>{Предложение по песне или альтернативному названию} | {Коллектив}<br><br>Предложения по репертуару<br><br>{Автор}<br>{Предложение по песне или альтернативному названию} |
| 03 | /proposals — пустой список | Пока пусто. | {Коллектив}<br><br>Предложения по репертуару<br><br>Пока пусто. |
| 04 | Напоминания включены | Напоминания включены: накануне выступления, с 14:00 до 15:00 по Москве, если сет-лист не утверждён. | {Коллектив}<br><br>Напоминания включены: накануне выступления, с 14:00 до 15:00 по Москве, если сет-лист не утверждён. |
| 05 | Напоминания отключены | Напоминания для этого коллектива отключены. | {Коллектив}<br><br>Напоминания для этого коллектива отключены. |
| 06 | /backup — готовый архив | ZIP содержит каталог, выступления, общие версии и опубликованные предложения в JSON и CSV. Личные черновики и Telegram ID не включены. Восстановление из корзины: /trash; обратный импорт ZIP пока не реализован. | {Коллектив}<br><br>ZIP содержит каталог, выступления, общие версии и опубликованные предложения в JSON и CSV. Личные черновики и Telegram ID не включены. Восстановление из корзины: /trash; обратный импорт ZIP пока не реализован. |
| 07 | Назначить администратора | Проверьте и подтвердите:<br><br>Назначить администратором: {Исполнитель}? | {Коллектив}<br><br>Проверьте и подтвердите:<br><br>Назначить администратором: {Исполнитель}? |
| 08 | Снять права администратора | Проверьте и подтвердите:<br><br>Снять права администратора: {Исполнитель}? | {Коллектив}<br><br>Проверьте и подтвердите:<br><br>Снять права администратора: {Исполнитель}? |
| 09 | Результат принятия или отклонения заявки участника | Готово. | {Коллектив}<br><br>Готово. |
| 10 | Результат решения по предложению репертуара | Готово. | {Коллектив}<br><br>Готово. |
| 11 | Результат изменения языка общего чата | Готово. | {Коллектив}<br><br>Готово. |

### EN

| № | Экран или действие | Сейчас | Предлагается |
|---|---|---|---|
| 01 | /members — список и заявки | Membership and permissions · requests<br><br>{Names · permissions · statuses · requests} | {Band}<br><br>Membership and permissions · requests<br><br>{Names · permissions · statuses · requests} |
| 02 | /proposals — предложения | {Author}<br>{Song or alternative-title proposal} | {Band}<br><br>Repertoire proposals<br><br>{Author}<br>{Song or alternative-title proposal} |
| 03 | /proposals — пустой список | Nothing here yet. | {Band}<br><br>Repertoire proposals<br><br>Nothing here yet. |
| 04 | Напоминания включены | Group reminders enabled: the day before, between 14:00 and 15:00 Moscow time, if the setlist is not approved. | {Band}<br><br>Group reminders enabled: the day before, between 14:00 and 15:00 Moscow time, if the setlist is not approved. |
| 05 | Напоминания отключены | Reminders disabled for this group. | {Band}<br><br>Reminders disabled for this group. |
| 06 | /backup — готовый архив | The ZIP contains the catalog, concerts, shared versions and published proposals as JSON and CSV. Private drafts and Telegram IDs are excluded. Restore from trash using /trash; ZIP import is not implemented yet. | {Band}<br><br>The ZIP contains the catalog, concerts, shared versions and published proposals as JSON and CSV. Private drafts and Telegram IDs are excluded. Restore from trash using /trash; ZIP import is not implemented yet. |
| 07 | Назначить администратора | Review and confirm:<br><br>Grant administrator rights: {Performer}? | {Band}<br><br>Review and confirm:<br><br>Grant administrator rights: {Performer}? |
| 08 | Снять права администратора | Review and confirm:<br><br>Revoke administrator rights: {Performer}? | {Band}<br><br>Review and confirm:<br><br>Revoke administrator rights: {Performer}? |
| 09 | Результат принятия или отклонения заявки участника | Done. | {Band}<br><br>Done. |
| 10 | Результат решения по предложению репертуара | Done. | {Band}<br><br>Done. |
| 11 | Результат изменения языка общего чата | Done. | {Band}<br><br>Done. |

## Песни, редактирование и импорт репертуара

Для строк 12–13 в колонке «Предлагается» показан полный вариант. В сообщении непосредственно под исходным каталогом того же коллектива первая строка `{Коллектив}` / `{Band}` и следующий за ней пустой интервал опускаются. Остальной текст одинаков.

### RU

| № | Экран или действие | Сейчас | Предлагается |
|---|---|---|---|
| 12 | Карточка песни | {Название песни} · {Длительность}<br>{BPM, если указан}<br>{Свет, если указан}<br>{Комментарий, если указан} | {Коллектив}<br><br>{Название песни} · {Длительность}<br>{BPM, если указан}<br>{Свет, если указан}<br>{Комментарий, если указан} |
| 13 | Меню редактирования песни | {Название песни} · {Длительность}<br>{BPM, если указан}<br>{Свет, если указан}<br>{Комментарий, если указан} | {Коллектив}<br><br>{Название песни} · {Длительность}<br>{BPM, если указан}<br>{Свет, если указан}<br>{Комментарий, если указан} |
| 14 | Добавление песни — подтверждение | Проверьте и подтвердите:<br><br>{Название песни} · {Длительность}<br>{BPM, если указан}<br>{Свет, если указан}<br>{Комментарий, если указан} | {Коллектив}<br><br>Проверьте и подтвердите:<br><br>{Название песни} · {Длительность}<br>{BPM, если указан}<br>{Свет, если указан}<br>{Комментарий, если указан} |
| 15 | Изменение песни — подтверждение | Проверьте и подтвердите:<br><br>{Название песни} · {Длительность}<br>{BPM, если указан}<br>{Свет, если указан}<br>{Комментарий, если указан} | {Коллектив}<br><br>Проверьте и подтвердите:<br><br>{Название песни} · {Длительность}<br>{BPM, если указан}<br>{Свет, если указан}<br>{Комментарий, если указан} |
| 16 | Копирование песни — подтверждение | Проверьте и подтвердите:<br><br>{Название песни} · {Длительность}<br>{BPM, если указан}<br>{Свет, если указан}<br>{Комментарий, если указан} | {Коллектив}<br><br>Проверьте и подтвердите:<br><br>{Название песни} · {Длительность}<br>{BPM, если указан}<br>{Свет, если указан}<br>{Комментарий, если указан} |
| 17 | Дополнение пустых полей существующей песни — подтверждение | Проверьте и подтвердите:<br><br>{Название песни}<br>{Дополняемые поля}<br>{Примечание к импорту} | {Коллектив}<br><br>Проверьте и подтвердите:<br><br>{Название песни}<br>{Дополняемые поля}<br>{Примечание к импорту} |
| 18 | Архивация песни — подтверждение | Проверьте и подтвердите:<br><br>{Название песни} · {Длительность}<br>{BPM, если указан}<br>{Свет, если указан}<br>{Комментарий, если указан} | {Коллектив}<br><br>Перенести песню в архив?<br><br>{Название песни} · {Длительность}<br>{BPM, если указан}<br>{Свет, если указан}<br>{Комментарий, если указан} |
| 19 | Восстановление песни — подтверждение | Проверьте и подтвердите:<br><br>{Название песни} · {Длительность}<br>{BPM, если указан}<br>{Свет, если указан}<br>{Комментарий, если указан} | {Коллектив}<br><br>Восстановить песню из архива?<br><br>{Название песни} · {Длительность}<br>{BPM, если указан}<br>{Свет, если указан}<br>{Комментарий, если указан} |
| 20 | Песня сохранена | Песня «{name}» сохранена. | {Коллектив}<br><br>Песня «{name}» сохранена. |
| 21 | Изменение предложено администратору | Предложение отправлено администратору. | {Коллектив}<br><br>Предложение отправлено администратору. |
| 22 | Альтернативное название сохранено | Готово. | {Коллектив}<br><br>Готово. |
| 23 | Песня перенесена в архив или восстановлена | Готово. | {Коллектив}<br><br>Готово. |
| 24 | Импорт завершён | Добавлено песен: {count}. Дополнено песен: {supplemented}. Остальные повторы пропущены. | {Коллектив}<br><br>Добавлено песен: {count}. Дополнено песен: {supplemented}. Остальные повторы пропущены. |
| 25 | Импорт — определение заголовков | Есть ли в таблице строка заголовков? | {Коллектив}<br><br>Есть ли в таблице строка заголовков? |
| 26 | Импорт — неверные или нераспознанные колонки | Не удалось разобрать таблицу. Давайте уточним столбцы.<br><br>Есть ли в таблице строка заголовков? | {Коллектив}<br><br>Не удалось разобрать таблицу. Давайте уточним столбцы.<br><br>Есть ли в таблице строка заголовков? |
| 27 | Импорт — выбор столбца для каждого поля | Выберите столбец для поля «{field}»: | {Коллектив}<br><br>Выберите столбец для поля «{field}»: |

### EN

| № | Экран или действие | Сейчас | Предлагается |
|---|---|---|---|
| 12 | Карточка песни | {Song title} · {Duration}<br>{BPM, if set}<br>{Lighting, if set}<br>{Comment, if set} | {Band}<br><br>{Song title} · {Duration}<br>{BPM, if set}<br>{Lighting, if set}<br>{Comment, if set} |
| 13 | Меню редактирования песни | {Song title} · {Duration}<br>{BPM, if set}<br>{Lighting, if set}<br>{Comment, if set} | {Band}<br><br>{Song title} · {Duration}<br>{BPM, if set}<br>{Lighting, if set}<br>{Comment, if set} |
| 14 | Добавление песни — подтверждение | Review and confirm:<br><br>{Song title} · {Duration}<br>{BPM, if set}<br>{Lighting, if set}<br>{Comment, if set} | {Band}<br><br>Review and confirm:<br><br>{Song title} · {Duration}<br>{BPM, if set}<br>{Lighting, if set}<br>{Comment, if set} |
| 15 | Изменение песни — подтверждение | Review and confirm:<br><br>{Song title} · {Duration}<br>{BPM, if set}<br>{Lighting, if set}<br>{Comment, if set} | {Band}<br><br>Review and confirm:<br><br>{Song title} · {Duration}<br>{BPM, if set}<br>{Lighting, if set}<br>{Comment, if set} |
| 16 | Копирование песни — подтверждение | Review and confirm:<br><br>{Song title} · {Duration}<br>{BPM, if set}<br>{Lighting, if set}<br>{Comment, if set} | {Band}<br><br>Review and confirm:<br><br>{Song title} · {Duration}<br>{BPM, if set}<br>{Lighting, if set}<br>{Comment, if set} |
| 17 | Дополнение пустых полей существующей песни — подтверждение | Review and confirm:<br><br>{Song title}<br>{Fields to supplement}<br>{Import note} | {Band}<br><br>Review and confirm:<br><br>{Song title}<br>{Fields to supplement}<br>{Import note} |
| 18 | Архивация песни — подтверждение | Review and confirm:<br><br>{Song title} · {Duration}<br>{BPM, if set}<br>{Lighting, if set}<br>{Comment, if set} | {Band}<br><br>Move this song to the archive?<br><br>{Song title} · {Duration}<br>{BPM, if set}<br>{Lighting, if set}<br>{Comment, if set} |
| 19 | Восстановление песни — подтверждение | Review and confirm:<br><br>{Song title} · {Duration}<br>{BPM, if set}<br>{Lighting, if set}<br>{Comment, if set} | {Band}<br><br>Restore this song from the archive?<br><br>{Song title} · {Duration}<br>{BPM, if set}<br>{Lighting, if set}<br>{Comment, if set} |
| 20 | Песня сохранена | Song “{name}” saved. | {Band}<br><br>Song “{name}” saved. |
| 21 | Изменение предложено администратору | Proposal sent for administrator review. | {Band}<br><br>Proposal sent for administrator review. |
| 22 | Альтернативное название сохранено | Done. | {Band}<br><br>Done. |
| 23 | Песня перенесена в архив или восстановлена | Done. | {Band}<br><br>Done. |
| 24 | Импорт завершён | Songs added: {count}. Songs supplemented: {supplemented}. Other duplicates were skipped. | {Band}<br><br>Songs added: {count}. Songs supplemented: {supplemented}. Other duplicates were skipped. |
| 25 | Импорт — определение заголовков | Does the table include a header row? | {Band}<br><br>Does the table include a header row? |
| 26 | Импорт — неверные или нераспознанные колонки | Could not read the table. Let’s clarify the columns.<br><br>Does the table include a header row? | {Band}<br><br>Could not read the table. Let’s clarify the columns.<br><br>Does the table include a header row? |
| 27 | Импорт — выбор столбца для каждого поля | Select the column for “{field}”: | {Band}<br><br>Select the column for “{field}”: |

## Выступления и история

### RU

| № | Экран или действие | Сейчас | Предлагается |
|---|---|---|---|
| 28 | /past_concerts — список | Прошедшие<br><br>{Список выступлений} | {Коллектив}<br><br>Прошедшие<br><br>{Список выступлений} |
| 29 | /past_concerts — нет доступных выступлений | Прошедшие<br><br>В этом разделе нет доступных выступлений. Личные приглашения и участия можно открыть через /my_gigs. | {Коллектив}<br><br>Прошедшие<br><br>В этом разделе нет доступных выступлений. Личные приглашения и участия можно открыть через /my_gigs. |
| 30 | Данные выступления | {Дата} · {Выступление} · {Город}<br><br>Данные выступления<br><br>Эти поля необязательны. Их можно задать сейчас или позже в Опциях сет-листа этого выступления.<br><br>{Поля выступления} | {Коллектив}<br><br>{Дата} · {Выступление} · {Город}<br><br>Данные выступления<br><br>Эти поля необязательны. Их можно задать сейчас или позже в Опциях сет-листа этого выступления.<br><br>{Поля выступления} |
| 31 | Выступление создано — данные | Выступление создано.<br><br>{Дата} · {Выступление} · {Город}<br><br>Данные выступления<br><br>Эти поля необязательны. Их можно задать сейчас или позже в Опциях сет-листа этого выступления.<br><br>{Поля выступления} | {Коллектив}<br><br>Выступление создано.<br><br>{Дата} · {Выступление} · {Город}<br><br>Данные выступления<br><br>Эти поля необязательны. Их можно задать сейчас или позже в Опциях сет-листа этого выступления.<br><br>{Поля выступления} |
| 32 | /history и /versions | {Дата} · {Выступление} · {Город}<br><br>{Версии · авторы · даты изменений} | {Коллектив}<br><br>{Дата} · {Выступление} · {Город}<br><br>{Версии · авторы · даты изменений} |
| 33 | Просмотр отдельной исторической версии | {Заголовок версии и сет-лист} | {Коллектив}<br><br>{Дата} · {Выступление} · {Город}<br><br>{Заголовок версии и сет-лист} |
| 34 | Создание выступления — выбор состава | Выберите состав для нового выступления. Перед созданием покажем все данные для подтверждения. | {Коллектив}<br><br>{Дата} · {Выступление} · {Город}<br><br>Выберите состав для нового выступления. Перед созданием покажем все данные для подтверждения. |
| 35 | Создание выступления — финальное подтверждение | Проверьте и подтвердите:<br><br>Создать выступление {date} {title}?<br><br>{Поля выступления}<br><br>{Выбранный состав или «Состав не выбран»} | {Коллектив}<br><br>Проверьте и подтвердите:<br><br>Создать выступление {date} {title}?<br><br>{Поля выступления}<br><br>{Выбранный состав или «Состав не выбран»} |
| 36 | Перенос даты — подтверждение | Проверьте и подтвердите:<br><br>Перенести выступление на {date}? Сет-листы и варианты сохранятся. | {Коллектив}<br><br>{Дата} · {Выступление} · {Город}<br><br>Проверьте и подтвердите:<br><br>Перенести выступление на {date}? Сет-листы и варианты сохранятся. |
| 37 | Выбор типа выступления | Выберите тип выступления или введите свой вариант (до 200 символов). Знак - убирает тип. | {Коллектив}<br><br>{Дата} · {Выступление} · {Город}<br><br>Выберите тип выступления или введите свой вариант (до 200 символов). Знак - убирает тип. |
| 38 | Удаление выступления — подтверждение | Проверьте и подтвердите:<br><br>Удалить выступление «{name}» вместе со всеми версиями сет-листа, предложениями и личными черновиками участников? Каталог песен сохраняется. Восстановление через /trash доступно 30 дней, затем данные выступления будут окончательно удалены. | {Коллектив}<br><br>Проверьте и подтвердите:<br><br>Удалить выступление «{name}» вместе со всеми версиями сет-листа, предложениями и личными черновиками участников? Каталог песен сохраняется. Восстановление через /trash доступно 30 дней, затем данные выступления будут окончательно удалены. |
| 39 | Выступление перенесено в корзину | Выступление «{name}» перемещено в корзину на 30 дней. Восстановление: /trash. | {Коллектив}<br><br>Выступление «{name}» перемещено в корзину на 30 дней. Восстановление: /trash. |
| 40 | Выбор позиции для удаления или перемещения | Выберите позицию. Для перемещения затем выберите, на чьё место её поставить. | {Коллектив}<br><br>{Дата} · {Выступление} · {Город}<br><br>Выберите позицию. Для перемещения затем выберите, на чьё место её поставить. |
| 41 | Корзина — запись удалённого выступления | Выступление «{name}» — восстановление до {until}. | {Коллектив}<br><br>Выступление «{name}» — восстановление до {until}. |

### EN

| № | Экран или действие | Сейчас | Предлагается |
|---|---|---|---|
| 28 | /past_concerts — список | Past concerts<br><br>{Performance list} | {Band}<br><br>Past concerts<br><br>{Performance list} |
| 29 | /past_concerts — нет доступных выступлений | Past concerts<br><br>No performances are available here. Open /my_gigs for invitations and confirmed assignments. | {Band}<br><br>Past concerts<br><br>No performances are available here. Open /my_gigs for invitations and confirmed assignments. |
| 30 | Данные выступления | {Date} · {Performance} · {City}<br><br>Event details<br><br>These fields are optional. Set them now or later in this event’s setlist Options.<br><br>{Performance fields} | {Band}<br><br>{Date} · {Performance} · {City}<br><br>Event details<br><br>These fields are optional. Set them now or later in this event’s setlist Options.<br><br>{Performance fields} |
| 31 | Выступление создано — данные | Event created.<br><br>{Date} · {Performance} · {City}<br><br>Event details<br><br>These fields are optional. Set them now or later in this event’s setlist Options.<br><br>{Performance fields} | {Band}<br><br>Event created.<br><br>{Date} · {Performance} · {City}<br><br>Event details<br><br>These fields are optional. Set them now or later in this event’s setlist Options.<br><br>{Performance fields} |
| 32 | /history и /versions | {Date} · {Performance} · {City}<br><br>{Versions · authors · change dates} | {Band}<br><br>{Date} · {Performance} · {City}<br><br>{Versions · authors · change dates} |
| 33 | Просмотр отдельной исторической версии | {Version heading and setlist} | {Band}<br><br>{Date} · {Performance} · {City}<br><br>{Version heading and setlist} |
| 34 | Создание выступления — выбор состава | Choose a lineup. Review all details before creating the performance. | {Band}<br><br>{Date} · {Performance} · {City}<br><br>Choose a lineup. Review all details before creating the performance. |
| 35 | Создание выступления — финальное подтверждение | Review and confirm:<br><br>Create event on {date} {title}?<br><br>{Performance fields}<br><br>{Selected lineup or No lineup selected} | {Band}<br><br>Review and confirm:<br><br>Create event on {date} {title}?<br><br>{Performance fields}<br><br>{Selected lineup or No lineup selected} |
| 36 | Перенос даты — подтверждение | Review and confirm:<br><br>Move the performance to {date}? Setlists and variants will be preserved. | {Band}<br><br>{Date} · {Performance} · {City}<br><br>Review and confirm:<br><br>Move the performance to {date}? Setlists and variants will be preserved. |
| 37 | Выбор типа выступления | Choose a performance type or enter your own (up to 200 characters). Use - to clear. | {Band}<br><br>{Date} · {Performance} · {City}<br><br>Choose a performance type or enter your own (up to 200 characters). Use - to clear. |
| 38 | Удаление выступления — подтверждение | Review and confirm:<br><br>Delete concert “{name}” with all setlist versions, submissions and members’ private drafts? The song catalog is preserved. Restore using /trash within 30 days, after which concert data will be permanently deleted. | {Band}<br><br>Review and confirm:<br><br>Delete concert “{name}” with all setlist versions, submissions and members’ private drafts? The song catalog is preserved. Restore using /trash within 30 days, after which concert data will be permanently deleted. |
| 39 | Выступление перенесено в корзину | Concert “{name}” moved to trash for 30 days. Restore using /trash. | {Band}<br><br>Concert “{name}” moved to trash for 30 days. Restore using /trash. |
| 40 | Выбор позиции для удаления или перемещения | Select an entry; then select its destination when moving. | {Band}<br><br>{Date} · {Performance} · {City}<br><br>Select an entry; then select its destination when moving. |
| 41 | Корзина — запись удалённого выступления | Concert “{name}” — restore before {until}. | {Band}<br><br>Concert “{name}” — restore before {until}. |

## Составы и исполнители

### RU

| № | Экран или действие | Сейчас | Предлагается |
|---|---|---|---|
| 42 | /lineup — состав выступления | {Роли и исполнители} | {Коллектив}<br><br>{Дата} · {Выступление} · {Город}<br><br>👥 Состав выступления<br><br>{Роли и исполнители} |
| 43 | /lineup — пустой состав | В составе пока нет ролей. | {Коллектив}<br><br>{Дата} · {Выступление} · {Город}<br><br>👥 Состав выступления<br><br>В составе пока нет ролей. |
| 44 | Карточка сохранённого состава | {Название состава}<br><br>{Роли и исполнители} | {Коллектив}<br><br>{Название состава}<br><br>{Роли и исполнители} |
| 45 | Настройка роли сохранённого состава | Основной исполнитель и роль в составе | {Коллектив}<br><br>{Название состава}<br><br>{Роль}<br><br>Основной исполнитель и роль в составе |
| 46 | Удаление сохранённого состава — подтверждение | Удалить этот состав? Составы уже созданных выступлений сохранятся. | {Коллектив}<br><br>{Название состава}<br><br>Удалить этот состав? Составы уже созданных выступлений сохранятся. |
| 47 | Замена состава выступления — подтверждение | Заменить состав выступления? Текущие приглашения и участия будут отменены. Новый состав можно проверить перед отправкой приглашений. | {Коллектив}<br><br>{Дата} · {Выступление} · {Город}<br><br>{Название состава}<br><br>Заменить состав выступления? Текущие приглашения и участия будут отменены. Новый состав можно проверить перед отправкой приглашений. |
| 48 | Карточка постоянного исполнителя | {Роль} · {Исполнитель}<br><br>{Очередь исполнителей этой роли}<br><br>{Профиль исполнителя, если заполнен} | {Коллектив}<br><br>{Роль} · {Исполнитель}<br><br>{Очередь исполнителей этой роли}<br><br>{Профиль исполнителя, если заполнен} |
| 49 | Удаление постоянного назначения — подтверждение | Убрать это назначение из постоянной команды? Знакомство и существующие выступления сохранятся. | {Коллектив}<br><br>{Роль}<br><br>{Исполнитель}<br><br>Убрать это назначение из постоянной команды? Знакомство и существующие выступления сохранятся. |
| 50 | Карточка знакомого исполнителя | {Исполнитель}<br><br>Отмечено участий: {count}<br><br>{Профиль исполнителя, если заполнен} | {Коллектив}<br><br>{Исполнитель}<br><br>Отмечено участий: {count}<br><br>{Профиль исполнителя, если заполнен} |
| 51 | Карточка роли выступления | {Роль}<br><br>Здесь можно напомнить об ответе, пригласить другого исполнителя или убрать роль. При подмене подтверждённый участник остаётся в составе до согласования замены. | {Коллектив}<br><br>{Дата} · {Выступление} · {Город}<br><br>{Роль}<br><br>Здесь можно напомнить об ответе, пригласить другого исполнителя или убрать роль. При подмене подтверждённый участник остаётся в составе до согласования замены. |
| 52 | Выбор кандидата, включая отсутствие кандидатов | Выбрать исполнителя | {Коллектив}<br><br>{Дата} · {Выступление} · {Город}<br><br>{Роль}<br><br>Выбрать исполнителя |
| 53 | Все приглашения по роли | Все приглашения | {Коллектив}<br><br>{Дата} · {Выступление} · {Город}<br><br>{Роль}<br><br>Все приглашения |
| 54 | Приглашение выбранного исполнителя — подтверждение | Пригласить {name}? Перед отправкой проверим занятость. | {Коллектив}<br><br>{Дата} · {Выступление} · {Город}<br><br>{Роль}<br><br>Пригласить {name}? Перед отправкой проверим занятость. |
| 55 | Пересечение занятости при приглашении или ответе | На эту дату есть занятость. Это возможное пересечение: события учитываются на весь день. | {Коллектив}<br><br>{Дата} · {Выступление} · {Город}<br><br>{Роль}<br><br>{Исполнитель}<br><br>На эту дату есть занятость. Это возможное пересечение: события учитываются на весь день. |
| 56 | Удаление роли выступления — подтверждение | Удалить роль из выступления? Приглашения и подтверждённое участие по этой роли будут отменены. | {Коллектив}<br><br>{Дата} · {Выступление} · {Город}<br><br>{Роль}<br><br>Удалить роль из выступления? Приглашения и подтверждённое участие по этой роли будут отменены. |
| 57 | Отмена приглашения или участия — подтверждение | Отменить участие или приглашение этого человека? | {Коллектив}<br><br>{Дата} · {Выступление} · {Город}<br><br>{Роль}<br><br>{Исполнитель}<br><br>Отменить участие или приглашение этого человека? |
| 58 | Напоминание исполнителю поставлено в очередь | Отправка поставлена в очередь. Повторные нажатия не создают несколько напоминаний. | {Коллектив}<br><br>{Дата} · {Выступление} · {Город}<br><br>{Роль}<br><br>{Исполнитель}<br><br>Отправка поставлена в очередь. Повторные нажатия не создают несколько напоминаний. |
| 59 | Проверка занятости перед рассылкой | {Роли · исполнители · результаты проверки занятости} | {Коллектив}<br><br>{Дата} · {Выступление} · {Город}<br><br>{Роли · исполнители · результаты проверки занятости} |
| 60 | Запрос подмены ожидает решения | Исходное участие сохраняется, пока администратор не утвердит замену. | {Коллектив}<br><br>{Дата} · {Выступление} · {Город}<br><br>{Роль}<br><br>{Исполнитель}<br><br>Исходное участие сохраняется, пока администратор не утвердит замену. |
| 61 | Запрос подмены отменён | Готово. | {Коллектив}<br><br>{Дата} · {Выступление} · {Город}<br><br>{Роль}<br><br>{Исполнитель}<br><br>Готово. |

### EN

| № | Экран или действие | Сейчас | Предлагается |
|---|---|---|---|
| 42 | /lineup — состав выступления | {Roles and performers} | {Band}<br><br>{Date} · {Performance} · {City}<br><br>👥 Performance lineup<br><br>{Roles and performers} |
| 43 | /lineup — пустой состав | There are no roles in this lineup yet. | {Band}<br><br>{Date} · {Performance} · {City}<br><br>👥 Performance lineup<br><br>There are no roles in this lineup yet. |
| 44 | Карточка сохранённого состава | {Lineup name}<br><br>{Roles and performers} | {Band}<br><br>{Lineup name}<br><br>{Roles and performers} |
| 45 | Настройка роли сохранённого состава | Default performer and role | {Band}<br><br>{Lineup name}<br><br>{Role}<br><br>Default performer and role |
| 46 | Удаление сохранённого состава — подтверждение | Delete this lineup? Existing performance lineups will remain unchanged. | {Band}<br><br>{Lineup name}<br><br>Delete this lineup? Existing performance lineups will remain unchanged. |
| 47 | Замена состава выступления — подтверждение | Replace the performance lineup? Current invitations and assignments will be cancelled. Review the new lineup before inviting. | {Band}<br><br>{Date} · {Performance} · {City}<br><br>{Lineup name}<br><br>Replace the performance lineup? Current invitations and assignments will be cancelled. Review the new lineup before inviting. |
| 48 | Карточка постоянного исполнителя | {Role} · {Performer}<br><br>{Performer order for this role}<br><br>{Performer profile, if completed} | {Band}<br><br>{Role} · {Performer}<br><br>{Performer order for this role}<br><br>{Performer profile, if completed} |
| 49 | Удаление постоянного назначения — подтверждение | Remove this permanent assignment? The contact and existing bookings will remain. | {Band}<br><br>{Role}<br><br>{Performer}<br><br>Remove this permanent assignment? The contact and existing bookings will remain. |
| 50 | Карточка знакомого исполнителя | {Performer}<br><br>Recorded performances: {count}<br><br>{Performer profile, if completed} | {Band}<br><br>{Performer}<br><br>Recorded performances: {count}<br><br>{Performer profile, if completed} |
| 51 | Карточка роли выступления | {Role}<br><br>Send a reminder, choose another performer, or remove the role. A confirmed performer remains committed until a substitution is approved. | {Band}<br><br>{Date} · {Performance} · {City}<br><br>{Role}<br><br>Send a reminder, choose another performer, or remove the role. A confirmed performer remains committed until a substitution is approved. |
| 52 | Выбор кандидата, включая отсутствие кандидатов | Choose performer | {Band}<br><br>{Date} · {Performance} · {City}<br><br>{Role}<br><br>Choose performer |
| 53 | Все приглашения по роли | All invitations | {Band}<br><br>{Date} · {Performance} · {City}<br><br>{Role}<br><br>All invitations |
| 54 | Приглашение выбранного исполнителя — подтверждение | Invite {name}? Availability will be checked before sending. | {Band}<br><br>{Date} · {Performance} · {City}<br><br>{Role}<br><br>Invite {name}? Availability will be checked before sending. |
| 55 | Пересечение занятости при приглашении или ответе | There is a booking or busy period on this date. Events are all-day, so this is a possible conflict. | {Band}<br><br>{Date} · {Performance} · {City}<br><br>{Role}<br><br>{Performer}<br><br>There is a booking or busy period on this date. Events are all-day, so this is a possible conflict. |
| 56 | Удаление роли выступления — подтверждение | Remove this role? Its pending invitations and confirmed participation will be cancelled. | {Band}<br><br>{Date} · {Performance} · {City}<br><br>{Role}<br><br>Remove this role? Its pending invitations and confirmed participation will be cancelled. |
| 57 | Отмена приглашения или участия — подтверждение | Cancel this person's participation or invitation? | {Band}<br><br>{Date} · {Performance} · {City}<br><br>{Role}<br><br>{Performer}<br><br>Cancel this person's participation or invitation? |
| 58 | Напоминание исполнителю поставлено в очередь | Delivery queued. Repeated clicks do not create duplicate reminders. | {Band}<br><br>{Date} · {Performance} · {City}<br><br>{Role}<br><br>{Performer}<br><br>Delivery queued. Repeated clicks do not create duplicate reminders. |
| 59 | Проверка занятости перед рассылкой | {Roles · performers · availability results} | {Band}<br><br>{Date} · {Performance} · {City}<br><br>{Roles · performers · availability results} |
| 60 | Запрос подмены ожидает решения | Your original commitment remains until the administrator approves a substitute. | {Band}<br><br>{Date} · {Performance} · {City}<br><br>{Role}<br><br>{Performer}<br><br>Your original commitment remains until the administrator approves a substitute. |
| 61 | Запрос подмены отменён | Done. | {Band}<br><br>{Date} · {Performance} · {City}<br><br>{Role}<br><br>{Performer}<br><br>Done. |

## Предварительный состав выступления

### RU

| № | Экран или действие | Сейчас | Предлагается |
|---|---|---|---|
| 62 | Проверка подготовленного состава | Проверьте состав, затем нажмите «Проверить занятость».<br><br>{Роли и исполнители} | {Коллектив}<br><br>{Дата} · {Выступление} · {Город}<br><br>Проверьте состав, затем нажмите «Проверить занятость».<br><br>{Роли и исполнители} |
| 63 | Редактирование подготовленного состава | {Роли и исполнители} | {Коллектив}<br><br>{Дата} · {Выступление} · {Город}<br><br>{Роли и исполнители} |
| 64 | Выбор другого состава | Выберите состав для нового выступления. Перед созданием покажем все данные для подтверждения. | {Коллектив}<br><br>{Дата} · {Выступление} · {Город}<br><br>Выберите состав для нового выступления. Перед созданием покажем все данные для подтверждения. |
| 65 | Изменения состава ещё не сохранены | В составе есть несохранённые изменения. Сохраните их или отмените. | {Коллектив}<br><br>{Дата} · {Выступление} · {Город}<br><br>В составе есть несохранённые изменения. Сохраните их или отмените. |
| 66 | Исполнители ещё не выбраны | Пока не указаны исполнители. Измените состав и выберите кандидатов. | {Коллектив}<br><br>{Дата} · {Выступление} · {Город}<br><br>Пока не указаны исполнители. Измените состав и выберите кандидатов. |
| 67 | Выбор кандидата для строки предварительного состава | {Роль}<br><br>{Исполнитель} | {Коллектив}<br><br>{Дата} · {Выступление} · {Город}<br><br>{Роль}<br><br>{Исполнитель} |

### EN

| № | Экран или действие | Сейчас | Предлагается |
|---|---|---|---|
| 62 | Проверка подготовленного состава | Review the lineup, then tap Check availability.<br><br>{Roles and performers} | {Band}<br><br>{Date} · {Performance} · {City}<br><br>Review the lineup, then tap Check availability.<br><br>{Roles and performers} |
| 63 | Редактирование подготовленного состава | {Roles and performers} | {Band}<br><br>{Date} · {Performance} · {City}<br><br>{Roles and performers} |
| 64 | Выбор другого состава | Choose a lineup. Review all details before creating the performance. | {Band}<br><br>{Date} · {Performance} · {City}<br><br>Choose a lineup. Review all details before creating the performance. |
| 65 | Изменения состава ещё не сохранены | The lineup has unsaved changes. Save or discard them. | {Band}<br><br>{Date} · {Performance} · {City}<br><br>The lineup has unsaved changes. Save or discard them. |
| 66 | Исполнители ещё не выбраны | No performers selected yet. Edit the lineup and choose candidates. | {Band}<br><br>{Date} · {Performance} · {City}<br><br>No performers selected yet. Edit the lineup and choose candidates. |
| 67 | Выбор кандидата для строки предварительного состава | {Role}<br><br>{Performer} | {Band}<br><br>{Date} · {Performance} · {City}<br><br>{Role}<br><br>{Performer} |

## Запросы ввода в составах

### RU

| № | Экран или действие | Сейчас | Предлагается |
|---|---|---|---|
| 68 | Название роли | Введите название роли: например, Гитара, Вокал, Звукорежиссёр.<br>/cancel — отмена. | {Коллектив}<br><br>Введите название роли: например, Гитара, Вокал, Звукорежиссёр.<br>/cancel — отмена. |
| 69 | Добавление знакомого исполнителя | Введите роль и известный боту @ник или Telegram ID через пробел, дефис или двоеточие. Например:<br>Гитара  @musician<br>Человек должен предварительно открыть бота и отправить /start.<br>/cancel — отмена. | {Коллектив}<br><br>Введите роль и известный боту @ник или Telegram ID через пробел, дефис или двоеточие. Например:<br>Гитара  @musician<br>Человек должен предварительно открыть бота и отправить /start.<br>/cancel — отмена. |
| 70 | Состав в старом формате | Первая строка — название состава. Далее по одной роли на строку. Через пробел, дефис или двоеточие можно указать основного исполнителя:<br>Большой<br>Вокал  @singer<br>Гитара<br>Бас<br><br>Приглашения отправляются отдельно после выбора состава.<br>/cancel — отмена. | {Коллектив}<br><br>Первая строка — название состава. Далее по одной роли на строку. Через пробел, дефис или двоеточие можно указать основного исполнителя:<br>Большой<br>Вокал  @singer<br>Гитара<br>Бас<br><br>Приглашения отправляются отдельно после выбора состава.<br>/cancel — отмена. |
| 71 | Основной состав в старом формате | Введите роли основного состава, по одной на строку. Можно добавить @ник или Telegram ID через пробел, дефис или двоеточие:<br>Вокал @singer<br>Бас-гитара: @bassist<br>Звукорежиссёр<br>/cancel — отмена. | {Коллектив}<br><br>Введите роли основного состава, по одной на строку. Можно добавить @ник или Telegram ID через пробел, дефис или двоеточие:<br>Вокал @singer<br>Бас-гитара: @bassist<br>Звукорежиссёр<br>/cancel — отмена. |
| 72 | Название нового состава | Как назовём состав? Название поможет выбрать нужных участников при создании выступления. Например: Основной, Малый, DJ set или С танцорами.<br>/cancel — отмена. | {Коллектив}<br><br>Как назовём состав? Название поможет выбрать нужных участников при создании выступления. Например: Основной, Малый, DJ set или С танцорами.<br>/cancel — отмена. |
| 73 | Роли нового состава | Состав «{name}». Введите роли — по одной на строку. При желании укажите исполнителя через пробел, дефис или двоеточие:<br>Вокал @singer<br>Гитара: @guitarist<br>Звукорежиссёр<br>/cancel — отмена. | {Коллектив}<br><br>Состав «{name}». Введите роли — по одной на строку. При желании укажите исполнителя через пробел, дефис или двоеточие:<br>Вокал @singer<br>Гитара: @guitarist<br>Звукорежиссёр<br>/cancel — отмена. |
| 74 | Добавление роли в состав | Введите роль и, при необходимости, @ник исполнителя: Бэк-вокал: @singer.<br>/cancel — отмена. | {Коллектив}<br><br>{Название состава}<br><br>Введите роль и, при необходимости, @ник исполнителя: Бэк-вокал: @singer.<br>/cancel — отмена. |
| 75 | Изменение роли состава | Введите новое название роли в этом составе.<br>/cancel — отмена. | {Коллектив}<br><br>{Название состава}<br><br>{Роль}<br><br>Введите новое название роли в этом составе.<br>/cancel — отмена. |
| 76 | Изменение основного исполнителя | Введите @ник или Telegram ID нового основного исполнителя. Чтобы оставить роль свободной, введите дефис (-).<br>/cancel — отмена. | {Коллектив}<br><br>{Название состава}<br><br>{Роль}<br><br>Введите @ник или Telegram ID нового основного исполнителя. Чтобы оставить роль свободной, введите дефис (-).<br>/cancel — отмена. |
| 77 | Переименование состава | Введите новое название состава.<br>/cancel — отмена. | {Коллектив}<br><br>{Название состава}<br><br>Введите новое название состава.<br>/cancel — отмена. |
| 78 | Название копии состава | Введите название копии состава.<br>/cancel — отмена. | {Коллектив}<br><br>{Название состава}<br><br>Введите название копии состава.<br>/cancel — отмена. |
| 79 | Постоянные назначения | Введите роли и известные боту @ники или Telegram ID, по одному назначению на строку. Например:<br>Басист @musician<br>FOH: @engineer<br>Турик - @manager<br><br>Постоянные назначения относятся к коллективу и подставляются в новые выступления с выбранным составом. Человек должен предварительно открыть бота и отправить /start. /cancel — отмена. | {Коллектив}<br><br>Введите роли и известные боту @ники или Telegram ID, по одному назначению на строку. Например:<br>Басист @musician<br>FOH: @engineer<br>Турик - @manager<br><br>Постоянные назначения относятся к коллективу и подставляются в новые выступления с выбранным составом. Человек должен предварительно открыть бота и отправить /start. /cancel — отмена. |
| 80 | Приоритет исполнителя | Введите приоритет от 0 до 9999. Меньшее число — раньше в очереди основных исполнителей этой роли. /cancel — отмена. | {Коллектив}<br><br>{Роль}<br><br>{Исполнитель}<br><br>Введите приоритет от 0 до 9999. Меньшее число — раньше в очереди основных исполнителей этой роли. /cancel — отмена. |
| 81 | Новая роль выступления | Введите роль и, если хотите, основного исполнителя: Гитара @musician. Приглашение отправляется отдельно.<br>/cancel — отмена. | {Коллектив}<br><br>{Дата} · {Выступление} · {Город}<br><br>Введите роль и, если хотите, основного исполнителя: Гитара @musician. Приглашение отправляется отдельно.<br>/cancel — отмена. |
| 82 | Исполнитель для приглашения | Введите известный боту @ник или Telegram ID исполнителя.<br>/cancel — отмена. | {Коллектив}<br><br>{Дата} · {Выступление} · {Город}<br><br>{Роль}<br><br>Введите известный боту @ник или Telegram ID исполнителя.<br>/cancel — отмена. |
| 83 | Добавление строки предварительного состава | Введите роль и при желании исполнителя: Гитара @musician.<br>/cancel — отмена. | {Коллектив}<br><br>{Дата} · {Выступление} · {Город}<br><br>Введите роль и при желании исполнителя: Гитара @musician.<br>/cancel — отмена. |
| 84 | Кандидат для предварительного состава | Введите известный боту @ник или Telegram ID. Дефис (-) убирает кандидата.<br>/cancel — отмена. | {Коллектив}<br><br>{Дата} · {Выступление} · {Город}<br><br>{Роль}<br><br>Введите известный боту @ник или Telegram ID. Дефис (-) убирает кандидата.<br>/cancel — отмена. |

### EN

| № | Экран или действие | Сейчас | Предлагается |
|---|---|---|---|
| 68 | Название роли | Enter a role, for example Guitar, Vocals or Sound engineer.<br>/cancel — cancel. | {Band}<br><br>Enter a role, for example Guitar, Vocals or Sound engineer.<br>/cancel — cancel. |
| 69 | Добавление знакомого исполнителя | Enter a role and a known @username or Telegram ID, separated by a space, dash or colon:<br>Guitar  @musician<br>The person must first send /start to the bot.<br>/cancel — cancel. | {Band}<br><br>Enter a role and a known @username or Telegram ID, separated by a space, dash or colon:<br>Guitar  @musician<br>The person must first send /start to the bot.<br>/cancel — cancel. |
| 70 | Состав в старом формате | First line: lineup name. Then one role per line; optionally add a default performer after a space, dash or colon:<br>Full band<br>Vocals  @singer<br>Guitar<br>Bass<br>Invitations are sent separately.<br>/cancel — cancel. | {Band}<br><br>First line: lineup name. Then one role per line; optionally add a default performer after a space, dash or colon:<br>Full band<br>Vocals  @singer<br>Guitar<br>Bass<br>Invitations are sent separately.<br>/cancel — cancel. |
| 71 | Основной состав в старом формате | Enter main lineup roles, one per line. Optionally add @username or Telegram ID after a space, dash or colon:<br>Vocals @singer<br>Bass: @bassist<br>Sound engineer<br>/cancel — cancel. | {Band}<br><br>Enter main lineup roles, one per line. Optionally add @username or Telegram ID after a space, dash or colon:<br>Vocals @singer<br>Bass: @bassist<br>Sound engineer<br>/cancel — cancel. |
| 72 | Название нового состава | What shall we call this lineup? A name helps select performers when creating a performance. For example: Main, Small, DJ set or With dancers.<br>/cancel — cancel. | {Band}<br><br>What shall we call this lineup? A name helps select performers when creating a performance. For example: Main, Small, DJ set or With dancers.<br>/cancel — cancel. |
| 73 | Роли нового состава | Lineup “{name}”. Enter roles, one per line. Optionally add a performer after a space, dash or colon:<br>Vocals @singer<br>Guitar: @guitarist<br>Sound engineer<br>/cancel — cancel. | {Band}<br><br>Lineup “{name}”. Enter roles, one per line. Optionally add a performer after a space, dash or colon:<br>Vocals @singer<br>Guitar: @guitarist<br>Sound engineer<br>/cancel — cancel. |
| 74 | Добавление роли в состав | Enter a role and optionally its performer: Backing vocals: @singer.<br>/cancel — cancel. | {Band}<br><br>{Lineup name}<br><br>Enter a role and optionally its performer: Backing vocals: @singer.<br>/cancel — cancel. |
| 75 | Изменение роли состава | Enter the new role name for this lineup.<br>/cancel — cancel. | {Band}<br><br>{Lineup name}<br><br>{Role}<br><br>Enter the new role name for this lineup.<br>/cancel — cancel. |
| 76 | Изменение основного исполнителя | Enter @username or Telegram ID of the new default performer. Enter - to leave the role vacant.<br>/cancel — cancel. | {Band}<br><br>{Lineup name}<br><br>{Role}<br><br>Enter @username or Telegram ID of the new default performer. Enter - to leave the role vacant.<br>/cancel — cancel. |
| 77 | Переименование состава | Enter a new lineup name.<br>/cancel — cancel. | {Band}<br><br>{Lineup name}<br><br>Enter a new lineup name.<br>/cancel — cancel. |
| 78 | Название копии состава | Enter a name for the lineup copy.<br>/cancel — cancel. | {Band}<br><br>{Lineup name}<br><br>Enter a name for the lineup copy.<br>/cancel — cancel. |
| 79 | Постоянные назначения | Enter a role and a known Telegram @username or ID, one assignment per line:<br>Bass player @musician<br>FOH: @engineer<br>Tour manager - @manager<br><br>Assignments belong to the band and fill new performances using selected lineups. Each person must first open the bot and send /start. /cancel cancels input. | {Band}<br><br>Enter a role and a known Telegram @username or ID, one assignment per line:<br>Bass player @musician<br>FOH: @engineer<br>Tour manager - @manager<br><br>Assignments belong to the band and fill new performances using selected lineups. Each person must first open the bot and send /start. /cancel cancels input. |
| 80 | Приоритет исполнителя | Enter priority from 0 to 9999. Lower numbers come first among permanent performers for this role. /cancel cancels input. | {Band}<br><br>{Role}<br><br>{Performer}<br><br>Enter priority from 0 to 9999. Lower numbers come first among permanent performers for this role. /cancel cancels input. |
| 81 | Новая роль выступления | Enter a role and optionally a performer: Guitar @musician. Invitations are sent separately.<br>/cancel — cancel. | {Band}<br><br>{Date} · {Performance} · {City}<br><br>Enter a role and optionally a performer: Guitar @musician. Invitations are sent separately.<br>/cancel — cancel. |
| 82 | Исполнитель для приглашения | Enter a known @username or Telegram ID.<br>/cancel — cancel. | {Band}<br><br>{Date} · {Performance} · {City}<br><br>{Role}<br><br>Enter a known @username or Telegram ID.<br>/cancel — cancel. |
| 83 | Добавление строки предварительного состава | Enter a role and optionally its performer: Guitar @musician.<br>/cancel — cancel. | {Band}<br><br>{Date} · {Performance} · {City}<br><br>Enter a role and optionally its performer: Guitar @musician.<br>/cancel — cancel. |
| 84 | Кандидат для предварительного состава | Enter a known @username or Telegram ID. Enter - to clear the candidate.<br>/cancel — cancel. | {Band}<br><br>{Date} · {Performance} · {City}<br><br>{Role}<br><br>Enter a known @username or Telegram ID. Enter - to clear the candidate.<br>/cancel — cancel. |

## Календарные подписки

### RU

| № | Экран или действие | Сейчас | Предлагается |
|---|---|---|---|
| 85 | Показ или создание ссылки | Добавьте ссылку как подписку в Google Calendar или Apple Calendar. Её можно использовать на нескольких устройствах и передать тем, кому хотите показать свои выступления. Обновления могут приходить с задержкой.<br><br>{url} | {Область подписки}<br><br>Добавьте ссылку как подписку в Google Calendar или Apple Calendar. Её можно использовать на нескольких устройствах и передать тем, кому хотите показать свои выступления. Обновления могут приходить с задержкой.<br><br>{url} |
| 86 | Ранее выданная ссылка ещё не сохранена | Старая ссылка продолжает работать, но её ещё нет в сохранённом виде. Пришлите имеющуюся ссылку, чтобы показывать её повторно без переподключения. Если она потерялась, можно перевыпустить её с подтверждением. | {Область подписки}<br><br>Старая ссылка продолжает работать, но её ещё нет в сохранённом виде. Пришлите имеющуюся ссылку, чтобы показывать её повторно без переподключения. Если она потерялась, можно перевыпустить её с подтверждением. |
| 87 | Запрос имеющейся ссылки | Ответьте на это сообщение своей действующей календарной ссылкой. Бот проверит её принадлежность вашей подписке и сохранит без перевыпуска. /cancel — отменить ввод. | {Область подписки}<br><br>Ответьте на это сообщение своей действующей календарной ссылкой. Бот проверит её принадлежность вашей подписке и сохранит без перевыпуска. /cancel — отменить ввод. |
| 88 | Введённая ссылка не подходит | Эта ссылка не соответствует выбранной подписке. Проверьте, что скопировали её полностью.<br><br>Ответьте на это сообщение своей действующей календарной ссылкой. Бот проверит её принадлежность вашей подписке и сохранит без перевыпуска. /cancel — отменить ввод. | {Область подписки}<br><br>Эта ссылка не соответствует выбранной подписке. Проверьте, что скопировали её полностью.<br><br>Ответьте на это сообщение своей действующей календарной ссылкой. Бот проверит её принадлежность вашей подписке и сохранит без перевыпуска. /cancel — отменить ввод. |
| 89 | Сохранённая ссылка не открывается | Не удалось открыть сохранённую ссылку. Прежняя ссылка продолжает работать; её можно сохранить заново или перевыпустить. | {Область подписки}<br><br>Не удалось открыть сохранённую ссылку. Прежняя ссылка продолжает работать; её можно сохранить заново или перевыпустить. |
| 90 | Подписка отключена или ещё не создана | Подписка отключена. При необходимости можно создать новую ссылку. | {Область подписки}<br><br>Подписка отключена. При необходимости можно создать новую ссылку. |
| 91 | Подтверждение перевыпуска | Перевыпустить ссылку? Прежняя перестанет работать. Новую нужно будет добавить в календарное приложение заново. | {Область подписки}<br><br>Перевыпустить ссылку? Прежняя перестанет работать. Новую нужно будет добавить в календарное приложение заново. |
| 92 | Подтверждение отключения | Отключить эту подписку? Ссылка перестанет работать. Выступления и сет-листы в боте останутся. | {Область подписки}<br><br>Отключить эту подписку? Ссылка перестанет работать. Выступления и сет-листы в боте останутся. |
| 93 | Отмена изменения подписки | Готово. | {Область подписки}<br><br>Готово. |
| 94 | Инструкция для конкретной подписки | Apple Calendar: выберите добавление календаря по подписке и вставьте ссылку.<br><br>Google Calendar: откройте calendar.google.com в браузере → Другие календари → + → Добавить по URL. Вставьте ссылку; затем календарь появится и в мобильном приложении.<br><br>Это подписка, а не разовый импорт файла. Пароль Google или Apple боту не нужен. Меняйте выступления в боте. При отключении ссылки удалите ненужный календарь и из приложения: его старый кэш может остаться на устройстве.<br><br>Добавьте ссылку как подписку в Google Calendar или Apple Calendar. Её можно использовать на нескольких устройствах и передать тем, кому хотите показать свои выступления. Обновления могут приходить с задержкой.<br><br>{url} | {Область подписки}<br><br>Apple Calendar: выберите добавление календаря по подписке и вставьте ссылку.<br><br>Google Calendar: откройте calendar.google.com в браузере → Другие календари → + → Добавить по URL. Вставьте ссылку; затем календарь появится и в мобильном приложении.<br><br>Это подписка, а не разовый импорт файла. Пароль Google или Apple боту не нужен. Меняйте выступления в боте. При отключении ссылки удалите ненужный календарь и из приложения: его старый кэш может остаться на устройстве.<br><br>Добавьте ссылку как подписку в Google Calendar или Apple Calendar. Её можно использовать на нескольких устройствах и передать тем, кому хотите показать свои выступления. Обновления могут приходить с задержкой.<br><br>{url} |

### EN

| № | Экран или действие | Сейчас | Предлагается |
|---|---|---|---|
| 85 | Показ или создание ссылки | Add this URL as a subscription in Google Calendar or Apple Calendar. Use it on multiple devices and share it with people who should see your performances. Updates may be delayed.<br><br>{url} | {Subscription scope}<br><br>Add this URL as a subscription in Google Calendar or Apple Calendar. Use it on multiple devices and share it with people who should see your performances. Updates may be delayed.<br><br>{url} |
| 86 | Ранее выданная ссылка ещё не сохранена | Your previous link still works but is not yet stored in recoverable form. Submit it to show it again without reconnecting. If lost, replace it after confirmation. | {Subscription scope}<br><br>Your previous link still works but is not yet stored in recoverable form. Submit it to show it again without reconnecting. If lost, replace it after confirmation. |
| 87 | Запрос имеющейся ссылки | Reply to this message with your existing subscription URL. The bot will verify and save it without replacing it. /cancel cancels this input. | {Subscription scope}<br><br>Reply to this message with your existing subscription URL. The bot will verify and save it without replacing it. /cancel cancels this input. |
| 88 | Введённая ссылка не подходит | This URL does not match the selected subscription. Check that you copied it completely.<br><br>Reply to this message with your existing subscription URL. The bot will verify and save it without replacing it. /cancel cancels this input. | {Subscription scope}<br><br>This URL does not match the selected subscription. Check that you copied it completely.<br><br>Reply to this message with your existing subscription URL. The bot will verify and save it without replacing it. /cancel cancels this input. |
| 89 | Сохранённая ссылка не открывается | Could not open the stored link. Your previous link still works; save it again or replace it. | {Subscription scope}<br><br>Could not open the stored link. Your previous link still works; save it again or replace it. |
| 90 | Подписка отключена или ещё не создана | Subscription disabled. You can create a new link if needed. | {Subscription scope}<br><br>Subscription disabled. You can create a new link if needed. |
| 91 | Подтверждение перевыпуска | Replace this link? The old link will stop working. Add the new link to your calendar app again. | {Subscription scope}<br><br>Replace this link? The old link will stop working. Add the new link to your calendar app again. |
| 92 | Подтверждение отключения | Disable this subscription? The link will stop working. Performances and setlists in the bot will remain. | {Subscription scope}<br><br>Disable this subscription? The link will stop working. Performances and setlists in the bot will remain. |
| 93 | Отмена изменения подписки | Done. | {Subscription scope}<br><br>Done. |
| 94 | Инструкция для конкретной подписки | Apple Calendar: choose to add a subscription calendar and paste the URL.<br><br>Google Calendar: open calendar.google.com in a browser → Other calendars → + → From URL. Paste the URL; the calendar will also appear in the mobile app.<br><br>Use a subscription, not a one-off file import. No Google or Apple password is needed by the bot. Edit performances in the bot. After disabling a link, remove the calendar from your app too: an old cached copy may remain on your device.<br><br>Add this URL as a subscription in Google Calendar or Apple Calendar. Use it on multiple devices and share it with people who should see your performances. Updates may be delayed.<br><br>{url} | {Subscription scope}<br><br>Apple Calendar: choose to add a subscription calendar and paste the URL.<br><br>Google Calendar: open calendar.google.com in a browser → Other calendars → + → From URL. Paste the URL; the calendar will also appear in the mobile app.<br><br>Use a subscription, not a one-off file import. No Google or Apple password is needed by the bot. Edit performances in the bot. After disabling a link, remove the calendar from your app too: an old cached copy may remain on your device.<br><br>Add this URL as a subscription in Google Calendar or Apple Calendar. Use it on multiple devices and share it with people who should see your performances. Updates may be delayed.<br><br>{url} |

## Перенос коллектива в другой чат

### RU

| № | Экран или действие | Сейчас | Предлагается |
|---|---|---|---|
| 95 | Одноразовая команда переноса | Добавьте бота в новый чат и отправьте туда эту одноразовую команду от своего аккаунта:<br><br>/chat_transfer {token}<br><br>Действует 30 минут. Затем владелец нового чата должен подтвердить привязку. Владельцем коллектива останетесь вы. | {Коллектив}<br><br>Добавьте бота в новый чат и отправьте туда эту одноразовую команду от своего аккаунта:<br><br>/chat_transfer {token}<br><br>Действует 30 минут. Затем владелец нового чата должен подтвердить привязку. Владельцем коллектива останетесь вы. |
| 96 | Перенос отменён | Перенос отменён. Привязка коллектива не изменилась. | {Коллектив}<br><br>Перенос отменён. Привязка коллектива не изменилась. |

### EN

| № | Экран или действие | Сейчас | Предлагается |
|---|---|---|---|
| 95 | Одноразовая команда переноса | Add the bot to the new chat and send this single-use command from your own account:<br><br>/chat_transfer {token}<br><br>Valid for 30 minutes. The new chat owner must then confirm. You remain the group owner. | {Band}<br><br>Add the bot to the new chat and send this single-use command from your own account:<br><br>/chat_transfer {token}<br><br>Valid for 30 minutes. The new chat owner must then confirm. You remain the group owner. |
| 96 | Перенос отменён | Transfer cancelled. The group chat was not changed. | {Band}<br><br>Transfer cancelled. The group chat was not changed. |

## Где будут изменения

- `transport/controller.py` — участники, репертуар, настройки, подтверждения, выступления, история и импорт.
- `transport/staffing.py` — запросы ввода, приглашения, кандидаты и карточки знакомых исполнителей.
- `transport/staffing_lineups.py` — роли, составы, подтверждения замены и удаления.
- `transport/staffing_preparation.py` — предварительный состав выступления.
- `transport/permanent_team.py` — постоянные исполнители и удаление назначения.
- `transport/calendars.py` — ссылки и управление конкретной подпиской.
- `transport/collectives.py` — код переноса и подтверждение отмены.
- Словари RU/EN — подписи контекста и уточнения формулировок.

## Проверки перед выпуском

- Два коллектива с одинаковыми песнями, названиями составов и выступлений. В каждом самостоятельном ответе виден правильный коллектив.
- Открытие старой кнопки после переключения коллектива. Заголовок соответствует объекту кнопки.
- Пустые списки, подтверждения, отмена и сообщения о результате операции содержат контекст.
- Карточка и меню редактирования песни сразу под исходным каталогом не дублируют коллектив. При открытии отдельным сообщением, через другой вход или после появления промежуточных сообщений коллектив указан.
- Название коллектива не раскрывается до проверки прав доступа.
- Контекст сохраняется при редактировании сообщения, форматировании Article, разбиении ответа на страницы и повторном запросе ввода.
- Личная и сводная календарные подписки подписаны своей областью.
- RU и EN, личка и групповой чат, затем ручная проверка на тестовом боте.

## Согласование

- Пользователь согласовал все формулировки, включая дополнительные вопросы перед архивацией и восстановлением песни. Уточнено исключение для карточки и меню редактирования песни непосредственно под исходным каталогом.
- Охват заявки — тексты ответов Telegram из перечисленных сценариев.
