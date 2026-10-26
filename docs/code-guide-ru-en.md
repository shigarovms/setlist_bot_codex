# Как читать код / How to read the code

1. **main.py**: запуск, соединение с БД и фоновые задачи. / Startup, DB engine and workers.
2. **transport/telegram.py**: принимает Telegram update, начинает транзакцию, вызывает
   Controller и отправляет Response. / Receives an update, runs a transaction, sends the response.
3. **transport/controller.py**: переводит команды и кнопки в прикладные действия;
   это не место для прямых SQL или обращения к Telegram. / Translates commands and buttons
   into use cases; direct SQL and Telegram calls belong to adapters.
4. **application/**: сценарии каталога, концертов, личных черновиков и экспорта.
   / Use cases enforce permissions, versions, ownership and lifecycle boundaries.
5. **domain/**: структуры и чистые правила (время, паузы, даты). / Data classes and pure rules.
6. **infrastructure/**: SQLAlchemy реализует Store; ReportLab реализует PdfRenderer.
   / Adapters implement application ports using concrete libraries.

## Почему проверки повторяются / Why checks repeat

Видимость кнопки не даёт право на действие. Пользователь может нажать старую кнопку
после удаления концерта или изменения роли. Поэтому обработчик повторно проверяет
коллектив, владельца, версию и срок. / Hiding a button is not authorization. Recheck
owner, tenant, lifecycle epoch, version and expiry when executing a stale callback.

Транзакция либо сохраняет всё, либо откатывает всё. Блокировка коллектива защищает
счётчики и изменения между общим и личным чатами. / A transaction commits all changes
or rolls them all back. Tenant locks serialize changes across multiple chats.

Утверждённая ревизия неизменяема. Для световика создаётся отдельный снимок с актуальными
BPM/цветами/комментарием; порядок и время остаются утверждёнными. / An approved revision
is immutable; lighting export overlays current technical fields on a separate copy.

Не запускайте pytest на рабочей базе: фикстуры очищают таблицы. / Never run pytest
against a live database: fixtures truncate tables. Only use a dedicated `_test` database.
