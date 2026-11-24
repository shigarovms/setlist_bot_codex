import { requestDialog } from "./workspace-request-dialog.js";

const words = {
  ru: {
    title: "Корзина коллективов", empty: "В корзине нет коллективов, которые вы можете восстановить.",
    remove: "Переместить коллектив в корзину", restore: "Восстановить",
    until: "Можно восстановить до", next: "Продолжить", confirm: "Переместить в корзину",
    consequence: "Коллектив исчезнет из списка и будет отключён от чата Telegram. Восстановить его можно в течение 30 дней.",
    name: "Введите название коллектива для подтверждения",
    match: "Введите название коллектива так, как оно указано выше.",
    restoreChat: "Будет восстановлена привязка к прежнему чату, если он свободен.",
    close: "Закрыть", check: "Проверить результат", busy: "Обрабатываем запрос…",
    done: "Коллектив восстановлен.",
  },
  en: {
    title: "Band trash", empty: "There are no bands you can restore.",
    remove: "Move band to trash", restore: "Restore",
    until: "Can be restored until", next: "Continue", confirm: "Move to trash",
    consequence: "The band will disappear from the list and be unlinked from its Telegram chat. You can restore it for 30 days.",
    name: "Enter the band name to confirm", match: "Enter the band name as shown above.",
    restoreChat: "The original chat link will be restored if the chat is available.",
    close: "Close", check: "Check result", busy: "Processing your request…", done: "Band restored.",
  },
};

export function bandTrashTitle(language) { return words[language].title; }

function el(tag, text = "", attributes = {}) {
  const node = document.createElement(tag);
  node.textContent = text;
  for (const [key, value] of Object.entries(attributes)) node.setAttribute(key, value);
  return node;
}

export function bandDeleteButton({ data, band, language, request, onDelete }) {
  const t = words[language];
  const button = el("button", t.remove, { type: "button" });
  button.addEventListener("click", () => {
    const modal = requestDialog({ title: t.remove, labels: t, operation: "band_trash_action", request });
    const next = el("button", t.next, { type: "button" });
    next.addEventListener("click", () => modal.send(next, {
      action: "preview_delete", band, epoch: data.epoch,
    }, request, showConfirmation));
    modal.body.append(el("h3", data.band), el("p", t.consequence), next);

    function showConfirmation(preview) {
      const form = el("form", "", { class: "import-fields" });
      const name = el("input", "", { id: "band-trash-name", maxlength: "200", required: "" });
      const confirm = el("button", t.confirm, { type: "submit", class: "primary" });
      form.append(el("h3", preview.name), el("label", t.name, { for: name.id }), name, confirm);
      form.addEventListener("submit", event => {
        event.preventDefault();
        if (confirm.disabled) return;
        if (name.value.trim() !== preview.name) { modal.message.textContent = t.match; return; }
        modal.send(confirm, { action: "delete", preview: preview.request, name: name.value },
          request, () => { modal.dialog.close(); onDelete(); });
      });
      modal.body.replaceChildren(form);
      name.focus();
    }
  });
  return button;
}

export function showBandTrash({ root, data, language, dateText, request, refresh, status }) {
  const t = words[language];
  if (!data.bands.length) {
    root.append(el("p", t.empty));
    return;
  }
  const list = el("ul", "", { class: "workspace-list" });
  for (const row of data.bands) {
    const item = el("li");
    const button = el("button", t.restore, { type: "button" });
    button.addEventListener("click", () => {
      const modal = requestDialog({
        title: t.restore, labels: t, operation: "band_trash_action", request, refresh, status,
      });
      const confirm = el("button", t.restore, { type: "button", class: "primary" });
      confirm.addEventListener("click", () => modal.send(confirm, {
        action: "restore", band: row.id, epoch: row.epoch,
      }));
      modal.body.append(el("h3", row.name), el("p", t.restoreChat), confirm);
    });
    item.append(el("h2", row.name), el("p", `${t.until} ${dateText(row.until)}`), button);
    list.append(item);
  }
  root.append(list);
}
