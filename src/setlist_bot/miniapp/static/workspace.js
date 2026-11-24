const copy = {
  ru: {
    bands: "Мои коллективы", upcoming: "Предстоящие", past: "Прошедшие",
    noBands: "У вас пока нет коллективов. Вступить в коллектив можно через его чат в Telegram.",
    noPerformances: "В этом разделе пока нет доступных выступлений.",
    draft: "Открыть личный черновик", restricted: "Черновики доступны администраторам коллектива.",
    previous: "Назад", next: "Дальше", retry: "Повторить", loading: "Загрузка…",
    auth: "Откройте приложение через бота в Telegram.", performances: "К выступлениям",
  },
  en: {
    bands: "My bands", upcoming: "Upcoming", past: "Past",
    noBands: "You have no bands yet. You can join a band from its Telegram chat.",
    noPerformances: "There are no available performances here yet.",
    draft: "Open private draft", restricted: "Drafts are available to band admins.",
    previous: "Previous", next: "Next", retry: "Try again", loading: "Loading…",
    auth: "Open the app from the bot in Telegram.", performances: "Back to performances",
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

function element(tag, label, attributes = {}) {
  const node = document.createElement(tag);
  node.textContent = label;
  for (const [key, value] of Object.entries(attributes)) node.setAttribute(key, value);
  return node;
}

export function workspaceText(language, key) {
  return copy[language === "en" ? "en" : "ru"][key];
}

export async function showWorkspace({ api, authenticated, backButton }) {
  const main = document.getElementById("workspace-home");
  const title = document.getElementById("workspace-title");
  const content = document.getElementById("workspace-content");
  const status = document.getElementById("workspace-status");
  const band = workspaceParams.get("band");
  const past = workspaceParams.get("past") === "1" ? "1" : "0";
  const page = Number(workspaceParams.get("page")) || 0;
  let language = navigator.language.startsWith("ru") ? "ru" : "en";
  const t = (key) => workspaceText(language, key);
  main.hidden = false;
  title.textContent = t("bands");
  if (!authenticated) {
    status.textContent = t("auth");
    return;
  }
  if (band) {
    backButton?.show?.();
    backButton?.onClick?.(() => location.assign(bandUrl()));
  }
  async function load() {
    content.replaceChildren();
    status.textContent = t("loading");
    try {
      const data = band
        ? await api("performances", { band, past, page })
        : await api("bands");
      language = data.language === "en" ? "en" : "ru";
      document.documentElement.lang = language;
      title.textContent = band ? data.band : t("bands");
      status.textContent = "";
      if (band) showPerformances(data);
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
    } catch (error) {
      status.textContent = error.message;
      const retry = element("button", t("retry"), { type: "button" });
      retry.addEventListener("click", load);
      if (band) content.append(element("a", t("bands"), { href: bandUrl() }));
      content.append(retry);
    }
  }
  function showPerformances(data) {
    const nav = element("nav", "", { class: "workspace-nav" });
    nav.append(element("a", t("bands"), { href: bandUrl() }));
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
    const pages = element("nav", "", { class: "workspace-nav" });
    if (page > 0) pages.append(element("a", t("previous"), { href: bandUrl(band, past, page - 1) }));
    if (data.has_next) pages.append(element("a", t("next"), { href: bandUrl(band, past, page + 1) }));
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
