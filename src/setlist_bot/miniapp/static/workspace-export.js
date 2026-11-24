const words = {
  ru: {
    pdf: "Экспорт PDF", language: "Язык", audience: "Для кого", musicians: "Для музыкантов",
    lights: "Для световика", all: "Оба PDF в ZIP", font: "Шрифт", size: "Размер, pt", align: "Выравнивание",
    left: "По левому краю", center: "По центру", options: "Оформление PDF для музыкантов",
    settings: "Сохранить оформление", prepare: "Подготовить файл", download: "Скачать файл",
    waiting: "Запрос обрабатывается…", ready: "Файл готов к скачиванию.", saved: "Оформление сохранено.",
    retry: "Проверить результат",
  },
  en: {
    pdf: "Export PDF", language: "Language", audience: "Audience", musicians: "For musicians",
    lights: "For lighting", all: "Both PDFs in a ZIP", font: "Font", size: "Size, pt", align: "Alignment",
    left: "Left", center: "Center", options: "Musician PDF appearance",
    settings: "Save appearance", prepare: "Prepare file", download: "Download file",
    waiting: "Processing your request…", ready: "The file is ready to download.", saved: "Appearance saved.",
    retry: "Check result",
  },
};

function el(tag, value, attributes = {}) {
  const node = document.createElement(tag);
  node.textContent = value;
  for (const [key, attr] of Object.entries(attributes)) node.setAttribute(key, attr);
  return node;
}

export function pdfTitle(language) { return words[language].pdf; }

export function mountPdfExport({ root, dialog, message, options, language, version, revision, request }) {
  const t = words[language];
  const fields = el("fieldset", "", { class: "import-fields" });
  const controls = el("div", "", { class: "toolbar" });
  const appearance = el("details", "");
  appearance.append(el("summary", t.options));
  const download = el("a", t.download, { hidden: "" });
  const check = el("button", t.retry, { hidden: "", type: "button" });
  let pending = null, working = false, fileUrl;

  function select(container, id, label, choices, selected) {
    const node = el("select", "", { id });
    for (const [value, name] of choices) node.append(el("option", name, { value }));
    node.value = selected;
    container.append(el("label", label, { for: id }), node);
    return node;
  }
  const pdfLanguage = select(fields, "workspace-pdf-language", t.language,
    [["ru", "Русский"], ["en", "English"]], options.language);
  const audience = select(fields, "workspace-pdf-audience", t.audience,
    [["musicians", t.musicians], ["lights", t.lights], ["all", t.all]], "musicians");
  const font = select(appearance, "workspace-pdf-font", t.font,
    Object.entries(options.fonts), options.settings.font);
  const sizes = Array.from({ length: 17 }, (_, index) => [String(index + 24), String(index + 24)]);
  const size = select(appearance, "workspace-pdf-size", t.size, sizes, String(options.settings.size));
  const align = select(appearance, "workspace-pdf-align", t.align,
    [["left", t.left], ["center", t.center]], options.settings.align);
  const save = el("button", t.settings, { type: "button" });
  save.addEventListener("click", () => begin({ action: "settings", settings: { font: font.value, size: Number(size.value), align: align.value } }));
  appearance.append(save);
  const prepare = el("button", t.prepare, { type: "button", class: "primary" });
  prepare.addEventListener("click", () => begin({ action: "generate", audience: audience.value,
    language: pdfLanguage.value, version, revision }));
  controls.append(prepare);
  fields.append(appearance, controls);
  root.append(fields, check, download);

  function clearFile() {
    if (fileUrl) URL.revokeObjectURL(fileUrl);
    fileUrl = undefined;
    download.hidden = true;
  }
  dialog.addEventListener("close", clearFile, { once: true });
  for (const node of [pdfLanguage, audience]) node.addEventListener("change", clearFile);
  check.addEventListener("click", process);

  function begin(body) {
    if (pending) return;
    clearFile();
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
        if (!download.isConnected || !dialog.open) return;
        result = await request("catalog_status", { request: pending.receipt });
      }
      if (result.status === "pending") return;
      if (result.status === "failed") { pending = null; message.textContent = result.message; return; }
      if (result.export) {
        const file = await request("export_download", { request: result.request });
        if (!download.isConnected || !dialog.open) return;
        fileUrl = URL.createObjectURL(file.blob);
        download.download = file.name;
        download.href = fileUrl;
        download.hidden = false;
        message.textContent = t.ready;
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
