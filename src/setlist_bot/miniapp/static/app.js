import { withBotLabels } from "./bot-labels.js";
import "./modal-scroll.js";
import { clone, clock, total, numbered, wire, move, asText } from "./model.js";
import { DraftPersistence } from "./persistence.js";
import { bandUrl, pageBackText, performanceUrl, showWorkspace, workspaceParams, workspaceText } from "./workspace.js";
const telegram = window.Telegram?.WebApp;
const tg = telegram?.initData ? telegram : null,
  $ = (id) => document.getElementById(id);
const words = withBotLabels("app", {
  ru: {
    heading: "Ваш черновик", setlist: "Сет-лист", toCatalog: "К репертуару ↓", toSetlist: "К сет-листу ↑",
    loadShared: "Взять общий сет-лист за основу", saveShared: "Сохранить сет-лист как общий",
    loadSharedConfirm: "Заменить содержимое личного черновика текущим общим сет-листом?",
    saveSharedConfirm: "Заменить общий сет-лист выступления этим вариантом?",
    clearSharedConfirm: "Очистить общий сет-лист выступления?",
    sharedSaved: "Общий сет-лист сохранён.",
    sharedWorking: "Сохраняем общий сет-лист…", sharedCheck: "Проверить сохранение общего сет-листа",
    sharedConflict: "Общий сет-лист изменился. Ваш вариант сохранился в черновике. Возьмите актуальный список за основу, чтобы продолжить.",
    planned: "Плановая длительность",
    plannedHint: "45 — минуты, 1:15 — час и 15 минут, - — очистить",
    plannedSaved: "Плановая длительность сохранена.",
    plannedQueued: "Запрос в очереди. Нажмите, чтобы проверить результат.",
    plannedConflict: "Данные выступления изменились. Проверьте текущую длительность и повторите правку.",
    unknown: "Не задано",
    current: "Текущий черновик",
    currentShared: "Общий сет-лист", personalDraft: "Мой черновик",
    replacePersonal: "Заменить сохранённый личный черновик новым вариантом?",
    approvedProposal: "Общий сет-лист уже утверждён. Всё равно предложить свой вариант?",
    confirmProposal: "Да, предложить",
    search: "Поиск песни",
    private:
      "Черновик сохраняется автоматически. Отправка группе — только по кнопке.",
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
    older: "История", editorOptions: "Опции", emptyHistoryHint: "(взять из прошлого выступления)",
    undo: "↶ Отменить",
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
    add: "Добавить",
    pauseTitle: "Длительность паузы",
    editPause: "Изменить паузу",
    pauseText: "Текст паузы — можно оставить пустым",
    pauseChanged: "Пауза изменилась. Закройте окно и откройте её заново.",
    pauseHint: "Без единиц — минуты",
    average: "Средняя пауза",
    seconds: "сек",
    averageTitle: "Средняя пауза между песнями",
    averageHint: "В секундах. 0 — без пауз. Отдельные паузы сохраняются.",
    averageGroupHint: "В секундах. Для этого черновика и новых сет-листов коллектива. Уже созданные списки не изменятся.",
    averagePrivateHint: "В секундах. Изменится только ваш черновик. Паузу для новых выступлений задаёт администратор.",
    apply: "Применить",
    reload: "Обновите редактор, чтобы получить актуальный каталог.",
    editSong: "Редактировать песню", addSong: "＋ Добавить песню",
    songRestored: "Песня восстановлена в репертуаре.",
    songTitle: "Название *", songDuration: "Длительность ММ:СС *",
    songLights: "Цвета / свет", songComment: "Комментарий",
    songAliases: "Альтернативные названия — по одному на строку",
    songScope: "Меняется запись репертуара, поэтому правка отразится во всех её вхождениях в рабочих сет-листах. Правки участника требуют одобрения администратора.",
    songSave: "Сохранить", songPending: "Запрос сохраняется ботом…",
    songArchived: "Песня перенесена в архив репертуара.",
    songArchiveConfirm: "Песня исчезнет из репертуара. Её можно будет восстановить из архива.",
    songArchiveDraft: "Песня останется в текущем черновике. Чтобы сохранить следующие правки черновика, уберите её из списка или восстановите из архива.",
    songQueued: "Запрос в очереди. Проверьте сохранение перед новым изменением песни.",
    close: "Закрыть",
    songCheck: "Проверить сохранение", songSaved: "Репертуар обновлён.",
    songProposed: "Изменения предложены администратору. До одобрения репертуар не изменится.",
    songReload: "Загрузить актуальную",
  },
  en: {
    heading: "Private draft", setlist: "Setlist", toCatalog: "Go to repertoire ↓", toSetlist: "Back to setlist ↑",
    loadShared: "Start from the shared setlist", saveShared: "Save as shared setlist",
    loadSharedConfirm: "Replace your private draft with the current shared setlist?",
    saveSharedConfirm: "Replace the shared setlist for this performance with this version?",
    clearSharedConfirm: "Clear the shared setlist for this performance?",
    sharedSaved: "Shared setlist saved.",
    sharedWorking: "Saving the shared setlist…", sharedCheck: "Check shared setlist save",
    sharedConflict: "The shared setlist changed. Your version is saved as a draft. Start from the current shared setlist to continue.",
    planned: "Planned duration",
    plannedHint: "45 means minutes, 1:15 means 1 hour and 15 minutes, - clears the duration",
    plannedSaved: "Planned duration saved.",
    plannedQueued: "Request queued. Press to check the result.",
    plannedConflict: "The performance changed. Review the current duration and retry.",
    unknown: "Not set",
    current: "Current draft",
    currentShared: "Shared setlist", personalDraft: "My draft",
    replacePersonal: "Replace your saved personal draft with this version?",
    approvedProposal: "The shared setlist is already approved. Submit your version anyway?",
    confirmProposal: "Yes, submit",
    search: "Find a song",
    private:
      "Draft saved automatically. Only the publish button sends it to the group.",
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
    older: "History", editorOptions: "Options", emptyHistoryHint: "(use a past performance)",
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
    add: "Add",
    pauseTitle: "Pause duration",
    editPause: "Edit pause",
    pauseText: "Pause text — may be empty",
    pauseChanged: "This pause changed. Close the dialog and reopen it.",
    pauseHint: "Numbers without units mean minutes",
    average: "Average pause",
    seconds: "s",
    averageTitle: "Average pause between songs",
    averageHint: "In seconds. 0 means no pause. Individual pauses are kept.",
    averageGroupHint: "In seconds. Also applies to new group setlists. Individual pauses are kept.",
    averagePrivateHint: "In seconds. Only your draft changes. Administrators set the default for new performances.",
    apply: "Apply",
    reload: "Reopen the editor to refresh the catalog.",
    editSong: "Edit song", addSong: "＋ Add song",
    songRestored: "Song restored to the repertoire.",
    songTitle: "Title *", songDuration: "Duration MM:SS *",
    songLights: "Colors / lighting", songComment: "Comment",
    songAliases: "Alternative titles — one per line",
    songScope: "This updates the repertoire entry and all its occurrences in working setlists. Member edits require administrator approval.",
    songSave: "Save", songPending: "The bot is saving your request…",
    songArchived: "Song moved to the repertoire archive.",
    songArchiveConfirm: "The song will be removed from the repertoire. You can restore it from the archive.",
    songArchiveDraft: "The song will stay in this draft. To save further draft edits, remove it from the draft or restore it from the archive.",
    songQueued: "Request queued. Check its result before editing another song.",
    close: "Close",
    songCheck: "Check save", songSaved: "Repertoire updated.",
    songProposed: "Changes proposed to an administrator. The repertoire stays unchanged until approval.",
    songReload: "Load current song",
  },
});
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
const workspaceBand = workspaceParams.get("band");
let sharedBase = null, sharedAttempt = null, showingShared = false;
const workspaceDraft = session && workspaceBand && workspaceParams.get("view") === "draft";
const parentPerformance = workspaceParams.get("performance");
function draftParentUrl() {
  const past = workspaceParams.get("past"), page = workspaceParams.get("page");
  if (!parentPerformance || workspaceParams.get("draft_from") === "list")
    return bandUrl(workspaceBand, past, page);
  const url = performanceUrl(workspaceBand, parentPerformance, past, page);
  if (workspaceParams.get("from") !== "managed") return url;
  const managed = encodeURIComponent(workspaceParams.get("managed_page") || "0");
  const origin = workspaceParams.get("schedule_from") === "profile" ? "&schedule_from=profile" : "";
  return `${url}&from=managed&managed_page=${managed}${origin}`;
}
function status(text, error = false) {
  $("status").textContent = text;
  $("status").classList.toggle("error", error);
}
function dirty() {
  return saver?.dirty() ?? false;
}
const systemTheme = matchMedia("(prefers-color-scheme: dark)");
function theme() {
  // RU: Данные темы приходят из Telegram; список песен при смене темы не пересоздаём.
  // EN: Apply native theme colors without resetting the in-progress draft.
  const dark =
    tg?.colorScheme === "dark" ||
    (!tg?.colorScheme && systemTheme.matches);
  const root = document.documentElement;
  const scheme = dark ? "dark" : "light";
  root.dataset.theme = scheme;
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
      root.style.setProperty(target, color);
    else root.style.removeProperty(target);
  }
  root.style.removeProperty("color-scheme");
  root.style.removeProperty("background-color");
  if (!tg) return;
  const background = getComputedStyle(root).getPropertyValue("--bg").trim();
  if (!/^#[0-9a-f]{6}$/i.test(background)) return;
  try { sessionStorage.setItem("setlistbot.start-theme", JSON.stringify({ scheme, background })); }
  catch { /* Startup can still read Telegram's launch colors. */ }
  if (tg.isVersionAtLeast?.("6.1")) tg.setBackgroundColor?.(background);
}
theme();
tg?.onEvent?.("themeChanged", theme);
systemTheme.addEventListener("change", theme);
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
        ...(tg ? { Authorization: `tma ${tg.initData}` } : {}),
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
  if (operation === "export_download" && response.ok) {
    const disposition = response.headers.get("Content-Disposition") ?? "";
    return { blob: await response.blob(), name: /filename="([^"\r\n]+)"/.exec(disposition)?.[1] ?? "setlist.pdf" };
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
function change(next, gap = defaultPause, updateGroupDefault = false) {
  if (busy) return;
  if (next.length > 200) {
    status(t("limit"), true);
    return;
  }
  if (!mayReplacePersonalDraft()) return;
  showingShared = false;
  remember();
  current = next;
  defaultPause = gap;
  saver?.setDraft(current, defaultPause, updateGroupDefault);
  render();
  status(t("dirty"));
}
function mayReplacePersonalDraft() {
  if (showingShared && state.entries.length
    && JSON.stringify([wire(state.entries), state.default_pause_seconds]) !== JSON.stringify([wire(current), defaultPause])
    && !confirm(t("replacePersonal"))) return false;
  return true;
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
let indexedCatalog;
let songsById;
function catalogIndex() {
  if (indexedCatalog !== state.catalog) {
    indexedCatalog = state.catalog;
    songsById = new Map(indexedCatalog.map(song => [song.id, song]));
  }
  return songsById;
}

function render() {
  if (!state) return;
  $("event").textContent = [state.group, state.date, state.title]
    .filter(Boolean)
    .join(" · ");
  $("catalog").inert = busy;
  $("setlist").inert = busy;
  $("older").disabled = busy;
  $("older").hidden = !preview && !current.length;
  $("empty-history").hidden = !!preview || !!current.length;
  $("empty-history").disabled = busy;
  $("average-pause").disabled = busy || !!preview;
  $("average-pause").textContent = t("average");
  $("average-pause").setAttribute("aria-label", `${t("average")}: ${preview?.default_pause_seconds ?? defaultPause}${t("seconds")}`);
  $("use-history").disabled = busy;
  const items = preview?.entries ?? current,
    numbers = numbered(items);
  const catalog = catalogIndex();
  const rows = document.createDocumentFragment();
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
      if (!preview && catalog.has(item.song_id))
        row.append(button("✎", `${t("editSong")}: ${item.title}`, () => openSong(item.song_id), "song-edit"));
    }
    row.append(node("span", clock(item.duration_seconds), "song-time"));
    if (
      item.kind === "song" &&
      !catalog.has(item.song_id)
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
    rows.append(row);
  });
  $("setlist").replaceChildren(rows);
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
    : t(showingShared ? "currentShared" : "current");
  $("history").hidden = !preview;
  $("newer").hidden = !preview;
  $("newer").disabled = busy;
  $("use-history").hidden = !preview;
  $("catalog").hidden = !!preview;
  $("jump-catalog").hidden = !!preview;
  $("undo").disabled = !undos.length || !!preview || busy;
  $("add-pause").disabled = !!preview || busy;
  $("add-pause-bottom").disabled = !!preview || busy;
  $("add-pause-bottom").hidden = !!preview;
  renderSaveStatus();
  $("publish").disabled =
    !!preview || !current.length || busy || !!saver?.blocked;
  $("load-shared").hidden = !state.can_set_group_default || !!preview || !current.length;
  $("load-shared").disabled = busy || !!sharedAttempt || !!saver?.blocked;
  $("load-personal").hidden = !showingShared || !!preview;
  $("load-personal").disabled = busy;
  $("load-personal").textContent = t("personalDraft");
  $("save-shared").hidden = !!preview || ((!sharedBase || sharedBase.approved) && !sharedAttempt);
  $("shared-editor-actions").hidden = ["save-shared", "load-personal"].every(id => $(id).hidden);
  $("save-shared").disabled = busy || !!saver?.blocked;
  $("save-shared").textContent = t(sharedAttempt ? "sharedCheck" : "saveShared");
  dirty()
    ? tg?.enableClosingConfirmation?.()
    : tg?.disableClosingConfirmation?.();
  renderCatalog();
}
function songLabels() {
  for (const [id, word] of Object.entries({
    "song-title-label": "songTitle", "song-duration-label": "songDuration",
    "song-lights-label": "songLights", "song-comment-label": "songComment",
    "song-aliases-label": "songAliases", "song-scope": "songScope",
    "song-cancel": catalogAttempt ? "close" : "cancel", "song-reload": "songReload",
  })) $(id).textContent = t(word);
}
let visibleCatalog;
function renderCatalog() {
  if (!state || preview) return;
  const query = $("search").value.toLocaleLowerCase().replaceAll("ё", "е");
  if (visibleCatalog?.source === state.catalog && visibleCatalog.query === query && visibleCatalog.page === catalogPage) return;
  const songs = !query ? state.catalog : state.catalog.filter((song) =>
    song.title.toLocaleLowerCase().replaceAll("ё", "е").includes(query),
  );
  const pages = Math.max(1, Math.ceil(songs.length / 40));
  catalogPage = Math.min(catalogPage, pages - 1);
  visibleCatalog = { source: state.catalog, query, page: catalogPage };
  const rows = document.createDocumentFragment();
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
    rows.append(row);
  });
  $("catalog-list").replaceChildren(rows);
  $("catalog-empty").textContent = songs.length ? "" : workspaceText(lang, query ? "noMatches" : "noSongs");
  $("catalog-pages").hidden = pages === 1;
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
async function openHistory() {
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
}
for (const id of ["older", "empty-history"]) $(id).addEventListener("click", openHistory);
$("newer").addEventListener("click", () => {
  preview = null;
  render();
});
$("use-history").addEventListener("click", () => {
  if (!preview) return;
  if (dirty() && !confirm(t("replace"))) return;
  const catalog = catalogIndex();
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
    status(dirty() ? t("dirty") : current.length ? t("ready") : "");
  }
});
for (const [trigger, heading] of [["jump-catalog", "catalog-heading"], ["jump-setlist", "setlist-heading"]]) {
  $(trigger).addEventListener("click", () => {
    $(heading).focus({ preventScroll: true });
    $(heading).scrollIntoView({ block: "start" });
  });
}
$("search").addEventListener("input", () => {
  catalogPage = 0;
  renderCatalog();
});
let pointerInProgress = false,
  searchPointer = false,
  searchClick = false;
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
});
$("search").addEventListener("blur", settleSearchLayout);
$("search").addEventListener("keydown", (event) => {
  if (!event.isComposing && event.key === "Escape") {
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
    if (fieldAlignmentPending) scheduleFieldAlignment();
  }, true);
// RU/EN: Also handle keyboard activation and synthetic clicks without pointer events.
document.addEventListener("click", event => {
  if (searchClick) {
    searchClick = false;
    return;
  }
  leaveSearch(event);
}, true);
const mobileInput = matchMedia("(max-width: 640px), (any-pointer: coarse)");
const viewport = window.visualViewport;
const textInputTypes = new Set(["text", "search", "number", "tel", "email", "url", "password"]);

function editableText(field) {
  return (field instanceof HTMLInputElement && textInputTypes.has(field.type)
    || field instanceof HTMLTextAreaElement) && !field.readOnly && !field.disabled;
}

function hintDone(event) {
  if (editableText(event.target)) event.target.enterKeyHint = "done";
}

document.addEventListener("pointerdown", hintDone, true);
document.addEventListener("focusin", hintDone);
document.addEventListener("keydown", event => {
  const field = event.target;
  if (event.key !== "Enter" || event.isComposing || event.keyCode === 229) return;
  if (!editableText(field) && !(field instanceof HTMLInputElement && field.type === "date")) return;
  if (event.shiftKey && field instanceof HTMLTextAreaElement) return;
  event.preventDefault();
  field.blur();
}, true);
document.addEventListener("click", event => {
  if (!(event.target instanceof Element)) return;
  for (const menu of document.querySelectorAll(".action-menu[open]")) {
    if (!menu.contains(event.target) || event.target.closest(".action-menu-list a, .action-menu-list button"))
      menu.open = false;
  }
});
document.addEventListener("keydown", event => {
  if (event.key !== "Escape") return;
  const menu = event.target.closest?.(".action-menu[open]");
  if (menu) {
    event.preventDefault();
    menu.open = false;
    menu.querySelector("summary").focus();
  }
});
let fieldFrame = 0, fieldAlignmentPending = false, fieldContainer = null, lastViewportTop = 0;

function alignFocusedField() {
  const root = document.documentElement;
  const layoutHeight = root.clientHeight;
  const zoomed = viewport && Math.abs(viewport.scale - 1) > .01;
  const visibleTop = zoomed ? 0 : viewport?.offsetTop ?? 0;
  const visibleHeight = zoomed ? layoutHeight : viewport?.height ?? layoutHeight;
  const covered = Math.max(0, layoutHeight - visibleTop - visibleHeight);
  lastViewportTop = viewport?.offsetTop ?? 0;
  root.style.setProperty("--keyboard-top", `${visibleTop}px`);
  root.style.setProperty("--keyboard-bottom", `${covered}px`);
  root.style.setProperty("--visible-height", `${visibleHeight}px`);
  root.style.setProperty("--layout-height", `${layoutHeight}px`);

  const field = document.activeElement;
  const editable = editableText(field);
  const mobile = mobileInput.matches || covered > 48;
  const dialog = editable ? field.closest("dialog[open]") : null;
  const container = editable && !zoomed && mobile ? dialog ?? document.body : null;
  if (fieldContainer !== container) {
    fieldContainer?.classList.remove("keyboard-focus");
    fieldContainer = container;
    fieldContainer?.classList.add("keyboard-focus");
  }
  if (!editable || zoomed || (!mobile && field !== $("search"))) return;

  const safeTop = parseFloat(getComputedStyle(document.body).paddingTop) || 0;
  let target = visibleTop + safeTop + 12;
  if (dialog) {
    const heading = dialog.querySelector(".sheet-heading");
    const stickyHeight = heading && getComputedStyle(heading).position === "sticky"
      ? heading.getBoundingClientRect().height : 0;
    target = Math.max(target, dialog.getBoundingClientRect().top + stickyHeight + 10);
  }
  const offset = field.getBoundingClientRect().top - target;
  if (Math.abs(offset) <= 1) return;
  const scroller = dialog ?? document.scrollingElement;
  scroller?.scrollTo({ top: scroller.scrollTop + offset, behavior: "instant" });
}

function scheduleFieldAlignment() {
  fieldAlignmentPending = true;
  if (fieldFrame || pointerInProgress) return;
  fieldFrame = requestAnimationFrame(() => {
    fieldFrame = 0;
    if (pointerInProgress) return;
    fieldAlignmentPending = false;
    alignFocusedField();
  });
}

alignFocusedField();
document.addEventListener("focusin", scheduleFieldAlignment);
document.addEventListener("focusout", scheduleFieldAlignment);
document.addEventListener("close", scheduleFieldAlignment, true);
window.addEventListener("resize", scheduleFieldAlignment);
viewport?.addEventListener("resize", scheduleFieldAlignment);
viewport?.addEventListener("scroll", () => {
  if (viewport.offsetTop !== lastViewportTop) scheduleFieldAlignment();
});
tg?.onEvent?.("viewportChanged", scheduleFieldAlignment);
$("page-prev").addEventListener("click", () => {
  catalogPage--;
  renderCatalog();
});
$("page-next").addEventListener("click", () => {
  catalogPage++;
  renderCatalog();
});
let editingSong = null, catalogAttempt = null, catalogRequest = null, songSubmitting = false;
let songWorkspace = null, catalogOperation = null, songMode = "edit";
const songApi = (...args) => (songWorkspace?.api ?? api)(...args);
const songActionLabel = () => t(songMode === "restore" ? "restoreSong" : "songSave");
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
async function openSong(id = null, workspace = null, mode = "edit") {
  if ((!state && !workspace) || busy || preview) return;
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
    songWorkspace = workspace;
    songMode = mode;
    if (workspace) lang = workspace.language;
    const source = id ? await songApi("song", { song_id: id }) : null;
    editingSong = mode === "copy" ? null : source;
    fillSong(mode === "copy" ? { ...source, title: "", aliases: [] } : source);
    lockSongFields(mode === "restore");
    songLabels();
    $("song-error").textContent = "";
    $("song-dialog-title").textContent = t(
      mode === "copy" ? "copySong" : mode === "restore" ? "restoreSong" : id ? "repertoireSong" : "addSong",
    );
    $("song-submit").textContent = songActionLabel();
    $("song-delete").textContent = workspace?.archiveLabel ?? t("songDelete");
    $("song-delete").hidden = !editingSong || !!editingSong.archived || !(workspace?.admin ?? state?.can_set_group_default);
    $("song-reload").hidden = true;
    $("song-dialog").showModal();
  } catch (error) { (workspace?.status ?? status)(error.message, true); }
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
    editingSong = await songApi("song", { song_id: editingSong.id });
    fillSong(editingSong);
    $("song-error").textContent = "";
    $("song-reload").hidden = true;
  } catch (error) { $("song-error").textContent = error.message; }
});
async function refreshRepertoire() {
  const updated = await api("state");
  state.catalog = updated.catalog;
  const catalog = catalogIndex();
  const refresh = items => items.map(item => {
    const song = item.kind === "song" && catalog.get(item.song_id);
    return song ? { ...item, title: song.title, duration_seconds: song.duration_seconds, version: song.version } : item;
  });
  // RU: Порядок/повторы/паузы неизменны. Отмена порядка не отменяет правку репертуара.
  // EN: Refresh all occurrences, including Undo, without replacing the user's draft.
  current = refresh(current);
  undos = undos.map(item => ({ ...item, entries: refresh(item.entries) }));
  if (showingShared) {
    state = updated;
    persistence();
  } else saver.setDraft(current, defaultPause);
  render();
}
$("song-delete").addEventListener("click", () => {
  if (!editingSong || !(songWorkspace?.admin ?? state?.can_set_group_default) || catalogAttempt || songSubmitting) return;
  const used = current.some(item => item.song_id === editingSong.id);
  const warning = used ? `\n\n${t("songArchiveDraft")}` : "";
  if (confirm(`${editingSong.title}\n\n${t("songArchiveConfirm")}${warning}`)) void submitSong("catalog_archive");
});
$("song-form").addEventListener("submit", event => {
  event.preventDefault();
  void submitSong(songMode === "restore" ? "catalog_restore" : "catalog_save");
});
async function submitSong(operation) {
  if (songSubmitting || (!saver && !songWorkspace)) return;
  const song = editingSong;
  songSubmitting = true;
  busy = true;
  $("song-submit").disabled = true;
  $("song-cancel").disabled = true;
  $("song-reload").disabled = true;
  $("song-delete").disabled = true;
  saver?.pause();
  render();
  try {
    if (!catalogAttempt) {
      await saver?.flush();
      catalogOperation = operation;
      catalogAttempt = {
        request_id: crypto.randomUUID(), song_id: song?.id ?? null,
        expected: song?.version ?? null,
      };
      if (operation === "catalog_save") {
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
      ? await songApi("catalog_status", { request: catalogRequest })
      : await songApi(catalogOperation, catalogAttempt, "POST");
    catalogRequest = result.request;
    for (let i = 0; result.status === "pending" && i < 30; i++) {
      await new Promise(resolve => setTimeout(resolve, 1000));
      result = await songApi("catalog_status", { request: catalogRequest });
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
    if (songWorkspace) await songWorkspace.refresh();
    else await refreshRepertoire();
    catalogAttempt = catalogRequest = null;
    $("song-dialog").close();
    (songWorkspace?.status ?? status)(t(
      result.archived ? "songArchived" : catalogOperation === "catalog_restore" ? "songRestored"
        : result.proposed ? "songProposed" : "songSaved",
    ));
  } catch (error) {
    // Explicit validation rejection means nothing was queued. Network failures stay retryable.
    if (!catalogRequest && error.data) catalogAttempt = null;
    $("song-error").textContent = error.message;
    if (!catalogAttempt) lockSongFields(songMode === "restore");
    $("song-reload").hidden = !editingSong || error.data?.error !== "conflict";
    $("song-submit").textContent = catalogAttempt ? t("songCheck") : songActionLabel();
  } finally {
    songSubmitting = busy = false;
    $("song-submit").disabled = false;
    $("song-cancel").disabled = false;
    $("song-cancel").textContent = t(catalogAttempt ? "close" : "cancel");
    $("song-reload").disabled = false;
    $("song-delete").disabled = !!catalogAttempt;
    saver?.resume();
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
  if (gap !== defaultPause) change(clone(current), gap, state.can_set_group_default);
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
    (payload) => api("save", workspaceDraft
      ? { ...payload, update_group_default: payload.update_group_default === true }
      : payload, "POST"),
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
  if (!publishAttempt && !mayReplacePersonalDraft()) return;
  busy = true;
  saver.pause();
  render();
  try {
    await saver.flush();
    publishAttempt ??= {
      version: state.version,
      entries: wire(current),
      default_pause_seconds: defaultPause,
      request_id: crypto.randomUUID(),
    };
    status(t("publishing"));
    let result = await api("publish", publishAttempt, "POST");
    while (result.status === "confirm_approved") {
      if (!await confirmProposal()) { publishAttempt = null; status(""); return; }
      publishAttempt.approved_version = result.approved_version;
      result = await api("publish", publishAttempt, "POST");
    }
    if (result.version !== undefined) {
      state.version = result.version;
      saver.version = result.version;
      state.entries = clone(current);
      state.default_pause_seconds = defaultPause;
      showingShared = false;
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
      const target = performanceUrl(state.group_id, state.concert_id, workspaceParams.get("past"), workspaceParams.get("page"));
      const managed = workspaceParams.get("from") === "managed" ? new URLSearchParams({
        from: "managed", managed_page: workspaceParams.get("managed_page") || "0",
        schedule_from: workspaceParams.get("schedule_from") || "",
      }) : "";
      location.assign(`${target}${managed ? `&${managed}` : ""}&published=1`);
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
function confirmProposal() {
  return new Promise(resolve => {
    const dialog = node("dialog", undefined, "shared-tools");
    const heading = node("h2", t("approvedProposal"));
    heading.id = "confirm-proposal-title";
    dialog.setAttribute("aria-labelledby", heading.id);
    const actions = node("div", undefined, "toolbar");
    actions.append(button(t("cancel"), t("cancel"), () => dialog.close()),
      button(t("confirmProposal"), t("confirmProposal"), () => dialog.close("submit"), "primary"));
    dialog.append(heading, actions);
    document.body.append(dialog);
    dialog.addEventListener("close", () => { resolve(dialog.returnValue === "submit"); dialog.remove(); }, { once: true });
    dialog.showModal();
  });
}
$("load-personal").addEventListener("click", () => {
  if (!showingShared || busy) return;
  showingShared = false;
  current = withKeys(state.entries);
  defaultPause = state.default_pause_seconds;
  persistence();
  render();
});
$("load-shared").addEventListener("click", async () => {
  if (busy || sharedAttempt || preview || !confirm(t("loadSharedConfirm"))) return;
  busy = true;
  render();
  try {
    const next = await api("draft_shared");
    sharedBase = next;
    busy = false;
    change(withKeys(next.entries), next.default_pause_seconds);
  } catch (error) {
    status(error.message, true);
  } finally {
    busy = false;
    render();
  }
});
$("save-shared").addEventListener("click", async () => {
  if (busy || preview || !saver || saver.blocked) return;
  if (!sharedAttempt && (!sharedBase || sharedBase.approved
    || !confirm(t(current.length ? "saveSharedConfirm" : "clearSharedConfirm")))) return;
  busy = true;
  saver.pause();
  render();
  try {
    if (!sharedAttempt) {
      await saver.flush();
      sharedAttempt = { body: {
        action: "edit", version: sharedBase.version, token: sharedBase.token,
        entries: wire(current), default_pause_seconds: defaultPause, request_id: crypto.randomUUID(),
      } };
    }
    status(t("sharedWorking"));
    const readReceipt = () => api("catalog_status", { request: sharedAttempt.receipt });
    let result = sharedAttempt.receipt ? await readReceipt()
      : await api("shared_action", sharedAttempt.body, "POST");
    sharedAttempt.receipt = result.request;
    for (let index = 0; result.status === "pending" && index < 10; index++) {
      await new Promise(resolve => setTimeout(resolve, 1000));
      result = await readReceipt();
    }
    if (result.status === "pending") {
      status(t("sharedCheck"));
      return;
    }
    sharedAttempt = null;
    if (result.status === "failed") {
      if (["approved", "conflict"].includes(result.error)) sharedBase = null;
      status(result.error === "conflict" ? t("sharedConflict") : result.message, true);
      return;
    }
    sharedBase = result.shared;
    status(t("sharedSaved"));
  } catch (error) {
    if (error.data?.latest) saveError(error);
    else {
      if (!sharedAttempt?.receipt && error.data && !["busy", "unavailable"].includes(error.data.error))
        sharedAttempt = null;
      status(error.message, true);
    }
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
  status(current.length ? t("ready") : "");
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
  if (workspaceDraft) {
    location.assign(draftParentUrl());
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
    "setlist-heading": "setlist",
    "jump-catalog": "toCatalog",
    "jump-setlist": "toSetlist",
    "add-song": "addSong",
    "search-label": "search",
    "private-note": "private",
    save: "save",
    publish: "publish",
    "back-bot": "back",
    "add-pause": "addPause",
    "add-pause-bottom": "addPauseBottom",
    older: "older",
    "editor-options": "editorOptions",
    "empty-history-title": "older",
    "empty-history-hint": "emptyHistoryHint",
    newer: "backDraft",
    "history-title": "older",
    "history-close": "close",
    "planned-title": "planned",
    "planned-hint": "plannedHint",
    "planned-close": "close",
    "planned-submit": "songSave",
    undo: "undo",
    "use-history": "use",
    "load-shared": "loadShared",
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
  if (workspaceDraft) $("back-bot").textContent = parentPerformance
    ? (lang === "ru" ? "К выступлению" : "Back to performance")
    : pageBackText(workspaceText(lang, "performances"));
  for (const id of ["back-bot", "newer"]) {
    const arrow = node("span", "←", "page-back-arrow");
    arrow.setAttribute("aria-hidden", "true");
    $(id).prepend(arrow);
  }
  $("undo").setAttribute("aria-label", t("undoLabel"));
  $("search").placeholder = t("search");
  document.documentElement.lang = lang;
}
function onTelegramBack(navigate) {
  tg?.BackButton?.show?.();
  tg?.BackButton?.onClick?.(() => {
    const dialogs = document.querySelectorAll("dialog[open]");
    const dialog = dialogs[dialogs.length - 1];
    if (!dialog) {
      const menu = document.querySelector(".action-menu[open]");
      if (menu) { menu.open = false; return; }
      return navigate();
    }
    if (dialog.requestClose) dialog.requestClose();
    else if (dialog.dispatchEvent(new Event("cancel", { cancelable: true }))) dialog.close();
  });
}

async function boot() {
  // Dispatch only. The API checks the signature before trusting the launch target.
  const launch = new URLSearchParams(tg?.initData ?? "").get("start_param");
  if (!session && (workspaceParams.has("workspace") || !launch?.startsWith("sl_"))) {
    await showWorkspace({ api, openSong, authenticated: Boolean(tg?.initData), onBack: onTelegramBack });
    return;
  }
  $("editor").hidden = false;
  if (workspaceDraft) {
    onTelegramBack(() => {
      if (saver) $("back-bot").click();
      else location.assign(draftParentUrl());
    });
  }
  try {
    state = await api(session ? "state" : "launch");
    session = state.session;
    lang = state.language === "en" ? "en" : "ru";
    labels();
    $("event").textContent = [state.group, state.date, state.title]
      .filter(Boolean)
      .join(" · ");
    showingShared = !!(workspaceDraft && state.shared);
    const initial = showingShared ? state.shared : state;
    current = withKeys(initial.entries);
    defaultPause = initial.default_pause_seconds;
    persistence();
    if (state.can_set_group_default) {
      try { sharedBase = await api("draft_shared"); }
      catch (error) { status(error.message, true); render(); return; }
    }
    render();
    status(current.length ? t("ready") : "");
  } catch (error) {
    if (!tg && error.data?.error === "miniapp_auth") {
      $("editor").hidden = true;
      await showWorkspace({ api, openSong, authenticated: false });
      return;
    }
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
if (workspaceDraft)
  window.addEventListener("beforeunload", (event) => {
    if (dirty()) event.preventDefault();
  });
boot();
