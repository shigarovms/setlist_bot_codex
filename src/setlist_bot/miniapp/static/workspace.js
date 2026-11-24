import { clock } from "./model.js";

const copy = {
  ru: {
    bands: "Мои коллективы", upcoming: "Предстоящие", past: "Прошедшие",
    noBands: "У вас пока нет коллективов. Вступить в коллектив можно через его чат в Telegram.",
    noPerformances: "В этом разделе пока нет доступных выступлений.",
    draft: "Открыть личный черновик", restricted: "Черновики доступны администраторам коллектива.",
    previous: "Назад", next: "Дальше", retry: "Повторить", loading: "Загрузка…",
    auth: "Откройте приложение через бота в Telegram.", performances: "К выступлениям",
    login: "Войти через Telegram", logout: "Выйти", openBot: "Открыть бота",
    loginFailed: "Не удалось войти. Попробуйте ещё раз.",
    startBot: "Откройте бота и нажмите «Старт», затем повторите вход.",
    repertoire: "Репертуар", active: "Активные песни", archive: "Архив песен",
    search: "Найти песню", noSongs: "В этом разделе пока нет песен.", noMatches: "Песни не найдены.",
    toSongs: "К списку песен", duration: "Длительность", lights: "Свет", comment: "Комментарий",
    aliases: "Альтернативные названия", archived: "В архиве",
    addSong: "Добавить песню", editSong: "Редактировать", copySong: "Копировать",
    archiveSong: "В архив", restoreSong: "Восстановить песню",
    proposals: "Предложения", noProposals: "Предложений на рассмотрении нет.",
    approve: "Одобрить", reject: "Отклонить", proposedBy: "Автор", forSong: "Песня",
    approved: "Предложение одобрено.", rejected: "Предложение отклонено.",
    pending: "Запрос обрабатывается.", check: "Проверить результат",
  },
  en: {
    bands: "My bands", upcoming: "Upcoming", past: "Past",
    noBands: "You have no bands yet. You can join a band from its Telegram chat.",
    noPerformances: "There are no available performances here yet.",
    draft: "Open private draft", restricted: "Drafts are available to band admins.",
    previous: "Previous", next: "Next", retry: "Try again", loading: "Loading…",
    auth: "Open the app from the bot in Telegram.", performances: "Back to performances",
    login: "Sign in with Telegram", logout: "Sign out", openBot: "Open the bot",
    loginFailed: "Could not sign in. Try again.",
    startBot: "Open the bot and press Start, then sign in again.",
    repertoire: "Repertoire", active: "Active songs", archive: "Archived songs",
    search: "Find a song", noSongs: "There are no songs here yet.", noMatches: "No matching songs.",
    toSongs: "Back to song list", duration: "Duration", lights: "Lights", comment: "Notes",
    aliases: "Alternate titles", archived: "Archived",
    addSong: "Add a song", editSong: "Edit", copySong: "Copy",
    archiveSong: "Archive", restoreSong: "Restore song",
    proposals: "Suggestions", noProposals: "There are no pending suggestions.",
    approve: "Approve", reject: "Reject", proposedBy: "Author", forSong: "Song",
    approved: "Suggestion approved.", rejected: "Suggestion rejected.",
    pending: "The request is being processed.", check: "Check result",
  },
};

export const workspaceParams = new URLSearchParams(location.search);

export function bandUrl(band, past = "0", page = 0) {
  const params = new URLSearchParams({ workspace: "1" });
  if (band) params.set("band", band);
  if (past === "1") params.set("past", past);
  if (page) params.set("page", page);
  return `./?${params}`;
}

function repertoireUrl(band, { archived = "0", page = 0, query = "", song = "", proposals = false } = {}) {
  const params = new URLSearchParams({ workspace: "1", band, view: "repertoire" });
  if (archived === "1") params.set("archived", archived);
  if (page) params.set("page", page);
  if (query) params.set("query", query);
  if (song) params.set("song", song);
  if (proposals) params.set("proposals", "1");
  return `./?${params}`;
}

function element(tag, label, attributes = {}) {
  const node = document.createElement(tag);
  node.textContent = label;
  for (const [key, value] of Object.entries(attributes)) node.setAttribute(key, value);
  return node;
}

export function workspaceText(language, key) {
  return copy[language === "en" ? "en" : "ru"][key];
}

export async function showWorkspace({ api, openSong, authenticated, backButton }) {
  const main = document.getElementById("workspace-home");
  const title = document.getElementById("workspace-title");
  const content = document.getElementById("workspace-content");
  const status = document.getElementById("workspace-status");
  const band = workspaceParams.get("band");
  const past = workspaceParams.get("past") === "1" ? "1" : "0";
  const page = Number(workspaceParams.get("page")) || 0;
  const repertoire = band && workspaceParams.get("view") === "repertoire";
  const song = workspaceParams.get("song") || "";
  const proposals = repertoire && workspaceParams.get("proposals") === "1";
  const archived = workspaceParams.get("archived") === "1" ? "1" : "0";
  const query = workspaceParams.get("query") || "";
  const repertoireList = () => repertoireUrl(band, { archived, page, query });
  let language = navigator.language.startsWith("ru") ? "ru" : "en";
  let login;
  let catalogSession;
  const t = (key) => workspaceText(language, key);
  main.hidden = false;
  title.textContent = t("bands");
  if (!authenticated) {
    try {
      const config = await fetch("./auth/config");
      if (config.ok) login = await config.json();
    } catch {
      status.textContent = t("loginFailed");
      const retry = element("a", t("retry"), { href: bandUrl(band, past, page) });
      content.append(retry);
      return;
    }
    if (!login?.enabled) {
      status.textContent = t("auth");
      if (login?.bot_url) content.append(element("a", t("openBot"), { href: login.bot_url }));
      return;
    }
    if (workspaceParams.has("login")) {
      showLogin();
      return;
    }
  }
  function showLogin() {
    const result = workspaceParams.get("login");
    status.textContent = result ? t(result === "start_bot" ? "startBot" : "loginFailed") : "";
    content.replaceChildren(element("a", t("login"), { href: "./auth/login" }));
    if (result === "start_bot") content.append(element("a", t("openBot"), { href: login.bot_url }));
  }
  function signOutButton() {
    const button = element("button", t("logout"), { type: "button" });
    button.addEventListener("click", async () => {
      button.disabled = true;
      try {
        const result = await fetch("./auth/logout", { method: "POST" });
        if (!result.ok) throw new Error();
        location.assign(bandUrl());
      } catch {
        status.textContent = t("loginFailed");
        button.disabled = false;
      }
    });
    return button;
  }
  if (band) {
    backButton?.show?.();
    backButton?.onClick?.(() => {
      location.assign(repertoire ? (song || proposals ? repertoireList() : bandUrl(band)) : bandUrl());
    });
  }
  async function load() {
    content.replaceChildren();
    status.textContent = t("loading");
    try {
      const data = proposals ? await api("repertoire_proposals", { band, page }) : repertoire
        ? await api("repertoire", { band, archived, page, query, song })
        : band ? await api("performances", { band, past, page }) : await api("bands");
      language = data.language === "en" ? "en" : "ru";
      document.documentElement.lang = language;
      title.textContent = band ? data.band : t("bands");
      status.textContent = "";
      if (proposals) showProposals(data);
      else if (repertoire) showRepertoire(data);
      else if (band) showPerformances(data);
      else if (data.bands.length === 0) status.textContent = t("noBands");
      else {
        const list = element("ul", "", { class: "workspace-list" });
        for (const row of data.bands) {
          const item = element("li", "");
          item.append(element("a", row.name, { href: bandUrl(row.id) }));
          list.append(item);
        }
        content.append(list);
      }
      if (login?.enabled) content.append(signOutButton());
    } catch (error) {
      if (login?.enabled && error.data?.error === "miniapp_auth") {
        showLogin();
        return;
      }
      status.textContent = error.message;
      const retry = element("button", t("retry"), { type: "button" });
      retry.addEventListener("click", load);
      if (band) content.append(element("a", t("bands"), { href: bandUrl() }));
      content.append(retry);
    }
  }
  async function catalogApi(operation, payload, method) {
    if (operation === "song") {
      const data = await api("repertoire", { band, song: payload.song_id });
      return data.song;
    }
    catalogSession ??= api("open_repertoire", { band }, "POST");
    const opening = catalogSession;
    let session;
    try {
      ({ session } = await opening);
      return await api(operation, { ...payload, session }, method);
    } catch (error) {
      if (catalogSession === opening && (!session || error.data?.error === "miniapp_expired"))
        catalogSession = null;
      throw error;
    }
  }
  function songButton(label, admin, id = null, mode = "edit") {
    const button = element("button", t(label), { type: "button" });
    button.addEventListener("click", () => openSong(id, {
      api: catalogApi,
      language,
      admin,
      archiveLabel: t("archiveSong"),
      refresh: load,
      status: message => { status.textContent = message; },
    }, mode));
    return button;
  }
  function showPerformances(data) {
    const nav = element("nav", "", { class: "workspace-nav" });
    nav.append(element("a", t("bands"), { href: bandUrl() }));
    nav.append(element("a", t("repertoire"), { href: repertoireUrl(band) }));
    for (const [tab, key] of [["0", "upcoming"], ["1", "past"]]) {
      nav.append(element("a", t(key), {
        href: bandUrl(band, tab), ...(past === tab ? { "aria-current": "page" } : {}),
      }));
    }
    content.append(nav);
    if (!data.performances.length) status.textContent = t("noPerformances");
    const list = element("ul", "", { class: "workspace-list" });
    const date = new Intl.DateTimeFormat(language, { dateStyle: "medium", timeZone: "UTC" });
    for (const row of data.performances) {
      const item = element("li", "");
      const dateLabel = date.format(new Date(row.date));
      item.append(element("h2", row.title || dateLabel));
      item.append(element("p", [row.title ? dateLabel : "", row.city].filter(Boolean).join(" · ")));
      if (data.can_edit_draft) {
        const button = element("button", t("draft"), { type: "button" });
        button.addEventListener("click", () => openDraft(row.id, button));
        item.append(button);
      }
      list.append(item);
    }
    content.append(list);
    if (!data.can_edit_draft && data.performances.length)
      content.append(element("p", t("restricted")));
    showPages(data.has_next, (index) => bandUrl(band, past, index));
  }
  function showRepertoire(data) {
    const nav = element("nav", "", { class: "workspace-nav" });
    nav.append(element("a", t("performances"), { href: bandUrl(band) }));
    content.append(nav);
    if (data.song) {
      nav.append(element("a", t("toSongs"), { href: repertoireList() }));
      content.append(element("h2", data.song.title));
      if (data.song.archived) content.append(element("p", t("archived")));
      const details = element("dl", "", { class: "song-details" });
      for (const [label, value] of [
        [t("duration"), data.song.duration], ["BPM", data.song.bpm],
        [t("lights"), data.song.lights], [t("comment"), data.song.comment],
        [t("aliases"), data.song.aliases.join(" · ")],
      ]) {
        if (value) details.append(element("dt", label), element("dd", value));
      }
      content.append(details);
      const actions = element("div", "", { class: "toolbar" });
      if (data.song.archived && data.admin)
        actions.append(songButton("restoreSong", true, data.song.id, "restore"));
      if (!data.song.archived) actions.append(songButton("editSong", data.admin, data.song.id));
      actions.append(songButton("copySong", data.admin, data.song.id, "copy"));
      content.append(actions);
      return;
    }
    for (const [tab, key] of [["0", "active"], ["1", "archive"]]) {
      nav.append(element("a", t(key), {
        href: repertoireUrl(band, { archived: tab, query }),
        ...(archived === tab ? { "aria-current": "page" } : {}),
      }));
    }
    if (data.admin) nav.append(element("a", t("proposals"), { href: repertoireUrl(band, { proposals: true }) }));
    if (archived === "0") content.append(songButton("addSong", data.admin));
    const form = element("form", "", { action: "./", method: "get", class: "workspace-search" });
    for (const [name, value] of Object.entries({ workspace: "1", band, view: "repertoire", archived }))
      form.append(element("input", "", { type: "hidden", name, value }));
    form.append(
      element("label", t("search"), { for: "repertoire-query" }),
      element("input", "", { name: "query", type: "search", value: query, maxlength: "200", id: "repertoire-query" }),
      element("button", t("search"), { type: "submit" }),
    );
    content.append(form);
    if (!data.songs.length) status.textContent = t(query ? "noMatches" : "noSongs");
    const list = element("ul", "", { class: "workspace-list" });
    for (const row of data.songs) {
      const item = element("li", "");
      const link = element("a", row.title, { href: repertoireUrl(band, { archived, page, query, song: row.id }) });
      link.append(element("span", row.duration, { class: "workspace-duration" }));
      item.append(link);
      list.append(item);
    }
    content.append(list);
    showPages(data.has_next, (index) => repertoireUrl(band, { archived, page: index, query }));
  }
  const decisions = new Map();
  function showProposals(data) {
    const nav = element("nav", "", { class: "workspace-nav" });
    nav.append(element("a", t("toSongs"), { href: repertoireUrl(band) }));
    content.append(nav, element("h2", t("proposals")));
    if (!data.proposals.length) status.textContent = t("noProposals");
    const list = element("ul", "", { class: "workspace-list" });
    for (const row of data.proposals) {
      const item = element("li", "");
      const fields = row.fields;
      item.append(element("h3", fields.title || fields.alias), element("p", `${t("proposedBy")}: ${row.author}`));
      const details = element("dl", "", { class: "song-details" });
      for (const [label, value] of [
        [t("forSong"), row.original_title], [t("duration"), fields.duration_seconds && clock(fields.duration_seconds)],
        ["BPM", fields.bpm], [t("lights"), fields.lights], [t("comment"), fields.comment],
        [t("aliases"), fields.alias || (fields.aliases ? fields.aliases.join(" · ") || "—" : null)],
      ]) {
        if (value) details.append(element("dt", label), element("dd", value));
      }
      item.append(details);
      const actions = element("div", "", { class: "toolbar" });
      decisionButtons(row.id, actions);
      item.append(actions);
      list.append(item);
    }
    content.append(list);
    showPages(data.has_next, index => repertoireUrl(band, { proposals: true, page: index }));
  }
  function decisionButtons(proposalId, container) {
    const existing = decisions.get(proposalId);
    container.replaceChildren();
    for (const approve of existing ? [existing.payload.approve] : [true, false]) {
      const button = element("button", t(existing ? "check" : approve ? "approve" : "reject"), { type: "button" });
      button.addEventListener("click", () => decideProposal(proposalId, approve, container));
      container.append(button);
    }
  }
  async function decideProposal(proposalId, approve, container) {
    const attempt = decisions.get(proposalId) ?? {
      payload: { proposal_id: proposalId, approve, request_id: crypto.randomUUID() },
    };
    decisions.set(proposalId, attempt);
    for (const button of container.querySelectorAll("button")) button.disabled = true;
    status.textContent = t("pending");
    try {
      let result = attempt.receipt
        ? await catalogApi("catalog_status", { request: attempt.receipt })
        : await catalogApi("catalog_decide", attempt.payload, "POST");
      attempt.receipt = result.request;
      for (let i = 0; result.status === "pending" && i < 10; i++) {
        await new Promise(resolve => setTimeout(resolve, 1000));
        result = await catalogApi("catalog_status", { request: attempt.receipt });
      }
      if (result.status === "pending") return;
      decisions.delete(proposalId);
      await load();
      status.textContent = result.status === "failed" ? result.message : t(result.approved ? "approved" : "rejected");
    } catch (error) {
      if (error.data && !attempt.receipt) decisions.delete(proposalId);
      status.textContent = error.message;
    } finally {
      decisionButtons(proposalId, container);
    }
  }
  function showPages(hasNext, href) {
    const pages = element("nav", "", { class: "workspace-nav" });
    if (page > 0) pages.append(element("a", t("previous"), { href: href(page - 1) }));
    if (hasNext) pages.append(element("a", t("next"), { href: href(page + 1) }));
    if (pages.childElementCount) content.append(pages);
  }
  let opening = false;
  async function openDraft(performance, button) {
    if (opening) return;
    opening = true;
    button.disabled = true;
    status.textContent = t("loading");
    try {
      const { session } = await api("open_draft", { band, performance }, "POST");
      location.assign(`${bandUrl(band, past, page)}&view=draft#session=${encodeURIComponent(session)}`);
    } catch (error) {
      status.textContent = error.message;
      button.disabled = false;
      opening = false;
    }
  }
  await load();
}
