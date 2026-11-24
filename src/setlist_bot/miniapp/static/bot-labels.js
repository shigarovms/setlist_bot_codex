import { labels } from "./bot-labels-data.js";

export function withBotLabels(screen, text) {
  return {
    ru: { ...text.ru, ...labels.ru[screen] },
    en: { ...text.en, ...labels.en[screen] },
  };
}

export function fillButtonLabel(label, values) {
  return label.replace(/\{(\w+)\}/g, (placeholder, key) => values[key] ?? placeholder);
}
