import { clone, clock, total, numbered, wire, move, asText } from "./model.js";
import { DraftPersistence } from "./persistence.js";
const tg = window.Telegram?.WebApp,
  $ = (id) => document.getElementById(id);
const words = {
  ru: {
    heading: "Ваш черновик",
    planned: "Плановая длительность",
    plannedHint: "45 — минуты, 1:15 — час и 15 минут, - — очистить",
    plannedSaved: "Плановая длительность сохранена.",
    plannedQueued: "Запрос в очереди. Нажмите, чтобы проверить результат.",
    plannedConflict: "Данные выступления изменились. Проверьте текущую длительность и повторите правку.",
    unknown: "Не задано",
    current: "Текущий черновик",
    catalog: "Репертуар",
    search: "Поиск песни",
    private:
      "Черновик сохраняется автоматически. Отправка группе — только по кнопке.",
    publish: "📤 Предложить группе",
    publishing: "Отправляем предложение в группу…",
    queued: "Предложение в очереди. Нажмите ещё раз, чтобы проверить отправку.",
    failed:
      "Не удалось отправить предложение. Черновик сохранён. Можно повторить.",
    save: "Повторить сохранение",
    saveOk: "Сохранено", savePending: "Сохраняем…", saveFailed: "Не удалось сохранить",
    historyEmpty: "Пока нет доступных версий или прошлых выступлений.",
    backDraft: "К черновику", undoLabel: "Отменить последнее действие",
    shorter: "запас", longer: "дольше на", onTarget: "ровно по плану",
    repertoireSong: "Песня в репертуаре",
    back: "Вернуться к боту",
    pause: "пауза",
    addPause: "＋ Пауза",
    addPauseBottom: "+ пауза",
    older: "История",
    undo: "↶ Undo",
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
    editPause: "Изменить паузу",
    pauseText: "Текст паузы — можно оставить пустым",
    pauseChanged: "Пауза изменилась. Закройте окно и откройте её заново.",
    pauseHint: "Без единиц — минуты",
    average: "Средняя пауза",
    averageShort: "Сред. пауза",
    seconds: "сек",
    averageTitle: "Средняя пауза между песнями",
    averageHint: "В секундах. 0 — без пауз. Отдельные паузы сохраняются.",
    averageGroupHint: "В секундах. Для этого черновика и новых сет-листов коллектива. Уже созданные списки не изменятся.",
    averagePrivateHint: "В секундах. Изменится только ваш черновик. Паузу для новых выступлений задаёт администратор.",
    apply: "Применить",
    requestAccess: "Запросить права у владельца",
    reload: "Обновите редактор, чтобы получить актуальный каталог.",
    editSong: "Редактировать песню", addSong: "＋ Добавить песню",
    songTitle: "Название *", songDuration: "Длительность ММ:СС *",
    songLights: "Цвета / свет", songComment: "Комментарий",
    songAliases: "Альтернативные названия — по одному на строку",
    songScope: "Меняется запись репертуара, поэтому правка отразится во всех её вхождениях в рабочих сет-листах. Правки участника требуют одобрения администратора.",
    songSave: "Сохранить", songPending: "Запрос сохраняется ботом…",
    songDelete: "Удалить песню", songArchived: "Песня перенесена в архив репертуара.",
    songArchiveConfirm: "Песня исчезнет из репертуара. Её можно будет восстановить из архива.",
    songArchiveDraft: "Песня останется в текущем черновике. Чтобы сохранить следующие правки черновика, уберите её из списка или восстановите из архива.",
    songQueued: "Запрос в очереди. Проверьте сохранение перед новым изменением песни.",
    close: "Закрыть",
    songCheck: "Проверить сохранение", songSaved: "Репертуар обновлён.",
    songProposed: "Изменения предложены администратору. До одобрения репертуар не изменится.",
    songReload: "Загрузить актуальную",
  },
  en: {
    heading: "Private draft",
    planned: "Planned duration",
    plannedHint: "45 means minutes, 1:15 means 1 hour and 15 minutes, - clears the duration",
    plannedSaved: "Planned duration saved.",
    plannedQueued: "Request queued. Press to check the result.",
    plannedConflict: "The performance changed. Review the current duration and retry.",
    unknown: "Not set",
    current: "Current draft",
    catalog: "Repertoire",
    search: "Find a song",
    private:
      "Draft saved automatically. Only the publish button sends it to the group.",
    publish: "📤 Suggest to the band",
    publishing: "Sending your proposal…",
    queued: "Proposal queued. Press again to check delivery.",
    failed: "Delivery failed. Your draft is saved; you can retry.",
    save: "Retry save",
    saveOk: "Saved", savePending: "Saving…", saveFailed: "Could not save",
    historyEmpty: "No versions or past performances are available yet.",
    backDraft: "Back to draft", undoLabel: "Undo last action",
    shorter: "remaining", longer: "over by", onTarget: "on target",
    repertoireSong: "Song in repertoire",
    back: "Back to bot",
    pause: "pause",
    addPause: "＋ Pause",
    addPauseBottom: "+ pause",
    older: "History",
    undo: "↶ Undo",
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
    editPause: "Edit pause",
    pauseText: "Pause text — may be empty",
    pauseChanged: "This pause changed. Close the dialog and reopen it.",
    pauseHint: "Numbers without units mean minutes",
    average: "Average pause",
    averageShort: "Avg. pause",
    seconds: "s",
    averageTitle: "Average pause between songs",
    averageHint: "In seconds. 0 means no pause. Individual pauses are kept.",
    averageGroupHint: "In seconds. Also applies to new group setlists. Individual pauses are kept.",
    averagePrivateHint: "In seconds. Only your draft changes. Administrators set the default for new performances.",
    apply: "Apply",
    requestAccess: "Request administrator access",
    reload: "Reopen the editor to refresh the catalog.",
    editSong: "Edit song", addSong: "＋ Add song",
    songTitle: "Title *", songDuration: "Duration MM:SS *",
    songLights: "Colors / lighting", songComment: "Comment",
    songAliases: "Alternative titles — one per line",
    songScope: "This updates the repertoire entry and all its occurrences in working setlists. Member edits require administrator approval.",
    songSave: "Save", songPending: "The bot is saving your request…",
    songDelete: "Delete song", songArchived: "Song moved to the repertoire archive.",
    songArchiveConfirm: "The song will be removed from the repertoire. You can restore it from the archive.",
    songArchiveDraft: "The song will stay in this draft. To save further draft edits, remove it from the draft or restore it from the archive.",
    songQueued: "Request queued. Check its result before editing another song.",
    close: "Close",
    songCheck: "Check save", songSaved: "Repertoire updated.",
    songProposed: "Changes proposed to an administrator. The repertoire stays unchanged until approval.",
    songReload: "Load current song",
  },
};
let lang = "ru",
  state = null,
  current = [],
  defaultPause = 5,
  undos = [],
  preview = null,
  catalogPage = 0,
  busy = false,
  latest = null,
  saver = null,
  publishAttempt = null,
  editingPause = null,
  pauseDialogSerial = 0;
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
  undos.push({ entries: clone(current), gap: defaultPause });
  if (undos.length > 50) undos.shift();
  publishAttempt = null;
}
function change(next, gap = defaultPause) {
  if (busy) return;
  if (next.length > 200) {
    status(t("limit"), true);
    return;
  }
  remember();
  current = next;
  defaultPause = gap;
  saver?.setDraft(current, defaultPause);
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
  $("average-pause").disabled = busy || !!preview;
  $("average-pause").textContent = t("averageShort");
  $("average-pause").setAttribute("aria-label", `${t("average")}: ${preview?.default_pause_seconds ?? defaultPause}${t("seconds")}`);
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
    row.append(handle, node("span", numbers[index], "number"));
    if (item.kind === "pause") {
      const edit = button("", t("editPause"), () => openPause(index), "pause-label");
      edit.disabled = !!preview;
      edit.append(node("span", item.pause_label ?? t("pause"), "song-name"));
      const mark = node("span", "✎", "edit-mark");
      mark.setAttribute("aria-hidden", "true");
      edit.append(mark);
      row.append(edit);
    } else {
      row.append(node("span", item.title, "song-name"));
      if (!preview && state.catalog.some(song => song.id === item.song_id))
        row.append(button("✎", `${t("editSong")}: ${item.title}`, () => openSong(item.song_id), "song-edit"));
    }
    row.append(node("span", clock(item.duration_seconds), "song-time"));
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
  const seconds = preview ? preview.seconds : total(current, defaultPause);
  const target = state.planned_seconds;
  const delta = target - seconds;
  const plan = target ? clock(target) : t("unknown");
  const canEdit = state.can_set_group_default;
  $("planned").textContent = canEdit ? "" : plan;
  $("planned").hidden = canEdit;
  $("planned-edit").hidden = !canEdit;
  $("planned-edit").textContent = plan;
  $("planned-edit").setAttribute("aria-label", `${t("planned")}: ${plan}`);
  $("planned-delta").textContent = !target ? "" : delta === 0
    ? `· ${t("onTarget")}`
    : `· ${t(delta > 0 ? "shorter" : "longer")} ${clock(Math.abs(delta))}`;
  $("total").textContent = clock(seconds);
  $("empty").hidden = items.length > 0;
  $("empty").textContent = t("empty");
  $("history-label").textContent = preview
    ? `${preview.label} · ${clock(preview.seconds)}`
    : t("current");
  $("newer").hidden = !preview;
  $("newer").disabled = busy;
  $("use-history").hidden = !preview;
  $("catalog").hidden = !!preview;
  $("undo").disabled = !undos.length || !!preview || busy;
  $("add-pause").disabled = !!preview || busy;
  $("add-pause-bottom").disabled = !!preview || busy;
  $("add-pause-bottom").hidden = !!preview;
  renderSaveStatus();
  $("publish").disabled =
    !!preview || !current.length || busy || !!saver?.blocked;
  dirty()
    ? tg?.enableClosingConfirmation?.()
    : tg?.disableClosingConfirmation?.();
  renderCatalog();
}
function renderCatalog() {
  if (!state) return;
  $("add-song").textContent = t("addSong");
  for (const [id, word] of Object.entries({
    "song-title-label": "songTitle", "song-duration-label": "songDuration",
    "song-lights-label": "songLights", "song-comment-label": "songComment",
    "song-aliases-label": "songAliases", "song-scope": "songScope",
    "song-cancel": catalogAttempt ? "close" : "cancel", "song-reload": "songReload",
  })) $(id).textContent = t(word);
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
    row.dataset.songId = song.id;
    handle.addEventListener("pointerdown", (event) =>
      startDrag(event, { song }),
    );
    row.append(
      handle,
      node("span", clock(song.duration_seconds), "song-time"),
      node("span", song.title, "song-name"),
      button("✎", `${t("editSong")}: ${song.title}`, () => openSong(song.id), "song-edit"),
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
let suppressPauseClickUntil = 0;
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
        ? (current[source.index].pause_label ?? t("pause"))
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
    target?.closest("#add-pause-bottom")?.classList.add("drop-after");
    if (row)
      row.classList.add(
        e.clientY > row.getBoundingClientRect().top + row.offsetHeight / 2
          ? "drop-after"
          : "drop-before",
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
      suppressPauseClickUntil = performance.now() + 500;
      if (
        row ||
        target?.closest("#setlist") ||
        target?.closest("#empty") ||
        target?.closest("#add-pause-bottom")
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
      document.querySelectorAll(".drop-before, .drop-after")
        .forEach(n => n.classList.remove("drop-before", "drop-after"));
    }
    handle.removeEventListener("pointermove", update);
    handle.removeEventListener("pointerup", finish);
    handle.removeEventListener("pointercancel", cancel);
  };
  handle.addEventListener("pointermove", update);
  handle.addEventListener("pointerup", finish);
  handle.addEventListener("pointercancel", cancel);
  scrollFrame = requestAnimationFrame(auto);
}
$("older").addEventListener("click", async () => {
  if (busy || !state) return;
  const dialog = $("history-dialog"), list = $("history-sources");
  list.textContent = t("loading");
  dialog.showModal();
  try {
    const { sources } = await api("history_menu");
    list.replaceChildren();
    for (const source of sources) {
      const label = `${source.label} · ${clock(source.seconds)}`;
      list.append(button(label, label, async () => {
        if (busy) return;
        busy = true;
        render();
        try {
          preview = await api("history", { index: source.index });
          dialog.close();
        } catch (error) {
          list.prepend(node("p", error.message, "error"));
        } finally {
          busy = false;
          render();
        }
      }));
    }
    if (!sources.length) list.textContent = t("historyEmpty");
  } catch (error) {
    list.textContent = error.message;
  }
});
$("newer").addEventListener("click", () => {
  preview = null;
  render();
});
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
  const gap = preview.default_pause_seconds;
  preview = null;
  change(withKeys(replacement), gap);
});
$("undo").addEventListener("click", () => {
  if (undos.length) {
    const previous = undos.pop();
    current = previous.entries;
    defaultPause = previous.gap;
    publishAttempt = null;
    saver?.setDraft(current, defaultPause);
    render();
    status(dirty() ? t("dirty") : t("ready"));
  }
});
$("search").addEventListener("input", () => {
  catalogPage = 0;
  renderCatalog();
});
let searchScrollFrame = 0,
  pointerInProgress = false,
  searchPointer = false,
  searchClick = false;
function alignSearch() {
  cancelAnimationFrame(searchScrollFrame);
  searchScrollFrame = requestAnimationFrame(() => {
    const search = $("search");
    if (document.activeElement !== search) return;
    // RU: iOS может сдвинуть видимую область при открытии клавиатуры.
    // EN: Re-align after keyboard/viewport changes, not just before its animation.
    const visibleTop = window.visualViewport?.offsetTop ?? 0;
    const safeTop = Math.max(tg?.contentSafeAreaInset?.top ?? 0, tg?.safeAreaInset?.top ?? 0);
    const offset = search.getBoundingClientRect().top - visibleTop - safeTop - 12;
    if (Math.abs(offset) > 1)
      window.scrollTo({ top: window.scrollY + offset, behavior: "instant" });
  });
}
function settleSearchLayout() {
  requestAnimationFrame(() => {
    // RU: Не меняем размеры страницы посреди тапа или перетаскивания.
    // EN: Keep the clicked target in place until pointerup/click handlers finish.
    if (document.activeElement !== $("search") && !pointerInProgress)
      document.body.classList.remove("search-active");
  });
}
function leaveSearch(event) {
  if (event.target instanceof Element && event.target.closest("#search, label[for='search']"))
    return;
  if (document.activeElement === $("search")) $("search").blur();
  settleSearchLayout();
}
$("search").addEventListener("focus", () => {
  // RU: Новый вход в поиск начинает новый запрос; редактирование текущего не очищаем.
  // EN: Clear only on re-entry, never on input, autosave or cursor movement.
  $("search").value = "";
  catalogPage = 0;
  renderCatalog();
  document.body.classList.add("search-active");
  alignSearch();
});
$("search").addEventListener("blur", settleSearchLayout);
$("search").addEventListener("keydown", (event) => {
  if (!event.isComposing && ["Enter", "Escape"].includes(event.key)) {
    event.preventDefault();
    $("search").blur();
  }
});
document.addEventListener("pointerdown", (event) => {
  pointerInProgress = true;
  searchPointer = event.target instanceof Element && !!event.target.closest("#search");
  leaveSearch(event);
}, true);
for (const name of ["pointerup", "pointercancel"])
  document.addEventListener(name, event => {
    // RU: Прокрутка во время mousedown может поменять цель mouseup на компьютере.
    // EN: Restore input focus after the pointer sequence without clearing a second time.
    if (name === "pointerup" && searchPointer && event.pointerType === "mouse")
      $("search").focus({ preventScroll: true });
    searchClick = name === "pointerup" && searchPointer;
    pointerInProgress = false;
    searchPointer = false;
    settleSearchLayout();
  }, true);
// RU/EN: Also handle keyboard activation and synthetic clicks without pointer events.
document.addEventListener("click", event => {
  if (searchClick) {
    searchClick = false;
    return;
  }
  leaveSearch(event);
}, true);
window.addEventListener("resize", alignSearch);
window.visualViewport?.addEventListener("resize", alignSearch);
window.visualViewport?.addEventListener("scroll", alignSearch);
$("page-prev").addEventListener("click", () => {
  catalogPage--;
  renderCatalog();
});
$("page-next").addEventListener("click", () => {
  catalogPage++;
  renderCatalog();
});
let editingSong = null, catalogAttempt = null, catalogRequest = null, songSubmitting = false;
function fillSong(song) {
  $("song-title").value = song?.title ?? "";
  $("song-duration").value = song?.duration ?? "";
  $("song-bpm").value = song?.bpm ?? "";
  $("song-lights").value = song?.lights ?? "";
  $("song-comment").value = song?.comment ?? "";
  $("song-aliases").value = (song?.aliases ?? []).join("\n");
}
function lockSongFields(locked) {
  for (const element of $("song-form").querySelectorAll("input, textarea")) element.disabled = locked;
}
async function openSong(id = null) {
  if (!state || busy || preview) return;
  if (catalogAttempt) {
    // RU: Потерянный ответ повторяется с прежним ID, а не создаёт новую песню.
    // EN: Resume an unresolved operation before accepting another catalog mutation.
    $("song-dialog").showModal();
    $("song-error").textContent = t("songQueued");
    return;
  }
  busy = true;
  render();
  try {
    editingSong = id ? await api("song", { song_id: id }) : null;
    fillSong(editingSong);
    lockSongFields(false);
    $("song-error").textContent = "";
    $("song-dialog-title").textContent = t(id ? "repertoireSong" : "addSong");
    $("song-submit").textContent = t("songSave");
    $("song-delete").textContent = t("songDelete");
    $("song-delete").hidden = !editingSong || !state.can_set_group_default;
    $("song-reload").hidden = true;
    $("song-dialog").showModal();
  } catch (error) { status(error.message, true); }
  finally { busy = false; render(); }
}
$("add-song").addEventListener("click", () => openSong());
$("song-cancel").addEventListener("click", () => {
  if (!songSubmitting) $("song-dialog").close();
});
$("song-dialog").addEventListener("cancel", event => {
  if (songSubmitting) event.preventDefault();
});
$("song-reload").addEventListener("click", async () => {
  if (!editingSong || songSubmitting) return;
  try {
    editingSong = await api("song", { song_id: editingSong.id });
    fillSong(editingSong);
    $("song-error").textContent = "";
    $("song-reload").hidden = true;
  } catch (error) { $("song-error").textContent = error.message; }
});
async function refreshRepertoire() {
  const updated = await api("state");
  state.catalog = updated.catalog;
  const catalog = new Map(updated.catalog.map(song => [song.id, song]));
  const refresh = items => items.map(item => {
    const song = item.kind === "song" && catalog.get(item.song_id);
    return song ? { ...item, title: song.title, duration_seconds: song.duration_seconds, version: song.version } : item;
  });
  // RU: Порядок/повторы/паузы неизменны. Отмена порядка не отменяет правку репертуара.
  // EN: Refresh all occurrences, including Undo, without replacing the user's draft.
  current = refresh(current);
  undos = undos.map(item => ({ ...item, entries: refresh(item.entries) }));
  saver.setDraft(current, defaultPause);
  render();
}
$("song-delete").addEventListener("click", () => {
  if (!editingSong || !state?.can_set_group_default || catalogAttempt || songSubmitting) return;
  const used = current.some(item => item.song_id === editingSong.id);
  const warning = used ? `\n\n${t("songArchiveDraft")}` : "";
  if (confirm(`${editingSong.title}\n\n${t("songArchiveConfirm")}${warning}`)) void submitSong(true);
});
$("song-form").addEventListener("submit", event => {
  event.preventDefault();
  void submitSong();
});
async function submitSong(archive = false) {
  if (songSubmitting || !saver) return;
  const song = editingSong;
  songSubmitting = true;
  busy = true;
  $("song-submit").disabled = true;
  $("song-cancel").disabled = true;
  $("song-reload").disabled = true;
  $("song-delete").disabled = true;
  saver.pause();
  render();
  try {
    if (!catalogAttempt) {
      await saver.flush();
      catalogAttempt = {
        request_id: crypto.randomUUID(), song_id: song?.id ?? null,
        expected: song?.version ?? null,
      };
      if (archive) {
        catalogAttempt.archive = true;
      } else {
        catalogAttempt.fields = {
          title: $("song-title").value.trim(), duration_seconds: $("song-duration").value.trim(),
          bpm: $("song-bpm").value ? Number($("song-bpm").value) : null,
          lights: $("song-lights").value, comment: $("song-comment").value,
          aliases: $("song-aliases").value.split("\n").map(v => v.trim()).filter(Boolean),
        };
      }
    }
    lockSongFields(true);
    $("song-error").textContent = t("songPending");
    let result = catalogRequest
      ? await api("catalog_status", { request: catalogRequest })
      : await api(catalogAttempt.archive ? "catalog_archive" : "catalog_save", catalogAttempt, "POST");
    catalogRequest = result.request;
    for (let i = 0; result.status === "pending" && i < 30; i++) {
      await new Promise(resolve => setTimeout(resolve, 1000));
      result = await api("catalog_status", { request: catalogRequest });
    }
    if (result.status === "pending") {
      $("song-error").textContent = t("songQueued");
      $("song-submit").textContent = t("songCheck");
      return;
    }
    if (result.status === "failed") {
      catalogAttempt = catalogRequest = null;
      const error = new Error(result.message);
      error.data = result;
      throw error;
    }
    // Keep the receipt until catalog refresh succeeds; retry must not save twice.
    await refreshRepertoire();
    catalogAttempt = catalogRequest = null;
    $("song-dialog").close();
    status(t(result.archived ? "songArchived" : result.proposed ? "songProposed" : "songSaved"));
  } catch (error) {
    // Explicit validation rejection means nothing was queued. Network failures stay retryable.
    if (!catalogRequest && error.data) catalogAttempt = null;
    $("song-error").textContent = error.message;
    if (!catalogAttempt) lockSongFields(false);
    $("song-reload").hidden = !editingSong || error.data?.error !== "conflict";
    $("song-submit").textContent = t(catalogAttempt ? "songCheck" : "songSave");
  } finally {
    songSubmitting = busy = false;
    $("song-submit").disabled = false;
    $("song-cancel").disabled = false;
    $("song-reload").disabled = false;
    $("song-delete").disabled = !!catalogAttempt;
    saver.resume();
    render();
  }
}
function openPause(index = null) {
  if (!state || preview || busy) return;
  const item = index === null ? null : current[index];
  if (index !== null && item?.kind !== "pause") return;
  pauseDialogSerial++;
  $("pause-submit").disabled = false;
  editingPause = item ? { index, original: JSON.stringify(wire([item])) } : null;
  $("pause-error").textContent = "";
  $("pause-title").textContent = t(item ? "editPause" : "pauseTitle");
  $("pause-submit").textContent = t(item ? "apply" : "add");
  $("pause-text").value = item?.pause_label ?? t("pause");
  $("pause-value").value = item ? clock(item.duration_seconds) : "";
  $("pause-dialog").showModal();
  $("pause-value").focus();
}
$("add-pause").addEventListener("click", () => openPause());
$("add-pause-bottom").addEventListener("click", () => {
  if (performance.now() >= suppressPauseClickUntil) openPause();
});
$("pause-cancel").addEventListener("click", () => {
  pauseDialogSerial++;
  $("pause-dialog").close();
});
$("pause-dialog").addEventListener("cancel", () => { pauseDialogSerial++; });
$("average-pause").addEventListener("click", () => {
  if (!state || busy || preview) return;
  $("average-value").value = defaultPause;
  $("average-label").textContent = t(state.can_set_group_default ? "averageGroupHint" : "averagePrivateHint");
  $("average-dialog").showModal();
  $("average-value").focus();
});
$("average-cancel").addEventListener("click", () => $("average-dialog").close());
$("average-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const gap = Number($("average-value").value);
  if (!Number.isInteger(gap) || gap < 0 || gap > 86400) return;
  if (gap !== defaultPause) change(clone(current), gap);
  $("average-dialog").close();
});
$("pause-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  $("pause-submit").disabled = true;
  const serial = pauseDialogSerial;
  try {
    // RU/EN: Capture before awaiting the duration request; autosave can replace local keys.
    const edit = editingPause;
    const label = $("pause-text").value;
    const value = await api("pause", { value: $("pause-value").value }, "POST");
    if (serial !== pauseDialogSerial || !$("pause-dialog").open) return;
    const item = {
        kind: "pause",
        duration_seconds: value.seconds,
        ...(label !== t("pause") ? { pause_label: label } : {}),
        key: crypto.randomUUID(),
      };
    if (edit) {
      if (!current[edit.index] || JSON.stringify(wire([current[edit.index]])) !== edit.original)
        throw new Error(t("pauseChanged"));
      const updated = clone(current);
      updated[edit.index] = item;
      change(updated);
    } else change([...current, item]);
    $("pause-dialog").close();
  } catch (error) {
    if (serial === pauseDialogSerial) $("pause-error").textContent = error.message;
  } finally {
    if (serial === pauseDialogSerial) $("pause-submit").disabled = false;
  }
});
let plannedAttempt = null, plannedRequest = null;
$("planned-edit").addEventListener("click", () => {
  if (!state?.can_set_group_default) return;
  if (!plannedAttempt) {
    $("planned-value").value = "";
    $("planned-status").textContent = "";
    $("planned-submit").textContent = t("songSave");
    $("planned-value").readOnly = false;
  }
  $("planned-dialog").showModal();
});
$("planned-close").addEventListener("click", () => $("planned-dialog").close());
$("planned-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const submit = $("planned-submit"), input = $("planned-value"), resultText = $("planned-status");
  if (submit.disabled) return;
  plannedAttempt ??= {
    request_id: crypto.randomUUID(), expected: state.metadata_version, value: input.value.trim(),
  };
  submit.disabled = input.readOnly = true;
  resultText.textContent = t("songPending");
  try {
    let result = plannedRequest
      ? await api("catalog_status", { request: plannedRequest })
      : await api("duration_save", plannedAttempt, "POST");
    plannedRequest = result.request;
    for (let i = 0; result.status === "pending" && i < 10; i++) {
      await new Promise(resolve => setTimeout(resolve, 1000));
      result = await api("catalog_status", { request: plannedRequest });
    }
    if (result.status === "pending") {
      resultText.textContent = t("plannedQueued");
      return;
    }
    plannedAttempt = plannedRequest = null;
    if (result.status === "failed") {
      const error = new Error(result.message);
      error.data = result;
      throw error;
    }
    if (result.metadata_version >= state.metadata_version) {
      state.metadata_version = result.metadata_version;
      state.planned_seconds = result.planned_seconds;
    }
    $("planned-dialog").close();
    render();
    status(t("plannedSaved"));
  } catch (error) {
    if (!plannedRequest && error.data && error.data.error !== "unavailable") plannedAttempt = null;
    resultText.textContent = error.message;
    if (error.data?.error === "conflict") {
      resultText.textContent = t("plannedConflict");
      try {
        const latestState = await api("state");
        state.metadata_version = latestState.metadata_version;
        state.planned_seconds = latestState.planned_seconds;
        render();
      } catch (refreshError) {
        resultText.textContent = refreshError.message;
      }
    }
  } finally {
    submit.disabled = false;
    input.readOnly = !!plannedAttempt;
    submit.textContent = t(plannedAttempt ? "songCheck" : "songSave");
  }
});
function renderSaveStatus() {
  const failed = !!saver?.lastError;
  const retry = $("save");
  $("save-status").textContent = saver
    ? t(failed ? "saveFailed" : dirty() ? "savePending" : "saveOk")
    : "";
  retry.hidden = !failed || !!saver?.blocked;
  retry.disabled = !dirty() || busy || !!preview;
}
function saveError(error) {
  renderSaveStatus();
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
      if (saved.metadata_version < state.metadata_version) {
        saved.metadata_version = state.metadata_version;
        saved.planned_seconds = state.planned_seconds;
      }
      state = { ...state, ...saved };
      if (unchanged) {
        current = withKeys(saved.entries);
        defaultPause = saved.default_pause_seconds;
      }
      if (!drag) render();
      status(dirty() ? t("dirty") : t("saved"));
    },
    saveError,
    defaultPause,
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
  defaultPause = latest.default_pause_seconds;
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
    "add-pause-bottom": "addPauseBottom",
    older: "older",
    newer: "backDraft",
    "history-title": "older",
    "history-close": "close",
    "planned-title": "planned",
    "planned-hint": "plannedHint",
    "planned-close": "close",
    "planned-submit": "songSave",
    undo: "undo",
    "use-history": "use",
    "load-latest": "loadLatest",
    "copy-own": "copy",
    "conflict-close": "continue",
    "pause-cancel": "cancel",
    "pause-submit": "add",
    "pause-title": "pauseTitle",
    "pause-label": "pauseHint",
    "pause-text-label": "pauseText",
    "average-title": "averageTitle",
    "average-label": "averageHint",
    "average-cancel": "cancel",
    "average-submit": "apply",
  }))
    $(id).textContent = t(key);
  $("undo").setAttribute("aria-label", t("undoLabel"));
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
    defaultPause = state.default_pause_seconds;
    persistence();
    render();
    status(t("ready"));
  } catch (error) {
    status(error.message, true);
    if (error.data?.action_url) {
      $("access-request").hidden = false;
      $("access-request").textContent = t("requestAccess");
      $("access-request").onclick = () => {
        if (tg?.openTelegramLink) tg.openTelegramLink(error.data.action_url);
        else location.href = error.data.action_url;
        tg?.close?.();
      };
    }
  }
}
document.addEventListener("visibilitychange", () => {
  if (document.hidden && saver && dirty() && !saver.blocked)
    saver.flush().catch(() => {});
});
boot();
