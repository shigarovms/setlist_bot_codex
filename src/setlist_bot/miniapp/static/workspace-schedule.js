import { pollRequest, requestDialog } from "./workspace-request-dialog.js";

const words = {
  ru: {
    busy: "Запрос обрабатывается…", close: "Закрыть", check: "Проверить результат", done: "Изменения сохранены.",
    busyTitle: "Моя занятость", calendar: "Подписка на календарь", unavailable: "Подписка на календарь пока недоступна.",
    noPeriods: "Даты занятости пока не указаны.", addPeriod: "Добавить занятость", removePeriod: "Убрать занятость",
    from: "Первый день", to: "Последний день, если их несколько", save: "Сохранить", confirm: "Подтвердить",
    personal: "Мои подтверждённые участия", all: "Все мои коллективы", inaccessible: "Коллектив недоступен",
    enabled: "Подписка включена", disabled: "Подписка выключена",
    create: "Создать ссылку", reveal: "Показать ссылку", rotate: "Заменить ссылку", disable: "Отключить",
    revoke: "Прежняя ссылка перестанет работать.", link: "Ссылка ICS", copy: "Скопировать ссылку",
    copied: "Ссылка скопирована.", copyManually: "Скопируйте выделенный адрес вручную.",
    instructions: "Добавьте ссылку как подписку по URL в приложении календаря.",
    restore: "Восстановить прежнюю ссылку", legacy: "Введите сохранённую ранее ссылку или создайте новую.",
    previousLink: "Прежняя ссылка ICS",
  },
  en: {
    busy: "Processing your request…", close: "Close", check: "Check result", done: "Changes saved.",
    busyTitle: "My availability", calendar: "Calendar subscription", unavailable: "Calendar subscriptions are currently unavailable.",
    noPeriods: "You have no unavailable dates yet.", addPeriod: "Add unavailable dates", removePeriod: "Remove unavailable dates",
    from: "First day", to: "Last day, if more than one", save: "Save", confirm: "Confirm",
    personal: "My confirmed bookings", all: "All my bands", inaccessible: "Band unavailable",
    enabled: "Subscription enabled", disabled: "Subscription disabled",
    create: "Create link", reveal: "Show link", rotate: "Replace link", disable: "Disable",
    revoke: "The previous link will stop working.", link: "ICS link", copy: "Copy link",
    copied: "Link copied.", copyManually: "Copy the selected address manually.",
    instructions: "Add this link as a calendar subscription by URL.",
    restore: "Restore previous link", legacy: "Enter your saved link or create a new one.",
    previousLink: "Previous ICS link",
  },
};

export function scheduleLabel(language, key) { return words[language][key]; }

function el(tag, text = "", attrs = {}) {
  const node = document.createElement(tag);
  node.textContent = text;
  for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value);
  return node;
}

function button(text, click, primary = false) {
  const node = el("button", text, { type: "button", ...(primary ? { class: "primary" } : {}) });
  node.addEventListener("click", click);
  return node;
}

function field(root, label, id, attrs) {
  const input = el("input", "", { id, ...attrs });
  root.append(el("label", label, { for: id }), input);
  return input;
}

export async function showScheduleSettings({ root, section, language, dateText, request, refresh, status }) {
  const t = words[language];
  const operation = "schedule_action";
  const modal = title => requestDialog({ title, labels: t, operation, request, refresh, status });
  const availability = section === "busy";
  const listAction = availability ? "busy_list" : "calendar_list";
  const body = el("div");
  root.append(el("h2", t[availability ? "busyTitle" : "calendar"]), body);

  let pending;
  async function load() {
    pending ??= { api: request, payload: { action: listAction, request_id: crypto.randomUUID() } };
    body.replaceChildren(el("p", t.busy, { role: "status" }));
    try {
      const result = await pollRequest(pending, operation, () => body.isConnected);
      if (!result) return;
      if (result.status === "pending") {
        body.append(button(t.check, load));
        return;
      }
      pending = null;
      if (result.status === "failed") throw new Error(result.message);
      body.replaceChildren();
      if (availability) renderBusy(result.periods);
      else renderCalendars(result);
    } catch (error) {
      if (error.data?.error === "input_expired") pending = null;
      body.replaceChildren(el("p", error.message), button(t.check, load));
    }
  }

  function dates(row) {
    return [row.starts_on, row.ends_on].filter((value, i, values) => i === 0 || value !== values[0])
      .map(dateText).join(" – ");
  }

  function addPeriod() {
    const panel = modal(t.addPeriod);
    const form = el("form", "", { class: "import-fields" });
    const start = field(form, t.from, "busy-starts", { type: "date", required: "" });
    const end = field(form, t.to, "busy-ends", { type: "date" });
    start.addEventListener("change", () => { end.min = start.value; });
    const submit = el("button", t.save, { type: "submit", class: "primary" });
    form.append(submit);
    panel.body.append(form);
    form.addEventListener("submit", event => {
      event.preventDefault();
      panel.send(submit, { action: "busy_add", starts_on: start.value, ends_on: end.value });
    });
  }

  function renderBusy(rows) {
    body.append(button(t.addPeriod, addPeriod, true));
    if (!rows.length) body.append(el("p", t.noPeriods));
    const list = el("ul", "", { class: "workspace-list" });
    for (const row of rows) {
      const item = el("li");
      item.append(el("p", dates(row)), button(t.removePeriod, () => {
        const panel = modal(t.removePeriod);
        panel.body.append(el("p", dates(row)), button(t.confirm,
          event => panel.send(event.currentTarget, { action: "busy_remove", period: row.id }), true));
      }));
      list.append(item);
    }
    body.append(list);
  }

  function details(row, action) {
    const panel = modal(t[action]);
    const target = { subscription: row.id, version: row.version };
    const scopeName = row.name || t[row.scope] || t.inaccessible;
    const mutation = ["create", "rotate", "disable"].includes(action);

    function showUrl(result, changed) {
      panel.body.replaceChildren(el("p", t.instructions));
      const url = field(panel.body, t.link, "calendar-link", { readonly: "" });
      url.value = result.url;
      panel.body.append(button(t.copy, async () => {
        try {
          await navigator.clipboard.writeText(url.value);
          panel.message.textContent = t.copied;
        } catch {
          url.focus();
          url.select();
          panel.message.textContent = t.copyManually;
        }
      }, true));
      if (changed) panel.dialog.addEventListener("close", () => void refresh(), { once: true });
    }

    function restore() {
      const form = el("form", "", { class: "import-fields" });
      const input = field(form, t.previousLink, "calendar-restore", {
        type: "url", required: "", maxlength: "512", autocomplete: "off", spellcheck: "false",
      });
      const submit = el("button", t.restore, { type: "submit", class: "primary" });
      form.append(submit);
      panel.body.replaceChildren(form);
      input.focus();
      form.addEventListener("submit", event => {
        event.preventDefault();
        panel.send(submit, { ...target, action: "calendar_restore", url: input.value }, request,
          result => showUrl(result, true));
      });
    }

    const trigger = button(mutation ? t.confirm : t.reveal, event => send(event.currentTarget), true);
    function send(from) {
      panel.send(from, {
        ...(action === "create" ? { scope: row.scope, version: row.version } : target),
        action: `calendar_${action}`,
      }, request, async result => {
        if (result.url) showUrl(result, mutation);
        else if (result.legacy) {
          panel.body.replaceChildren(el("p", t.legacy), button(t.restore, restore, true),
            button(t.rotate, () => { panel.dialog.close(); details(row, "rotate"); }));
        } else {
          panel.dialog.close();
          await refresh();
          status(t.done);
        }
      });
    }
    panel.body.append(el("p", scopeName));
    if (action === "rotate" || action === "disable") panel.body.append(el("p", t.revoke));
    panel.body.append(trigger);
    if (action === "reveal") send(trigger);
  }

  function renderCalendars(result) {
    if (!result.enabled) {
      body.append(el("p", t.unavailable));
      return;
    }
    const list = el("ul", "", { class: "workspace-list" });
    for (const row of result.scopes) {
      const item = el("li");
      item.append(el("h3", row.name || t[row.scope] || t.inaccessible),
        el("p", t[row.enabled ? "enabled" : "disabled"]));
      const actions = el("div", "", { class: "toolbar" });
      const keys = !row.enabled ? ["create"] : row.available ? ["reveal", "rotate", "disable"] : ["disable"];
      for (const key of keys) actions.append(button(t[key], () => details(row, key), key === "create" || key === "reveal"));
      item.append(actions);
      list.append(item);
    }
    body.append(list);
  }
  await load();
}
