import { clock } from "./model.js";
import { pdfTitle, mountPdfExport } from "./workspace-export.js";
import { requestDialog } from "./workspace-request-dialog.js";

const words = {
  ru: {
    title: "Мои выступления", invitations: "Приглашения на выступления", confirmed: "Подтверждённые",
    history: "История", invitation: "Приглашение на выступление", back: "К приглашению",
    empty: "В этом разделе пока нет выступлений.", pending: "Ожидает ответа",
    reconfirm: "Нужно подтвердить новую дату", conditional: "Участие под вопросом",
    accepted: "Подмена принята, ожидает утверждения", cancelled: "Отменено", declined: "Отклонено",
    replaced: "Передано другому исполнителю", inactive: "Приглашение закрыто",
    confirmedStatus: "Участие подтверждено", yes: "Подтвердить участие", no: "Отказаться",
    tentative: "Могу, если найду подмену", force: "Подтвердить несмотря на конфликт",
    setlist: "Открыть сет-лист", working: "Рабочий сет-лист", approved: "Утверждённый сет-лист",
    version: "Версия", noSongs: "Сет-лист пока пуст.", pause: "Пауза",
    close: "Закрыть", check: "Проверить результат", confirm: "Подтвердить",
    busy: "Запрос обрабатывается…", done: "Ответ сохранён.",
  },
  en: {
    title: "My performances", invitations: "Performance invitations", confirmed: "Confirmed",
    history: "History", invitation: "Performance invitation", back: "Back to invitation",
    empty: "There are no performances here yet.", pending: "Awaiting your response",
    reconfirm: "Confirm the new date", conditional: "Tentative booking",
    accepted: "Substitute accepted, awaiting approval", cancelled: "Cancelled", declined: "Declined",
    replaced: "Assigned to another performer", inactive: "Invitation closed",
    confirmedStatus: "Booking confirmed", yes: "Confirm booking", no: "Decline",
    tentative: "If I find a substitute", force: "Confirm despite the conflict",
    setlist: "Open setlist", working: "Working setlist", approved: "Approved setlist",
    version: "Version", noSongs: "The setlist is empty.", pause: "Pause",
    close: "Close", check: "Check result", confirm: "Confirm",
    busy: "Processing your request…", done: "Response saved.",
  },
};

export function gigLabel(language, key) { return words[language][key]; }

function el(tag, label = "", attrs = {}) {
  const node = document.createElement(tag);
  node.textContent = label;
  for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value);
  return node;
}

function button(label, click, primary = false) {
  const node = el("button", label, { type: "button", ...(primary ? { class: "primary" } : {}) });
  node.addEventListener("click", click);
  return node;
}

export function showGigs({ root, data, language, dateText, invitationUrl, setlistUrl, historyUrl, request, options, refresh, status }) {
  const t = words[language];
  const statusLabel = value => t[value === "confirmed" ? "confirmedStatus" : value] || t.inactive;

  function reply(row, answer) {
    const label = t[answer === "conditional" ? "tentative" : answer];
    const panel = requestDialog({ title: label, labels: t, operation: "gig_reply", request, refresh, status });
    const context = [data.band, dateText(row.date), row.role].join(" · ");
    panel.body.append(el("p", context));
    const payload = { answer, version: row.version };
    function complete(result) {
      panel.dialog.close();
      if (result.invitation_status === "declined") location.assign(historyUrl);
      else return refresh().then(() => status(t.done));
    }
    function failed(result) {
      if (result.error !== "staff_busy") return;
      panel.body.replaceChildren(el("p", context), button(t.force, event => panel.send(
        event.currentTarget, { ...payload, busy_receipt: result.request }, request, complete,
      ), true));
    }
    panel.body.append(button(t.confirm, event => panel.send(event.currentTarget, payload, request, complete, failed), true));
  }

  async function pdf() {
    const dialog = el("dialog", "", { class: "shared-tools", "aria-label": pdfTitle(language) });
    const body = el("div");
    const message = el("p", t.busy, { role: "status" });
    dialog.append(el("h2", pdfTitle(language)), body, message, button(t.close, () => dialog.close()));
    document.body.append(dialog);
    dialog.addEventListener("close", () => dialog.remove(), { once: true });
    dialog.showModal();
    try {
      const config = await options();
      if (!dialog.open) return;
      message.textContent = "";
      mountPdfExport({
        root: body, dialog, message, options: config, language, version: data.version,
        revision: null, request,
      });
    } catch (error) {
      message.textContent = error.message;
    }
  }

  if (data.invitation) {
    const row = data.invitation;
    root.append(el("h2", row.title || t.invitation),
      el("p", [dateText(row.date), row.city, row.type].filter(Boolean).join(" · ")),
      el("p", [row.role, statusLabel(row.status)].join(" · ")));
    if (row.info) root.append(el("p", row.info, { class: "performance-info" }));
    if (row.setlist) root.append(el("a", t.setlist, { href: setlistUrl }));
    const actions = el("div", "", { class: "toolbar" });
    for (const answer of row.answers)
      actions.append(button(t[answer === "conditional" ? "tentative" : answer], () => reply(row, answer), answer === "yes"));
    root.append(actions);
    return;
  }

  if (data.entries) {
    root.append(el("h2", data.title || dateText(data.date)), el("p", [
      t[data.approved ? "approved" : "working"], `${t.version} ${data.version}`, clock(data.seconds),
    ].join(" · ")));
    if (data.approved && data.entries.length) root.append(button(pdfTitle(language), pdf, true));
    if (!data.entries.length) root.append(el("p", t.noSongs));
    const list = el("ol", "", { class: "workspace-list shared-setlist" });
    let track = 0;
    for (const item of data.entries) {
      const row = el("li", "", { class: item.pause ? "shared-pause" : "" });
      row.append(el("span", item.pause ? "" : String(++track)),
        el("span", item.title ?? t.pause), el("span", clock(item.seconds)));
      list.append(row);
    }
    root.append(list);
    return;
  }

  if (!data.invitations.length) root.append(el("p", t.empty));
  const list = el("ul", "", { class: "workspace-list" });
  for (const row of data.invitations) {
    const item = el("li");
    const heading = [row.band, row.title].filter(Boolean).join(" · ");
    item.append(invitationUrl ? el("a", heading, { href: invitationUrl(row.id, row.status) }) : el("strong", heading),
      el("p", [dateText(row.date), row.city, row.role, statusLabel(row.status)].filter(Boolean).join(" · ")));
    list.append(item);
  }
  root.append(list);
}
