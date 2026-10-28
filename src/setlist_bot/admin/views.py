"""Small server-rendered UI: all user content is escaped, no external fonts or scripts."""

import secrets
from datetime import datetime
from html import escape
from typing import Any
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from setlist_bot.admin.security import AdminSettings, form_proof

NAV = [
    ("overview", "Обзор", "/"),
    ("users", "Пользователи", "/users"),
    ("groups", "Коллективы", "/groups"),
    ("concerts", "Мероприятия", "/concerts"),
    ("support", "Поддержка", "/support"),
    ("queue", "Очередь отправки", "/queue"),
    ("runtime", "Мониторинг", "/runtime"),
    ("audit", "Журнал действий", "/audit"),
]


def h(value: Any) -> str:
    return escape(str(value if value is not None else "—"), quote=True)


def stamp(value: Any) -> str:
    return (
        h(value.astimezone(ZoneInfo("Europe/Moscow")).strftime("%d.%m.%Y %H:%M:%S"))
        if isinstance(value, datetime)
        else "До обновления / неизвестно"
    )


def bytes_label(value: int) -> str:
    return f"{value / 1024**3:.2f} ГБ" if value >= 1024**3 else f"{value / 1024**2:.1f} МБ"


def link(path: str, label: Any) -> str:
    return f'<a href="{h(path)}">{h(label)}</a>'


def badge(label: str, state: str = "") -> str:
    return f'<span class="badge {h(state)}">{h(label)}</span>'


def table(headers: list[str], rows: list[list[str]]) -> str:
    return (
        '<div class="table-wrap"><table><thead><tr>'
        + "".join(f"<th>{h(x)}</th>" for x in headers)
        + "</tr></thead><tbody>"
        + (
            "".join("<tr>" + "".join("<td>" + x + "</td>" for x in row) + "</tr>" for row in rows)
            or f'<tr><td colspan="{len(headers)}" class="empty">Пока ничего нет</td></tr>'
        )
        + "</tbody></table></div>"
    )


def form(
    settings: AdminSettings,
    session: str,
    action: str,
    target: Any,
    body: str,
    label: str,
    *,
    danger: bool = False,
) -> str:
    nonce = secrets.token_hex(16)
    proof = form_proof(settings, session, action, str(target), nonce)
    return f'<form method="post" action="/actions/{h(action)}/{h(target)}" class="action-form"><input type="hidden" name="nonce" value="{nonce}"><input type="hidden" name="proof" value="{proof}">{body}<button class="{"danger" if danger else "primary"}" type="submit">{h(label)}</button></form>'


def page(
    settings: AdminSettings,
    session: str,
    title: str,
    body: str,
    *,
    section: str = "",
    notice: str = "",
) -> str:
    logout = form(settings, session, "logout", "session", "", "Выйти")
    navigation = "".join(
        f'<a class="nav-link {"selected" if key == section else ""}" href="{path}">{label}</a>'
        for key, label, path in NAV
    )
    environment = "ОСНОВНАЯ БАЗА" if settings.contour == "production" else "ТЕСТОВЫЙ КОНТУР"
    if settings.read_only:
        environment += " · ТОЛЬКО ПРОСМОТР"
    if settings.console_switch:
        environment += (
            '<div class="inline-actions"><a href="/_contour/sandbox">Тестовый</a>'
            + (
                '<a href="/_contour/production">Основной</a>'
                if settings.production_available
                else '<span class="hint">Основной ещё не подключён</span>'
            )
            + "</div>"
        )
    return document(
        title,
        f"""<aside class="sidebar"><a class="brand" href="/"><span class="brand-mark">S</span><span>setlist<span class="brand-sub">CONTROL ROOM</span></span></a><div class="environment">{environment}</div><nav>{navigation}</nav><div class="sidebar-bottom"><span>{h(settings.username)}</span>{logout}</div></aside><main><header><div><div class="eyebrow">АДМИНИСТРИРОВАНИЕ БОТА</div><h1>{h(title)}</h1></div><span class="clock">Москва · UTC+3</span></header>{'<div class="notice">' + h(notice) + "</div>" if notice else ""}{body}<footer>Личные черновики скрыты. Изменения записываются в журнал. <a href="/runtime">Состояние системы →</a></footer></main>""",
    )


def document(title: str, body: str) -> str:
    return f'<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{h(title)} · Setlist Admin</title><link rel="stylesheet" href="/static/admin.css"><script defer src="/static/admin.js"></script></head><body>{body}</body></html>'


def login(username: str, token: str, error: str = "") -> str:
    return document(
        "Вход",
        f'''<div class="login-shell"><section class="login-card"><div class="brand-mark">S</div><div class="eyebrow">SETLIST CONTROL ROOM</div><h1>Добро пожаловать</h1><p class="muted">Личный кабинет владельца бота</p>{'<div class="notice error">' + h(error) + "</div>" if error else ""}<form method="post" action="/login"><input type="hidden" name="csrf" value="{h(token)}"><label>Логин<input name="username" autocomplete="username" maxlength="100" value="{h(username)}" required></label><label>Пароль<input name="password" type="password" autocomplete="current-password" maxlength="256" required></label><button class="primary" type="submit">Войти в админку →</button></form><p class="hint">Доступ через SSH-туннель или HTTPS. Пароль админки не является токеном Telegram.</p></section></div>''',
    )


def search(
    query: str, state: str, choices: list[tuple[str, str]], extra: dict[str, str] | None = None
) -> str:
    options = "".join(
        f'<option value="{h(key)}" {"selected" if key == state else ""}>{h(label)}</option>'
        for key, label in choices
    )
    return (
        '<form class="toolbar" method="get">'
        + "".join(
            f'<input type="hidden" name="{h(key)}" value="{h(value)}">'
            for key, value in (extra or {}).items()
        )
        + f'<input name="q" placeholder="Поиск по имени, названию или ID…" value="{h(query)}" maxlength="200">'
        + (f'<select name="state">{options}</select>' if options else "")
        + '<button type="submit">Найти</button></form>'
    )


def pagination(
    path: str,
    query: str,
    state: str,
    page_number: int,
    has_next: bool,
    extra: dict[str, str] | None = None,
) -> str:
    parts = []
    if page_number:
        parts.append(
            link(
                path
                + "?"
                + urlencode({**(extra or {}), "q": query, "state": state, "page": page_number - 1}),
                "← Назад",
            )
        )
    parts.append(f"<span>Страница {page_number + 1} · по 25 записей</span>")
    if has_next:
        parts.append(
            link(
                path
                + "?"
                + urlencode({**(extra or {}), "q": query, "state": state, "page": page_number + 1}),
                "Дальше →",
            )
        )
    return '<div class="pagination">' + "".join(parts) + "</div>"


def metric(label: str, value: Any, hint: str, key: str = "") -> str:
    return f'<article class="metric"><span>{h(label)}</span><strong {"data-metric=" + h(key) if key else ""}>{h(value)}</strong><small>{h(hint)}</small></article>'


COMPONENT_NAMES = {
    "telegram_api": "Связь с Telegram API",
    "bot": "Процесс бота",
    "outbox": "Отправка сообщений",
    "cleanup": "Уборка данных",
    "reminders": "Напоминания",
    "telegram": "События Telegram",
}
STATE_NAMES = {
    "running": "Работает",
    "waiting": "Ожидание",
    "retrying": "Повтор после ошибки",
    "error": "Ошибка обработки",
    "updates_received": "Обновления поступают",
    "no_errors_recorded": "Ошибки не зафиксированы",
    "process_running": "Процесс активен",
    "polling": "Получение обновлений",
    "polling_error": "Ошибка получения обновлений",
}


def host_panel(host: dict[str, Any], contour: str = "sandbox") -> str:
    """A host report is untrusted input too; malformed or stale data is not a green check."""
    try:
        collected = datetime.fromisoformat(str(host["collected_at"]))
        if collected.tzinfo is None:
            raise ValueError("Missing timezone")
        from datetime import UTC

        age = (datetime.now(UTC) - collected).total_seconds()
        old = age > 180 or age < 0
        pairs = [
            ("Отчёт получен", stamp(collected)),
            (
                "Диск: свободно / всего",
                h(bytes_label(int(host["disk_free_bytes"])))
                + " / "
                + h(bytes_label(int(host["disk_total_bytes"]))),
            ),
        ]
        if "memory_available_bytes" in host and "memory_total_bytes" in host:
            pairs.append(
                (
                    "RAM: доступно / всего",
                    h(bytes_label(int(host["memory_available_bytes"])))
                    + " / "
                    + h(bytes_label(int(host["memory_total_bytes"]))),
                )
            )
        pairs.append(("Нагрузка за минуту", h(host.get("load_1m", "—"))))
        backup_age = host.get("backup_age_seconds")
        label = "основной базы" if contour == "production" else "песочницы"
        pairs.append(("Последняя копия " + label, h(host.get("last_backup", "Копия не найдена"))))
        if backup_age is not None:
            hours = int(backup_age) // 3600
            pairs.append(("Возраст копии", badge(f"{hours}ч", "warn" if hours > 30 else "good")))
        timer = str(host.get("backup_timer", "unknown"))
        pairs.append(
            (
                "Таймер копий " + label,
                badge(
                    "Активен" if timer == "active" else "Не настроен / не активен",
                    "good" if timer == "active" else "warn",
                ),
            )
        )
        return (
            '<section class="panel"><div class="section-heading"><h2>Сервер и резервные копии</h2>'
            + badge("Отчёт устарел" if old else "Свежий отчёт", "warn" if old else "good")
            + '</div><dl class="meta">'
            + "".join("<dt>" + h(label) + "</dt><dd>" + value + "</dd>" for label, value in pairs)
            + "</dl></section>"
        )
    except (KeyError, TypeError, ValueError, OverflowError):
        return '<section class="panel"><h2>Сервер и резервные копии</h2><p class="hint">Отчёт хоста повреждён или неполон. Проверьте сборщик, данные не считаются актуальными.</p></section>'


def delivery_status(row: dict[str, Any]) -> tuple[str, str]:
    if row.get("discarded_at"):
        return "Исключено из отправки", "warn"
    if row.get("delivered_at"):
        return "Доставлено", "good"
    if row.get("last_error"):
        return ("Не доставлено: " if row.get("sent") else "Ожидает повтора: ") + str(
            row["last_error"]
        ), "bad"
    return ("Обработано", "") if row.get("sent") else ("В очереди", "warn")
