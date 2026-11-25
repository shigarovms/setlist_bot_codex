import { withBotLabels } from "./bot-labels.js";
import { pollRequest } from "./workspace-request-dialog.js";

const words = withBotLabels("workspace-timing", {
  ru: {
    history: "История", template: "Взять за шаблон", frequent: "Часто выбираемые",
    past: "Прошедшие выступления", noHistory: "Подходящих выступлений пока нет.",
    previous: "Назад", next: "Дальше", close: "Закрыть", loading: "Загрузка…",
    start: "Начало", end: "Окончание", event: "Событие", nextDay: "На следующий день",
    typical: ["Сбор", "Саундчек", "Открытие дверей", "Выступление", "Окончание"],
    add: "Добавить строку", remove: "Удалить строку", save: "Сохранить",
    zone: "Часовой пояс площадки", responsible: "Ответственный за тайминг",
    defaultResponsible: "По умолчанию · владелец коллектива",
    saved: "Тайминг сохранён.", wait: "Выполняем запрос…", check: "Проверить результат",
    notifyConfirm: "Отправить сохранённый тайминг всем подтвердившим участие?",
    queued: count => `Уведомления в очереди: ${count}.`,
    noRecipients: "Пока никто не подтвердил участие.",
    replace: "Подставить строки выбранного тайминга в таблицу?",
  },
  en: {
    history: "History", template: "Use as template", frequent: "Frequently used",
    past: "Past performances", noHistory: "No matching past performances yet.",
    previous: "Previous", next: "Next", close: "Close", loading: "Loading…",
    start: "Start", end: "End", event: "Event", nextDay: "Next day",
    typical: ["Call time", "Soundcheck", "Doors open", "Performance", "End"],
    add: "Add row", remove: "Remove row", save: "Save",
    zone: "Venue time zone", responsible: "Schedule coordinator",
    defaultResponsible: "Default · band owner",
    saved: "Schedule saved.", wait: "Processing…", check: "Check result",
    notifyConfirm: "Send the saved schedule to all confirmed performers?",
    queued: count => `Notifications queued: ${count}.`,
    noRecipients: "Nobody has confirmed yet.",
    replace: "Fill the table with the selected schedule?",
  },
});

export function timingLabel(language) { return words[language].title; }

function element(tag, text = "", attrs = {}) {
  const node = document.createElement(tag);
  node.textContent = text;
  for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value);
  return node;
}
function button(label, action, primary = false) {
  const node = element("button", label, { type: "button", class: primary ? "primary" : "" });
  node.addEventListener("click", action);
  return node;
}

export function historyButton({ language, read, select, timing = false }) {
  const t = words[language];
  return button(t.history, () => {
    const dialog = element("dialog", "", { class: "shared-tools", "aria-label": t.history });
    const list = element("div");
    const status = element("p", "", { role: "status" });
    const nav = element("div", "", { class: "toolbar" });
    dialog.append(element("h2", t.history), status, list, nav, button(t.close, () => dialog.close()));
    document.body.append(dialog);
    dialog.addEventListener("close", () => dialog.remove(), { once: true });
    dialog.showModal();
    function section(heading, rows) {
      if (!rows.length) return;
      list.append(element("h3", heading));
      const items = element("ul", "", { class: "workspace-list" });
      for (const row of rows) {
        const item = element("li");
        item.append(element("p", row.label));
        if (timing) item.append(summary(row.rows));
        item.append(button(t.template, () => {
          if (select(row) !== false) dialog.close();
        }));
        items.append(item);
      }
      list.append(items);
    }
    async function load(page) {
      list.replaceChildren();
      nav.replaceChildren();
      status.textContent = t.loading;
      try {
        const data = await read(page);
        if (!dialog.open) return;
        status.textContent = data.performances.length ? "" : t.noHistory;
        section(t.frequent, data.recommended);
        section(t.past, data.performances);
        if (page) nav.append(button(t.previous, () => void load(page - 1)));
        if (data.has_next) nav.append(button(t.next, () => void load(page + 1)));
      } catch (error) { status.textContent = error.message; }
    }
    void load(0);
  });
}
function summary(rows) {
  const table = element("table", "", { class: "timing-table" });
  const body = table.createTBody();
  for (const row of rows) {
    const tr = body.insertRow();
    tr.insertCell().textContent = row.start + (row.end ? `–${row.end}` : "") + (row.next_day ? " (+1)" : "");
    tr.insertCell().textContent = row.label;
  }
  return table;
}

export function showTiming({ root, data, performance, back, request, readHistory, refresh }) {
  const t = words[data.language] || words.ru;
  const nav = element("nav", "", { class: "workspace-nav" });
  nav.append(element("a", t.previous, { href: back }));
  root.append(nav);
  root.append(element("h2", t.title), element("p", data.event));
  if (data.preliminary) root.append(element("strong", t.preliminary));
  if (!data.rows.length) root.append(element("p", t.empty));
  if (!data.editor) {
    root.append(element("p", data.zone), summary(data.rows));
    return;
  }
  const status = element("p", "", { role: "status" });
  const form = element("form", "", { class: "timing-form" });
  const fields = element("fieldset", "", { class: "timing-fields" });
  const table = element("table", "", { class: "timing-table timing-edit-table" });
  const headings = table.createTHead().insertRow();
  headings.append(element("th", t.start, { scope: "col" }), element("th", t.event, { scope: "col" }));
  const body = table.createTBody();
  const zone = element("select", "", { id: "timing-zone" });
  for (const name of data.zones) zone.append(element("option", name, { value: name }));
  zone.value = data.zone;
  let owner;
  const preliminary = element("input", "", { type: "checkbox" });
  preliminary.checked = data.preliminary;
  const flag = element("label"); flag.append(preliminary, t.preliminary);
  fields.append(flag, element("label", t.zone, { for: zone.id }), zone);
  if (data.admin) {
    owner = element("select", "", { id: "timing-owner" });
    owner.append(element("option", t.defaultResponsible, { value: "" }));
    for (const member of data.members) owner.append(element("option", member.name || member.id, { value: member.id }));
    owner.value = data.responsible;
    fields.append(element("label", t.responsible, { for: owner.id }), owner);
  }
  const add = button(t.add, () => addRow({}).focus());
  const save = element("button", t.save, { type: "submit", class: "primary" });
  const notify = button(t.notify, () => {
    if (confirm(t.notifyConfirm)) void start({ action: "notify" });
  });
  notify.disabled = !data.rows.length;
  const retry = button(t.check, () => void deliver());
  retry.hidden = true;
  const history = historyButton({
    language: data.language, timing: true, read: readHistory,
    select: source => {
      if (body.rows.length && !confirm(t.replace)) return false;
      body.replaceChildren();
      for (const item of source.rows) addRow(item);
    },
  });
  const controls = element("div", "", { class: "toolbar" });
  controls.append(add, history);
  const events = element("datalist", "", { id: "timing-events" });
  for (const label of t.typical) events.append(element("option", "", { value: label }));
  fields.append(table, events, controls, save, notify);
  form.append(fields, status, retry);
  root.append(form);
  let attempt, working = false;
  for (const row of data.rows) addRow(row);

  function addRow(initial) {
    const row = body.insertRow();
    const start = element("input", "", { type: "time", name: "start", required: "", "aria-label": t.start });
    const end = element("input", "", { type: "time", name: "end", "aria-label": t.end });
    const title = element("input", "", { type: "text", name: "label", maxlength: "200", required: "", list: "timing-events", "aria-label": t.event });
    const nextDay = element("input", "", { name: "next_day", type: "checkbox" });
    const day = element("label"); day.append(nextDay, t.nextDay);
    start.value = initial.start || "";
    end.value = initial.end || "";
    title.value = initial.label || "";
    nextDay.checked = !!initial.next_day;
    row.insertCell().append(start, element("span", t.end, { class: "timing-end-label" }), end);
    const remove = button("×", () => { row.remove(); add.disabled = false; });
    remove.setAttribute("aria-label", t.remove);
    row.insertCell().append(title, day, remove);
    add.disabled = body.rows.length >= 60;
    return start;
  }
  form.addEventListener("submit", event => {
    event.preventDefault();
    const rows = Array.from(body.rows, row => {
      const values = Object.fromEntries(Array.from(row.querySelectorAll("input"), field =>
        [field.name, field.type === "checkbox" ? field.checked : field.value]));
      return values;
    });
    void start({ action: "save", rows, zone: zone.value, preliminary: preliminary.checked,
      ...(owner ? { responsible: owner.value } : {}) });
  });
  async function start(payload) {
    if (attempt) return;
    attempt = { api: request, payload: { ...payload, performance, version: data.version,
      epoch: data.epoch, request_id: crypto.randomUUID() } };
    await deliver();
  }
  async function deliver() {
    if (working || !attempt) return;
    fields.disabled = working = true;
    retry.hidden = true;
    status.textContent = t.wait;
    try {
      const result = await pollRequest(attempt, "timing_action", () => form.isConnected);
      if (!result || result.status === "pending") return;
      const action = attempt.payload.action;
      attempt = null;
      if (result.status === "failed") { status.textContent = result.message; return; }
      if (action === "save") await refresh(t.saved);
      else status.textContent = result.queued ? t.queued(result.queued) : t.noRecipients;
    } catch (error) {
      if (!attempt?.receipt && error.data && !["busy", "unavailable"].includes(error.data.error)) attempt = null;
      status.textContent = error.message;
    } finally {
      working = false;
      fields.disabled = !!attempt;
      retry.hidden = !attempt;
    }
  }
}
