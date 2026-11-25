import { timingLabel } from "./workspace-timing.js";
import { withBotLabels } from "./bot-labels.js";
import { clock } from "./model.js";
import { pdfTitle, mountPdfExport } from "./workspace-export.js";
import { requestDialog } from "./workspace-request-dialog.js";

const words = withBotLabels("workspace-gigs", {
  ru: {
    schedule: "Предстоящие",
    invitation: "Приглашение на выступление", back: "К приглашению",
    empty: "В этом разделе пока нет выступлений.", pending: "Ожидает ответа",
    reconfirm: "Нужно подтвердить новую дату", conditional: "Участие под вопросом",
    accepted: "Подмена принята, ожидает утверждения", cancelled: "Отменено", declined: "Отклонено",
    replaced: "Передано другому исполнителю", inactive: "Приглашение закрыто",
    confirmedStatus: "Участие подтверждено",
    working: "Рабочий сет-лист", approved: "Утверждённый сет-лист",
    version: "Версия", noSongs: "Сет-лист пока пуст.", pause: "Пауза",
    close: "Закрыть", check: "Проверить результат",
    busy: "Запрос обрабатывается…", done: "Ответ сохранён.",
    awaitingReplacement: "Запрос на подмену отправлен.", replacementSources: "Где нужна подмена",
    requested: "Запрос сохранён.", withdrawn: "Запрос на подмену отменён.",
  },
  en: {
    schedule: "Upcoming",
    invitation: "Performance invitation", back: "Back to invitation",
    empty: "There are no performances here yet.", pending: "Awaiting your response",
    reconfirm: "Confirm the new date", conditional: "Tentative booking",
    accepted: "Substitute accepted, awaiting approval", cancelled: "Cancelled", declined: "Declined",
    replaced: "Assigned to another performer", inactive: "Invitation closed",
    confirmedStatus: "Booking confirmed",
    working: "Working setlist", approved: "Approved setlist",
    version: "Version", noSongs: "The setlist is empty.", pause: "Pause",
    close: "Close", check: "Check result",
    busy: "Processing your request…", done: "Response saved.",
    awaitingReplacement: "Substitution requested.", replacementSources: "Bookings that may need a substitute",
    requested: "Request saved.", withdrawn: "Substitution request cancelled.",
  },
});

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

export function showGigs({ root, data, language, dateText, invitationUrl, setlistUrl, timingUrl, historyUrl, request, requestFor, managedUrl, options, refresh, status }) {
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

  function replacement(source, target = null) {
    const existing = source.replacement;
    const label = t[existing ? "cancelReplacement" : "requestReplacement"];
    const scopedRequest = requestFor(source.id);
    const panel = requestDialog({
      title: label, labels: t, operation: "gig_replacement", request: scopedRequest, refresh, status,
    });
    const command = { action: existing ? "cancel" : "request", version: source.version };
    if (existing) Object.assign(command, { replacement: existing.id, expected: existing.version });
    else if (target) command.target = target;
    panel.body.append(el("p", [source.band || data.band, source.title].filter(Boolean).join(" · ")));
    panel.body.append(button(t.confirm, event => panel.send(event.currentTarget, command, scopedRequest,
      async result => {
        panel.dialog.close();
        await refresh();
        status(t[result.replacement_status === "cancelled" ? "withdrawn" : "requested"]);
      }), true));
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
    if (row.status === "confirmed") root.append(el("a", timingLabel(language), { href: timingUrl(row) }));
    const actions = el("div", "", { class: "toolbar" });
    for (const answer of row.answers)
      actions.append(button(t[answer === "conditional" ? "tentative" : answer], () => reply(row, answer), answer === "yes"));
    if (row.can_request_replacement)
      actions.append(button(t.requestReplacement, () => replacement(row)));
    if (row.replacement)
      actions.append(button(t.cancelReplacement, () => replacement(row)));
    root.append(actions);
    if (row.replacement) root.append(el("p", t.awaitingReplacement));
    if (row.replacement_sources.length) {
      root.append(el("h3", t.replacementSources));
      const choices = el("ul", "", { class: "workspace-list" });
      for (const source of row.replacement_sources) {
        const choice = el("li");
        choice.append(el("p", [source.band, source.title].filter(Boolean).join(" · ")));
        if (source.can_request || source.replacement) {
          const label = t[source.replacement ? "cancelReplacement" : "requestReplacement"];
          choice.append(button(label, () => replacement(source, row.id)));
        } else choice.append(el("p", t.awaitingReplacement));
        choices.append(choice);
      }
      root.append(choices);
    }
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

  if (data.managed) {
    if (!data.managed.length) root.append(el("p", t.empty));
    const list = el("ul", "", { class: "workspace-list" });
    for (const row of data.managed) {
      const item = el("li");
      item.append(el("a", [row.band, row.title].filter(Boolean).join(" · "), { href: managedUrl(row) }),
        el("p", [dateText(row.date), row.city].filter(Boolean).join(" · ")));
      list.append(item);
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
    if (invitationUrl && row.other_roles.length) {
      const roles = el("div", "", { class: "toolbar" });
      for (const role of row.other_roles) roles.append(el("a", role.role, { href: invitationUrl(role.id) }));
      item.append(roles);
    }
    list.append(item);
  }
  root.append(list);
}
