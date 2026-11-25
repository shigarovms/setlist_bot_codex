import { withBotLabels } from "./bot-labels.js";
import { clock, numbered } from "./model.js";
import { repertoireImport } from "./repertoire-import.js";
import { repertoireMusic } from "./repertoire-music.js";
import { mountSharedSetlist } from "./shared-setlist.js";
import { performanceAction, performanceLabel } from "./workspace-performances.js";
import { bandSettingsTitle, showBandSettings } from "./workspace-band-settings.js";
import { bandDeleteButton, bandTrashTitle, showBandTrash } from "./workspace-band-trash.js";
import { teamLabel, teamView } from "./workspace-team.js";
import { showStaffDirectory, staffingLabel } from "./workspace-staffing.js";
import { rosterLabel, showRoster } from "./workspace-roster.js";
import { gigLabel, showGigs } from "./workspace-gigs.js";
import { scheduleLabel, showScheduleSettings } from "./workspace-schedule.js";
import { profileLabel, showProfile } from "./workspace-profile.js";
import { helpTitle, showHelp } from "./workspace-help.js";

const copy = withBotLabels("workspace", {
  ru: {
    upcoming: "Предстоящие",
    noBands: "У вас пока нет коллективов.",
    noPerformances: "В этом разделе пока нет доступных выступлений.",
    restricted: "Черновики доступны администраторам коллектива.",
    previous: "Назад", next: "Дальше", retry: "Повторить", loading: "Загрузка…",
    auth: "Откройте приложение через бота в Telegram.",
    joinBand: "Как вступить в коллектив",
    joinBandHelp: "В чате коллектива отправьте /register и укажите свои роли. Администратор рассмотрит заявку. Если вам уже прислали приглашение, откройте его здесь.",
    telegramChat: "Действия в Telegram",
    chatActionsHelp: "Уборку можно открыть и прямо в чате коллектива из «Опций» в карточке сет-листа.",
    login: "Войти через Telegram", logout: "Выйти", openBot: "Открыть бота",
    loginFailed: "Не удалось войти. Попробуйте ещё раз.",
    startBot: "Откройте бота и нажмите «Старт», затем повторите вход.",
    active: "Активные песни", archive: "Архив песен",
    search: "Найти песню", noSongs: "В этом разделе пока нет песен.", noMatches: "Песни не найдены.",
    toSongs: "К списку песен", duration: "Длительность", lights: "Свет", comment: "Комментарий",
    aliases: "Альтернативные названия", archived: "В архиве",
    addSong: "Добавить песню",
    noProposals: "Предложений на рассмотрении нет.",
    proposedBy: "Автор", forSong: "Песня",
    approved: "Предложение одобрено.", rejected: "Предложение отклонено.",
    pending: "Запрос обрабатывается.", check: "Проверить результат",
    importSongs: "Импорт таблицы", adminOnly: "Импорт доступен администратору коллектива.",
    performance: "Выступление", shared: "Общий сет-лист", approvedVersion: "Утверждён", workingVersion: "В работе",
    version: "Версия", total: "Итого", planned: "План", noEntries: "В общем сет-листе пока нет песен.", pause: "Пауза",
  },
  en: {
    upcoming: "Upcoming",
    noBands: "You have no bands yet.",
    noPerformances: "There are no available performances here yet.",
    restricted: "Drafts are available to band admins.",
    previous: "Previous", next: "Next", retry: "Try again", loading: "Loading…",
    auth: "Open the app from the bot in Telegram.",
    joinBand: "How to join a band",
    joinBandHelp: "Send /register in the band's chat and enter your roles. An admin will review your request. If you already have an invitation, open it here.",
    telegramChat: "Telegram chat actions",
    chatActionsHelp: "You can also open cleanup from Options on a setlist card in the band's chat.",
    login: "Sign in with Telegram", logout: "Sign out", openBot: "Open the bot",
    loginFailed: "Could not sign in. Try again.",
    startBot: "Open the bot and press Start, then sign in again.",
    active: "Active songs", archive: "Archived songs",
    search: "Find a song", noSongs: "There are no songs here yet.", noMatches: "No matching songs.",
    toSongs: "Back to song list", duration: "Duration", lights: "Lights", comment: "Notes",
    aliases: "Alternate titles", archived: "Archived",
    addSong: "Add a song",
    noProposals: "There are no pending suggestions.",
    proposedBy: "Author", forSong: "Song",
    approved: "Suggestion approved.", rejected: "Suggestion rejected.",
    pending: "The request is being processed.", check: "Check result",
    importSongs: "Import a table", adminOnly: "Only band admins can import songs.",
    performance: "Performance", shared: "Shared setlist", approvedVersion: "Approved", workingVersion: "Working",
    version: "Version", total: "Total", planned: "Planned", noEntries: "The shared setlist is empty.", pause: "Pause",
  },
});

export const workspaceParams = new URLSearchParams(location.search);

export function bandUrl(band, past = "0", page = 0) {
  const params = new URLSearchParams({ workspace: "1" });
  if (band) params.set("band", band);
  if (past === "1") params.set("past", past);
  if (page) params.set("page", page);
  return `./?${params}`;
}

export function performanceUrl(band, performance, past, page) {
  return `${bandUrl(band, past, page)}&view=performance&performance=${encodeURIComponent(performance)}`;
}

function repertoireUrl(band, { archived = "0", page = 0, query = "", song = "", proposals = false, importing = false, music = false } = {}) {
  const params = new URLSearchParams({ workspace: "1", band, view: "repertoire" });
  if (archived === "1") params.set("archived", archived);
  if (page) params.set("page", page);
  if (query) params.set("query", query);
  if (song) params.set("song", song);
  if (proposals) params.set("proposals", "1");
  if (importing) params.set("import", "1");
  if (music) params.set("music", "1");
  return `./?${params}`;
}

function element(tag, label, attributes = {}) {
  const node = document.createElement(tag);
  node.textContent = label;
  for (const [key, value] of Object.entries(attributes)) node.setAttribute(key, value);
  return node;
}

function telegramLink(label, href) {
  const link = element("a", label, { href, target: "_blank", rel: "noopener noreferrer" });
  link.addEventListener("click", event => {
    const webApp = window.Telegram?.WebApp;
    if (!webApp?.initData || !webApp.openTelegramLink) return;
    event.preventDefault();
    webApp.openTelegramLink(href);
  });
  return link;
}

export function workspaceText(language, key) {
  return copy[language === "en" ? "en" : "ru"][key];
}

export async function showWorkspace({ api, openSong, authenticated, onBack }) {
  const main = document.getElementById("workspace-home");
  const title = document.getElementById("workspace-title");
  const content = document.getElementById("workspace-content");
  const status = document.getElementById("workspace-status");
  const band = workspaceParams.get("band");
  const past = workspaceParams.get("past") === "1" ? "1" : "0";
  const page = Number(workspaceParams.get("page")) || 0;
  const view = workspaceParams.get("view");
  const profile = !band && view === "profile";
  const help = !band && view === "help";
  const bandSettings = band && view === "band-settings";
  const bandTrash = !band && view === "band-trash";
  const gigs = !band && view === "my-gigs";
  const gigInvitation = gigs && workspaceParams.get("invitation");
  const gigSetlist = gigInvitation && workspaceParams.get("setlist") === "1";
  const gigSections = ["schedule", "invitations", "confirmed", "history", "managed", "busy", "calendar"];
  const gigSection = gigSections.includes(workspaceParams.get("section"))
    ? workspaceParams.get("section") : "schedule";
  const scheduleSettings = gigs && !gigInvitation && ["busy", "calendar"].includes(gigSection);
  const managedPage = workspaceParams.get("from") === "managed" ? workspaceParams.get("managed_page") || "0" : null;
  const performance = band && ["performance", "lineup"].includes(view) && workspaceParams.get("performance");
  const roster = performance && view === "lineup";
  const trash = band && workspaceParams.get("view") === "performance-trash";
  const repertoire = band && workspaceParams.get("view") === "repertoire";
  const team = band && workspaceParams.get("view") === "team";
  const teamContact = team && workspaceParams.get("contact");
  const inbox = !band && workspaceParams.get("view") === "team-inbox";
  const staffing = band && (view === "staff-contacts" ? "contacts" : view === "staff-lineups" ? "lineups" : "");
  const staffRecord = staffing && workspaceParams.get("record");
  const song = workspaceParams.get("song") || "";
  const proposals = repertoire && workspaceParams.get("proposals") === "1";
  const importing = repertoire && workspaceParams.get("import") === "1";
  const music = repertoire && workspaceParams.get("music") === "1";
  const archived = workspaceParams.get("archived") === "1" ? "1" : "0";
  const query = workspaceParams.get("query") || "";
  const repertoireList = () => repertoireUrl(band, { archived, page, query });
  const teamUrl = (index = 0) => `${bandUrl(band, past, index)}&view=team`;
  const inboxUrl = () => `${bandUrl()}&view=team-inbox`;
  const profileUrl = () => `${bandUrl()}&view=profile`;
  const helpUrl = () => `${bandUrl()}&view=help`;
  const bandTrashUrl = (index = 0) => `${bandUrl(null, "0", index)}&view=band-trash`;
  const fromProfile = profile || workspaceParams.get("schedule_from") === "profile";
  const scheduleOrigin = fromProfile ? "&schedule_from=profile" : "";
  const scheduleParent = () => fromProfile ? profileUrl() : bandUrl();
  function gigsUrl(section = "schedule", index = 0, invitation = null, setlist = false) {
    const target = new URLSearchParams({ view: "my-gigs", section });
    if (invitation) target.set("invitation", invitation);
    if (setlist) target.set("setlist", "1");
    return `${bandUrl(null, "0", index)}&${target}${scheduleOrigin}`;
  }
  function performanceParent() {
    return managedPage === null ? bandUrl(band, past, page) : gigsUrl("managed", managedPage);
  }
  function withManagedOrigin(url) {
    return managedPage === null ? url : `${url}&from=managed&managed_page=${encodeURIComponent(managedPage)}${scheduleOrigin}`;
  }
  function staffingUrl(section, index = 0, search = "", record = null) {
    const target = new URLSearchParams({ view: `staff-${section}` });
    if (search) target.set("query", search);
    if (record) target.set("record", record);
    return `${bandUrl(band, past, index)}&${target}`;
  }
  let language = navigator.language.startsWith("ru") ? "ru" : "en";
  let login;
  let catalogSession;
  let sharedSession;
  const gigSessions = new Map();
  let accountSession;
  const t = (key) => workspaceText(language, key);
  main.hidden = false;
  title.textContent = t("bands");
  if (!authenticated) {
    try {
      const config = await fetch("./auth/config");
      if (config.ok) login = await config.json();
    } catch {
      status.textContent = t("loginFailed");
      const retry = element("a", t("retry"), { href: location.href });
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
    const href = gigInvitation
      ? `./auth/login?invitation=${encodeURIComponent(gigInvitation)}` : "./auth/login";
    content.replaceChildren(element("a", t("login"), { href }));
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
  if (band || inbox || gigs || profile || help || bandTrash) {
    onBack?.(() => {
      if (gigs) location.assign(gigSetlist ? gigsUrl(gigSection, page, gigInvitation)
        : gigInvitation ? gigsUrl(gigSection, page) : scheduleSettings ? gigsUrl() : scheduleParent());
      else if (roster) location.assign(withManagedOrigin(performanceUrl(band, performance, past, page)));
      else if (performance) location.assign(performanceParent());
      else if (bandSettings) location.assign(bandUrl(band, past, page));
      else if (trash) location.assign(bandUrl(band, past));
      else if (team) location.assign(teamContact ? teamUrl(page) : bandUrl(band, past));
      else if (staffing) location.assign(staffRecord ? staffingUrl(staffing, page, query) : teamUrl());
      else if (repertoire) location.assign(song || proposals || importing || music ? repertoireList() : bandUrl(band));
      else location.assign(bandUrl());
    });
  }
  async function load() {
    content.replaceChildren();
    status.textContent = t("loading");
    try {
      let data;
      if (profile) data = await api("profile");
      else if (help) data = await api("help");
      else if (bandTrash) data = await api("band_trash", { page });
      else if (scheduleSettings) data = await openAccount();
      else if (gigs) data = gigInvitation ? await api(gigSetlist ? "gig_setlist" : "gig_card", { invitation: gigInvitation })
        : await api("my_gigs", { section: gigSection, page });
      else if (roster) data = await api("performance_roster", { band, performance });
      else if (performance) data = await api("performance", { band, performance });
      else if (bandSettings) data = await api("band_settings", { band });
      else if (trash) data = await api("performance_trash", { band, page });
      else if (teamContact) data = await api("team_contact", { band, contact: teamContact });
      else if (team) data = await api("team", { band, page });
      else if (staffing) data = await api("staff_directory", {
        section: staffing, band, page, query, ...(staffRecord ? { record: staffRecord } : {}),
      });
      else if (inbox) data = await api("team_inbox");
      else if (music) data = await api("music_search", { band });
      else if (proposals) data = await api("repertoire_proposals", { band, page });
      else if (repertoire) data = await api("repertoire", { band, archived, page, query, song });
      else if (band) data = await api("performances", { band, past, page });
      else data = await api("bands");
      language = data.language === "en" ? "en" : "ru";
      document.documentElement.lang = language;
      title.textContent = band || gigInvitation ? data.band : gigs ? gigLabel(language, "title")
        : inbox ? teamLabel(language, "inbox") : profile ? profileLabel(language, "title")
          : help ? helpTitle(language) : bandTrash ? bandTrashTitle(language) : t("bands");
      status.textContent = "";
      if (gigs) await showPersonalGigs(data);
      else if (profile) {
        showProfile({
          root: content, data, language, request: accountApi, refresh: load,
          preview: payload => api("profile_roles", payload, "POST"),
          status: message => { status.textContent = message; },
        });
      }
      else if (help) {
        const nav = element("nav", "", { class: "workspace-nav" });
        nav.append(element("a", t("bands"), { href: bandUrl() }));
        content.append(nav);
        showHelp({
          root: content, topics: data.topics, language, request: accountApi,
          status: message => { status.textContent = message; },
        });
      }
      else if (bandTrash) {
        const nav = element("nav", "", { class: "workspace-nav" });
        nav.append(element("a", t("bands"), { href: bandUrl() }));
        content.append(nav);
        showBandTrash({
          root: content, data, language, dateText, request: accountApi, refresh: load,
          status: message => { status.textContent = message; },
        });
        showPages(data.has_next, bandTrashUrl);
      }
      else if (roster) showPerformanceRoster(data);
      else if (performance) showPerformance(data);
      else if (bandSettings) {
        const nav = element("nav", "", { class: "workspace-nav" });
        nav.append(element("a", t("performances"), { href: bandUrl(band, past, page) }));
        content.append(nav, element("h2", bandSettingsTitle(language)));
        showBandSettings({
          root: content, data, language, request: catalogApi, refresh: load,
          status: message => { status.textContent = message; },
        });
        const { transfer_url: transfer, cleanup_url: cleanup } = data.telegram;
        if (transfer || cleanup) {
          const actions = element("div", "", { class: "toolbar" });
          if (transfer) actions.append(telegramLink(t("transferChat"), transfer));
          if (cleanup) actions.append(telegramLink(t("cleanupChat"), cleanup));
          content.append(element("h3", t("telegramChat")), actions);
          if (cleanup) content.append(element("p", t("chatActionsHelp")));
        }
        if (data.admin) content.append(bandDeleteButton({
          data, band, language, request: accountApi, onDelete: () => location.assign(bandTrashUrl()),
        }));
      }
      else if (trash) showPerformanceTrash(data);
      else if (team || inbox) showTeam(data);
      else if (staffing) showStaffing(data);
      else if (music) showMusic(data);
      else if (proposals) showProposals(data);
      else if (importing) showImport(data);
      else if (repertoire) showRepertoire(data);
      else if (band) showPerformances(data);
      else {
        const nav = element("nav", "", { class: "workspace-nav" });
        nav.append(element("a", profileLabel(language, "title"), { href: profileUrl() }),
          element("a", gigLabel(language, "title"), { href: gigsUrl() }),
          element("a", teamLabel(language, "inbox"), { href: inboxUrl() }),
          element("a", helpTitle(language), { href: helpUrl() }),
          element("a", bandTrashTitle(language), { href: bandTrashUrl() }));
        content.append(nav);
        if (data.bands.length === 0) status.textContent = t("noBands");
        const list = element("ul", "", { class: "workspace-list" });
        for (const row of data.bands) {
          const item = element("li", "");
          item.append(element("a", row.name, { href: bandUrl(row.id) }));
          list.append(item);
        }
        content.append(list);
        const join = element("details", "");
        join.append(element("summary", t("joinBand")), element("p", t("joinBandHelp")),
          element("a", teamLabel(language, "inbox"), { href: inboxUrl() }));
        content.append(telegramLink(t("connectBand"), data.connect_url), join);
      }
      if (login?.enabled) content.append(signOutButton());
      if (profile) {
        const nav = element("nav", "", { class: "workspace-nav profile-nav" });
        nav.append(element("a", gigLabel(language, "title"), { href: gigsUrl() }),
          element("a", t("back"), { href: bandUrl() }));
        content.append(nav);
      }
    } catch (error) {
      if (login?.enabled && error.data?.error === "miniapp_auth") {
        showLogin();
        return;
      }
      status.textContent = error.message;
      const retry = element("button", t("retry"), { type: "button" });
      retry.addEventListener("click", load);
      if (band || gigs || profile || help || bandTrash) content.append(element("a", gigs ? gigLabel(language, "title") : t("bands"),
        { href: gigs ? gigsUrl() : bandUrl() }));
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
  async function sharedApi(operation, payload, method) {
    sharedSession ??= api("open_shared", { band, performance }, "POST");
    const opening = sharedSession;
    let session;
    try {
      ({ session } = await opening);
      return await api(operation, { ...payload, session }, method);
    } catch (error) {
      if (sharedSession === opening && (!session || error.data?.error === "miniapp_expired")) sharedSession = null;
      throw error;
    }
  }
  async function gigApi(operation, payload, method, invitation = gigInvitation) {
    if (!gigSessions.has(invitation))
      gigSessions.set(invitation, api("open_gig", { invitation }, "POST"));
    const opening = gigSessions.get(invitation);
    let session;
    try {
      ({ session } = await opening);
      return await api(operation, { ...payload, session }, method);
    } catch (error) {
      if (gigSessions.get(invitation) === opening && (!session || error.data?.error === "miniapp_expired"))
        gigSessions.delete(invitation);
      throw error;
    }
  }
  function openAccount() {
    accountSession ??= api("open_account", {}, "POST").catch(error => {
      accountSession = null;
      throw error;
    });
    return accountSession;
  }
  async function accountApi(operation, payload, method) {
    const opening = openAccount();
    let session;
    try {
      ({ session } = await opening);
      return await api(operation, { ...payload, session }, method);
    } catch (error) {
      if (accountSession === opening && (!session || error.data?.error === "miniapp_expired"))
        accountSession = null;
      throw error;
    }
  }
  async function showPersonalGigs(data) {
    const label = key => gigLabel(language, key);
    const nav = element("nav", "", { class: "workspace-nav" });
    if (gigInvitation) nav.append(element("a", label(gigSetlist ? "back" : "title"), {
      href: gigSetlist ? gigsUrl(gigSection, page, gigInvitation) : gigsUrl(gigSection, page),
    }));
    else if (scheduleSettings) {
      nav.append(element("a", label("title"), { href: gigsUrl() }));
      nav.append(element("a", scheduleLabel(language, gigSection === "busy" ? "calendar" : "busyTitle"),
        { href: gigsUrl(gigSection === "busy" ? "calendar" : "busy") }));
    } else {
      nav.append(element("a", fromProfile ? profileLabel(language, "title") : t("bands"), { href: scheduleParent() }));
      const sections = ["schedule", "invitations", "confirmed", "history"];
      if (data.can_manage || gigSection === "managed") sections.push("managed");
      for (const section of sections)
        nav.append(element("a", label(section), {
          href: gigsUrl(section), ...(section === gigSection ? { "aria-current": "page" } : {}),
        }));
    }
    content.append(nav);
    if (scheduleSettings) {
      await showScheduleSettings({
        root: content, section: gigSection, language, dateText, request: accountApi,
        refresh: load, status: message => { status.textContent = message; },
      });
      return;
    }
    if (!gigInvitation) {
      const options = element("div", "", { class: "toolbar" });
      options.append(element("a", scheduleLabel(language, "busyTitle"), { href: gigsUrl("busy") }),
        element("a", scheduleLabel(language, "calendar"), { href: gigsUrl("calendar") }));
      content.append(options);
    }
    showGigs({
      root: content, data, language, dateText, refresh: load,
      invitationUrl: gigSection === "history" ? null : id => gigsUrl(gigSection, page, id),
      setlistUrl: gigsUrl(gigSection, page, gigInvitation, true), historyUrl: gigsUrl("history"),
      request: gigApi, options: () => api("export_options", { invitation: gigInvitation }),
      requestFor: invitation => (operation, payload, method) => gigApi(operation, payload, method, invitation),
      managedUrl: row => `${performanceUrl(row.band_id, row.id, "0", 0)}&from=managed&managed_page=${page}${scheduleOrigin}`,
      status: message => { status.textContent = message; },
    });
    if (!gigInvitation) showPages(data.has_next, index => gigsUrl(gigSection, index));
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
    nav.append(element("a", teamLabel(language, "title"), { href: teamUrl() }));
    nav.append(element("a", bandSettingsTitle(language), {
      href: `${bandUrl(band, past, page)}&view=band-settings`,
    }));
    const tabs = element("nav", "", { class: "workspace-nav section-tabs", "aria-label": t("events") });
    for (const [tab, key] of [["0", "upcoming"], ["1", "past"]]) {
      tabs.append(element("a", t(key), {
        href: bandUrl(band, tab), ...(past === tab ? { "aria-current": "page" } : {}),
      }));
    }
    content.append(nav, element("h2", t("events")), tabs);
    if (data.admin) {
      const actions = element("div", "", { class: "toolbar" });
      actions.append(managePerformance("create"), element("a", performanceLabel(language, "trash"),
        { href: `${bandUrl(band, past)}&view=performance-trash` }));
      content.append(actions);
    }
    if (!data.performances.length) status.textContent = t("noPerformances");
    const list = element("ul", "", { class: "workspace-list" });
    for (const row of data.performances) {
      const item = element("li", "");
      const dateLabel = dateText(row.date);
      const name = element("h3", "");
      name.append(element("a", row.title || dateLabel, { href: performanceUrl(band, row.id, past, page) }));
      item.append(name);
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
  function dateText(value) {
    return new Intl.DateTimeFormat(language, { dateStyle: "medium", timeZone: "UTC" }).format(new Date(value));
  }
  function showTeam(data) {
    const nav = element("nav", "", { class: "workspace-nav" });
    const label = inbox ? t("bands") : teamLabel(language, teamContact ? "title" : "back");
    nav.append(element("a", label, {
      href: teamContact ? teamUrl(page) : bandUrl(band, past),
    }));
    if (team && !teamContact)
      nav.append(element("a", teamLabel(language, "inbox"), { href: inboxUrl() }));
    if (team && !teamContact && data.admin)
      for (const section of ["contacts", "lineups"])
        nav.append(element("a", staffingLabel(language, section), { href: staffingUrl(section) }));
    content.append(nav);
    const view = teamView({
      root: content, language, dateText, refresh: load,
      status: message => { status.textContent = message; },
      contactUrl: id => `${teamUrl(page)}&contact=${encodeURIComponent(id)}`,
      exit: () => location.assign(teamContact ? teamUrl(page) : bandUrl()),
      requests: {
        group: catalogApi,
        recipient: reference => api("team_recipient", { band, reference }, "POST"),
        personal: membership => {
          let opening;
          return async (operation, payload, method) => {
            opening ??= api("open_team_membership", { membership }, "POST");
            const current = opening;
            let session;
            try {
              ({ session } = await current);
              return await api(operation, { ...payload, session }, method);
            } catch (error) {
              if (opening === current && (!session || error.data?.error === "miniapp_expired"))
                opening = null;
              throw error;
            }
          };
        },
      },
    });
    if (inbox) view.renderInbox(data.invitations);
    else if (teamContact) view.renderContact(data);
    else {
      view.renderTeam(data);
      showPages(data.has_next, teamUrl);
    }
  }
  function showStaffing(data) {
    const label = key => staffingLabel(language, key);
    const nav = element("nav", "", { class: "workspace-nav" });
    const listUrl = staffingUrl(staffing, page, query);
    nav.append(element("a", label(staffRecord ? staffing : "team"), {
      href: staffRecord ? listUrl : teamUrl(),
    }));
    if (!staffRecord) {
      const other = staffing === "contacts" ? "lineups" : "contacts";
      nav.append(element("a", label(other), { href: staffingUrl(other) }));
    }
    content.append(nav);
    if (!staffRecord) {
      const form = element("form", "", { method: "get", action: "./", class: "workspace-search" });
      for (const [name, value] of Object.entries({ workspace: "1", band, past, view: `staff-${staffing}` }))
        form.append(element("input", "", { type: "hidden", name, value }));
      form.append(element("label", label("search"), { for: "staff-query" }),
        element("input", "", { type: "search", name: "query", value: query, maxlength: "200", id: "staff-query" }),
        element("button", label("search"), { type: "submit" }));
      content.append(form);
    }
    showStaffDirectory({
      root: content, language, data, section: staffing, listUrl, query,
      recordUrl: id => staffingUrl(staffing, page, query, id),
      request: catalogApi, preview: body => api("staff_lineup_preview", { ...body, band }, "POST"),
      recipient: reference => api("team_recipient", { band, reference }, "POST"),
      refresh: load, status: message => { status.textContent = message; },
    });
    if (!staffRecord) showPages(data.has_next, index => staffingUrl(staffing, index, query));
  }
  function managePerformance(action, info) {
    return performanceAction({
      action, performance: info, language, dateText, request: catalogApi,
      readOptions: () => api("performance_options", { band }),
      onDone: result => {
        const tab = result.past ? "1" : "0";
        if (action === "delete") location.assign(managedPage === null ? bandUrl(band, past) : performanceParent());
        else if (action === "update" && tab === past) return load();
        else location.assign(withManagedOrigin(performanceUrl(band, result.performance, tab, 0)));
      },
    });
  }
  function showPerformanceTrash(data) {
    const tManage = key => performanceLabel(language, key);
    const nav = element("nav", "", { class: "workspace-nav" });
    nav.append(element("a", t("performances"), { href: bandUrl(band, past) }));
    content.append(nav, element("h2", tManage("trash")));
    if (!data.performances.length) status.textContent = tManage("empty");
    const list = element("ul", "", { class: "workspace-list" });
    for (const row of data.performances) {
      const item = element("li", "");
      item.append(element("h3", row.title || dateText(row.date)),
        element("p", [row.title ? dateText(row.date) : "", `${tManage("until")} ${dateText(row.until)}`].filter(Boolean).join(" · ")),
        managePerformance("restore", row));
      list.append(item);
    }
    content.append(list);
    showPages(data.has_next, index => `${bandUrl(band, past, index)}&view=performance-trash`);
  }
  function showPerformance(data) {
    const info = data.performance, list = data.setlist;
    const nav = element("nav", "", { class: "workspace-nav" });
    nav.append(element("a", t("performances"), { href: performanceParent() }));
    nav.append(element("a", rosterLabel(language, "title"), {
      href: withManagedOrigin(`${bandUrl(band, past, page)}&view=lineup&performance=${encodeURIComponent(performance)}`),
    }));
    content.append(nav, element("h2", info.title || t("performance")),
      element("p", [dateText(info.date), info.city, info.type].filter(Boolean).join(" · ")));
    if (info.info) content.append(element("p", info.info, { class: "performance-info" }));
    if (data.admin) {
      content.append(managePerformance("update", info));
    }
    content.append(element("h3", t("shared")),
      element("p", `${t(list.approved ? "approvedVersion" : "workingVersion")} · ${t("version")} ${list.version}`));
    const timing = [`${t("total")} ${clock(list.seconds)}`];
    if (info.planned_seconds) timing.push(`${t("planned")} ${clock(info.planned_seconds)}`);
    content.append(element("p", timing.join(" · "), { class: "performance-timing" }));
    let editorButton;
    if (data.can_edit_draft) {
      editorButton = element("button", t("draft"), { type: "button", class: list.approved ? "" : "primary" });
      editorButton.addEventListener("click", () => openDraft(info.id, editorButton));
    }
    mountSharedSetlist({
      root: content, data, language, editorButton,
      read: (operation, params = {}) => api(operation, { band, performance, ...params }),
      request: sharedApi, refresh: load, status: message => { status.textContent = message; },
    });
    if (list.entries.length) {
      const rows = element("ol", "", { class: "workspace-list shared-setlist", "aria-label": t("shared") });
      const numbers = numbered(list.entries);
      for (const [index, row] of list.entries.entries()) {
        const item = element("li", "", { class: row.kind === "pause" ? "shared-pause" : "" });
        const name = row.kind === "pause" ? row.label ?? t("pause") : row.title;
        const details = [row.bpm ? `${row.bpm} BPM` : null, row.lights, row.comment].filter(Boolean).join(" · ");
        const title = element("span", name);
        if (details) title.append(element("small", details));
        item.append(element("span", numbers[index], { "aria-hidden": "true" }), title,
          element("span", clock(row.duration_seconds), { class: "workspace-duration" }));
        rows.append(item);
      }
      content.append(rows);
    } else content.append(element("p", t("noEntries")));
    if (data.admin) {
      const remove = managePerformance("delete", info);
      remove.classList.add("destructive");
      content.append(remove);
    }
  }
  function showPerformanceRoster(data) {
    const nav = element("nav", "", { class: "workspace-nav" });
    nav.append(element("a", rosterLabel(language, "back"), {
      href: withManagedOrigin(performanceUrl(band, performance, past, page)),
    }));
    content.append(nav);
    showRoster({
      root: content, data, language, dateText, request: sharedApi,
      preview: body => api("staff_lineup_preview", { ...body, band }, "POST"),
      recipient: reference => api("team_recipient", { band, reference }, "POST"),
      refresh: load, status: message => { status.textContent = message; },
    });
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
    const tabs = element("nav", "", { class: "workspace-nav section-tabs", "aria-label": t("repertoire") });
    content.append(element("h2", t("repertoire")), tabs);
    for (const [tab, key] of [["0", "active"], ["1", "archive"]]) {
      tabs.append(element("a", t(key), {
        href: repertoireUrl(band, { archived: tab, query }),
        ...(archived === tab ? { "aria-current": "page" } : {}),
      }));
    }
    const actions = element("div", "", { class: "toolbar" });
    if (archived === "0") actions.append(songButton("addSong", data.admin));
    if (data.admin) actions.append(element("a", t("proposals"), { href: repertoireUrl(band, { proposals: true }) }));
    if (data.admin && archived === "0")
      actions.append(element("a", t("importSongs"), { href: repertoireUrl(band, { importing: true }) }),
        element("a", t("music"), { href: repertoireUrl(band, { music: true }) }));
    if (actions.childElementCount) content.append(actions);
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
  function showImport(data) {
    content.append(element("a", t("toSongs"), { href: repertoireUrl(band) }));
    if (!data.admin) {
      status.textContent = t("adminOnly");
      return;
    }
    repertoireImport({
      root: content, language,
      preview: input => api("repertoire_import_preview", { ...input, band }, "POST"),
      request: catalogApi,
    });
  }
  function showMusic(data) {
    content.append(element("a", t("toSongs"), { href: repertoireUrl(band) }));
    repertoireMusic({
      root: content, language, band: data.band, search: data.search,
      read: () => api("music_search", { band }), request: catalogApi,
    });
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
  async function openDraft(performanceId, button) {
    if (opening) return;
    opening = true;
    button.disabled = true;
    status.textContent = t("loading");
    try {
      const { session } = await api("open_draft", { band, performance: performanceId }, "POST");
      const parent = performance ? `&performance=${encodeURIComponent(performance)}` : "";
      const destination = withManagedOrigin(`${bandUrl(band, past, page)}&view=draft${parent}`);
      location.assign(`${destination}#session=${encodeURIComponent(session)}`);
    } catch (error) {
      status.textContent = error.message;
      button.disabled = false;
      opening = false;
    }
  }
  await load();
}
