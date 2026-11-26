const words = {
  ru: {
    title: "Входящие запросы", personal: "Мне ответить", admin: "Мне как администратору",
    back: "К входящим запросам", empty: "Сейчас нет запросов, требующих решения.",
    team_invite: "Приглашение в коллектив", roles: "Предложены новые роли",
    gig: "Приглашение на выступление", date_change: "Подтвердить новую дату",
    join: "Заявка на вступление", role_request: "Заявка на новые роли",
    catalog: "Предложение по репертуару", replacement: "Подобрать подмену",
    approve_replacement: "Утвердить подмену", setlist: "Предложение сет-листа",
    approve: "Запрос на утверждение сет-листа", reopen: "Запрос на возврат сет-листа в работу",
    ready: "Выступление готово", checklist: "Готовность выступления",
    noTeam: "Состав не задан", noRoles: "Состав · нужных ролей нет", optionalTeam: "Состав не требуется",
    slots: (count, total) => `Состав · ${count} из ${total} ролей подтверждены`,
    replacements: count => count ? `Открытые подмены · ${count}` : "Подмены · открытых нет",
    setlistReady: "Сет-лист · утверждён", setlistDraft: "Сет-лист · в работе",
    timingReady: "Тайминг · готов", timingDraft: "Тайминг · предварительный",
    timingEmpty: "Тайминг · ещё не готов",
  },
  en: {
    title: "Incoming requests", personal: "For me", admin: "For me as an admin",
    back: "Back to incoming requests", empty: "There are no requests that need a decision.",
    team_invite: "Band invitation", roles: "New roles offered",
    gig: "Performance invitation", date_change: "Confirm the new date",
    join: "Membership request", role_request: "Request for new roles",
    catalog: "Repertoire suggestion", replacement: "Find a substitute",
    approve_replacement: "Approve substitute", setlist: "Setlist proposal",
    approve: "Setlist approval request", reopen: "Request to reopen the setlist",
    ready: "Performance ready", checklist: "Performance readiness",
    noTeam: "Lineup not set", noRoles: "Lineup · no required roles", optionalTeam: "Lineup not required",
    slots: (count, total) => `Lineup · ${count} of ${total} roles confirmed`,
    replacements: count => count ? `Open substitution requests · ${count}` : "Substitutions · none open",
    setlistReady: "Setlist · approved", setlistDraft: "Setlist · in progress",
    timingReady: "Schedule · ready", timingDraft: "Schedule · preliminary",
    timingEmpty: "Schedule · not ready yet",
  },
};

export function inboxLabel(language, key = "title") { return words[language][key]; }

function element(tag, text, attributes = {}) {
  const node = document.createElement(tag);
  node.textContent = text;
  for (const [name, value] of Object.entries(attributes)) node.setAttribute(name, value);
  return node;
}

export function showInbox({ root, data, language, dateText, href }) {
  const t = words[language];
  for (const section of ["personal", "admin"]) {
    if (!data[section].length) continue;
    const list = element("ul", "", { class: "workspace-list" });
    for (const row of data[section]) {
      const item = element("li", "");
      const metadata = [row.band, row.title, row.date ? dateText(row.date) : "", row.subject].filter(Boolean);
      item.append(element("a", t[row.kind], { href: href(row) }), element("p", metadata.join(" · ")));
      list.append(item);
    }
    root.append(element("h2", t[section]), list);
  }
  if (!data.personal.length && !data.admin.length) root.append(element("p", t.empty));
}

export function showReadiness({ root, readiness, approved, language, roster, timing }) {
  const t = words[language];
  const section = element("details", "", { class: "performance-readiness", "aria-labelledby": "readiness-title" });
  const list = element("ul", "", { class: "workspace-list" });
  const slot = readiness.missing_slot ? `&focus=role-${readiness.missing_slot}` : "";
  const swap = readiness.replacement ? `&focus=replacement-${readiness.replacement}` : "";
  const labels = [
    [readiness.optional ? t.optionalTeam : readiness.slots ? t.slots(readiness.confirmed, readiness.slots) : readiness.configured ? t.noRoles : t.noTeam,
      roster + slot, readiness.optional || (readiness.configured && readiness.confirmed === readiness.slots)],
    [t.replacements(readiness.replacements), roster + swap, !readiness.replacements],
    [t[approved ? "setlistReady" : "setlistDraft"], "#shared-list", approved],
    [t[readiness.timing === "ready" ? "timingReady" : readiness.timing === "preliminary" ? "timingDraft" : "timingEmpty"],
      timing, readiness.timing === "ready"],
  ];
  const count = labels.filter(([, , ready]) => ready).length;
  const heading = element("summary", `${t[readiness.ready ? "ready" : "checklist"]} · ${count}/${labels.length}`,
    { id: "readiness-title", class: `readiness-toggle ${readiness.ready ? "status-ready" : "status-pending"}` });
  for (const [label, href, ready] of labels) {
    const item = element("li", "");
    item.append(element("a", label, { href, class: ready ? "status-ready" : "status-pending" }));
    list.append(item);
  }
  section.append(heading, list);
  root.append(section);
}
