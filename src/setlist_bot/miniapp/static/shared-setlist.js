import { withBotLabels } from "./bot-labels.js";
import { clock, numbered } from "./model.js";
import { mountPdfExport, pdfTitle } from "./workspace-export.js";

const copy = withBotLabels("shared-setlist", {
  ru: {
    versions: "Варианты и история",
    close: "Закрыть", pause: "Пауза",
    noVariants: "Сохранённых вариантов пока нет.", submitted: "Предложение", version: "Версия",
    history: "История", revisionActions: { created: "Создан", approved: "Утверждён", reopened: "Возвращён в работу", restored: "Восстановлен", proposal_applied: "Применён вариант" }, edited: "Изменён",
    applied: "Применено", stale: "Общий список изменился после публикации.", preview: "Показать вариант",
    replace: "Заменить общий порядок песен и пауз этим вариантом?",
    confirmReopen: "Снять утверждение и вернуть сет-лист в работу?",
    askReopen: "Запросить возврат в работу",
    requested: "Запрос поставлен в очередь на отправку администратору в Telegram.", duplicate: "Такой запрос уже ожидает решения.",
    requests: "Входящие запросы", noRequests: "Нет запросов на рассмотрении.",
    setlistProposal: "Предложение сет-листа", declined: "Отклонено",
    proposalApplied: "Предложение применено.", proposalDeclined: "Предложение отклонено.",
    approvedProposal: "Чтобы применить вариант, сначала верните общий сет-лист в работу.",
    accepted: "Запрос принят.", rejected: "Запрос отклонён.",
    done: "Общий сет-лист обновлён.", working: "Запрос обрабатывается…", check: "Проверить результат",
    previous: "Назад", next: "Дальше",
  },
  en: {
    versions: "Variants and history",
    close: "Close", pause: "Pause",
    noVariants: "There are no saved versions or proposals.", submitted: "Proposal", version: "Version",
    history: "History", revisionActions: { created: "Created", approved: "Approved", reopened: "Reopened", restored: "Restored", proposal_applied: "Proposal applied" }, edited: "Edited",
    applied: "Applied", stale: "The shared list has changed since publication.", preview: "View version",
    replace: "Replace the current song and pause order with this version?",
    confirmReopen: "Reopen the approved setlist for editing?",
    askReopen: "Request reopening",
    requested: "The request is queued for delivery to an admin in Telegram.", duplicate: "This request is already awaiting a decision.",
    requests: "Incoming requests", noRequests: "There are no requests awaiting a decision.",
    setlistProposal: "Setlist proposal", declined: "Declined",
    proposalApplied: "Proposal applied.", proposalDeclined: "Proposal declined.",
    approvedProposal: "Reopen the shared setlist before applying this proposal.",
    accepted: "Request accepted.", rejected: "Request declined.",
    done: "Shared setlist updated.", working: "Processing your request…", check: "Check result",
    previous: "Previous", next: "Next",
  },
});

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

export function mountSharedSetlist({ root, data, language, editorButton, decision, read, request, refresh, status }) {
  const t = copy[language];
  const shared = data.setlist;
  const canDecide = shared.approved || shared.entries.length > 0;
  const actions = el("div", "", { class: canDecide ? "toolbar shared-actions has-decision" : "toolbar shared-actions" });
  const dialog = el("dialog", "", { "aria-labelledby": "shared-tools-title", class: "shared-tools" });
  const toolbar = el("div", "", { class: "toolbar" });
  const heading = el("h2", "", { id: "shared-tools-title" });
  const message = el("p", "", { role: "status" });
  const content = el("div", "");
  const returnNav = el("div", "", { class: "page-back", hidden: "" });
  const tabs = el("div", "", { class: "toolbar section-tabs", role: "group", "aria-label": t.versions, hidden: "" });
  const variantTab = button(t.variants, () => openVariants());
  const historyTab = button(t.history, () => openVariants(0, true));
  tabs.append(variantTab, historyTab);
  const check = button(t.check, () => process(), { hidden: "" });
  const dialogCheck = button(t.check, () => process(), { hidden: "" });
  let attempt = null, working = false;
  let view = 0;
  let requestsRead = 0;
  const close = button(t.close, closeDialog);
  dialog.append(returnNav, heading, tabs, message, content, toolbar, dialogCheck, close);
  root.append(actions, check, dialog);

  if (shared.approved) actions.append(button(t.downloadPdf, () => openExport(shared.version), { class: "primary pdf-export-action" }));
  if (editorButton) actions.append(editorButton);
  const requestCount = el("span", "", { class: "shared-incoming-count", hidden: "" });
  if (data.admin) {
    const incoming = button(t.requests, () => openRequests(), { class: "shared-incoming" });
    incoming.append(requestCount);
    actions.append(incoming);
  }
  actions.append(button(t.versions, () => openVariants(), { class: "shared-versions-action" }));
  if (canDecide) {
    const label = shared.approved ? (data.admin ? t.reopen : t.askReopen) : (data.admin ? t.approve : t.askApprove);
    actions.append(button(label, () => {
      if (shared.approved && data.admin && !confirm(t.confirmReopen)) return;
      void submit(shared.approved ? "reopen" : "approve", shared);
    }));
  }

  function closeDialog() {
    dialog.close();
    if (attempt) {
      status(message.textContent || t.working);
      check.hidden = working;
    }
  }
  dialog.addEventListener("cancel", event => { event.preventDefault(); closeDialog(); });

  function open(title) {
    heading.textContent = title;
    message.textContent = "";
    content.replaceChildren();
    toolbar.replaceChildren();
    returnNav.replaceChildren();
    returnNav.hidden = tabs.hidden = true;
    if (!dialog.open) dialog.showModal();
    const serial = ++view;
    return () => dialog.open && view === serial;
  }

  async function openVariants(page = 0, history = false) {
    if (attempt) return;
    const current = open(t.versions);
    tabs.hidden = false;
    variantTab.setAttribute("aria-pressed", String(!history));
    historyTab.setAttribute("aria-pressed", String(history));
    message.textContent = t.working;
    try {
      const result = await read(history ? "shared_history" : "shared_variants", { page });
      if (!current()) return;
      message.textContent = result.variants.length ? "" : t.noVariants;
      const list = el("ul", "", { class: "workspace-list" });
      for (const row of result.variants) {
        const item = el("li", "");
        const title = row.kind === "submission" ? t.submitted : `${t.version} ${row.number}`;
        item.append(el("p", `${title} · ${row.author} · ${clock(row.seconds)}`),
          button(t.preview, () => openVariant(row, page, history)));
        if (row.created_at && (history || row.kind === "submission")) {
          const time = new Date(row.created_at).toLocaleString(language);
          const label = t.revisionActions[row.action] ?? t.edited;
          item.append(el("p", history ? `${label} · ${time}` : time));
        }
        if (row.applied || row.declined || row.stale)
          item.append(el("p", row.applied ? t.applied : row.declined ? t.declined : t.stale));
        list.append(item);
      }
      content.append(list);
      if (page > 0) toolbar.append(button(t.previous, () => openVariants(page - 1, history)));
      if (result.has_next) toolbar.append(button(t.next, () => openVariants(page + 1, history)));
    } catch (error) { if (current()) message.textContent = error.message; }
  }

  async function fetchRequests() {
    const serial = ++requestsRead;
    const result = await read("shared_requests");
    if (serial === requestsRead && actions.isConnected) {
      const count = result.requests.length + result.proposals.length;
      requestCount.textContent = count ? ` ${count}` : "";
      requestCount.hidden = !count;
    }
    return result;
  }

  if (data.admin && decision) void openRequests(decision);
  else if (data.admin) void fetchRequests().catch(() => {});

  async function openRequests(focus = null) {
    if (attempt) return;
    const current = open(t.requests);
    message.textContent = t.working;
    try {
      const result = await fetchRequests();
      if (!current()) return;
      message.textContent = result.requests.length || result.proposals.length ? "" : t.noRequests;
      const list = el("ul", "", { class: "workspace-list" });
      for (const row of result.requests) {
        const item = el("li", "", { id: `shared-request-${row.id}` });
        const acceptLabel = row.operation === "approve" ? t.approve : t.reopen;
        const actions = el("div", "", { class: "toolbar" });
        actions.append(button(acceptLabel, () => {
          if (row.operation !== "reopen" || confirm(t.confirmReopen))
            void submit("decide", shared, { request: row.id, accept: true });
        }), button(t.reject, () => submit("decide", shared, { request: row.id, accept: false })));
        item.append(el("p", `${row.requester} · ${row.operation === "approve" ? t.askApprove : t.askReopen}`), actions);
        list.append(item);
      }
      for (const row of result.proposals) {
        const item = el("li", "", { id: `shared-request-${row.id}` });
        item.append(el("p", `${t.setlistProposal} · ${row.author} · ${clock(row.seconds)}`),
          button(t.preview, () => openVariant({ ...row, kind: "submission" }, 0, false, true)));
        list.append(item);
      }
      content.append(list);
      if (focus) document.getElementById(`shared-request-${focus}`)?.scrollIntoView({ block: "center" });
    } catch (error) { if (current()) message.textContent = error.message; }
  }

  async function openVariant(row, page, history, inbox = false) {
    const current = open(row.kind === "submission" ? `${t[inbox ? "setlistProposal" : "submitted"]} · ${row.author}` : `${t.version} ${row.number}`);
    const back = button("", () => inbox ? openRequests() : openVariants(page, history));
    back.append(el("span", "←", { class: "page-back-arrow", "aria-hidden": "true" }), t.previous);
    returnNav.append(back);
    returnNav.hidden = false;
    message.textContent = t.working;
    try {
      const result = await read("shared_variant", { kind: row.kind, variant: row.id });
      if (!current()) return;
      message.textContent = row.applied ? t.applied : row.declined ? t.declined
        : row.stale ? t.stale : inbox && shared.approved ? t.approvedProposal : "";
      const list = el("ol", "", { class: "workspace-list shared-setlist" });
      const numbers = numbered(result.entries);
      for (const [index, entry] of result.entries.entries()) {
        const item = el("li", "");
        item.append(el("span", numbers[index]), el("span", entry.kind === "pause" ? entry.pause_label ?? t.pause : entry.title),
          el("span", clock(entry.duration_seconds), { class: "workspace-duration" }));
        list.append(item);
      }
      content.append(list);
      if (row.action === "approved" && result.entries.length)
        toolbar.append(button(pdfTitle(language), () => openExport(row.number, row.id)));
      const mayApply = row.kind === "submission" ? data.admin : data.can_edit_draft;
      if (mayApply && !shared.approved && !row.applied && !row.declined && !row.stale && row.number !== shared.version) {
        toolbar.append(button(row.kind === "submission" ? t.apply : t.restore, () => {
          if (confirm(t.replace)) void submit(row.kind === "submission" ? "apply" : "restore", shared, { variant: row.id });
        }, { class: "primary" }));
      }
      if (inbox) toolbar.append(button(t.reject, () => submit("decline_proposal", shared, { variant: row.id })));
    } catch (error) { if (current()) message.textContent = error.message; }
  }

  async function openExport(version, revision = null) {
    if (attempt) return;
    const current = open(pdfTitle(language));
    message.textContent = t.working;
    try {
      const options = await read("export_options");
      if (!current()) return;
      message.textContent = "";
      mountPdfExport({ root: content, dialog, message, options, language, version, revision, request });
    } catch (error) { if (current()) message.textContent = error.message; }
  }

  async function submit(action, base, values = {}) {
    if (attempt) return;
    attempt = { body: { action, version: base.version, token: base.token, ...values, request_id: crypto.randomUUID() } };
    await process();
  }

  async function process() {
    if (!attempt || working) return;
    working = true;
    actions.inert = content.inert = toolbar.inert = true;
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
      dialog.close();
      await refresh();
      status(result.proposal_decision === "applied" ? t.proposalApplied
        : result.proposal_decision === "declined" ? t.proposalDeclined
        : result.requested ? (result.duplicate ? t.duplicate : t.requested)
        : result.decision === true ? t.accepted : result.decision === false ? t.rejected : t.done);
    } catch (error) {
      if (!attempt?.receipt && error.data && !["busy", "unavailable"].includes(error.data.error)) attempt = null;
      tell(error.message);
    } finally {
      working = false;
      actions.inert = content.inert = toolbar.inert = !!attempt;
      check.hidden = !attempt || dialog.open;
      dialogCheck.hidden = !attempt || !dialog.open;
    }
  }
}
