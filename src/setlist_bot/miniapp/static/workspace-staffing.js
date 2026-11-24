import { requestDialog } from "./workspace-request-dialog.js";

const words = {
  ru: {
    contacts: "Знакомые исполнители", lineups: "Сохранённые составы", team: "К постоянной команде",
    createContact: "Добавить исполнителя", createLineup: "Создать состав", editLineup: "Редактировать состав",
    emptyContacts: "Знакомых исполнителей пока нет.", emptyLineups: "Сохранённых составов пока нет.",
    noMatches: "Совпадений нет.", search: "Поиск", name: "Название состава", role: "Роль",
    reference: "Telegram ID или @username", optional: "Исполнитель (по желанию)",
    roles: "Роли", noPerson: "Свободная роль", suggested: "Автоподбор", chosen: "Запасной исполнитель",
    preferred: "Предлагать первым", normal: "Можно приглашать", hidden: "Не предлагать",
    preference: "Подбор на выступления", experience: "Выступлений", alreadyPermanent: "Исполнитель уже в постоянной команде.",
    city: "Город", substitutions: "Подмены", travel: "Выезды", about: "О себе", yes: "Да", no: "Нет",
    add: "Добавить роль", remove: "Убрать роль", up: "Переместить выше", down: "Переместить ниже",
    quick: "Вставить список ролей", quickHint: "По одной роли на строку. Например «Вокал» или «Гитара: @username».",
    copy: "Копировать состав", delete: "Удалить состав", deletePrompt: "Удалить этот сохранённый состав?",
    preview: "Предпросмотр", back: "Изменить", save: "Сохранить", close: "Закрыть", check: "Проверить результат",
    busy: "Запрос обрабатывается…", done: "Изменения сохранены.",
  },
  en: {
    contacts: "Known performers", lineups: "Saved lineups", team: "Back to permanent team",
    createContact: "Add performer", createLineup: "Create lineup", editLineup: "Edit lineup",
    emptyContacts: "No known performers yet.", emptyLineups: "No saved lineups yet.",
    noMatches: "No matches.", search: "Search", name: "Lineup name", role: "Role",
    reference: "Telegram ID or @username", optional: "Performer (optional)",
    roles: "Roles", noPerson: "Open role", suggested: "Suggested performer", chosen: "Fallback performer",
    preferred: "Suggest first", normal: "Can invite", hidden: "Do not suggest",
    preference: "Performance suggestions", experience: "Performances", alreadyPermanent: "This performer is already on the permanent team.",
    city: "City", substitutions: "Substitutions", travel: "Travel", about: "About", yes: "Yes", no: "No",
    add: "Add role", remove: "Remove role", up: "Move up", down: "Move down",
    quick: "Paste a list of roles", quickHint: "Use one role per line, such as “Vocals” or “Guitar: @username”.",
    copy: "Copy lineup", delete: "Delete lineup", deletePrompt: "Delete this saved lineup?",
    preview: "Preview", back: "Edit", save: "Save", close: "Close", check: "Check result",
    busy: "Processing your request…", done: "Changes saved.",
  },
};

export function staffingLabel(language, key) { return words[language][key]; }

function el(tag, label = "", attrs = {}) {
  const node = document.createElement(tag);
  node.textContent = label;
  for (const [name, value] of Object.entries(attrs)) node.setAttribute(name, value);
  return node;
}

function button(label, handler, primary = false, attrs = {}) {
  const node = el("button", label, { type: "button", ...attrs });
  if (primary) node.classList.add("primary");
  node.addEventListener("click", handler);
  return node;
}

function field(root, label, id, attrs, type = "input") {
  const input = el(type, "", { id, ...attrs });
  root.append(el("label", label, { for: id }), input);
  return input;
}

function review(panel, form, submit, t, load, render) {
  form.addEventListener("submit", async event => {
    event.preventDefault();
    if (submit.disabled) return;
    submit.disabled = true;
    panel.message.textContent = t.busy;
    try {
      const value = await load();
      if (!panel.dialog.open) return;
      panel.message.textContent = "";
      render(value);
      panel.body.append(button(t.back, () => panel.body.replaceChildren(form)));
    } catch (error) {
      panel.message.textContent = error.message;
    } finally {
      submit.disabled = false;
    }
  });
}

function lineupRows(rows, t, previewMode = false) {
  const list = el("ol", "", { class: "workspace-list" });
  for (const row of rows) {
    const item = el("li");
    item.append(el("strong", row.role));
    if (previewMode) {
      item.append(el("p", row.name ? `${row.name} · Telegram ID ${row.telegram_id}` : t.noPerson));
    } else {
      item.append(el("p", row.suggested ? `${t.suggested} · ${row.suggested}` : t.noPerson));
      if (row.name && row.name !== row.suggested) item.append(el("p", `${t.chosen} · ${row.name}`));
    }
    list.append(item);
  }
  return list;
}

export function mountLineupForm({ panel, language, preview, onSave, initial, allowEmpty, showName = true, single = false }) {
  const t = words[language];
  const form = el("form", "", { class: "import-fields" });
  const name = field(form, t.name, "staff-lineup-name", { maxlength: "100", required: "" });
  name.value = initial?.name || "";
  name.hidden = !showName;
  form.querySelector("label").hidden = !showName;
  const rows = el("div", "", { class: "import-fields" });
  const toggle = el("input", "", { id: "staff-lineup-quick", type: "checkbox" });
  const toggleLabel = el("label", "", { for: toggle.id });
  toggleLabel.append(toggle, document.createTextNode(t.quick));
  const quickArea = el("div", "", { hidden: "" });
  const quick = field(quickArea, t.quickHint, "staff-lineup-paste", { rows: "5", maxlength: "8000" }, "textarea");
  const submit = el("button", t.preview, { type: "submit", class: "primary" });
  const add = button(t.add, () => addRow().focus());
  let serial = 0;
  function sync() {
    const items = [...rows.children];
    items.forEach((row, index) => {
      if (single) return;
      row.querySelector("[data-move=up]").disabled = index === 0;
      row.querySelector("[data-move=down]").disabled = index === items.length - 1;
    });
    add.disabled = items.length >= 40;
    submit.disabled = !toggle.checked && !allowEmpty && items.length === 0;
  }
  function addRow(saved = {}) {
    const index = serial++;
    const item = el("fieldset", "", { class: "staff-lineup-row import-fields" });
    const role = field(item, t.role, `staff-lineup-role-${index}`, { maxlength: "100", required: "", "data-role": "" });
    const person = field(item, t.optional, `staff-lineup-person-${index}`, {
      maxlength: "64", placeholder: t.reference, "data-reference": "",
    });
    role.value = saved.role || "";
    person.value = saved.reference || "";
    const controls = el("div", "", { class: "toolbar" });
    controls.append(
      button("↑", () => { rows.insertBefore(item, item.previousElementSibling); sync(); }, false, { "data-move": "up", "aria-label": t.up }),
      button("↓", () => { item.nextElementSibling.after(item); sync(); }, false, { "data-move": "down", "aria-label": t.down }),
      button(t.remove, () => { item.remove(); sync(); }),
    );
    if (!single) item.append(controls);
    rows.append(item);
    sync();
    return role;
  }
  if (!single) form.append(toggleLabel, quickArea);
  form.append(rows);
  if (!single) form.append(add);
  form.append(submit);
  const initialRows = initial?.rows ?? [];
  for (const row of initialRows.length || allowEmpty ? initialRows : [{}]) addRow(row);
  sync();
  toggle.addEventListener("change", () => {
    rows.hidden = add.hidden = toggle.checked;
    quickArea.hidden = !toggle.checked;
    quick.required = toggle.checked;
    for (const input of rows.querySelectorAll("input")) input.disabled = toggle.checked;
    sync();
    if (toggle.checked) quick.focus();
  });
  panel.body.append(form);
  review(panel, form, submit, t, () => preview({
    name: name.value, ...(toggle.checked ? { quick: quick.value } : {
      rows: Array.from(rows.children, row => ({
        role: row.querySelector("[data-role]").value,
        reference: row.querySelector("[data-reference]").value,
      })),
    }),
  }), plan => {
    panel.body.replaceChildren(...(showName ? [el("h3", plan.name)] : []), lineupRows(plan.rows, t, true),
      button(t.save, event => onSave(event, plan), true));
  });
}

export function showStaffDirectory({ root, language, data, section, recordUrl, listUrl, query, request, preview, recipient, refresh, status }) {
  const t = words[language];
  const modal = title => requestDialog({ title, labels: t, operation: "staff_action", request, refresh, status });

  function contactAdd() {
    const panel = modal(t.createContact);
    const form = el("form", "", { class: "import-fields" });
    const role = field(form, t.role, "staff-contact-role", { maxlength: "100", required: "" });
    const reference = field(form, t.reference, "staff-contact-account", { maxlength: "64", required: "" });
    const submit = el("button", t.preview, { type: "submit", class: "primary" });
    form.append(submit);
    panel.body.append(form);
    review(panel, form, submit, t, async () => {
      const selectedRole = role.value;
      const account = reference.value;
      return { role: selectedRole, reference: account, person: await recipient(account) };
    }, chosen => {
      const person = chosen.person;
      panel.body.replaceChildren(el("h3", person.name), el("p", chosen.role),
        el("p", [person.username && `@${person.username}`, `Telegram ID ${person.telegram_id}`].filter(Boolean).join(" · ")),
        button(t.save, event => panel.send(event.currentTarget, {
          action: "contact_add", role: chosen.role, reference: chosen.reference, person: person.id,
        }, request, async result => {
          panel.dialog.close();
          if (result.permanent) status(t.alreadyPermanent);
          else location.assign(recordUrl(result.contact));
        }), true));
    });
  }

  function contactCard(row) {
    root.append(el("h2", row.name), el("p", row.role));
    const details = el("dl", "", { class: "song-details" });
    details.append(el("dt", t.experience), el("dd", row.experience));
    for (const [key, value] of Object.entries(row.profile)) {
      const formatted = Array.isArray(value) ? value.join(", ") : typeof value === "boolean" ? t[value ? "yes" : "no"] : value;
      if (formatted) details.append(el("dt", t[key]), el("dd", formatted));
    }
    root.append(details, button(t.preference + " · " + t[row.preference], () => {
      const panel = modal(t.preference);
      const form = el("form", "", { class: "import-fields" });
      const select = field(form, t.preference, "staff-contact-preference", {}, "select");
      for (const value of ["preferred", "normal", "hidden"]) select.append(el("option", t[value], { value }));
      select.value = row.preference;
      const save = el("button", t.save, { type: "submit", class: "primary" });
      form.append(save);
      form.addEventListener("submit", event => {
        event.preventDefault();
        panel.send(save, { action: "contact_preference", target: row.id, version: row.version, preference: select.value });
      });
      panel.body.append(form);
    }));
  }

  function editLineup(lineup = null) {
    const panel = modal(t[lineup ? "editLineup" : "createLineup"]);
    mountLineupForm({
      panel, language, initial: lineup, preview, allowEmpty: !!lineup,
      onSave: (event, plan) => panel.send(event.currentTarget, {
        ...plan, action: lineup ? "lineup_update" : "lineup_create", target: lineup?.id, version: lineup?.version,
      }, request, result => {
        panel.dialog.close();
        if (lineup) return refresh().then(() => status(t.done));
        location.assign(recordUrl(result.lineup));
      }),
    });
  }

  function lineupCard(row) {
    root.append(el("h2", row.name), lineupRows(row.rows, t));
    const actions = el("div", "", { class: "toolbar" });
    const target = { target: row.id, version: row.version };
    actions.append(button(t.editLineup, () => editLineup(row), true), button(t.copy, () => {
      const panel = modal(t.copy);
      const form = el("form", "", { class: "import-fields" });
      const name = field(form, t.name, "staff-lineup-copy-name", { maxlength: "100", required: "" });
      const save = el("button", t.save, { type: "submit", class: "primary" });
      form.append(save);
      form.addEventListener("submit", event => {
        event.preventDefault();
        panel.send(save, { action: "lineup_copy", ...target, name: name.value }, request,
          result => location.assign(recordUrl(result.lineup)));
      });
      panel.body.append(form);
    }), button(t.delete, () => {
      const panel = modal(t.delete);
      panel.body.append(el("p", row.name), el("p", t.deletePrompt),
        button(t.delete, event => panel.send(event.currentTarget, { action: "lineup_delete", ...target },
          request, () => location.assign(listUrl)), true));
    }));
    root.append(actions);
  }

  if (data.contact) return contactCard(data.contact);
  if (data.lineup) return lineupCard(data.lineup);
  const contacts = section === "contacts";
  root.append(el("h2", t[section]), contacts ? button(t.createContact, contactAdd, true)
    : button(t.createLineup, () => editLineup(), true));
  if (!data.rows.length) root.append(el("p", t[query ? "noMatches" : contacts ? "emptyContacts" : "emptyLineups"]));
  const list = el("ul", "", { class: "workspace-list" });
  for (const row of data.rows) {
    const item = el("li");
    item.append(el("a", contacts ? `${row.name} · ${row.role}` : row.name, { href: recordUrl(row.id) }),
      el("small", contacts ? t[row.preference] : `${t.roles} · ${row.count}`));
    list.append(item);
  }
  root.append(list);
}
