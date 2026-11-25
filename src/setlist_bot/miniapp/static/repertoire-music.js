import { fillButtonLabel, withBotLabels } from "./bot-labels.js";
import { clock } from "./model.js";

const words = withBotLabels("repertoire-music", {
  ru: {
    query: "Имя исполнителя или ссылка на его страницу",
    start: "Начать поиск", waiting: "Поиск идёт в фоне и может занять 20–30 минут. Можно вернуться позже.",
    cancel: "Отменить поиск", choose: "Выберите исполнителя",
    failed: "Не удалось найти песни. Уточните исполнителя и попробуйте снова.",
    cancelled: "Поиск отменён.", done: n => `Добавлено песен: ${n}.`,
    select: "Отбор песен", count: (selected, total) => `Выбрано ${selected} из ${total}`,
    partial: "Часть источников недоступна. Показаны найденные песни.",
    selectedOnly: "Только выбранные",
    duration: "Длительность ММ:СС", save: "Указать длительность", missing: "Нет длительности",
    needDuration: title => `Укажите длительность песни «${title}» или снимите отметку.`,
    empty: "Нет выбранных песен.",
    preview: "Предпросмотр импорта", duplicates: n => `Уже в каталоге: ${n}.`,
    bpm: ({ found, missing, conflicts }) => `BPM найден: ${found}. Нет данных: ${missing}. Расхождения: ${conflicts}.`,
    noNew: "Все выбранные песни уже есть в каталоге.",
    pending: "Запрос обрабатывается…", check: "Проверить результат",
  },
  en: {
    query: "Artist name or a link to their page",
    start: "Start search", waiting: "Search runs in the background and can take up to 20–30 minutes. You can come back later.",
    cancel: "Cancel search", choose: "Choose the artist",
    failed: "Couldn't find songs. Check the artist and try again.",
    cancelled: "Search cancelled.", done: n => `Added ${n} songs.`,
    select: "Select songs", count: (selected, total) => `${selected} of ${total} selected`,
    partial: "Some sources are unavailable. Showing the songs we found.",
    selectedOnly: "Selected only",
    duration: "Duration MM:SS", save: "Set duration", missing: "Duration needed",
    needDuration: title => `Set the duration for “${title}” or deselect it.`,
    empty: "No songs selected.",
    preview: "Preview import", duplicates: n => `Already in the repertoire: ${n}.`,
    bpm: ({ found, missing, conflicts }) => `BPM found: ${found}. Missing: ${missing}. Conflicts: ${conflicts}.`,
    noNew: "All selected songs are already in the repertoire.",
    pending: "Processing your request…", check: "Check result",
  },
});

const PAGE_SIZE = 20;

function el(tag, text, attrs = {}) {
  const node = document.createElement(tag);
  node.textContent = text;
  for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value);
  return node;
}

export function repertoireMusic({ root, language, band, search, read, request }) {
  const t = words[language];
  const panel = el("div", "");
  const status = el("p", "", { role: "status", "aria-live": "polite" });
  const check = el("button", t.check, { type: "button", hidden: "" });
  root.append(el("h2", t.title), status, check, panel);
  let job = search;
  let attempt = null, pending = false, refreshing = null, timer;
  let selectedOnly = false, page = 0;

  function button(label, click, primary = false) {
    const control = el("button", label, { type: "button" });
    if (primary) control.className = "primary";
    control.addEventListener("click", click);
    return control;
  }

  function startForm() {
    const form = el("form", "", { class: "workspace-search" });
    const input = el("input", "", { id: "music-query", maxlength: 300, required: "", value: job?.query || band });
    form.append(el("label", t.query, { for: input.id }), input, el("button", t.start, { type: "submit", class: "primary" }));
    form.addEventListener("submit", event => {
      event.preventDefault();
      void mutate({ action: "start", query: input.value });
    });
    return form;
  }

  function action(action, values = {}) {
    return mutate({ action, job: job.id, version: job.version, ...values });
  }

  function selection() {
    const included = job.tracks.filter(row => row.selected);
    const visible = selectedOnly ? included : job.tracks;
    const heading = el("h3", t.select);
    panel.append(heading, el("p", t.count(included.length, job.tracks.length)));
    if (job.has_partial_results) panel.append(el("p", t.partial));
    const controls = el("div", "", { class: "toolbar" });
    const filter = el("input", "", { type: "checkbox", id: "music-selected-only" });
    filter.checked = selectedOnly;
    filter.addEventListener("change", () => { selectedOnly = filter.checked; page = 0; draw(); });
    const filterLabel = el("label", "", { for: filter.id });
    filterLabel.append(filter, document.createTextNode(t.selectedOnly));
    controls.append(button(t.all, () => action("all", { include: true })),
      button(t.none, () => action("all", { include: false })),
      filterLabel);
    panel.append(controls);
    showPages(visible, trackRow);
    if (!included.length) panel.append(el("p", t.empty));
    const preview = button(t.previewAction, () => {
      const missing = included.find(row => !row.seconds);
      if (!missing) { void action("preview"); return; }
      page = Math.floor(visible.indexOf(missing) / PAGE_SIZE);
      draw();
      status.textContent = t.needDuration(missing.title);
      document.getElementById(`track-time-${missing.index}`)?.focus();
    }, true);
    preview.disabled = !included.length;
    panel.append(preview, button(t.cancel, () => action("cancel")));
  }

  function trackRow(row) {
    const item = el("li", "");
    const label = el("label", "", { class: "music-track" });
    const selected = el("input", "", { type: "checkbox" });
    selected.checked = row.selected;
    selected.addEventListener("change", () => action("toggle", { index: row.index }));
    label.append(selected, el("span", row.title), el("span", row.seconds ? clock(row.seconds) : t.missing, { class: "workspace-duration" }));
    item.append(label);
    if (row.selected && !row.seconds) {
      const form = el("form", "", { class: "workspace-search" });
      const input = el("input", "", { id: `track-time-${row.index}`, placeholder: "03:20", maxlength: 20,
        required: "", pattern: "[0-9]{1,5}:[0-5][0-9]", "aria-label": `${row.title} · ${t.duration}` });
      form.append(input, el("button", t.save, { type: "submit" }));
      form.addEventListener("submit", event => {
        event.preventDefault();
        void action("duration", { index: row.index, value: input.value });
      });
      item.append(form);
    }
    return item;
  }

  function showPages(rows, render) {
    const pages = Math.max(1, Math.ceil(rows.length / PAGE_SIZE));
    page = Math.max(0, Math.min(page, pages - 1));
    const list = el("ul", "", { class: "workspace-list" });
    for (const row of rows.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE)) list.append(render(row));
    panel.append(list);
    if (pages <= 1) return;
    const nav = el("nav", "", { class: "workspace-nav" });
    const back = button(t.previous, () => { page--; draw(); });
    const next = button(t.next, () => { page++; draw(); });
    back.disabled = page === 0;
    next.disabled = page + 1 === pages;
    nav.append(back, el("span", `${page + 1} / ${pages}`), next);
    panel.append(nav);
  }

  function draw() {
    panel.replaceChildren();
    clearTimeout(timer);
    switch (job?.status) {
      case "searching":
        panel.append(el("p", t.waiting), button(t.refresh, refresh), button(t.cancel, () => action("cancel")));
        schedule();
        break;
      case "artists": {
        const list = el("ul", "", { class: "workspace-list" });
        job.artists.forEach((artist, index) => {
          const item = el("li", "");
          item.append(button(artist.name, () => action("artist", { index })), el("p", artist.examples.join(" · ")));
          list.append(item);
        });
        panel.append(el("h3", t.choose), list, button(t.cancel, () => action("cancel")));
        break;
      }
      case "choosing": selection(); break;
      case "preview":
        panel.append(el("h3", t.preview), el("p", t.duplicates(job.duplicates)));
        if (job.bpm) panel.append(el("p", t.bpm(job.bpm)));
        showPages(job.songs, row => el("li", `${row.title} · ${clock(row.duration_seconds)}${row.bpm ? ` · ${row.bpm} BPM` : ""}`));
        const commit = fillButtonLabel(t.commit, { count: job.songs.length });
        panel.append(job.songs.length ? button(commit, () => action("commit"), true) : el("p", t.noNew),
          button(t.back, () => action("back")), button(t.cancel, () => action("cancel")));
        break;
      default:
        if (job?.status === "failed") panel.append(el("p", t.failed));
        if (job?.status === "cancelled") panel.append(el("p", t.cancelled));
        if (job?.status === "done") panel.append(el("p", t.done(job.imported)));
        panel.append(startForm());
    }
    panel.inert = !!attempt;
  }

  function schedule() {
    clearTimeout(timer);
    if (job?.status === "searching" && !attempt)
      timer = setTimeout(() => document.hidden ? schedule() : refresh(), 5000);
  }

  function refresh() {
    if (pending) return;
    refreshing ??= read().then(data => {
      if (job?.status !== data.search?.status) page = 0;
      job = data.search;
      draw();
    }).catch(error => {
      status.textContent = error.message;
      schedule();
    }).finally(() => { refreshing = null; });
    return refreshing;
  }

  async function mutate(payload) {
    if (attempt) return;
    attempt = { body: { ...payload, request_id: crypto.randomUUID() } };
    panel.inert = true;
    clearTimeout(timer);
    await refreshing;
    await process();
  }

  async function process() {
    if (pending) return;
    pending = true;
    check.hidden = true;
    status.textContent = t.pending;
    try {
      let result = attempt.receipt
        ? await request("catalog_status", { request: attempt.receipt })
        : await request("music_action", attempt.body, "POST");
      attempt.receipt = result.request;
      for (let i = 0; result.status === "pending" && i < 8; i++) {
        await new Promise(resolve => setTimeout(resolve, 1000));
        result = await request("catalog_status", { request: attempt.receipt });
      }
      if (result.status === "pending") return;
      attempt = null;
      status.textContent = result.status === "failed" ? result.message : "";
    } catch (error) {
      if (!attempt.receipt && error.data && !["busy", "unavailable"].includes(error.data.error)) attempt = null;
      status.textContent = error.message;
    } finally {
      pending = false;
      check.hidden = !attempt;
      if (!attempt) { panel.inert = false; await refresh(); }
    }
  }

  check.addEventListener("click", process);
  draw();
}
