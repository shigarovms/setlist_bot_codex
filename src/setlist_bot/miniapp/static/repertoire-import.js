import { withBotLabels } from "./bot-labels.js";
import { clock } from "./model.js";

const text = withBotLabels("repertoire-import", {
  ru: {
    title: "Импорт песен", source: "Вставьте таблицу из Excel или Google Sheets либо текст CSV",
    header: "Первая строка", auto: "Определить автоматически",

    preview: "Предпросмотр",
    added: "Новые песни", supplements: "Дополнения", duplicates: "Повторы", errors: "Ошибки",
    ready: "Проверьте изменения перед импортом.", nothing: "В таком виде импортировать нечего.",
    working: "Проверяем таблицу…", pending: "Импорт обрабатывается.", check: "Проверить результат",
    success: (added, updated) => `Добавлено песен: ${added}. Дополнено: ${updated}.`,
    fields: { title: "Название", time: "Длительность", lights: "Свет", bpm: "BPM", comment: "Комментарий" },
  },
  en: {
    title: "Import songs", source: "Paste a table from Excel or Google Sheets, or paste CSV text",
    header: "First row", auto: "Detect automatically",

    preview: "Preview",
    added: "New songs", supplements: "Updates", duplicates: "Duplicates", errors: "Errors",
    ready: "Review the changes before importing.", nothing: "This preview has nothing to import.",
    working: "Checking the table…", pending: "Import is being processed.", check: "Check result",
    success: (added, updated) => `Added ${added} songs and updated ${updated}.`,
    fields: { title: "Title", time: "Duration", lights: "Lights", bpm: "BPM", comment: "Notes" },
  },
});

function el(tag, label, attributes = {}) {
  const element = document.createElement(tag);
  element.textContent = label;
  for (const [key, value] of Object.entries(attributes)) element.setAttribute(key, value);
  return element;
}

export function repertoireImport({ root, language, preview, request }) {
  const words = text[language];
  const tools = el("fieldset", "", { class: "import-fields" });
  const source = el("textarea", "", { id: "import-source", rows: "7", maxlength: "100000", required: "" });
  const header = el("select", "", { id: "import-header" });
  for (const [value, label] of [["", words.auto], ["yes", words.withHeader], ["no", words.withoutHeader]])
    header.append(el("option", label, { value }));
  const mapping = el("fieldset", "", { hidden: "" });
  const mapButton = el("button", words.columns, { type: "button", hidden: "" });
  const previewButton = el("button", words.preview, { type: "submit" });
  const form = el("form", "");
  tools.append(el("label", words.source, { for: "import-source" }), source,
    el("label", words.header, { for: "import-header" }), header, mapping, mapButton, previewButton);
  form.append(tools);
  const review = el("div", "");
  const status = el("p", "", { role: "status" });
  const apply = el("button", words.apply, { type: "button", hidden: "", class: "primary" });
  root.append(el("h2", words.title), form, status, apply, review);

  let columns = [], planInput = null, attempt = null, receipt = null;

  function invalidate() {
    planInput = null;
    apply.hidden = true;
    review.replaceChildren();
    status.textContent = "";
  }
  for (const input of [source, header]) input.addEventListener("input", () => {
    invalidate();
    mapping.hidden = mapButton.hidden = true;
    mapping.replaceChildren();
  });
  mapping.addEventListener("change", invalidate);
  mapButton.addEventListener("click", () => {
    invalidate();
    mapping.hidden = false;
    mapButton.hidden = true;
    const selects = Object.entries(words.fields).flatMap(([key, label]) => {
      const select = el("select", "", { id: `import-column-${key}`, "data-field": key });
      select.append(el("option", words.skip, { value: "-1" }));
      columns.forEach((name, index) => select.append(el("option", `${index + 1}. ${name}`, { value: index })));
      return [el("label", label, { for: select.id }), select];
    });
    mapping.replaceChildren(el("legend", words.columns), ...selects);
    mapping.querySelector("select")?.focus();
  });

  form.addEventListener("submit", async event => {
    event.preventDefault();
    invalidate();
    const has_header = header.value === "" ? null : header.value === "yes";
    const selections = Object.fromEntries(
      Array.from(mapping.querySelectorAll("select"), select => [select.dataset.field, Number(select.value)]),
    );
    const input = { source: source.value, has_header, mapping: mapping.hidden ? null : selections };
    tools.disabled = true;
    status.textContent = words.working;
    try {
      const plan = await preview(input);
      columns = plan.columns;
      mapButton.hidden = !mapping.hidden || !columns.length;
      if (plan.ready) planInput = { ...input, fingerprint: plan.fingerprint };
      renderPreview(plan);
      status.textContent = plan.error || (plan.ready ? words.ready : words.nothing);
      apply.hidden = !planInput;
    } catch (error) {
      status.textContent = error.message;
    } finally { tools.disabled = false; }
  });

  function describe(fields) {
    return [fields.duration_seconds && `${words.fields.time}: ${clock(fields.duration_seconds)}`,
      fields.bpm && `BPM: ${fields.bpm}`, fields.lights && `${words.fields.lights}: ${fields.lights}`,
      fields.comment && `${words.fields.comment}: ${fields.comment}`].filter(Boolean).join(" · ");
  }
  function renderPreview(plan) {
    const rows = [
      [words.added, plan.new.map(fields => [fields.title, describe(fields)])],
      [words.supplements, plan.supplements.map(row => [row.title, describe(row.fields)])],
      [words.duplicates, plan.duplicates.map(title => [title])],
      [words.errors, plan.errors.map(row => [`${row.row}. ${row.title}`, row.message])],
    ];
    for (const [label, items] of rows) {
      const section = items.length ? el("details", "") : el("p", `${label} (0)`);
      if (items.length) {
        section.append(el("summary", `${label} (${items.length})`));
        const list = el("ul", "", { class: "workspace-list" });
        for (const [title, details] of items) {
          const row = el("li", title);
          if (details) row.append(el("p", details));
          list.append(row);
        }
        section.append(list);
      }
      review.append(section);
    }
  }

  apply.addEventListener("click", async () => {
    if (!planInput) return;
    attempt ??= { ...planInput, request_id: crypto.randomUUID() };
    tools.disabled = apply.disabled = true;
    status.textContent = words.pending;
    try {
      let result = receipt
        ? await request("catalog_status", { request: receipt })
        : await request("catalog_import", attempt, "POST");
      receipt = result.request;
      for (let i = 0; result.status === "pending" && i < 10; i++) {
        await new Promise(resolve => setTimeout(resolve, 1000));
        result = await request("catalog_status", { request: receipt });
      }
      if (result.status === "pending") return;
      invalidate();
      attempt = receipt = null;
      status.textContent = result.status === "failed" ? result.message : words.success(result.added, result.supplemented);
    } catch (error) {
      if (!receipt && error.data && !["unavailable", "busy"].includes(error.data.error)) {
        invalidate();
        attempt = null;
      }
      status.textContent = error.message;
    } finally {
      tools.disabled = !!attempt;
      apply.disabled = false;
      apply.textContent = attempt ? words.check : words.apply;
    }
  });
}
