import { withBotLabels } from "./bot-labels.js";
import { requestDialog } from "./workspace-request-dialog.js";

const words = withBotLabels("workspace-help", {
  ru: {
    title: "Справка и поддержка", commands: "Команды Telegram",
    prompt: "Опишите, что вы хотели сделать и что произошло.",
    routing: "В поддержку уйдут текст обращения и ваш профиль Telegram. Ответ придёт в личный чат с ботом.",
    message: "Текст обращения", send: "Отправить обращение", empty: "Введите текст обращения.",
    check: "Проверить результат", close: "Закрыть",
    busy: "Сохраняем обращение…", done: "Обращение принято. Ответ придёт в личный чат с ботом.",
  },
  en: {
    title: "Help and support", commands: "Telegram commands",
    prompt: "Describe what you were trying to do and what happened.",
    routing: "Support receives your message and Telegram profile. The bot will reply in your private chat.",
    message: "Your message", send: "Send request", empty: "Enter a message.",
    check: "Check result", close: "Close", busy: "Submitting your request…",
    done: "Request accepted. The bot will reply in your private chat.",
  },
});

export function helpTitle(language) { return words[language].title; }

function el(tag, text = "", attributes = {}) {
  const node = document.createElement(tag);
  node.textContent = text;
  for (const [key, value] of Object.entries(attributes)) node.setAttribute(key, value);
  return node;
}

export function showHelp({ root, topics, language, request, status }) {
  const t = words[language];
  const items = el("ul", "", { class: "workspace-list" });
  for (const { title, text, commands } of topics) {
    const item = el("li");
    const topic = el("details", "", { name: "workspace-help-topic" });
    const reference = el("details");
    reference.append(el("summary", t.commands), el("p", commands, { class: "workspace-help-commands" }));
    topic.append(el("summary", title), el("p", text), reference);
    item.append(topic);
    items.append(item);
  }
  const contact = el("button", t.contact, { type: "button" });
  contact.addEventListener("click", contactSupport);
  root.append(contact, items);

  function contactSupport() {
    const modal = requestDialog({
      title: t.contact, labels: t, operation: "support_submit", request, status,
    });
    const form = el("form", "", { class: "import-fields" });
    const text = el("textarea", "", {
      id: "support-message", maxlength: "4000", rows: "5", required: "", autofocus: "",
    });
    const send = el("button", t.send, { type: "submit", class: "primary" });
    form.append(
      el("p", t.prompt), el("label", t.message, { for: text.id }), text, el("small", t.routing), send,
    );
    form.addEventListener("submit", event => {
      event.preventDefault();
      if (send.disabled) return;
      if (!text.value.trim()) { modal.message.textContent = t.empty; return; }
      modal.send(send, { text: text.value });
    });
    modal.body.append(form);
    text.focus();
  }
}
