import { fillButtonLabel, withBotLabels } from "./bot-labels.js";
const copy = withBotLabels("workspace-performances", {
  ru: {
    trash: "Корзина выступлений", empty: "Корзина пуста.",
    until: "Можно восстановить до", preview: "Предпросмотр", save: "Сохранить",
    reviewCreate: "Сверить и создать", backButton: "Назад", loadingOptions: "Загружаем варианты…",
    other: "Другое", otherCity: "Другой город", ownType: "Укажите тип", ownCity: "Укажите город",
    back: "Изменить", cancel: "Закрыть", check: "Проверить результат", waiting: "Запрос обрабатывается…",
    date: "Дата", title: "Название", city: "Город", performance_type: "Тип", info: "Информация",
    duration: "Длительность", durationHint: "Минуты или часы:минуты:секунды",
    quick: "Ввести одной строкой", quickLabel: "Дата и данные выступления",
    quickHint: "Например: 15.12.2026  Санкт-Петербург  Фест  45 мин",
    lineup: "Состав",
    noLineup: "Без сохранённого состава",
    dateChanged: "При изменении даты участникам нужно будет подтвердить участие заново.",
    deletePrompt: "Выступление, его сет-листы и черновики будут скрыты. Действующие приглашения отменятся. Восстановление доступно 30 дней.",
  },
  en: {
    trash: "Performance trash", empty: "The trash is empty.",
    until: "Can be restored until", preview: "Preview", save: "Save",
    reviewCreate: "Review and create", backButton: "Back", loadingOptions: "Loading options…",
    other: "Other", otherCity: "Another city", ownType: "Enter a type", ownCity: "Enter a city",
    back: "Edit", cancel: "Close", check: "Check result", waiting: "Processing your request…",
    date: "Date", title: "Title", city: "City", performance_type: "Type", info: "Information",
    duration: "Duration", durationHint: "Minutes or hours:minutes:seconds",
    quick: "Enter on one line", quickLabel: "Date and performance details",
    quickHint: "For example: 15.12.2026  London  Festival  45 min",
    lineup: "Lineup",
    noLineup: "Without a saved lineup",
    dateChanged: "After changing the date, performers will need to confirm their participation again.",
    deletePrompt: "The performance, its setlists and drafts will be hidden. Active invitations will be cancelled. You can restore the performance for 30 days.",
  },
});

export function performanceLabel(language, key) { return copy[language][key]; }

function el(tag, value = "", attributes = {}) {
  const node = document.createElement(tag);
  node.textContent = value;
  for (const [key, attr] of Object.entries(attributes)) node.setAttribute(key, attr);
  return node;
}

function duration(seconds) {
  if (!seconds) return "";
  return [Math.floor(seconds / 3600), Math.floor(seconds / 60) % 60, seconds % 60]
    .map(value => String(value).padStart(2, "0")).join(":");
}

export function performanceAction({ language, action, request, readOptions, performance, onDone, dateText }) {
  const t = copy[language];
  const label = action === "restore" ? fillButtonLabel(t.restore, { name: performance.label }) : t[action];
  const button = el("button", label, { type: "button", ...(action === "create" ? { class: "primary" } : {}) });
  button.addEventListener("click", open);
  return button;

  function open() {
    const dialog = el("dialog", "", { class: "shared-tools", "aria-label": label });
    const body = el("div");
    const message = el("p", "", { role: "status" });
    const controls = el("div", "", { class: "toolbar" });
    const check = el("button", t.check, { type: "button", hidden: "" });
    const close = el("button", t.cancel, { type: "button" });
    controls.append(check, close);
    close.addEventListener("click", () => dialog.close());
    dialog.append(el("h2", label), body, message, controls);
    document.body.append(dialog);
    dialog.addEventListener("close", () => dialog.remove(), { once: true });
    let attempt, busy = false;
    const target = performance ? { performance: performance.id, epoch: performance.epoch, version: performance.version } : {};
    const form = el("form", "", { class: "performance-form" });
    const fields = el("fieldset", "", { class: "import-fields" });
    form.append(fields);
    check.addEventListener("click", process);
    dialog.showModal();

    function preview(result) {
      const details = el("dl", "", { class: "song-details" });
      for (const [field, value] of Object.entries(result.fields)) {
        if (!value) continue;
        const formatted = field === "date" ? dateText(value) : field === "duration" ? duration(value) : value;
        details.append(el("dt", t[field]), el("dd", formatted));
      }
      if (action === "create")
        details.append(el("dt", t.lineup), el("dd", result.lineup || t.noLineup));
      body.replaceChildren(details);
      if (result.date_changed) body.append(el("p", t.dateChanged));
      if (action === "delete") body.append(el("p", t.deletePrompt));
      const confirm = el("button", t[action === "update" ? "save" : "confirm"], { type: "button", class: "primary" });
      confirm.addEventListener("click", () => begin({ action: "confirm", preview: result.request }));
      const actions = el("div", "", { class: "performance-actions" });
      actions.append(confirm);
      if (action === "create" || action === "update") {
        const back = el("button", t.back, { type: "button" });
        back.addEventListener("click", () => {
          body.replaceChildren(form);
          close.hidden = action === "create";
        });
        actions.append(back);
      }
      body.append(actions);
      confirm.focus();
    }

    function withCustom(parent, name, options, ownLabel, otherLabel) {
      const id = `workspace-performance-${name}`;
      const select = el("select", "", { id });
      select.append(el("option", "—", { value: "" }));
      for (const [index, option] of options.entries())
        select.append(el("option", option.label, { value: index }));
      select.append(el("option", otherLabel, { value: "other" }));
      const custom = el("input", "", { name, id: `${id}-custom`, maxlength: 200 });
      const extra = el("div", "", { class: "import-fields", hidden: "" });
      extra.append(el("label", ownLabel, { for: custom.id }), custom);
      select.addEventListener("change", () => {
        extra.hidden = select.value !== "other";
        custom.required = !extra.hidden;
        if (custom.required) custom.focus();
      });
      parent.append(el("label", t[name], { for: id }), select, extra);
      return () => select.value === "other" ? custom.value : options[select.value]?.value ?? "";
    }

    function showForm(options) {
      const inputFields = el("div", "", { class: "import-fields" });
      const initial = { ...performance, duration: duration(performance?.planned_seconds), performance_type: performance?.type };
      const choices = {};
      for (const [name, max] of [["date", 10], ["title", 200], ["city", 200], ["performance_type", 200], ["duration", 80], ["info", 2000]]) {
        if (options && (name === "city" || name === "performance_type")) {
          const city = name === "city";
          const values = city ? options.cities.map(value => ({ value, label: value }))
            : Object.entries(options.types).map(([key, value]) => ({ value, label: t[key] }));
          choices[name] = withCustom(inputFields, name, values,
            city ? t.ownCity : t.ownType, city ? t.otherCity : t.other);
          continue;
        }
        const id = `workspace-performance-${name}`;
        const attributes = { id, name, maxlength: max };
        const control = name === "info" ? el("textarea", "", { ...attributes, rows: 4 })
          : el("input", "", { ...attributes, type: name === "date" ? "date" : "text" });
        control.value = initial[name] || "";
        if (name === "date") control.required = true;
        if (name === "duration") {
          control.placeholder = t.durationHint;
          control.setAttribute("aria-describedby", "workspace-duration-hint");
        }
        inputFields.append(el("label", t[name], { for: id }), control);
        if (name === "duration") inputFields.append(el("small", t.durationHint, { id: "workspace-duration-hint" }));
      }
      fields.append(inputFields);
      let quick;
      if (action === "create") {
        const toggle = el("input", "", { type: "checkbox", id: "workspace-quick-performance" });
        const quickFields = el("div", "", { hidden: "" });
        quick = el("input", "", { id: "workspace-quick-input", maxlength: "4000", "aria-describedby": "workspace-quick-hint" });
        quickFields.append(el("label", t.quickLabel, { for: quick.id }), quick,
          el("small", t.quickHint, { id: "workspace-quick-hint" }));
        toggle.addEventListener("change", () => {
          inputFields.hidden = toggle.checked;
          for (const input of inputFields.querySelectorAll("input, select, textarea"))
            input.disabled = toggle.checked;
          quickFields.hidden = !toggle.checked;
          quick.required = toggle.checked;
          if (toggle.checked) quick.focus();
        });
        const toggleLabel = el("label", "", { for: toggle.id });
        toggleLabel.append(toggle, document.createTextNode(t.quick));
        fields.prepend(toggleLabel, quickFields);
      }
      let lineup;
      if (options?.lineups.length) {
        lineup = el("select", "", { id: "workspace-performance-lineup" });
        lineup.append(el("option", t.noLineup, { value: "" }));
        for (const row of options.lineups) lineup.append(el("option", row.name, { value: row.id }));
        fields.append(el("label", t.lineup, { for: lineup.id }), lineup);
      }
      const actions = el("div", "", { class: "performance-actions" });
      actions.append(el("button", action === "create" ? t.reviewCreate : t.preview, { type: "submit", class: "primary" }));
      if (action === "create") {
        const back = el("button", t.backButton, { type: "button" });
        back.addEventListener("click", () => dialog.close());
        actions.append(back);
        close.hidden = true;
      }
      fields.append(actions);
      form.addEventListener("submit", event => {
        event.preventDefault();
        const values = quick?.required ? { quick: quick.value } : Object.fromEntries(new FormData(form));
        if (action === "create") {
          if (!quick.required) {
            values.city = choices.city();
            values.performance_type = choices.performance_type();
          }
          values.lineup = lineup?.value || (quick.required ? "named" : "none");
        }
        begin({ action, ...target, ...values });
      });
      body.replaceChildren(form);
      fields.querySelector("input[type=date]")?.focus();
    }

    function enableBody(enabled) {
      close.hidden = enabled && action === "create" && body.contains(form);
      fields.disabled = !enabled;
      for (const control of body.querySelectorAll("button, select")) {
        if (!enabled) {
          control.dataset.wasDisabled = String(control.disabled);
          control.disabled = true;
        } else if (control.dataset.wasDisabled !== undefined) {
          control.disabled = control.dataset.wasDisabled === "true";
          delete control.dataset.wasDisabled;
        }
      }
    }

    function begin(payload) {
      if (attempt) return;
      attempt = { payload: { ...payload, request_id: crypto.randomUUID() } };
      enableBody(false);
      void process();
    }

    async function process() {
      if (!attempt || busy) return;
      busy = true;
      check.hidden = true;
      message.textContent = t.waiting;
      try {
        let result = attempt.receipt ? await request("catalog_status", { request: attempt.receipt })
          : await request("performance_action", attempt.payload, "POST");
        attempt.receipt = result.request;
        for (let index = 0; result.status === "pending" && index < 8; index++) {
          await new Promise(resolve => setTimeout(resolve, 1000));
          if (!dialog.open) return;
          result = await request("catalog_status", { request: attempt.receipt });
        }
        if (result.status === "pending" || !dialog.open) return;
        const payload = attempt.payload;
        attempt = null;
        if (result.status === "failed") { message.textContent = result.message; return; }
        message.textContent = "";
        if (payload.action === "confirm") {
          dialog.close();
          await onDone(result);
        } else {
          enableBody(true);
          preview(result);
        }
      } catch (error) {
        if (!attempt?.receipt && error.data && !["busy", "unavailable"].includes(error.data.error)) attempt = null;
        message.textContent = error.message;
      } finally {
        busy = false;
        if (!attempt) enableBody(true);
        check.hidden = !attempt;
      }
    }

    if (action === "create") {
      message.textContent = t.loadingOptions;
      readOptions().then(options => {
        if (!dialog.open) return;
        message.textContent = "";
        showForm(options);
      }).catch(error => { message.textContent = error.message; });
    } else if (action === "update") showForm();
    else begin({ action, ...target });
  }
}
