# Проверка каталога, расписания и длительности в песочнице

Ветка `codex/catalog-schedule-timing`, бот `@botToPract696_bot`. Вершина схемы
`c91a72e6b304`. Новых миграций и настроек окружения нет.

На VPS в каталоге тестового бота выполните:

```bash
(
  set -e
  cd /opt/setlist_bot_sandbox
  test -z "$(git status --porcelain)"
  git rev-parse HEAD
  git fetch origin refs/heads/codex/catalog-schedule-timing:refs/remotes/origin/codex/catalog-schedule-timing
  if git show-ref --verify --quiet refs/heads/codex/catalog-schedule-timing; then
    git switch codex/catalog-schedule-timing
    git merge --ff-only origin/codex/catalog-schedule-timing
  else
    git switch -c codex/catalog-schedule-timing origin/codex/catalog-schedule-timing
  fi
  bash scripts/update-sandbox-admin.sh server
  docker exec setlist-sandbox-bot-1 .venv/bin/setlist-bot --check
  docker logs --since 3m --tail 60 setlist-sandbox-bot-1
)
```

Сохраните SHA, выведенный перед переключением, для отката по
[инструкции песочницы](test-release-2026-11-10-interface.md#откат).
В журнале должны появиться `Command menus verified` и запуск polling тестового бота.

1. На iPhone и Mac вызовите `/catalog` и нажмите на название прямо в таблице.
   Карточка должна открыться ниже каталога с кнопкой «Изменить». Выберите другую
   песню и проверьте, что обновилась та же карточка. Затем вызовите `/catalog`
   заново, выберите песню и убедитесь, что карточка появилась ниже свежей таблицы.
   Проверьте также выбор со второй страницы и повторите в личке и групповом чате.
2. В личном меню проверьте отсутствие `/calendar`, затем введите команду вручную.
   В «Моём профиле» нажмите «Моё расписание» прямо над «Назад». В расписании
   проверьте кнопку «Подключить календарь» и убедитесь, что ранее выданная ссылка
   по-прежнему открывается.
3. Заново откройте Mini App в светлой и тёмной темах. Сверьте шрифт времени
   черновика, `/` и плановой длительности, а также цвет текста всей строки.
   Нажмите на кнопку длительности под администратором, измените и сохраните
   значение. Под участником план должен отображаться без кнопки.
