import { withBotLabels } from "./bot-labels.js";
import { requestDialog } from "./workspace-request-dialog.js";

const words = withBotLabels("workspace-band-settings", {
  ru: {
    edit: "Изменить настройки", setlists: "Сет-листы",
    chat: "Чат коллектива", style: "Оформление сет-листов в Telegram",
    suggestions: "Кто может предлагать изменения и варианты",
    everyone: "Участники коллектива", admins: "Только администраторы",
    pause: "Пауза по умолчанию, сек.", pauseHint: "Применяется к новым выступлениям.",
    language: "Язык сообщений в чате", reminders: "Напоминания о неутверждённых сет-листах",
    reminderTime: "Бот напоминает накануне выступления с 14:00 до 15:00 по Москве.",
    enabled: "Включены", disabled: "Отключены", save: "Сохранить настройки",
    close: "Закрыть", check: "Проверить результат", busy: "Сохраняем настройки…",
    done: "Настройки коллектива сохранены.",
    conflict: "Настройки уже изменились. Закройте окно, чтобы увидеть актуальные значения.",
  },
  en: {
    edit: "Edit settings", setlists: "Setlists", chat: "Band chat",
    style: "Setlist appearance in Telegram",
    suggestions: "Who can suggest edits and variants", everyone: "Band members",
    admins: "Admins only", pause: "Default pause in seconds", pauseHint: "Applies to new performances.",
    language: "Chat message language", reminders: "Reminders about unapproved setlists",
    reminderTime: "The bot sends a reminder the day before the performance, from 14:00 to 15:00 Moscow time.",
    enabled: "Enabled", disabled: "Disabled", save: "Save settings", close: "Close",
    check: "Check result", busy: "Saving settings…", done: "Band settings saved.",
    conflict: "The settings have changed. Close this window to see the latest values.",
  },
});

export function bandSettingsTitle(language) { return words[language].title; }

function el(tag, text = "", attributes = {}) {
  const node = document.createElement(tag);
  node.textContent = text;
  for (const [key, value] of Object.entries(attributes)) node.setAttribute(key, value);
  return node;
}

export function showBandSettings({ root, data, language, request, refresh, status }) {
  const t = words[language];
  const current = data.settings;
  const styleChoices = { article: t.article, mono: t.mono };
  const contributorChoices = { false: t.everyone, true: t.admins };
  const languageChoices = { ru: "Русский", en: "English" };
  const reminderChoices = { true: t.enabled, false: t.disabled };
  const setlistFields = [
    ["setlist_style", t.style, styleChoices],
    ["admins_only_suggestions", t.suggestions, contributorChoices],
  ];
  const chatFields = [
    ["language", t.language, languageChoices],
    ["reminders_enabled", t.reminders, reminderChoices, t.reminderTime],
  ];
  if (data.admin) {
    const edit = el("button", t.edit, { type: "button" });
    edit.addEventListener("click", openEditor);
    root.append(edit);
    setlistFields.push(["default_pause_seconds", t.pause, null, t.pauseHint]);
  }
  appendSummary(t.setlists, setlistFields);
  if (data.admin) appendSummary(t.chat, chatFields);

  function appendSummary(heading, fields) {
    const summary = el("dl", "", { class: "song-details" });
    for (const [key, label, choices, hint] of fields) {
      const value = choices ? choices[String(current[key])] : current[key];
      const row = el("dd", value);
      if (hint) row.append(el("br"), el("small", hint));
      summary.append(el("dt", label), row);
    }
    root.append(el("h3", heading), summary);
  }

  function openEditor() {
    const modal = requestDialog({
      title: t.edit, labels: t, operation: "band_settings_save", request, refresh, status,
    });
    const form = el("form", "", { class: "import-fields" });
    const controls = new Map();
    for (const [section, fields] of [[t.setlists, setlistFields], [t.chat, chatFields]]) {
      const fieldset = el("fieldset", "", { class: "import-fields" });
      fieldset.append(el("legend", section));
      for (const [key, label, choices, hint] of fields) {
        const id = `band-setting-${key}`;
        const control = choices ? el("select", "", { id })
          : el("input", "", { id, type: "number", min: "0", max: "86400", step: "1", required: "" });
        for (const [value, title] of Object.entries(choices ?? {}))
          control.append(el("option", title, { value }));
        control.value = String(current[key]);
        controls.set(key, control);
        fieldset.append(el("label", label, { for: id }), control);
        if (hint) {
          control.setAttribute("aria-describedby", `${id}-hint`);
          fieldset.append(el("small", hint, { id: `${id}-hint` }));
        }
      }
      form.append(fieldset);
    }
    const save = el("button", t.save, { type: "submit", class: "primary" });
    form.append(save);
    form.addEventListener("submit", event => {
      event.preventDefault();
      if (save.disabled) return;
      const expected = {}, changes = {};
      for (const [key, control] of controls) {
        const previous = current[key];
        const value = typeof previous === "boolean" ? control.value === "true"
          : typeof previous === "number" ? control.valueAsNumber : control.value;
        if (previous === value) continue;
        expected[key] = previous;
        changes[key] = value;
      }
      if (!Object.keys(changes).length) { modal.dialog.close(); return; }
      modal.send(save, { expected, changes }, request, undefined, result => {
        if (result.error !== "conflict") return;
        modal.message.textContent = t.conflict;
        modal.dialog.addEventListener("close", () => void refresh(), { once: true });
      });
    });
    modal.body.append(form);
  }
}
