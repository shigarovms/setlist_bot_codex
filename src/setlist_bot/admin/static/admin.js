'use strict';
// RU: Повторный клик блокируется в браузере; сервер независимо проверяет одноразовый nonce.
// EN: Browser UX is only an extra guard; the database enforces one-time actions.
for (const form of document.querySelectorAll('form[method="post"]')) {
  form.addEventListener('submit', () => {
    for (const button of form.querySelectorAll('button[type="submit"]')) button.disabled = true;
  });
}
if (document.querySelector('[data-metric]')) {
  setInterval(async () => {
    if (document.hidden) return;
    try {
      const response = await fetch('/api/overview', {credentials: 'same-origin', cache: 'no-store'});
      if (!response.ok || response.redirected) return;
      const data = await response.json();
      for (const element of document.querySelectorAll('[data-metric]')) {
        const value = data[element.dataset.metric];
        if (Number.isFinite(value)) element.textContent = String(value);
      }
    } catch (_) { /* An unavailable API must not erase the last known snapshot. */ }
  }, 30000);
}
