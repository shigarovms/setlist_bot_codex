import { withBotLabels } from "./bot-labels.js";
const words = withBotLabels("workspace-export", {
  ru: {
    language: "Язык", audience: "Для кого",
    font: "Шрифт", size: "Размер, pt", align: "Выравнивание",
    mode: "Подбор оформления", fixed: "Размер шрифта", fit: "Количество страниц",
    invalidPages: "Укажите целое число страниц.", counting: "Считаем страницы…",
    pageCount: count => `Страниц: ${count}`,
    chosenSize: size => `Размер шрифта: ${size} pt`,
    minimumPages: count => `При минимальном кегле получится ${count} стр.`,
    small: "Текст может быть мелким на сцене.", very_small: "Текст может быть очень мелким на сцене.",
    recommended: count => `Для чтения со сцены рекомендуем ${count} стр.`,
    left: "По левому краю", center: "По центру",
    settings: "Сохранить оформление", download: "Скачать PDF", send: "Отправить в личный чат",
    downloadMusicians: "Скачать PDF для музыкантов", downloadLights: "Скачать PDF для световика",
    waiting: "Запрос обрабатывается…", ready: "PDF готовы к скачиванию.", saved: "Оформление сохранено.",
    sent: "PDF отправлены в личный чат с ботом.",
    retry: "Проверить результат",
  },
  en: {
    language: "Language", audience: "Audience",
    font: "Font", size: "Size, pt", align: "Alignment",
    mode: "Sizing", fixed: "Font size", fit: "Page count",
    invalidPages: "Enter a whole number of pages.", counting: "Counting pages…",
    pageCount: count => `Pages: ${count}`,
    chosenSize: size => `Font size: ${size} pt`,
    minimumPages: count => `The smallest font still needs ${count} pages.`,
    small: "Text may be small on stage.", very_small: "Text may be very small on stage.",
    recommended: count => `Recommended for reading on stage: ${count} pages.`,
    left: "Left", center: "Center",
    settings: "Save appearance", download: "Download PDF", send: "Send to private chat",
    downloadMusicians: "Download musician PDF", downloadLights: "Download lighting PDF",
    waiting: "Processing your request…", ready: "The PDFs are ready to download.", saved: "Appearance saved.",
    sent: "The bot sent the PDFs to your private chat.",
    retry: "Check result",
  },
});

function el(tag, value, attributes = {}) {
  const node = document.createElement(tag);
  node.textContent = value;
  for (const [key, attr] of Object.entries(attributes)) node.setAttribute(key, attr);
  return node;
}

export function pdfTitle(language) { return words[language].pdf; }

function select(container, id, label, choices, selected) {
  const node = el("select", "", { id });
  for (const [value, name] of choices) node.append(el("option", name, { value }));
  node.value = selected;
  container.append(el("label", label, { for: id }), node);
  return node;
}

export function pdfAppearanceFields(container, options, language, prefix = "workspace-pdf") {
  const t = words[language];
  const font = select(container, `${prefix}-font`, t.font,
    Object.entries(options.fonts), options.settings.font);
  const mode = select(container, `${prefix}-mode`, t.mode,
    [["size", t.fixed], ["pages", t.fit]], options.settings.pages ? "pages" : "size");
  const manual = el("div", "", { class: "import-fields" });
  const fitted = el("div", "", { class: "import-fields" });
  const sizes = Array.from({ length: 17 }, (_, index) => [String(index + 24), String(index + 24)]);
  const size = select(manual, `${prefix}-size`, t.size, sizes, String(options.settings.size));
  const pages = el("input", "", { id: `${prefix}-pages`, type: "number", inputmode: "numeric",
    min: "1", max: String(options.max_pages ?? 200), step: "1", required: "" });
  pages.value = options.settings.pages ?? 1;
  fitted.append(el("label", t.fit, { for: pages.id }), pages);
  container.append(manual, fitted);
  const align = select(container, `${prefix}-align`, t.align,
    [["left", t.left], ["center", t.center]], options.settings.align);
  mode.addEventListener("change", updateMode);
  updateMode();
  return () => {
    const appearance = { font: font.value, size: Number(size.value), align: align.value };
    if (mode.value === "size") return appearance;
    if (!pages.checkValidity()) return null;
    return { ...appearance, pages: pages.valueAsNumber };
  };
  function updateMode() {
    manual.hidden = mode.value === "pages";
    fitted.hidden = pages.disabled = !manual.hidden;
  }
}

export function mountPdfExport({ root, dialog, message, options, language, version, revision, request }) {
  const t = words[language];
  const fields = el("fieldset", "", { class: "import-fields" });
  const controls = el("div", "", { class: "toolbar" });
  const appearance = el("details", "");
  appearance.append(el("summary", t.options));
  const downloads = el("div", "", { class: "toolbar workspace-pdf-downloads" });
  const check = el("button", t.retry, { hidden: "", type: "button" });
  const urls = [];
  let pending = null, working = false;

  const pdfLanguage = select(fields, "workspace-pdf-language", t.language,
    [["ru", "Русский"], ["en", "English"]], options.language);
  const audience = select(fields, "workspace-pdf-audience", t.audience,
    [["musicians", t.musicians], ["lights", t.lights], ["all", t.all]], "musicians");
  const readAppearance = pdfAppearanceFields(appearance, options, language);
  const pagePreview = el("div", "", { class: "pdf-page-preview", role: "status" });
  let previewTimer, previewSerial = 0;
  function selection() {
    const settings = audience.value === "lights" ? options.settings : readAppearance();
    return settings && { audience: audience.value, language: pdfLanguage.value, version, revision, settings };
  }
  const save = el("button", t.settings, { type: "button" });
  save.addEventListener("click", () => {
    const settings = readAppearance();
    if (settings) begin({ action: "settings", settings });
    else message.textContent = t.invalidPages;
  });
  appearance.append(save);
  const methods = window.Telegram?.WebApp?.initData ? ["telegram", "download"] : ["download", "telegram"];
  for (const [index, delivery] of methods.entries()) {
    const button = el("button", t[delivery === "telegram" ? "send" : "download"],
      { type: "button", ...(index === 0 ? { class: "primary" } : {}) });
    button.addEventListener("click", () => {
      const current = selection();
      if (current) begin({ ...current, action: "generate", delivery });
      else message.textContent = t.invalidPages;
    });
    controls.append(button);
  }
  fields.append(appearance, pagePreview, controls);
  root.append(fields, check, downloads);

  function clearFiles() {
    for (const url of urls.splice(0)) URL.revokeObjectURL(url);
    downloads.replaceChildren();
  }
  dialog.addEventListener("close", () => {
    clearTimeout(previewTimer);
    previewSerial++;
    clearFiles();
  }, { once: true });
  for (const node of [pdfLanguage, audience, appearance]) node.addEventListener("change", () => {
    clearFiles();
    message.textContent = "";
    schedulePreview();
  });
  appearance.addEventListener("input", event => {
    if (event.target.type === "number") schedulePreview();
  });
  check.addEventListener("click", process);
  schedulePreview();

  function schedulePreview() {
    clearTimeout(previewTimer);
    const serial = ++previewSerial;
    appearance.hidden = audience.value === "lights";
    const current = selection();
    pagePreview.textContent = current ? t.counting : t.invalidPages;
    if (!current) return;
    previewTimer = setTimeout(async () => {
      if (!dialog.open || !pagePreview.isConnected) return;
      try {
        const result = await request("export_preview", current, "POST");
        if (serial !== previewSerial || !dialog.open || !pagePreview.isConnected) return;
        pagePreview.replaceChildren();
        for (const [target, count] of Object.entries(result.page_counts)) {
          const label = current.audience === "all" ? `${t[target]} · ` : "";
          pagePreview.append(el("p", label + t.pageCount(count)));
        }
        if (result.font_size) pagePreview.append(el("p", t.chosenSize(result.font_size)));
        if (result.page_counts.musicians > current.settings.pages)
          pagePreview.append(el("p", t.minimumPages(result.page_counts.musicians)));
        if (result.legibility) pagePreview.append(
          el("p", t[result.legibility], { class: "pdf-stage-warning" }),
          el("p", t.recommended(result.recommended_pages)),
        );
      } catch (error) {
        if (serial === previewSerial && dialog.open && pagePreview.isConnected)
          pagePreview.textContent = error.message;
      }
    }, 180);
  }

  function begin(body) {
    if (pending) return;
    clearFiles();
    pending = { payload: { ...body, request_id: crypto.randomUUID() } };
    void process();
  }
  async function process() {
    if (!pending || working) return;
    working = fields.disabled = true;
    check.hidden = true;
    message.textContent = t.waiting;
    try {
      let result = pending.receipt ? await request("catalog_status", { request: pending.receipt })
        : await request("export_action", pending.payload, "POST");
      pending.receipt = result.request;
      for (let i = 0; result.status === "pending" && i < 8; i++) {
        await new Promise(resolve => setTimeout(resolve, 1000));
        if (!downloads.isConnected || !dialog.open) return;
        result = await request("catalog_status", { request: pending.receipt });
      }
      if (result.status === "pending") return;
      if (result.status === "failed") { pending = null; message.textContent = result.message; return; }
      if (result.delivery === "telegram") message.textContent = t.sent;
      else if (result.export) {
        const targets = result.audience === "all" ? ["musicians", "lights"] : [result.audience];
        const files = await Promise.all(targets.map(audience => request("export_download", {
          request: result.request, audience,
        })));
        if (!downloads.isConnected || !dialog.open) return;
        for (const [index, file] of files.entries()) {
          const url = URL.createObjectURL(file.blob);
          urls.push(url);
          downloads.append(el("a", targets[index] === "musicians" ? t.downloadMusicians : t.downloadLights, {
            href: url, download: file.name,
          }));
        }
        message.textContent = t.ready;
        if (files.length === 1) downloads.firstElementChild.click();
      } else message.textContent = t.saved;
      pending = null;
    } catch (error) {
      if (!pending?.receipt && error.data && !["busy", "unavailable"].includes(error.data.error)) pending = null;
      message.textContent = error.message;
    } finally {
      working = false;
      fields.disabled = !!pending;
      check.hidden = !pending;
    }
  }
}
