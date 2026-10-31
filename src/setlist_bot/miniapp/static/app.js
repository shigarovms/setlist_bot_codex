import { clone, clock, total, numbered, wire, move, asText } from "./model.js";
import { DraftPersistence } from "./persistence.js";
const tg = window.Telegram?.WebApp,
  $ = (id) => document.getElementById(id);
const words = {
  ru: {
    heading: "Личный черновик",
    planned: "Выступаем",
    unknown: "не задано",
    current: "Текущий черновик",
    catalog: "Репертуар",
    search: "Поиск песни",
    private:
      "Черновик сохраняется автоматически. Отправка группе — только по кнопке.",
    publish: "📤 Предложить в группу и закрыть",
    publishing: "Отправляем предложение в группу…",
    queued: "Предложение в очереди. Нажмите ещё раз, чтобы проверить отправку.",
    failed:
      "Не удалось отправить предложение. Черновик сохранён. Можно повторить.",
    save: "Сохранить черновик",
    back: "Вернуться к боту",
    pause: "пауза",
    addPause: "＋ Пауза",
    undo: "↶ Отменить",
    remove: "Убрать из сет-листа",
    empty:
      "Добавьте песни из репертуара ниже или возьмите прошлый сет-лист за основу.",
    use: "Взять за основу",
    replace:
      "Заменить набранный список выбранным? Текущие изменения останутся доступны через отмену.",
    limit: "В списке может быть не больше 200 позиций.",
    loading: "Загрузка…",
    saved: "Сохранено в личный черновик. Группе не отправлено.",
    dirty: "Есть несохранённые изменения",
    ready: "Перетаскивайте за ручку ⋮⋮ или добавляйте кнопкой +",
    wait: "Сохранение…",
    missing: "Песня недоступна — удалите её или обновите каталог",
    auth: "Откройте редактор кнопкой под сет-листом в Telegram.",
    confirmClose:
      "Есть несохранённые изменения. Вернуться к боту без сохранения?",
    conflict:
      "Черновик изменён на другом устройстве. Ваш ввод сохранён на экране. Ниже актуальный вариант:",
    loadLatest: "Загрузить актуальный",
    copy: "Скопировать мой список",
    continue: "Продолжить мой ввод",
    copied: "Список скопирован",
    cancel: "Отмена",
    add: "Добавить",
    pauseTitle: "Длительность паузы",
    pauseHint: "Без единиц — минуты",
    reload: "Обновите редактор, чтобы получить актуальный каталог.",
  },
  en: {
    heading: "Private draft",
    planned: "Planned",
    unknown: "unknown",
    current: "Current draft",
    catalog: "Repertoire",
    search: "Find a song",
    private:
      "Draft saved automatically. Only the publish button sends it to the group.",
    publish: "📤 Propose to group and close",
    publishing: "Sending your proposal…",
    queued: "Proposal queued. Press again to check delivery.",
    failed: "Delivery failed. Your draft is saved; you can retry.",
    save: "Save draft",
    back: "Back to bot",
    pause: "pause",
    addPause: "＋ Pause",
    undo: "↶ Undo",
    remove: "Remove from setlist",
    empty: "Add songs below or use a previous performance as a starting point.",
    use: "Use as starting point",
    replace:
      "Replace this list? Your current changes will remain available through Undo.",
    limit: "Up to 200 entries are allowed.",
    loading: "Loading…",
    saved: "Saved as your private draft. Not published to the group.",
    dirty: "Unsaved changes",
    ready: "Drag using ⋮⋮ or add with +",
    wait: "Saving…",
    missing: "Song unavailable — remove it or refresh the catalog",
    auth: "Open the editor using its button below a setlist in Telegram.",
    confirmClose: "You have unsaved changes. Return without saving?",
    conflict:
      "The draft changed on another device. Your input remains on screen. Current saved version:",
    loadLatest: "Load current version",
    copy: "Copy my list",
    continue: "Continue editing",
    copied: "List copied",
    cancel: "Cancel",
    add: "Add",
    pauseTitle: "Pause duration",
    pauseHint: "Numbers without units mean minutes",
    reload: "Reopen the editor to refresh the catalog.",
  },
};
let lang = "ru",
  state = null,
  current = [],
  undos = [],
  preview = null,
  historyIndex = -1,
  catalogPage = 0,
  busy = false,
  latest = null,
  saver = null,
  publishAttempt = null;
const t = (key) => words[lang][key] ?? key;
let session = new URLSearchParams(location.hash.slice(1)).get("session");
function status(text, error = false) {
  $("status").textContent = text;
  $("status").classList.toggle("error", error);
}
function dirty() {
  return saver?.dirty() ?? false;
}
function theme() {
  // RU: Данные темы приходят из Telegram; список песен при смене темы не пересоздаём.
  // EN: Apply native theme colors without resetting the in-progress draft.
  const dark =
    tg?.colorScheme === "dark" ||
    (!tg?.colorScheme && matchMedia("(prefers-color-scheme: dark)").matches);
  document.documentElement.dataset.theme = dark ? "dark" : "light";
  const map = {
    bg_color: "--bg",
    text_color: "--text",
    secondary_bg_color: "--panel",
    hint_color: "--hint",
    button_color: "--button",
    button_text_color: "--button-text",
  };
  for (const [source, target] of Object.entries(map)) {
    const color = tg?.themeParams?.[source];
    if (/^#[0-9a-f]{3,8}$/i.test(color ?? ""))
      document.documentElement.style.setProperty(target, color);
    else document.documentElement.style.removeProperty(target);
  }
}
theme();
tg?.onEvent?.("themeChanged", theme);
tg?.ready?.();
tg?.expand?.();
async function api(operation, payload = {}, method = "GET") {
  const url = new URL(`./api/${operation}`, location.href);
  const body = { session, ...payload };
  if (method === "GET")
    for (const [key, value] of Object.entries(body))
      url.searchParams.set(key, value);
  let response;
  try {
    response = await fetch(url, {
      method,
      headers: {
        Authorization: `tma ${tg?.initData ?? ""}`,
        ...(method === "POST" ? { "Content-Type": "application/json" } : {}),
      },
      body: method === "POST" ? JSON.stringify(body) : undefined,
      cache: "no-store",
    });
  } catch {
    throw new Error(
      lang === "ru"
        ? "Нет связи. Изменения остались на экране — повторите сохранение."
        : "Connection failed. Your changes remain on screen; retry saving.",
    );
  }
  const data = await response.json();
  if (!response.ok) {
    const error = new Error(data.message ?? "Ошибка / Error");
    error.data = data;
    throw error;
  }
  return data;
}
function withKeys(items) {
  // RU: Два вхождения одной песни — разные строки, им нужны разные локальные ключи.
  // EN: Occurrence identity is independent of the catalog song ID.
  return items.map((item) => ({ ...clone(item), key: crypto.randomUUID() }));
}
function remember() {
  undos.push(clone(current));
  if (undos.length > 50) undos.shift();
  publishAttempt = null;
}
function change(next) {
  if (busy) return;
  if (next.length > 200) {
    status(t("limit"), true);
    return;
  }
  remember();
  current = next;
  saver?.setEntries(current);
  render();
  status(t("dirty"));
}
function songEntry(song) {
  return {
    kind: "song",
    song_id: song.id,
    title: song.title,
    version: song.version,
    duration_seconds: song.duration_seconds,
    pause_before: null,
    key: crypto.randomUUID(),
  };
}
function node(tag, text, className) {
  const element = document.createElement(tag);
  if (text !== undefined) element.textContent = text;
  if (className) element.className = className;
  return element;
}
function button(text, label, handler, className) {
  const b = node("button", text, className);
  b.type = "button";
  b.setAttribute("aria-label", label);
  b.addEventListener("click", handler);
  return b;
}
function render() {
  if (!state) return;
  $("event").textContent = [state.group, state.date, state.title]
    .filter(Boolean)
    .join(" · ");
  $("catalog").inert = busy;
  $("setlist").inert = busy;
  $("older").disabled = busy;
  $("use-history").disabled = busy;
  const items = preview?.entries ?? current,
    numbers = numbered(items);
  $("setlist").replaceChildren();
  items.forEach((item, index) => {
    const row = node(
      "li",
      undefined,
      "entry" + (item.kind === "pause" ? " pause" : ""),
    );
    row.dataset.index = index;
    const handle = button(
      "⋮⋮",
      lang === "ru" ? "Переместить позицию" : "Move entry",
      () => {},
      "handle",
    );
    handle.disabled = !!preview;
    handle.addEventListener("pointerdown", (event) =>
      startDrag(event, { index }),
    );
    handle.addEventListener("keydown", (event) => {
      if (preview || !["ArrowUp", "ArrowDown"].includes(event.key)) return;
      event.preventDefault();
      change(move(current, index, index + (event.key === "ArrowUp" ? -1 : 1)));
      $("setlist")
        .children[
          Math.max(
            0,
            Math.min(
              current.length - 1,
              index + (event.key === "ArrowUp" ? -1 : 1),
            ),
          )
        ].querySelector(".handle")
        .focus();
    });
    row.append(
      handle,
      node("span", numbers[index], "number"),
      node(
        "span",
        item.kind === "pause" ? t("pause") : item.title,
        "song-name",
      ),
      node("span", clock(item.duration_seconds), "song-time"),
    );
    if (
      item.kind === "song" &&
      !state.catalog.some((song) => song.id === item.song_id)
    ) {
      row.classList.add("unavailable");
      row.title = t("missing");
    }
    const remove = button(
      "×",
      lang === "ru" ? "Удалить позицию" : "Remove entry",
      () => change(current.filter((_, i) => i !== index)),
      "remove",
    );
    remove.disabled = !!preview;
    row.append(remove);
    $("setlist").append(row);
  });
  $("total").textContent = clock(
    preview ? preview.seconds : total(current, state.default_pause_seconds),
  );
  $("planned").textContent =
    `${t("planned")}: ${state.planned_seconds ? clock(state.planned_seconds) : t("unknown")}`;
  $("empty").hidden = items.length > 0;
  $("empty").textContent = t("empty");
  $("history-label").textContent = preview
    ? `${preview.label} · ${clock(preview.seconds)}`
    : t("current");
  $("newer").disabled = historyIndex < 0 || busy;
  $("use-history").hidden = !preview;
  $("catalog").hidden = !!preview;
  $("undo").disabled = !undos.length || !!preview || busy;
  $("add-pause").disabled = !!preview || busy;
  $("save").disabled = !!preview || !dirty() || busy;
  $("publish").disabled =
    !!preview || !current.length || busy || !!saver?.blocked;
  $("remove-zone").hidden = !!preview;
  dirty()
    ? tg?.enableClosingConfirmation?.()
    : tg?.disableClosingConfirmation?.();
  renderCatalog();
}
function renderCatalog() {
  if (!state) return;
  const query = $("search").value.toLocaleLowerCase().replaceAll("ё", "е");
  const songs = state.catalog.filter((song) =>
    song.title.toLocaleLowerCase().replaceAll("ё", "е").includes(query),
  );
  const pages = Math.max(1, Math.ceil(songs.length / 40));
  catalogPage = Math.min(catalogPage, pages - 1);
  $("catalog-list").replaceChildren();
  songs.slice(catalogPage * 40, (catalogPage + 1) * 40).forEach((song) => {
    const row = node("div", undefined, "catalog-entry"),
      handle = button(
        "⋮⋮",
        lang === "ru" ? "Перетащить в сет-лист" : "Drag into setlist",
        () => {},
        "handle",
      );
    handle.addEventListener("pointerdown", (event) =>
      startDrag(event, { song }),
    );
    row.append(
      handle,
      node("span", clock(song.duration_seconds), "song-time"),
      node("span", song.title, "song-name"),
      button(
        "+",
        lang === "ru" ? "Добавить песню" : "Add song",
        () => change([...current, songEntry(song)]),
        "add",
      ),
    );
    $("catalog-list").append(row);
  });
  $("page-label").textContent = `${catalogPage + 1} / ${pages}`;
  $("page-prev").disabled = catalogPage === 0;
  $("page-next").disabled = catalogPage + 1 >= pages;
}
let drag = null,
  scrollFrame = 0;
function startDrag(event, source) {
  // RU: Только ручка блокирует жест прокрутки; остальной экран прокручивается обычно.
  // EN: Pointer capture supports mouse and touch; all drag edits remain local until Save.
  if (preview || busy || event.button > 0) return;
  event.preventDefault();
  const handle = event.currentTarget;
  handle.setPointerCapture(event.pointerId);
  const ghost = node(
    "div",
    source.song?.title ??
      (current[source.index].kind === "pause"
        ? t("pause")
        : current[source.index].title),
    "ghost",
  );
  document.body.append(ghost);
  drag = { ...source, ghost, x: event.clientX, y: event.clientY, moved: false };
  const update = (e) => {
    if (!drag) return;
    drag.moved = true;
    drag.x = e.clientX;
    drag.y = e.clientY;
    ghost.style.left = `${e.clientX + 12}px`;
    ghost.style.top = `${e.clientY - 20}px`;
    document
      .querySelectorAll(".drop-before, .drop-after")
      .forEach((n) => n.classList.remove("drop-before", "drop-after"));
    const target = document.elementFromPoint(e.clientX, e.clientY);
    const row = target?.closest("#setlist .entry");
    if (row)
      row.classList.add(
        e.clientY > row.getBoundingClientRect().top + row.offsetHeight / 2
          ? "drop-after"
          : "drop-before",
      );
    $("remove-zone").classList.toggle(
      "active",
      !!target?.closest("[data-remove]"),
    );
  };
  const auto = () => {
    if (!drag) return;
    const margin = 85,
      delta = drag.y < margin ? -12 : drag.y > innerHeight - margin ? 12 : 0;
    if (delta) scrollBy(0, delta);
    scrollFrame = requestAnimationFrame(auto);
  };
  const finish = (e) => {
    if (!drag) return;
    const target = document.elementFromPoint(e.clientX, e.clientY),
      row = target?.closest("#setlist .entry");
    if (drag.moved) {
      if (target?.closest("[data-remove]") && drag.index !== undefined)
        change(current.filter((_, i) => i !== drag.index));
      else if (
        row ||
        target?.closest("#setlist") ||
        target?.closest("#empty")
      ) {
        let at = row ? Number(row.dataset.index) : current.length;
        if (
          row &&
          e.clientY > row.getBoundingClientRect().top + row.offsetHeight / 2
        )
          at++;
        if (drag.song) {
          const list = clone(current);
          list.splice(at, 0, songEntry(drag.song));
          change(list);
        } else {
          if (at > drag.index) at--;
          change(move(current, drag.index, at));
        }
      }
    }
    ghost.remove();
    drag = null;
    cancelAnimationFrame(scrollFrame);
    $("remove-zone").classList.remove("active");
    document
      .querySelectorAll(".drop-before, .drop-after")
      .forEach((n) => n.classList.remove("drop-before", "drop-after"));
    handle.removeEventListener("pointermove", update);
    handle.removeEventListener("pointerup", finish);
    handle.removeEventListener("pointercancel", cancel);
  };
  const cancel = () => {
    if (drag) {
      ghost.remove();
      drag = null;
      cancelAnimationFrame(scrollFrame);
    }
    $("remove-zone").classList.remove("active");
    handle.removeEventListener("pointermove", update);
    handle.removeEventListener("pointerup", finish);
    handle.removeEventListener("pointercancel", cancel);
  };
  handle.addEventListener("pointermove", update);
  handle.addEventListener("pointerup", finish);
  handle.addEventListener("pointercancel", cancel);
  scrollFrame = requestAnimationFrame(auto);
}
async function browse(index) {
  // RU: История — отдельный preview. Текущий ввод заменяется только кнопкой ниже.
  // EN: Browsing past performances must not overwrite current edits.
  if (busy) return;
  if (index < 0) {
    preview = null;
    historyIndex = -1;
    render();
    return;
  }
  try {
    const source = await api("history", { index });
    preview = source;
    historyIndex = index;
    render();
    status(t("ready"));
  } catch (error) {
    status(error.message, true);
  }
}
$("older").addEventListener("click", () => browse(historyIndex + 1));
$("newer").addEventListener("click", () => browse(historyIndex - 1));
$("use-history").addEventListener("click", () => {
  if (!preview) return;
  if (dirty() && !confirm(t("replace"))) return;
  const catalog = new Map(state.catalog.map((song) => [song.id, song]));
  const replacement = preview.entries.map((item) => {
    if (item.kind === "pause") return item;
    const song = catalog.get(item.song_id);
    return song
      ? {
          ...item,
          title: song.title,
          version: song.version,
          duration_seconds: song.duration_seconds,
        }
      : item;
  });
  preview = null;
  historyIndex = -1;
  change(withKeys(replacement));
});
$("undo").addEventListener("click", () => {
  if (undos.length) {
    current = undos.pop();
    publishAttempt = null;
    saver?.setEntries(current);
    render();
    status(dirty() ? t("dirty") : t("ready"));
  }
});
$("search").addEventListener("input", () => {
  catalogPage = 0;
  renderCatalog();
});
$("page-prev").addEventListener("click", () => {
  catalogPage--;
  renderCatalog();
});
$("page-next").addEventListener("click", () => {
  catalogPage++;
  renderCatalog();
});
$("add-pause").addEventListener("click", () => {
  $("pause-error").textContent = "";
  $("pause-value").value = "";
  $("pause-dialog").showModal();
  $("pause-value").focus();
});
$("pause-cancel").addEventListener("click", () => $("pause-dialog").close());
$("pause-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  $("pause-submit").disabled = true;
  try {
    const value = await api("pause", { value: $("pause-value").value }, "POST");
    change([
      ...current,
      {
        kind: "pause",
        duration_seconds: value.seconds,
        key: crypto.randomUUID(),
      },
    ]);
    $("pause-dialog").close();
  } catch (error) {
    $("pause-error").textContent = error.message;
  } finally {
    $("pause-submit").disabled = false;
  }
});
function saveError(error) {
  if (error.data?.latest) {
    saver.blocked = true;
    saver.lastError = error;
    saver.pause();
    latest = error.data.latest;
    $("conflict-note").textContent = t("conflict");
    $("latest-list").textContent = asText(latest.entries);
    if (!$("conflict-dialog").open) $("conflict-dialog").showModal();
  } else status(error.message, true);
}
function persistence() {
  saver = new DraftPersistence(
    state.version,
    current,
    (payload) => api("save", payload, "POST"),
    (saved, unchanged) => {
      state = { ...state, ...saved };
      if (unchanged) current = withKeys(saved.entries);
      if (!drag) render();
      status(dirty() ? t("dirty") : t("saved"));
    },
    saveError,
  );
}
$("save").addEventListener("click", async () => {
  if (busy || preview || !saver) return;
  busy = true;
  saver.pause();
  render();
  status(t("wait"));
  try {
    await saver.flush();
  } catch {
    /* saveError keeps the input visible */
  } finally {
    busy = false;
    saver.resume();
    render();
  }
});
$("publish").addEventListener("click", async () => {
  if (busy || preview || !saver || saver.blocked) return;
  busy = true;
  saver.pause();
  render();
  try {
    await saver.flush();
    publishAttempt ??= {
      version: state.version,
      entries: wire(current),
      request_id: crypto.randomUUID(),
    };
    status(t("publishing"));
    let result = await api("publish", publishAttempt, "POST");
    if (result.version !== undefined) {
      state.version = result.version;
      saver.version = result.version;
    }
    // RU: Закрываем только после подтверждения доставки outbox, не после постановки в очередь.
    // EN: No Bot API token in the web process; the existing worker confirms actual delivery.
    for (
      let attempt = 0;
      result.status === "pending" && attempt < 30;
      attempt++
    ) {
      await new Promise((resolve) => setTimeout(resolve, 1500));
      result = await api("publication");
    }
    if (result.status === "delivered") {
      tg?.disableClosingConfirmation?.();
      status(
        lang === "ru"
          ? "Предложение отправлено в группу."
          : "Proposal sent to the group.",
      );
      tg?.close?.();
    } else if (result.status === "failed") {
      publishAttempt = null;
      status(t("failed"), true);
    } else status(t("queued"));
  } catch (error) {
    saveError(error);
  } finally {
    busy = false;
    saver.resume();
    render();
  }
});
$("load-latest").addEventListener("click", () => {
  if (!latest) return;
  if (dirty() && !confirm(t("replace"))) return;
  state = { ...state, ...latest };
  current = withKeys(latest.entries);
  saver?.pause();
  persistence();
  publishAttempt = null;
  undos = [];
  $("conflict-dialog").close();
  render();
  status(t("ready"));
});
$("copy-own").addEventListener("click", async () => {
  try {
    await navigator.clipboard.writeText(asText(current));
    status(t("copied"));
  } catch {
    status(asText(current));
  }
});
$("conflict-close").addEventListener("click", () =>
  $("conflict-dialog").close(),
);
$("back-bot").addEventListener("click", async () => {
  if (!state || busy || !saver) return;
  busy = true;
  saver.pause();
  render();
  try {
    await saver.flush();
  } catch {
    busy = false;
    saver.resume();
    render();
    return;
  }
  if (tg?.openTelegramLink) tg.openTelegramLink(state.bot_url);
  else location.href = state.bot_url;
  tg?.close?.();
});
function labels() {
  for (const [id, key] of Object.entries({
    heading: "heading",
    "catalog-heading": "catalog",
    "search-label": "search",
    "private-note": "private",
    save: "save",
    publish: "publish",
    "back-bot": "back",
    "add-pause": "addPause",
    undo: "undo",
    "remove-zone": "remove",
    "use-history": "use",
    "load-latest": "loadLatest",
    "copy-own": "copy",
    "conflict-close": "continue",
    "pause-cancel": "cancel",
    "pause-submit": "add",
    "pause-title": "pauseTitle",
    "pause-label": "pauseHint",
  }))
    $(id).textContent = t(key);
  $("search").placeholder = t("search");
  document.documentElement.lang = lang;
}
async function boot() {
  if (!tg?.initData) {
    status(t("auth"), true);
    $("save").disabled = true;
    return;
  }
  try {
    state = await api(session ? "state" : "launch");
    session = state.session;
    lang = state.language === "en" ? "en" : "ru";
    labels();
    $("event").textContent = [state.group, state.date, state.title]
      .filter(Boolean)
      .join(" · ");
    current = withKeys(state.entries);
    persistence();
    render();
    status(t("ready"));
  } catch (error) {
    status(error.message, true);
  }
}
document.addEventListener("visibilitychange", () => {
  if (document.hidden && saver && dirty() && !saver.blocked)
    saver.flush().catch(() => {});
});
boot();
