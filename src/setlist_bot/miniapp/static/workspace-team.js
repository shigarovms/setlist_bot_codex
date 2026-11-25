import { withBotLabels } from "./bot-labels.js";
import { requestDialog } from "./workspace-request-dialog.js";
import { performerCaption } from "./workspace-staffing.js";

const copy = withBotLabels("workspace-team", {
  ru: {
    inbox: "Приглашения в команду", back: "К коллективу",
    roles: "Роли", myRoles: "Мои роли", noRoles: "Роли не назначены", empty: "Приглашений в команду пока нет.",
    owner: "Владелец", admin: "Администратор", member: "Участник", active: "В команде",
    pending: "Заявка на вступление", invited: "Ожидает ответа", left: "Покинул команду",
    waiting: "Предложенные роли", requested: "Запрошенные роли",
    inviteIntro: "Приглашение даёт доступ к коллективу после принятия.",
    addIntro: "Коллектив предлагает вам дополнительные роли.",
    reference: "@username", roleInput: "Роли, по одной на строку",
    request_roles: "Запросить новые роли",
    removeHelp: "Подтверждённое участие в будущих выступлениях сохранится.",
    gigs: "Подтверждённые будущие выступления", noGigs: "Подтверждённых будущих выступлений нет.",
    transfer: "Передать владение",
    transferHelp: "Введите название коллектива для подтверждения передачи владения.",
    hidden: "Скрыт из подбора", priority: "Приоритет",
    roleHelp: "Участник сохранит доступ к коллективу и своим выступлениям.",
    preview: "Предпросмотр", close: "Закрыть", check: "Проверить результат",
    busy: "Запрос обрабатывается…", done: "Изменения сохранены.", reload: "Обновить список",
  },
  en: {
    inbox: "Team invitations", back: "Back to band",
    roles: "Roles", myRoles: "My roles", noRoles: "No roles assigned", empty: "No team invitations yet.",
    owner: "Owner", admin: "Administrator", member: "Member", active: "On the team",
    pending: "Membership application", invited: "Awaiting response", left: "Left the team",
    waiting: "Proposed roles", requested: "Requested roles",
    inviteIntro: "Accepting the invitation gives you access to the band.",
    addIntro: "The band has offered you additional roles.",
    reference: "@username", roleInput: "Roles, one per line",
    request_roles: "Request new roles",
    removeHelp: "Confirmed bookings for upcoming performances will be kept.",
    gigs: "Confirmed upcoming performances", noGigs: "There are no confirmed upcoming performances.",
    transfer: "Transfer ownership",
    transferHelp: "Enter the band name to confirm the ownership transfer.",
    hidden: "Hidden from suggestions", priority: "Priority",
    roleHelp: "The member will keep access to the band and their performances.",
    preview: "Preview", close: "Close", check: "Check result",
    busy: "Processing your request…", done: "Changes saved.", reload: "Refresh list",
  },
});

export function teamLabel(language, key) { return copy[language][key]; }

function el(tag, text = "", attributes = {}) {
  const node = document.createElement(tag);
  node.textContent = text;
  for (const [key, value] of Object.entries(attributes)) node.setAttribute(key, value);
  return node;
}

function button(text, handler, primary = false) {
  const node = el("button", text, { type: "button", ...(primary ? { class: "primary" } : {}) });
  node.addEventListener("click", handler);
  return node;
}

export function teamView({ root, language, requests, refresh, status, dateText, contactUrl, exit }) {
  const t = copy[language];

  const modal = title => requestDialog({
    title, labels: t, operation: "team_action", request: requests.group, refresh, status,
  });

  function confirm({ key, label, detail, payload, api, done }) {
    const panel = modal(t[key]);
    if (label) panel.body.append(el("p", label));
    if (detail) panel.body.append(el("p", detail));
    panel.body.append(button(t.confirm, event => panel.send(event.currentTarget, payload, api, done), true));
  }

  function membershipAction(key, row) {
    return button(t[key], () => confirm({
      key, label: row.name, payload: { action: key, target: row.id, version: row.version },
    }));
  }

  function remove(row) {
    const key = row.self ? "leave" : "remove";
    const panel = modal(t[key]);
    const api = row.self ? requests.personal(row.id) : requests.group;
    const payload = { target: row.id, version: row.version };
    const loading = button(t.reload, event => load(event.currentTarget));
    panel.body.append(el("p", row.name), loading);
    function load(trigger) {
      panel.send(trigger, { ...payload, action: `${key}_preview` }, api, result => {
        loading.remove();
        panel.body.append(el("p", t.removeHelp));
        if (result.bookings.length) {
          const list = el("ul");
          for (const gig of result.bookings) list.append(el("li", [dateText(gig.date), gig.title].filter(Boolean).join(" · ")));
          panel.body.append(el("h3", t.gigs), list);
        } else panel.body.append(el("p", t.noGigs));
        panel.body.append(button(t.confirm, event => panel.send(
          event.currentTarget, { ...payload, action: key }, api,
          row.self ? () => { panel.dialog.close(); exit(); } : undefined,
        ), true));
      });
    }
    load(loading);
  }

  function transfer(row) {
    const panel = modal(t.transfer);
    panel.body.append(el("p", row.name));
    const form = el("form", "", { class: "import-fields" });
    const input = el("input", "", { id: "team-owner-name", required: "", maxlength: "200" });
    const save = el("button", t.confirm, { type: "submit", class: "primary" });
    form.append(el("label", t.transferHelp, { for: input.id }), input, save);
    form.addEventListener("submit", event => {
      event.preventDefault();
      panel.send(save, { action: "transfer", target: row.id, version: row.version, confirmation: input.value });
    });
    panel.body.append(form);
    input.focus();
  }

  function roleForm(inviting = false) {
    const action = inviting ? "invite" : "request_roles";
    const panel = modal(t[action]);
    const form = el("form", "", { class: "import-fields" });
    const roles = el("textarea", "", { id: "team-roles-input", rows: "3", maxlength: "4000", required: "" });
    let reference;
    if (inviting) {
      reference = el("input", "", { id: "team-recipient", maxlength: "64", required: "", autocomplete: "off" });
      form.append(el("label", t.reference, { for: reference.id }), reference);
    }
    const submit = el("button", inviting ? t.preview : t.confirm, { type: "submit", class: "primary" });
    form.append(el("label", t.roleInput, { for: roles.id }), roles, submit);
    panel.body.append(form);
    form.addEventListener("submit", async event => {
      event.preventDefault();
      const value = roles.value;
      if (!inviting) {
        panel.send(submit, { action, roles: value });
        return;
      }
      if (submit.disabled) return;
      submit.disabled = true;
      panel.message.textContent = t.busy;
      const identifier = reference.value;
      try {
        const person = await requests.recipient(identifier);
        if (!panel.dialog.open) return;
        panel.message.textContent = "";
        const heading = el("h3");
        heading.append(performerCaption(person));
        panel.body.replaceChildren(heading,
          el("p", value, { class: "performance-info" }),
          button(t.invite, event => panel.send(event.currentTarget, {
            action, target: person.id, reference: identifier, roles: value,
          }), true));
      } catch (error) {
        panel.message.textContent = error.message;
      } finally {
        submit.disabled = false;
      }
    });
  }

  function renderTeam(data) {
    root.append(el("h2", t[data.admin ? "title" : "myRoles"]));
    const menu = el("div", "", { class: "toolbar" });
    if (data.admin) menu.append(button(t.invite, () => roleForm(true), true));
    menu.append(button(t.request_roles, () => roleForm()));
    root.append(menu);
    const list = el("ul", "", { class: "workspace-list" });
    for (const row of data.members) {
      const item = el("li", "", { id: `team-${row.id}` });
      const heading = el("h3");
      heading.append(performerCaption(row));
      item.append(heading, el("p", `${t[row.right]} · ${t[row.status]}`));
      const roles = el("div", "", { class: "toolbar" });
      for (const role of row.roles) {
        roles.append(data.admin ? el("a", role.name, { href: contactUrl(role.id) }) : el("span", role.name));
      }
      if (roles.childElementCount) item.append(roles);
      else item.append(el("p", t.noRoles));
      const ops = el("div", "", { class: "toolbar" });
      if (row.invitation) {
        item.append(el("p", `${t.waiting} · ${row.invitation.join(", ")}`));
        ops.append(membershipAction("retry", row), membershipAction("withdraw", row));
      }
      if (row.requested?.length) item.append(el("p", `${t.requested} · ${row.requested.join(", ")}`));
      if (data.admin && (row.requested || row.status === "pending"))
        ops.append(membershipAction("approve", row), membershipAction("reject", row));
      if (data.owner && !row.self && row.status === "active") {
        ops.append(membershipAction(row.right === "admin" ? "revoke" : "grant", row),
          button(t.transfer, () => transfer(row)));
      }
      if (row.removable || (row.self && !data.owner))
        ops.append(button(t[row.self ? "leave" : "remove"], () => remove(row)));
      if (ops.childElementCount) item.append(ops);
      list.append(item);
    }
    root.append(list);
  }

  function renderContact(row) {
    root.append(el("h2", row.name), el("p", `${row.performer} · ${t.priority} ${row.priority}`));
    const list = el("ol");
    for (const person of row.order)
      list.append(el("li", person.hidden ? `${person.name} · ${t.hidden}` : person.name));
    root.append(list);
    const target = { target: row.id, version: row.version };
    const actions = el("div", "", { class: "toolbar" });
    for (const [key, step] of [["up", -1], ["down", 1]]) {
      if (row[key]) actions.append(button(t[key], () => confirm({
        key, label: row.performer, payload: { action: "role_move", ...target, step },
      })));
    }
    actions.append(button(t[row.hidden ? "show" : "hide"], () => confirm({
      key: row.hidden ? "show" : "hide", label: row.performer, payload: { action: "role_visibility", ...target },
    })), button(t.prioritySave, () => {
      const panel = modal(t.prioritySave);
      const form = el("form", "", { class: "import-fields" });
      const input = el("input", "", { id: "team-role-priority", type: "number", min: "0", max: "9999", required: "" });
      const save = el("button", t.confirm, { type: "submit", class: "primary" });
      input.value = row.priority;
      form.append(el("label", t.priority, { for: input.id }), input, save);
      form.addEventListener("submit", event => {
        event.preventDefault();
        panel.send(save, { action: "role_priority", ...target, priority: Number(input.value) });
      });
      panel.body.append(form);
      input.focus();
    }), button(t.role_remove, () => confirm({
      key: "role_remove", label: row.performer, detail: t.roleHelp,
      payload: { action: "role_remove", ...target }, done: exit,
    })));
    root.append(actions);
  }

  function renderInbox(rows) {
    const list = el("ul", "", { class: "workspace-list" });
    if (!rows.length) root.append(el("p", t.empty));
    for (const row of rows) {
      const item = el("li", "", { id: `team-${row.id}` });
      item.append(el("h3", row.band), el("p", t[row.already_member ? "addIntro" : "inviteIntro"]),
        el("p", `${t.roles} · ${row.roles.join(", ")}`));
      const ops = el("div", "", { class: "toolbar" });
      for (const [key, accept] of [[row.already_member ? "add" : "join", true], ["decline", false]]) {
        ops.append(button(t[key], () => confirm({
          key, label: row.band, detail: row.roles.join(", "),
          payload: { action: "respond", version: row.version, accept }, api: requests.personal(row.id),
        }), accept));
      }
      item.append(ops);
      list.append(item);
    }
    root.append(list);
  }

  return { renderTeam, renderContact, renderInbox };
}
