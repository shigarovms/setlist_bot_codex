import { asText, clock, move, numbered, total, wire } from "./model.js";
import { mountPdfExport, pdfTitle } from "./workspace-export.js";

const copy = {
  ru: {
    edit: "Редактировать общий сет-лист", save: "Сохранить общий список", close: "Закрыть",
    note: "Сохранённые изменения увидит весь коллектив.", discard: "Закрыть без сохранения общего списка?",
    clear: "Сохранить общий список пустым?", empty: "Список пуст.", total: "Итого",
    gap: "Средняя пауза, сек.", pause: "Пауза", seconds: "Длительность паузы, сек.", caption: "Подпись паузы",
    repertoire: "Найти песню", addPause: "Добавить паузу", unavailable: "Песня недоступна. Уберите её, чтобы сохранить список.",
    up: "Выше", down: "Ниже", remove: "Удалить", add: "Добавить", limit: "В списке может быть не больше 200 позиций.",
    invalid: "Проверьте длительности пауз и среднюю паузу.",
    variants: "Варианты", noVariants: "Сохранённых вариантов пока нет.", submitted: "Предложение", version: "Версия",
    history: "История", revisionActions: { created: "Создан", approved: "Утверждён", reopened: "Возвращён в работу", restored: "Восстановлен", proposal_applied: "Применён вариант" }, edited: "Изменён",
    applied: "Применено", stale: "Общий список изменился после публикации.", preview: "Показать вариант",
    apply: "Применить к общему списку", restore: "Восстановить вариант", replace: "Заменить общий порядок песен и пауз этим вариантом?",
    approve: "Утвердить", reopen: "Вернуть в работу", confirmReopen: "Снять утверждение и вернуть сет-лист в работу?",
    askApprove: "Запросить утверждение", askReopen: "Запросить возврат в работу",
    requested: "Запрос поставлен в очередь на отправку администратору в Telegram.", duplicate: "Такой запрос уже ожидает решения.",
    requests: "Входящие запросы", noRequests: "Нет запросов на рассмотрении.",
    reject: "Отклонить", accepted: "Запрос принят.", rejected: "Запрос отклонён.",
    done: "Общий сет-лист обновлён.", working: "Запрос обрабатывается…", check: "Проверить результат",
    newState: "Открыть актуальную версию", copyOrder: "Скопировать мой порядок", copied: "Порядок скопирован.",
    previous: "Назад", next: "Дальше",
  },
  en: {
    edit: "Edit shared setlist", save: "Save shared setlist", close: "Close",
    note: "Saved changes are visible to the whole band.", discard: "Close without saving changes to the shared setlist?",
    clear: "Save the shared setlist as empty?", empty: "The setlist is empty.", total: "Total",
    gap: "Average pause, sec.", pause: "Pause", seconds: "Pause duration, sec.", caption: "Pause label",
    repertoire: "Find a song", addPause: "Add pause", unavailable: "This song is unavailable. Remove it to save the setlist.",
    up: "Move up", down: "Move down", remove: "Remove", add: "Add", limit: "A setlist can contain up to 200 entries.",
    invalid: "Check the values for pauses and average pause.",
    variants: "Versions and proposals", noVariants: "There are no saved versions or proposals.", submitted: "Proposal", version: "Version",
    history: "History", revisionActions: { created: "Created", approved: "Approved", reopened: "Reopened", restored: "Restored", proposal_applied: "Proposal applied" }, edited: "Edited",
    applied: "Applied", stale: "The shared list has changed since publication.", preview: "View version",
    apply: "Apply to the shared setlist", restore: "Restore this version", replace: "Replace the current song and pause order with this version?",
    approve: "Approve", reopen: "Reopen for editing", confirmReopen: "Reopen the approved setlist for editing?",
    askApprove: "Request approval", askReopen: "Request reopening",
    requested: "The request is queued for delivery to an admin in Telegram.", duplicate: "This request is already awaiting a decision.",
    requests: "Incoming requests", noRequests: "There are no requests awaiting a decision.",
    reject: "Decline", accepted: "Request accepted.", rejected: "Request declined.",
    done: "Shared setlist updated.", working: "Processing your request…", check: "Check result",
    newState: "Open the latest version", copyOrder: "Copy my order", copied: "Order copied.",
    previous: "Previous", next: "Next",
  },
};

function el(tag, title, attrs = {}) {
  const node = document.createElement(tag);
  node.textContent = title;
  for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value);
  return node;
}

function button(title, action, attrs = {}) {
  const node = el("button", title, { type: "button", ...attrs });
  node.addEventListener("click", action);
  return node;
}

export function mountSharedSetlist({ root, data, language, read, request, refresh, status }) {
  const t = copy[language];
  const shared = data.setlist;
  const actions = el("div", "", { class: "toolbar" });
  const dialog = el("dialog", "", { "aria-labelledby": "shared-tools-title", class: "shared-tools" });
  const toolbar = el("div", "", { class: "toolbar" });
  const heading = el("h2", "", { id: "shared-tools-title" });
  const message = el("p", "", { role: "status" });
  const content = el("div", "");
  const check = button(t.check, () => process(), { hidden: "" });
  const dialogCheck = button(t.check, () => process(), { hidden: "" });
  let attempt = null, working = false, dirty = () => false;
  const close = button(t.close, closeDialog);
  dialog.append(heading, message, content, toolbar, dialogCheck, close);
  root.append(actions, check, dialog);

  if (data.can_edit_draft && !shared.approved) actions.append(button(t.edit, () => openEditor()));
  if (shared.approved) actions.append(button(pdfTitle(language), () => openExport(shared.version)));
  actions.append(button(t.variants, () => openVariants()));
  actions.append(button(t.history, () => openVariants(0, true)));
  if (data.admin) actions.append(button(t.requests, () => openRequests()));
  if (shared.approved || shared.entries.length) {
    const label = shared.approved ? (data.admin ? t.reopen : t.askReopen) : (data.admin ? t.approve : t.askApprove);
    actions.append(button(label, () => {
      if (shared.approved && data.admin && !confirm(t.confirmReopen)) return;
      void submit(shared.approved ? "reopen" : "approve", shared);
    }));
  }

  function closeDialog() {
    if (attempt || working || (dirty() && !confirm(t.discard))) return;
    dirty = () => false;
    dialog.close();
  }
  dialog.addEventListener("cancel", event => { event.preventDefault(); closeDialog(); });
  const warnBeforeLeaving = event => { if (dialog.open && dirty()) event.preventDefault(); };
  dialog.addEventListener("close", () => window.removeEventListener("beforeunload", warnBeforeLeaving));

  function open(title) {
    dirty = () => false;
    heading.textContent = title;
    message.textContent = "";
    content.replaceChildren();
    toolbar.replaceChildren();
    if (!dialog.open) {
      dialog.showModal();
      window.addEventListener("beforeunload", warnBeforeLeaving);
    }
  }

  async function openVariants(page = 0, history = false) {
    if (attempt) return;
    open(history ? t.history : t.variants);
    message.textContent = t.working;
    try {
      const result = await read(history ? "shared_history" : "shared_variants", { page });
      if (!dialog.open) return;
      message.textContent = result.variants.length ? "" : t.noVariants;
      const list = el("ul", "", { class: "workspace-list" });
      for (const row of result.variants) {
        const item = el("li", "");
        const title = row.kind === "submission" ? t.submitted : `${t.version} ${row.number}`;
        item.append(el("p", `${title} · ${row.author} · ${clock(row.seconds)}`),
          button(t.preview, () => openVariant(row, page, history)));
        if (history && row.created_at) {
          const time = new Date(row.created_at).toLocaleString(language);
          const label = t.revisionActions[row.action] ?? t.edited;
          item.append(el("p", `${label} · ${time}`));
        }
        if (row.applied || row.stale) item.append(el("p", row.applied ? t.applied : t.stale));
        list.append(item);
      }
      content.append(list);
      if (page > 0) toolbar.append(button(t.previous, () => openVariants(page - 1, history)));
      if (result.has_next) toolbar.append(button(t.next, () => openVariants(page + 1, history)));
    } catch (error) { message.textContent = error.message; }
  }

  async function openRequests() {
    if (attempt) return;
    open(t.requests);
    message.textContent = t.working;
    try {
      const result = await read("shared_requests");
      if (!dialog.open) return;
      message.textContent = result.requests.length ? "" : t.noRequests;
      const list = el("ul", "", { class: "workspace-list" });
      for (const row of result.requests) {
        const item = el("li", "");
        const acceptLabel = row.operation === "approve" ? t.approve : t.reopen;
        item.append(el("p", `${row.requester} · ${row.operation === "approve" ? t.askApprove : t.askReopen}`),
          button(acceptLabel, () => {
            if (row.operation !== "reopen" || confirm(t.confirmReopen))
              void submit("decide", shared, { request: row.id, accept: true });
          }), button(t.reject, () => submit("decide", shared, { request: row.id, accept: false })));
        list.append(item);
      }
      content.append(list);
    } catch (error) { message.textContent = error.message; }
  }

  async function openVariant(row, page, history) {
    open(row.kind === "submission" ? `${t.submitted} · ${row.author}` : `${t.version} ${row.number}`);
    message.textContent = t.working;
    try {
      const result = await read("shared_variant", { kind: row.kind, variant: row.id });
      if (!dialog.open) return;
      message.textContent = row.applied ? t.applied : row.stale ? t.stale : "";
      const list = el("ol", "", { class: "workspace-list shared-setlist" });
      const numbers = numbered(result.entries);
      for (const [index, entry] of result.entries.entries()) {
        const item = el("li", "");
        item.append(el("span", numbers[index]), el("span", entry.kind === "pause" ? entry.pause_label ?? t.pause : entry.title),
          el("span", clock(entry.duration_seconds), { class: "workspace-duration" }));
        list.append(item);
      }
      content.append(list);
      toolbar.append(button(t.previous, () => openVariants(page, history)));
      if (row.action === "approved" && result.entries.length)
        toolbar.append(button(pdfTitle(language), () => openExport(row.number, row.id)));
      if (data.can_edit_draft && !shared.approved && !row.applied && !row.stale && row.number !== shared.version) {
        toolbar.append(button(row.kind === "submission" ? t.apply : t.restore, () => {
          if (confirm(t.replace)) void submit(row.kind === "submission" ? "apply" : "restore", shared, { variant: row.id });
        }, { class: "primary" }));
      }
    } catch (error) { message.textContent = error.message; }
  }

  async function openExport(version, revision = null) {
    if (attempt) return;
    open(pdfTitle(language));
    message.textContent = t.working;
    try {
      const options = await read("export_options");
      if (!dialog.open) return;
      message.textContent = "";
      mountPdfExport({ root: content, dialog, message, options, language, version, revision, request });
    } catch (error) { message.textContent = error.message; }
  }

  async function openEditor() {
    if (attempt) return;
    open(t.edit);
    message.textContent = t.working;
    try {
      const state = await read("shared_editor");
      if (!dialog.open) return;
      message.textContent = "";
      paintEditor(state);
    } catch (error) { message.textContent = error.message; }
  }

  function paintEditor(state) {
    let entries = state.entries;
    const catalog = new Set(state.catalog.map(song => song.id));
    const initial = JSON.stringify([wire(entries), state.default_pause_seconds]);
    const duration = el("strong", "", { "aria-live": "polite" });
    const gap = el("input", "", { id: "shared-gap", type: "number", min: 0, max: 86400, value: state.default_pause_seconds, required: "" });
    const list = el("ol", "", { class: "workspace-list" });
    const search = el("input", "", { id: "shared-song-search", type: "search" });
    const matches = el("div", "", { class: "toolbar" });
    const more = el("div", "", { class: "workspace-nav" });
    let songPage = 0;
    dirty = () => JSON.stringify([wire(entries), gap.valueAsNumber]) !== initial;
    content.append(el("p", t.note), duration,
      el("label", t.gap, { for: gap.id }), gap,
      button(t.addPause, () => addEntry({ kind: "pause", duration_seconds: 60 })),
      el("label", t.repertoire, { for: search.id }), search, matches, more, list);
    toolbar.append(button(t.save, () => {
      if (!gap.checkValidity() || !entries.every(row => row.kind === "song" || Number.isInteger(row.duration_seconds)
          && row.duration_seconds > 0 && row.duration_seconds <= 86400)) {
        message.textContent = t.invalid;
        return;
      }
      if (!entries.length && !confirm(t.clear)) return;
      void submit("edit", state, { entries: wire(entries), default_pause_seconds: gap.valueAsNumber });
    }, { class: "primary" }));
    toolbar.append(button(t.copyOrder, async () => {
      try { await navigator.clipboard.writeText(asText(entries)); message.textContent = t.copied; }
      catch { message.textContent = asText(entries); }
    }));
    toolbar.append(button(t.newState, () => {
      if (!dirty() || confirm(t.discard)) void openEditor();
    }));

    function timer() {
      const valid = entries.every(row => Number.isInteger(row.duration_seconds) && row.duration_seconds > 0);
      duration.textContent = `${t.total} ${valid ? clock(total(entries, gap.valueAsNumber || 0)) : "—"}`;
    }
    function addEntry(entry) {
      if (entries.length >= 200) { message.textContent = t.limit; return; }
      entries = [...entries, entry];
      draw();
    }
    function songs() {
      const query = search.value.toLocaleLowerCase().replaceAll("ё", "е").trim();
      const rows = state.catalog.filter(song => song.title.toLocaleLowerCase().replaceAll("ё", "е").includes(query));
      matches.replaceChildren(...rows.slice(songPage * 12, songPage * 12 + 12).map(song =>
        button(`${t.add} · ${song.title}`, () => addEntry({ ...song, song_id: song.id, kind: "song", pause_before: null }))));
      more.replaceChildren();
      if (songPage) more.append(button(t.previous, () => { songPage--; songs(); }));
      if (rows.length > (songPage + 1) * 12) more.append(button(t.next, () => { songPage++; songs(); }));
    }
    function draw(focusIndex = null, moveDirection = null) {
      const numbers = numbered(entries);
      list.replaceChildren();
      if (!entries.length) list.append(el("li", t.empty));
      for (const [index, entry] of entries.entries()) {
        const item = el("li", "", { class: "shared-edit-row" });
        if (entry.kind === "pause") {
          const caption = el("input", "", { value: entry.pause_label ?? "", maxlength: 1000, placeholder: t.pause, "aria-label": t.caption });
          caption.addEventListener("input", () => { entry.pause_label = caption.value; });
          const seconds = el("input", "", { type: "number", value: entry.duration_seconds, min: 1, max: 86400, required: "", "aria-label": t.seconds });
          seconds.addEventListener("input", () => { entry.duration_seconds = seconds.valueAsNumber; timer(); });
          item.append(caption, seconds);
        } else {
          item.append(el("p", `${numbers[index]}. ${entry.title} · ${clock(entry.duration_seconds)}`));
          if (!catalog.has(entry.song_id)) item.append(el("p", t.unavailable));
        }
        const buttons = el("div", "", { class: "toolbar" });
        for (const [label, step] of [[t.up, -1], [t.down, 1]]) {
          const control = button(label, () => { entries = move(entries, index, index + step); draw(index + step, step); });
          control.disabled = index + step < 0 || index + step >= entries.length;
          buttons.append(control);
          if (focusIndex === index && moveDirection === step) {
            queueMicrotask(() => (control.disabled ? buttons.querySelector("button:not(:disabled)") : control)?.focus());
          }
        }
        buttons.append(button(t.remove, () => { entries = entries.filter((_, i) => i !== index); draw(); }));
        item.append(buttons);
        list.append(item);
      }
      timer();
    }
    gap.addEventListener("input", timer);
    search.addEventListener("input", () => { songPage = 0; songs(); });
    songs(); draw();
  }

  async function submit(action, base, values = {}) {
    if (attempt) return;
    attempt = { body: { action, version: base.version, token: base.token, ...values, request_id: crypto.randomUUID() } };
    await process();
  }

  async function process() {
    if (!attempt || working) return;
    working = true;
    actions.inert = content.inert = toolbar.inert = close.disabled = true;
    check.hidden = dialogCheck.hidden = true;
    const tell = text => { if (dialog.open) message.textContent = text; else status(text); };
    tell(t.working);
    try {
      let result = attempt.receipt ? await request("catalog_status", { request: attempt.receipt })
        : await request("shared_action", attempt.body, "POST");
      attempt.receipt = result.request;
      for (let i = 0; result.status === "pending" && i < 8; i++) {
        await new Promise(resolve => setTimeout(resolve, 1000));
        result = await request("catalog_status", { request: attempt.receipt });
      }
      if (result.status === "pending") return;
      attempt = null;
      if (result.status === "failed") { tell(result.message); return; }
      dirty = () => false;
      dialog.close();
      await refresh();
      status(result.requested ? (result.duplicate ? t.duplicate : t.requested)
        : result.decision === true ? t.accepted : result.decision === false ? t.rejected : t.done);
    } catch (error) {
      if (!attempt?.receipt && error.data && !["busy", "unavailable"].includes(error.data.error)) attempt = null;
      tell(error.message);
    } finally {
      working = false;
      actions.inert = content.inert = toolbar.inert = close.disabled = !!attempt;
      check.hidden = !attempt || dialog.open;
      dialogCheck.hidden = !attempt || !dialog.open;
    }
  }
}
