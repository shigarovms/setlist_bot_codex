const copy = {
  ru: {
    create: "Создать выступление", update: "Параметры выступления", delete: "Переместить в корзину",
    restore: "Восстановить выступление", trash: "Корзина выступлений", empty: "Корзина пуста.",
    until: "Можно восстановить до", preview: "Предпросмотр", confirm: "Подтвердить", save: "Сохранить",
    back: "Изменить", cancel: "Закрыть", check: "Проверить результат", waiting: "Запрос обрабатывается…",
    date: "Дата", title: "Название", city: "Город", performance_type: "Тип", info: "Информация",
    duration: "Длительность", durationHint: "Минуты или часы:минуты:секунды",
    quick: "Ввести одной строкой", quickLabel: "Дата и данные выступления",
    quickHint: "Например: 15.12.2026  Санкт-Петербург  Фест  45 мин",
    choose: "Выберите сохранённый состав", lineup: "Состав",
    noLineup: "Без сохранённого состава",
    dateChanged: "При изменении даты участникам нужно будет подтвердить участие заново.",
    deletePrompt: "Выступление, его сет-листы и черновики будут скрыты. Действующие приглашения отменятся. Восстановление доступно 30 дней.",
  },
  en: {
    create: "Create performance", update: "Performance settings", delete: "Move to trash",
    restore: "Restore performance", trash: "Performance trash", empty: "The trash is empty.",
    until: "Can be restored until", preview: "Preview", confirm: "Confirm", save: "Save",
    back: "Edit", cancel: "Close", check: "Check result", waiting: "Processing your request…",
    date: "Date", title: "Title", city: "City", performance_type: "Type", info: "Information",
    duration: "Duration", durationHint: "Minutes or hours:minutes:seconds",
    quick: "Enter on one line", quickLabel: "Date and performance details",
    quickHint: "For example: 15.12.2026  London  Festival  45 min",
    choose: "Choose a saved lineup", lineup: "Lineup",
    noLineup: "Without a saved lineup",
    dateChanged: "After changing the date, performers will need to confirm their participation again.",
    deletePrompt: "The performance, its setlists and drafts will be hidden. Active invitations will be cancelled. You can restore the performance for 30 days.",
  },
};

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

export function performanceAction({ language, action, request, performance, onDone, dateText }) {
  const t = copy[language];
  const button = el("button", t[action], { type: "button", ...(action === "create" ? { class: "primary" } : {}) });
  button.addEventListener("click", open);
  return button;

  function open() {
    const dialog = el("dialog", "", { class: "shared-tools", "aria-label": t[action] });
    const body = el("div");
    const message = el("p", "", { role: "status" });
    const controls = el("div", "", { class: "toolbar" });
    const check = el("button", t.check, { type: "button", hidden: "" });
    const close = el("button", t.cancel, { type: "button" });
    controls.append(check, close);
    close.addEventListener("click", () => dialog.close());
    dialog.append(el("h2", t[action]), body, message, controls);
    document.body.append(dialog);
    dialog.addEventListener("close", () => dialog.remove(), { once: true });
    let attempt, busy = false;
    const target = performance ? { performance: performance.id, epoch: performance.epoch, version: performance.version } : {};
    const form = el("form");
    const fields = el("fieldset", "", { class: "import-fields" });
    form.append(fields);
    check.addEventListener("click", process);
    dialog.showModal();

    function preview(result, retry) {
      if (result.choose_lineup) {
        chooseLineup(result.lineups, retry);
        return;
      }
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
      const actions = el("div", "", { class: "toolbar" });
      actions.append(confirm);
      if (action === "create" || action === "update") {
        const back = el("button", t.back, { type: "button" });
        back.addEventListener("click", () => body.replaceChildren(form));
        actions.append(back);
      }
      body.append(actions);
      confirm.focus();
    }

    function chooseLineup(lineups, retry) {
      const select = el("select", "", { id: "workspace-performance-lineup" });
      for (const { id, name } of [{ id: "", name: t.choose }, ...lineups, { id: "none", name: t.noLineup }])
        select.append(el("option", name, { value: id }));
      const next = el("button", t.preview, { type: "button", class: "primary", disabled: "" });
      select.addEventListener("change", () => { next.disabled = !select.value; });
      next.addEventListener("click", () => begin({ ...retry, lineup: select.value }));
      const back = el("button", t.back, { type: "button" });
      back.addEventListener("click", () => body.replaceChildren(form));
      body.replaceChildren(el("label", t.choose, { for: select.id }), select, next, back);
      select.focus();
    }

    function showForm() {
      const inputFields = el("div", "", { class: "import-fields" });
      const initial = { ...performance, duration: duration(performance?.planned_seconds), performance_type: performance?.type };
      for (const [name, max] of [["date", 10], ["title", 200], ["city", 200], ["performance_type", 200], ["duration", 80], ["info", 2000]]) {
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
          inputFields.querySelector("input[type=date]").required = !toggle.checked;
          quickFields.hidden = !toggle.checked;
          quick.required = toggle.checked;
          if (toggle.checked) quick.focus();
        });
        const toggleLabel = el("label", "", { for: toggle.id });
        toggleLabel.append(toggle, document.createTextNode(t.quick));
        fields.prepend(toggleLabel, quickFields);
      }
      fields.append(el("button", t.preview, { type: "submit", class: "primary" }));
      form.addEventListener("submit", event => {
        event.preventDefault();
        const values = quick?.required ? { quick: quick.value } : Object.fromEntries(new FormData(form));
        begin({ action, ...target, ...values });
      });
      body.replaceChildren(form);
      fields.querySelector("input[type=date]")?.focus();
    }

    function enableBody(enabled) {
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
          preview(result, payload);
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

    if (action === "create" || action === "update") showForm();
    else begin({ action, ...target });
  }
}
