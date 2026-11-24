import { fillButtonLabel, withBotLabels } from "./bot-labels.js";
import { requestDialog } from "./workspace-request-dialog.js";
import { mountLineupForm, performerCaption } from "./workspace-staffing.js";

const words = withBotLabels("workspace-roster", {
  ru: {
    back: "К выступлению", empty: "Добавьте роли для этого выступления.",
    noSlots: "В составе пока нет ролей.",
    applyWarning: "Применение другого состава отменит действующие приглашения и подтверждения.",
    candidate: "Намечен",
    vacancy: "Свободная роль", optional: "Роль отключена",
    slotWarning: "Действующие приглашения на эту роль будут отменены.",
    confirmed: "Подтверждено", pending: "Ждём ответа", reconfirm: "Ждём подтверждения новой даты",
    conditional: "Участие под вопросом", accepted: "Подмена согласована исполнителем",
    busyDay: "Есть конфликт", tentative: "Возможен конфликт", unknown: "Конфликтов не найдено",
    force: "Подтверждаю действие при конфликте занятости",
    pick: "Проверить", myself: "Назначить себя",
    outside: "Найти по @username", none: "Подходящих знакомых исполнителей пока нет.",
    waitingReplacement: "Ищем подмену", substituted: "Подмена утверждена",
    candidateInvited: "Кандидат на подмену", sendReplacement: "Пригласить на подмену",
    assignReplacement: "Встать на подмену", cancelReplacementHint: "Приглашения кандидатам будут отозваны.",
    close: "Закрыть", check: "Проверить результат",
    busy: "Запрос обрабатывается…", done: "Изменения сохранены.",
    sent: "Приглашения поставлены в очередь.", assigned: "Ваше участие подтверждено.",
  },
  en: {
    back: "Back to performance", empty: "Add roles for this performance.",
    noSlots: "The lineup has no roles yet.",
    applyWarning: "Applying another lineup will cancel current invitations and confirmed bookings.",
    candidate: "Selected",
    vacancy: "Open role", optional: "Role disabled",
    slotWarning: "Active invitations for this role will be cancelled.",
    confirmed: "Confirmed", pending: "Awaiting response", reconfirm: "Awaiting confirmation of the new date",
    conditional: "Tentative booking", accepted: "Substitute accepted",
    busyDay: "Conflicting booking", tentative: "Possible conflict", unknown: "No known conflict",
    force: "Continue despite the availability conflict",
    pick: "Check", myself: "Assign myself",
    outside: "Find by @username", none: "No matching performers in your directory yet.",
    waitingReplacement: "Looking for a substitute", substituted: "Substitution approved",
    candidateInvited: "Substitute candidate", sendReplacement: "Invite substitute",
    assignReplacement: "Take over the role", cancelReplacementHint: "Invitations to candidates will be withdrawn.",
    close: "Close", check: "Check result",
    busy: "Processing your request…", done: "Changes saved.",
    sent: "Invitations queued.", assigned: "Your booking is confirmed.",
  },
});

export function rosterLabel(language, key) { return words[language][key]; }

function el(tag, value = "", attrs = {}) {
  const node = document.createElement(tag);
  node.textContent = value;
  for (const [key, attr] of Object.entries(attrs)) node.setAttribute(key, attr);
  return node;
}

function button(label, handler, primary = false) {
  const node = el("button", label, { type: "button", ...(primary ? { class: "primary" } : {}) });
  node.addEventListener("click", handler);
  return node;
}

export function showRoster({ root, data, language, dateText, request, preview, recipient, refresh, status }) {
  const t = words[language];
  const modal = title => requestDialog({ title, labels: t, operation: "roster_action", request, refresh, status });
  const target = row => ({ target: row.id, expected: row.version });
  const payload = (action, extra = {}) => ({ action, version: data.version, ...extra });
  const availability = value => t[value === "busy" ? "busyDay" : value];

  function confirm(key, command, context, caution) {
    const panel = modal(t[key]);
    panel.body.append(el("p", context));
    if (caution) panel.body.append(el("p", caution));
    panel.body.append(button(t.confirm, event => panel.send(event.currentTarget, command), true));
  }

  function edit(action) {
    const add = action === "add";
    const panel = modal(t[add ? "add" : "prepare"]);
    const initial = {
      name: data.name || t.title,
      rows: add ? [] : data.slots.map(row => ({ role: row.role, ...row.preferred })),
    };
    mountLineupForm({
      panel, language, initial, preview, showName: false, single: add, allowEmpty: !add,
      onSave: (event, plan) => panel.send(event.currentTarget, payload(action, { rows: plan.rows })),
    });
  }

  function applyTemplate() {
    const panel = modal(t.apply);
    const form = el("form", "", { class: "import-fields" });
    const select = el("select", "", { id: "roster-template", required: "" });
    for (const row of data.templates) select.append(el("option", row.name, { value: row.id }));
    const submit = el("button", t.applyConfirm, { type: "submit", class: "primary" });
    form.append(el("label", t.apply, { for: select.id }), select);
    if (data.slots.some(slot => slot.invitations.length))
      form.append(el("p", t.applyWarning));
    form.append(submit);
    form.addEventListener("submit", event => {
      event.preventDefault();
      const row = data.templates.find(row => row.id === select.value);
      panel.send(submit, payload("apply_template", target(row)));
    });
    panel.body.append(form);
  }

  function showReview(panel, record) {
    const list = el("ul", "", { class: "workspace-list" });
    for (const row of record.review) {
      const item = el("li", `${row.role} · `);
      item.append(performerCaption({ name: row.person, profile_url: row.profile_url }), ` · ${availability(row.status)}`);
      list.append(item);
    }
    const conflict = record.review.some(row => row.status === "busy");
    const recipients = record.recipients ?? record.review.filter(row => !row.self).length;
    const allSelf = recipients === 0;
    let label;
    if (record.replacement) label = allSelf ? t.assignReplacement : t.sendReplacement;
    else if (allSelf) label = conflict ? t.forceAssign : t.assign;
    else label = fillButtonLabel(conflict ? t.forceSend : t.send, { count: recipients });
    const send = button(label, event => panel.send(event.currentTarget, {
      action: "send", preview: record.request, force: !!force?.checked,
    }, request, async result => {
      panel.dialog.close();
      await refresh();
      status(t[result.replacement_confirmed ? "substituted" : result.recipients ? "sent" : "assigned"]);
    }), true);
    let force;
    panel.body.replaceChildren(list);
    if (conflict) {
      force = el("input", "", { type: "checkbox", id: "roster-conflict-confirm" });
      const label = el("label", "", { for: force.id });
      label.append(force, document.createTextNode(t.force));
      send.disabled = true;
      force.addEventListener("change", () => { send.disabled = !force.checked; });
      panel.body.append(label);
    }
    panel.body.append(send);
    send.focus();
  }

  function reviewDefaults() {
    const panel = modal(t.review);
    const retry = button(t.review, event => check(event.currentTarget));
    const check = trigger => panel.send(trigger, payload("review"), request, result => showReview(panel, result));
    panel.body.append(retry);
    check(retry);
  }

  function choose(slot, replacement = null) {
    const panel = modal(t.suggest);
    const scope = { ...target(slot), ...(replacement ? { replacement: replacement.id } : {}) };
    const heading = el("h3", slot.role);
    const loading = button(t.review, event => candidates(event.currentTarget));
    panel.body.append(heading, loading);
    function lookup(trigger, id, reference) {
      panel.send(trigger, payload("review", { ...scope, person: id, reference }), request,
        result => showReview(panel, result));
    }
    function candidates(trigger) {
      panel.send(trigger, payload("candidates", scope), request, result => {
        loading.remove();
        const list = el("ul", "", { class: "workspace-list" });
        const rows = result.candidates.filter(row => row.id !== result.self?.id);
        if (!rows.length) panel.body.append(el("p", t.none));
        for (const person of rows) {
          const row = el("li");
          row.append(performerCaption(person), el("p", availability(person.status)),
            button(t.pick, event => lookup(event.currentTarget, person.id)));
          list.append(row);
        }
        panel.body.append(list);
        if (result.self) panel.body.append(button(t.myself, event => lookup(event.currentTarget, result.self.id)));
        const form = el("form", "", { class: "import-fields" });
        const input = el("input", "", { id: "roster-lookup", required: "", maxlength: "64" });
        const submit = el("button", t.pick, { type: "submit" });
        form.append(el("label", t.outside, { for: input.id }), input, submit);
        form.addEventListener("submit", async event => {
          event.preventDefault();
          if (submit.disabled) return;
          submit.disabled = true;
          panel.message.textContent = t.busy;
          const reference = input.value;
          try {
            const person = await recipient(reference);
            if (panel.dialog.open) lookup(submit, person.id, reference);
          } catch (error) {
            panel.message.textContent = error.message;
            submit.disabled = false;
          }
        });
        panel.body.append(form);
      });
    }
    candidates(loading);
  }

  root.append(el("h2", t.title), el("p", [data.title, dateText(data.date), data.name].filter(Boolean).join(" · ")));
  if (data.admin) {
    const menu = el("div", "", { class: "toolbar" });
    if (data.can_prepare) menu.append(button(t.prepare, () => edit("prepare"), true));
    if (data.slots.length < 40) menu.append(button(t.add, () => edit("add")));
    if (data.templates.length) menu.append(button(t.apply, applyTemplate));
    if (data.upcoming && data.can_send_defaults) menu.append(button(t.review, reviewDefaults, true));
    root.append(menu);
  }
  if (!data.slots.length) root.append(el("p", t[data.admin ? "empty" : "noSlots"]));
  const list = el("ul", "", { class: "workspace-list" });
  for (const slot of data.slots) {
    const card = el("li");
    card.append(el("h3", slot.role));
    if (!slot.required) card.append(el("p", t.optional));
    else if (!slot.invitations.length) {
      const name = el("p", slot.preferred ? `${t.candidate} · ` : t.vacancy);
      if (slot.preferred) name.append(performerCaption(slot.preferred));
      card.append(name);
    }
    for (const row of slot.invitations) {
      const summary = [t[row.status]];
      if (row.needs_replacement) summary.push(t.waitingReplacement);
      if (row.substituted) summary.push(t.substituted);
      if (row.replacement) summary.push(t.candidateInvited);
      const line = el("p");
      line.append(performerCaption(row), ` · ${summary.join(" · ")}`);
      card.append(line);
      if (data.admin) {
        const controls = el("div", "", { class: "toolbar" });
        if (data.upcoming && row.status === "confirmed") {
          if (row.request) {
            controls.append(button(t.findReplacement, () => choose(slot, row.request)),
              button(t.cancelReplacement, () => confirm(
                "cancelReplacement", payload("cancel_replacement", target(row.request)), row.name,
                t.cancelReplacementHint,
              )));
          } else controls.append(button(t.requestReplacement, () => confirm(
            "requestReplacement", payload("request_replacement", target(row)), `${slot.role} · ${row.name}`,
          )));
        }
        if (row.replacement && row.status === "accepted") controls.append(button(t.approveReplacement, () => confirm(
          "approveReplacement", payload("approve_replacement", target(row)), `${slot.role} · ${row.name}`,
        ), true));
        if (data.upcoming && ["pending", "reconfirm", "conditional"].includes(row.status))
          controls.append(button(t.remind, () => confirm("remind", payload("remind", target(row)), row.name)));
        const action = row.status === "confirmed" ? "cancelBooking" : "cancel";
        controls.append(button(t[action], () => confirm(action, payload("cancel", target(row)), `${slot.role} · ${row.name}`)));
        card.append(controls);
      }
    }
    if (data.admin) {
      const controls = el("div", "", { class: "toolbar" });
      if (data.upcoming && slot.required && !slot.invitations.some(row => row.status === "confirmed"))
        controls.append(button(t.suggest, () => choose(slot)));
      controls.append(button(t[slot.required ? "disable" : "needed"], () => confirm(
        slot.required ? "disable" : "needed",
        payload("needed", { ...target(slot), required: !slot.required }), slot.role,
        slot.required && slot.invitations.length ? t.slotWarning : "",
      )), button(t.remove, () => confirm(
        "remove", payload("remove", target(slot)), slot.role,
        slot.invitations.length ? t.slotWarning : "",
      )));
      card.append(controls);
    }
    list.append(card);
  }
  root.append(list);
}
