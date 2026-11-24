import { withBotLabels } from "./bot-labels.js";
import { pdfAppearanceFields } from "./workspace-export.js";
import { requestDialog } from "./workspace-request-dialog.js";

const labels = withBotLabels("workspace-profile", {
  ru: {
    name: "Имя или сценический псевдоним",
    roles: "Профессиональные роли", city: "Город", substitutions: "Готовность к подменам",
    travel: "Выезды в другие города", about: "О себе", yes: "Да", no: "Нет", empty: "Не указано",
    choose: "Уточнить роли", review: "Подтвердите профессиональные роли",
    choice: "Выберите роль", change: "Вернуться к анкете",
    rolesHint: "Укажите роли в нужном порядке, по одной на строку или через запятую.",
    soundHint: "Выберите FOH, MON или общее название, если не хотите указывать специализацию.",
    cleared: "Список профессиональных ролей будет очищен.",
    visibility: "Анкета доступна администраторам коллективов, где вы в команде или в списке знакомых исполнителей.",
    language: "Язык интерфейса",
    pdfHint: "Эти настройки применяются к PDF для музыкантов в боте и приложении.",
    confirm: "Сохранить", check: "Проверить результат", close: "Закрыть",
    busy: "Сохраняем…", done: "Изменения сохранены.",
  },
  en: {
    name: "Name or stage name", roles: "Professional roles",
    city: "City", substitutions: "Available to substitute", travel: "Available to travel",
    about: "About me", yes: "Yes", no: "No", empty: "Not specified",
    choose: "Review roles", review: "Confirm your professional roles",
    choice: "Choose a role", change: "Back to profile form",
    rolesHint: "List roles in order, one per line or separated by commas.",
    soundHint: "Choose FOH, MON, or a general role if you prefer to leave your specialty unspecified.",
    cleared: "Your professional roles will be cleared.",
    visibility: "Your profile is available to admins of bands where you are on the team or listed as a contact.",
    language: "Interface language",
    pdfHint: "These settings apply to musician PDFs in the bot and the app.",
    confirm: "Save", check: "Check result", close: "Close", busy: "Saving…", done: "Changes saved.",
  },
});
const locales = [["ru", "Русский"], ["en", "English"]];
const fields = ["name", "roles", "city", "substitutions", "travel", "about"];
const preferences = ["substitutions", "travel"];

export function profileLabel(language, key) { return labels[language][key]; }

function el(tag, value = "", attributes = {}) {
  const node = document.createElement(tag);
  node.textContent = value;
  for (const [key, attr] of Object.entries(attributes)) node.setAttribute(key, attr);
  return node;
}

function button(text, handler) {
  const node = el("button", text, { type: "button" });
  node.addEventListener("click", handler);
  return node;
}

function select(choices, value, id) {
  const node = el("select", "", { id });
  for (const [key, label] of choices) node.append(el("option", label, { value: key }));
  node.value = value;
  return node;
}

export function showProfile({ root, data, language, request, preview, refresh, status }) {
  const t = labels[language];
  const current = data.profile;
  const details = el("dl", "", { class: "song-details" });
  const valueLabel = value => value === true ? t.yes : value === false ? t.no : value || t.empty;
  for (const key of fields) {
    const value = key === "roles" ? current.roles?.join(", ") : current[key];
    details.append(el("dt", t[key]), el("dd", valueLabel(value)));
  }
  details.append(el("dt", t.language), el("dd", locales.find(([key]) => key === language)[1]),
    el("dt", t.pdf), el("dd", `${data.pdf.fonts[data.pdf.settings.font]} · ${data.pdf.settings.size} pt`));
  const actions = el("div", "", { class: "toolbar" });
  actions.append(button(t.edit, edit), button(t.language, changeLanguage), button(t.pdf, changePdf));
  root.append(details, actions, el("p", t.visibility));

  function open(title) {
    return requestDialog({ title, labels: t, operation: "profile_action", request, refresh, status });
  }

  function changeLanguage() {
    const window = open(t.language);
    const choices = select(locales, language, "profile-language");
    const save = button(t.confirm, () => {
      if (choices.value === language) { window.dialog.close(); return; }
      window.send(save, { action: "language", expected: language, language: choices.value }, request,
        async result => {
          window.dialog.close();
          await refresh();
          status(labels[result.language].done);
        });
    });
    window.body.append(el("label", t.language, { for: choices.id }), choices, save);
  }

  function changePdf() {
    const window = open(t.pdf);
    const form = el("div", "", { class: "import-fields" });
    const readAppearance = pdfAppearanceFields(form, data.pdf, language, "profile-pdf");
    const save = button(t.confirm, () => window.send(save, {
      action: "pdf_settings", expected: data.pdf.settings, settings: readAppearance(),
    }));
    window.body.append(el("p", t.pdfHint), form, save);
  }

  function edit() {
    const window = open(t.edit);
    const form = el("form", "", { class: "import-fields" });
    const originalRoles = current.roles?.join("\n") ?? "";
    const inputs = {};
    const commit = el("button", t.save, { type: "submit", class: "primary" });
    for (const key of fields) {
      const id = `profile-${key}`;
      let input;
      if (preferences.includes(key)) {
        input = select([["", t.empty], ["true", t.yes], ["false", t.no]],
          String(current[key] ?? ""), id);
      } else if (key === "roles" || key === "about") {
        input = el("textarea", "", { id, rows: "3", maxlength: key === "roles" ? "5000" : "1000" });
        input.value = key === "roles" ? originalRoles : current[key] ?? "";
      } else {
        input = el("input", "", { id, maxlength: "200" });
        input.value = current[key] ?? "";
      }
      inputs[key] = input;
      form.append(el("label", t[key], { for: id }), input);
      if (key === "roles") {
        input.setAttribute("aria-describedby", "profile-roles-hint");
        form.append(el("small", t.rolesHint, { id: "profile-roles-hint" }));
      }
    }
    inputs.roles.addEventListener("input", () => {
      commit.textContent = inputs.roles.value === originalRoles ? t.save : t.choose;
    });
    form.append(commit);
    window.body.append(form);
    form.addEventListener("submit", async event => {
      event.preventDefault();
      if (commit.disabled) return;
      const patch = {};
      for (const key of fields.filter(key => key !== "roles")) {
        const raw = inputs[key].value;
        const isPreference = preferences.includes(key);
        const value = isPreference ? (raw === "" ? null : raw === "true") : raw.trim();
        if (value !== (current[key] ?? (isPreference ? null : ""))) patch[key] = value;
      }
      const command = { action: "profile_save", version: data.version, patch };
      if (inputs.roles.value === originalRoles) {
        if (Object.keys(patch).length) window.send(commit, command);
        else window.dialog.close();
        return;
      }
      commit.disabled = true;
      form.inert = true;
      window.message.textContent = "";
      try {
        const source = inputs.roles.value;
        const plan = await preview({ version: data.version, source });
        if (window.dialog.open) reviewRoles(plan, source, command);
      } catch (error) {
        window.message.textContent = error.message;
      } finally {
        commit.disabled = false;
        form.inert = false;
      }
    });

    function reviewRoles(plan, source, command) {
      const review = el("form", "", { class: "import-fields" });
      const heading = el("h3", t.review, { tabindex: "-1" });
      const save = el("button", t.save, { type: "submit", class: "primary" });
      review.append(heading);
      if (!plan.roles.length) review.append(el("p", t.cleared));
      const choices = plan.roles.map((role, index) => {
        if (role.title) {
          review.append(el("p", role.title));
          return () => role.title;
        }
        const input = select([["", t.choice], ...role.choices.map(choice => [choice, choice])],
          "", `profile-role-${index}`);
        input.required = true;
        review.append(el("label", role.source, { for: input.id }), input);
        if (role.sound) review.append(el("small", t.soundHint));
        return () => input.value;
      });
      const back = button(t.change, () => {
        if (save.disabled) return;
        window.body.replaceChildren(form);
        window.message.textContent = "";
        inputs.roles.focus();
      });
      const controls = el("div", "", { class: "toolbar" });
      controls.append(save, back);
      review.append(controls);
      review.addEventListener("submit", event => {
        event.preventDefault();
        window.send(save, { ...command, roles: {
          source, language: plan.language, choices: choices.map(read => read()),
        } });
      });
      window.body.replaceChildren(review);
      heading.focus();
    }
  }
}
