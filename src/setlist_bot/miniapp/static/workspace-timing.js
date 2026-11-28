import { withBotLabels } from "./bot-labels.js";
import { pollRequest } from "./workspace-request-dialog.js";

const words = withBotLabels("workspace-timing", {
  ru: {
    history: "История", template: "Взять за шаблон", frequent: "Часто выбираемые",
    past: "Прошедшие выступления", noHistory: "Подходящих выступлений пока нет.",
    previous: "Назад", next: "Дальше", close: "Закрыть", loading: "Загрузка…",
    start: "Начало", end: "Конец", event: "Событие", swipeHint: "(чтобы удалить, смахните влево)",
    typical: ["Сбор", "Саундчек", "Открытие дверей", "Выступление", "Окончание"],
    add: "Добавить строку", remove: "Удалить строку", save: "Сохранить",
    zone: "Часовой пояс", automatic: "Автоматически", hour: "Часы", minute: "Минуты",
    setTime: "Готово", clearTime: "Убрать время окончания",
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
    start: "Start", end: "End", event: "Event", swipeHint: "(swipe left to remove)",
    typical: ["Call time", "Soundcheck", "Doors open", "Performance", "End"],
    add: "Add row", remove: "Remove row", save: "Save",
    zone: "Time zone", automatic: "Automatic", hour: "Hours", minute: "Minutes",
    setTime: "Done", clearTime: "Remove end time",
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
  const control = button(t.history, () => {
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
        if (timing) item.append(summary(row.rows, row.date, language));
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
  if (!timing) {
    const hint = language === "en" ? "(use a past performance as a template)" : "(взять прошлое как шаблон)";
    control.replaceChildren(element("strong", t.history), " ", element("em", hint));
  }
  return control;
}
function dateAt(date, offset, language) {
  const current = new Date(`${date}T00:00:00Z`);
  current.setUTCDate(current.getUTCDate() + offset);
  return new Intl.DateTimeFormat(language, { dateStyle: "long", timeZone: "UTC" }).format(current);
}

function timingTable(t, editable = false) {
  const table = element("table", "", { class: editable ? "timing-table timing-edit-table" : "timing-table" });
  const headings = table.createTHead().insertRow();
  for (const key of ["start", "end", "event"]) headings.append(element("th", t[key], { scope: "col" }));
  if (editable) headings.lastElementChild.append(element("em", t.swipeHint, { class: "timing-swipe-hint" }));
  return table;
}

function dateDivider(date, day, language) {
  const row = element("tr", "", { class: "timing-date" });
  row.append(element("td", dateAt(date, day, language), { colspan: "3" }));
  return row;
}

function summary(rows, date, language) {
  const table = timingTable(words[language] || words.ru);
  const body = table.createTBody();
  const multiDay = rows.some(row => row.day || row.next_day);
  let lastDay;
  for (const row of rows) {
    const day = row.day ?? Number(!!row.next_day);
    if (date && multiDay && day !== lastDay) {
      body.append(dateDivider(date, day, language));
    }
    lastDay = day;
    const tr = body.insertRow();
    tr.className = "timing-event-row";
    tr.insertCell().textContent = row.start;
    tr.insertCell().textContent = row.end || "";
    tr.insertCell().textContent = row.label;
  }
  return table;
}

function timeWheel(title, value, optional, t, apply) {
  const dialog = element("dialog", "", { class: "shared-tools timing-wheel-dialog", "aria-label": title });
  const wheels = element("div", "", { class: "timing-wheels" });
  const format = number => String(number).padStart(2, "0");
  const [hours, minutes] = (value || "12:00").split(":").map(Number);
  const hour = wheel(t.hour, Array.from({ length: 24 }, (_, index) => index), hours);
  const steps = Array.from({ length: 12 }, (_, index) => index * 5);
  if (!steps.includes(minutes)) steps.push(minutes);
  steps.sort((a, b) => a - b);
  const minute = wheel(t.minute, steps, minutes);
  const actions = element("div", "", { class: "toolbar" });
  actions.append(button(t.close, () => dialog.close()), button(t.setTime, () => {
    apply(`${format(hour.value())}:${format(minute.value())}`);
    dialog.close();
  }, true));
  if (optional && value) actions.append(button(t.clearTime, () => { apply(""); dialog.close(); }));
  dialog.append(element("h2", title), wheels, actions);
  document.body.append(dialog);
  dialog.addEventListener("close", () => dialog.remove(), { once: true });
  dialog.showModal();
  hour.setInitial();
  minute.setInitial();

  function wheel(label, values, initial) {
    const column = element("div");
    const options = element("div", "", { class: "timing-wheel", role: "group", "aria-label": label });
    const choices = values.map((number, index) => button(format(number), () => {
      options.scrollTop = offset(index);
    }));
    const offset = index => choices[index].offsetTop - (options.clientHeight - choices[index].offsetHeight) / 2;
    const index = () => Math.max(0, Math.min(values.length - 1,
      Math.round((options.scrollTop - offset(0)) / choices[0].offsetHeight)));
    const selected = () => {
      for (const [position, choice] of choices.entries())
        choice.setAttribute("aria-pressed", String(position === index()));
    };
    options.addEventListener("scroll", selected, { passive: true });
    options.append(...choices);
    column.append(element("p", label), options);
    wheels.append(column);
    return {
      value: () => values[index()],
      setInitial: () => { options.scrollTop = offset(values.indexOf(initial)); selected(); },
    };
  }
}

export function showTiming({ root, data, performance, request, readHistory, refresh }) {
  const t = words[data.language] || words.ru;
  root.append(element("h2", t.title), element("p", data.event));
  if (data.preliminary) root.append(element("strong", t.preliminary));
  if (!data.rows.length) root.append(element("p", t.empty));
  if (!data.editor) {
    root.append(element("p", data.zone_label), summary(data.rows, data.date, data.language));
    return;
  }
  const status = element("p", "", { role: "status" });
  const form = element("form", "", { class: "timing-form" });
  const fields = element("fieldset", "", { class: "timing-fields" });
  const table = timingTable(t, true);
  const body = table.createTBody();
  const eventRows = () => Array.from(body.querySelectorAll(".timing-event-row"));
  const zone = element("select", "", { id: "timing-zone" });
  zone.append(element("option", `${t.automatic} · ${data.auto_zone}`, { value: "" }));
  for (const choice of data.zones) zone.append(element("option", choice.label, { value: choice.id }));
  zone.value = data.manual_zone;
  const preliminary = element("input", "", { type: "checkbox" });
  preliminary.checked = data.preliminary;
  const flag = element("label"); flag.append(preliminary, t.preliminary);
  fields.append(flag, element("label", t.zone, { for: zone.id }), zone);
  const add = button(t.add, addNext);
  const save = button(t.save, saveRows, true);
  const notify = button(t.notify, () => {
    if (confirm(t.notifyConfirm)) void start({ action: "notify" });
  });
  notify.disabled = !data.rows.length;
  const retry = button(t.check, () => void deliver());
  retry.hidden = true;
  const history = historyButton({
    language: data.language, timing: true, read: readHistory,
    select: source => {
      if (eventRows().length && !confirm(t.replace)) return false;
      body.replaceChildren();
      revealed = null;
      for (const item of source.rows) addRow(item);
      updateDates();
    },
  });
  const controls = element("div", "", { class: "toolbar" });
  controls.append(add, history);
  const events = element("datalist", "", { id: "timing-events" });
  for (const label of t.typical) events.append(element("option", "", { value: label }));
  fields.append(table, events, controls, save, notify);
  form.append(fields, status, retry);
  root.append(form);
  let attempt, working = false, revealed = null;
  for (const row of data.rows) addRow(row);
  updateDates();

  function addNext() {
    if (add.disabled) return;
    addRow({}).focus();
  }

  function updateDates() {
    body.querySelectorAll(".timing-date").forEach(row => row.remove());
    let previous = "", day = 0;
    const rows = eventRows();
    for (const row of rows) {
      const start = row.querySelector('[name="start"]').value;
      if (start && previous && start < previous) {
        day += 1;
        body.insertBefore(dateDivider(data.date, day, data.language), row);
      }
      if (start) previous = start;
    }
    if (day) body.insertBefore(dateDivider(data.date, 0, data.language), rows[0]);
  }

  function addRow(initial) {
    const row = body.insertRow();
    row.className = "timing-event-row";
    const title = element("input", "", { type: "text", name: "label", maxlength: "200", required: "", list: "timing-events", "aria-label": t.event });
    title.value = initial.label || "";
    const start = timeControl("start", false), end = timeControl("end", true);
    row.insertCell().append(start.input, start.trigger);
    row.insertCell().append(end.input, end.trigger);
    const remove = button("×", () => {
      if (revealed === row) revealed = null;
      row.remove();
      add.disabled = false;
      updateDates();
    });
    remove.className = "timing-remove";
    remove.setAttribute("aria-label", t.remove);
    const eventFields = element("div", "", { class: "timing-event-fields" });
    eventFields.append(title, remove);
    row.insertCell().append(eventFields);
    add.disabled = eventRows().length >= 60;
    let swipe = null, suppressClick = false;
    row.addEventListener("pointerdown", event => {
      suppressClick = false;
      if (event.pointerType !== "touch" || !event.isPrimary) return;
      if (revealed && revealed !== row) {
        revealed.classList.remove("timing-revealed");
        revealed = null;
      }
      swipe = { id: event.pointerId, x: event.clientX, y: event.clientY, time: event.timeStamp };
    });
    row.addEventListener("pointercancel", () => { swipe = null; });
    function revealSwipe(event) {
      if (event.pointerId !== swipe?.id) return;
      const dx = event.clientX - swipe.x, dy = event.clientY - swipe.y;
      const elapsed = Math.max(1, event.timeStamp - swipe.time);
      const fast = Math.abs(dx) >= 18 && Math.abs(dx) / elapsed >= .24;
      if ((!fast && Math.abs(dx) < 32) || Math.abs(dx) <= Math.abs(dy) * 1.35) return;
      swipe = null;
      suppressClick = true;
      revealed = dx < 0 ? row : null;
      row.classList.toggle("timing-revealed", revealed === row);
    }
    row.addEventListener("pointermove", revealSwipe);
    row.addEventListener("pointerup", event => {
      revealSwipe(event);
      swipe = null;
    });
    row.addEventListener("click", event => {
      if (!suppressClick || !event.detail) return;
      suppressClick = false;
      event.preventDefault();
      event.stopPropagation();
    }, true);
    return start.trigger;

    function timeControl(name, optional) {
      const input = element("input", "", { type: "hidden", name });
      const trigger = button("", () => timeWheel(t[name], input.value, optional, t, show));
      trigger.className = "timing-time";
      trigger.setAttribute("aria-haspopup", "dialog");
      function show(value) {
        input.value = value;
        trigger.textContent = value || (optional ? "" : "–:–");
        trigger.setAttribute("aria-label", [t[name], value].filter(Boolean).join(" · "));
        if (row.cells.length) updateDates();
      }
      show(initial[name] || "");
      return { input, trigger };
    }
  }
  form.addEventListener("submit", event => event.preventDefault());
  function saveRows() {
    const missing = eventRows().find(row => !row.querySelector('[name="start"]').value);
    if (missing) { missing.querySelector(".timing-time").click(); return; }
    if (!form.reportValidity()) return;
    const rows = eventRows().map(row => {
      const values = Object.fromEntries(Array.from(row.querySelectorAll("input"), field =>
        [field.name, field.type === "checkbox" ? field.checked : field.value]));
      return values;
    });
    void start({ action: "save", rows, zone: zone.value, preliminary: preliminary.checked });
  }
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
