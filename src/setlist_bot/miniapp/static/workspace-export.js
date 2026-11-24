const words = {
  ru: {
    pdf: "Экспорт PDF", language: "Язык", audience: "Для кого", musicians: "Для музыкантов",
    lights: "Для световика", all: "Для всех · два PDF", font: "Шрифт", size: "Размер, pt", align: "Выравнивание",
    left: "По левому краю", center: "По центру", options: "Оформление PDF для музыкантов",
    settings: "Сохранить оформление", download: "Скачать PDF", send: "Отправить в личный чат",
    downloadMusicians: "Скачать PDF для музыкантов", downloadLights: "Скачать PDF для световика",
    waiting: "Запрос обрабатывается…", ready: "PDF готовы к скачиванию.", saved: "Оформление сохранено.",
    sent: "PDF отправлены в личный чат с ботом.",
    retry: "Проверить результат",
  },
  en: {
    pdf: "Export PDF", language: "Language", audience: "Audience", musicians: "For musicians",
    lights: "For lighting", all: "For everyone · two PDFs", font: "Font", size: "Size, pt", align: "Alignment",
    left: "Left", center: "Center", options: "Musician PDF appearance",
    settings: "Save appearance", download: "Download PDF", send: "Send to private chat",
    downloadMusicians: "Download musician PDF", downloadLights: "Download lighting PDF",
    waiting: "Processing your request…", ready: "The PDFs are ready to download.", saved: "Appearance saved.",
    sent: "The bot sent the PDFs to your private chat.",
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
  const sizes = Array.from({ length: 17 }, (_, index) => [String(index + 24), String(index + 24)]);
  const size = select(container, `${prefix}-size`, t.size, sizes, String(options.settings.size));
  const align = select(container, `${prefix}-align`, t.align,
    [["left", t.left], ["center", t.center]], options.settings.align);
  return () => ({ font: font.value, size: Number(size.value), align: align.value });
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
  const save = el("button", t.settings, { type: "button" });
  save.addEventListener("click", () => begin({ action: "settings", settings: readAppearance() }));
  appearance.append(save);
  const methods = window.Telegram?.WebApp?.initData ? ["telegram", "download"] : ["download", "telegram"];
  for (const [index, delivery] of methods.entries()) {
    const button = el("button", t[delivery === "telegram" ? "send" : "download"],
      { type: "button", ...(index === 0 ? { class: "primary" } : {}) });
    button.addEventListener("click", () => begin({ action: "generate", audience: audience.value,
      language: pdfLanguage.value, version, revision, delivery }));
    controls.append(button);
  }
  fields.append(appearance, controls);
  root.append(fields, check, downloads);

  function clearFiles() {
    for (const url of urls.splice(0)) URL.revokeObjectURL(url);
    downloads.replaceChildren();
  }
  dialog.addEventListener("close", clearFiles, { once: true });
  for (const node of [pdfLanguage, audience]) node.addEventListener("change", () => {
    clearFiles();
    message.textContent = "";
  });
  check.addEventListener("click", process);

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
