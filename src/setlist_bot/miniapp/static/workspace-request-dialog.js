function el(tag, text = "", attributes = {}) {
  const node = document.createElement(tag);
  node.textContent = text;
  for (const [key, value] of Object.entries(attributes)) node.setAttribute(key, value);
  return node;
}

function button(text, handler) {
  const node = el("button", text, { type: "button" });
  node.addEventListener("click", handler);
  return node;
}

export async function pollRequest(attempt, operation, isCurrent) {
  let result = attempt.receipt ? await attempt.api("catalog_status", { request: attempt.receipt })
    : await attempt.api(operation, attempt.payload, "POST");
  attempt.receipt = result.request;
  for (let i = 0; result.status === "pending" && i < 8; i++) {
    await new Promise(resolve => setTimeout(resolve, 1000));
    if (!isCurrent()) return null;
    result = await attempt.api("catalog_status", { request: attempt.receipt });
  }
  return isCurrent() ? result : null;
}

export function requestDialog({ title, labels: t, operation, request, refresh, status }) {
  const dialog = el("dialog", "", { class: "shared-tools", "aria-label": title });
  const body = el("div");
  const message = el("p", "", { role: "status" });
  const actions = el("div", "", { class: "toolbar" });
  const retry = button(t.check, () => void deliver());
  retry.hidden = true;
  actions.append(retry, button(t.close, () => dialog.close()));
  dialog.append(el("h2", title), body, message, actions);
  document.body.append(dialog);
  dialog.addEventListener("close", () => dialog.remove(), { once: true });
  let attempt, busy = false;
  dialog.showModal();

  async function finish() {
    dialog.close();
    await refresh?.();
    status(t.done);
  }

  async function deliver() {
    if (!attempt || busy) return;
    busy = true;
    const current = attempt;
    retry.hidden = true;
    message.textContent = t.busy;
    try {
      const result = await pollRequest(current, operation, () => dialog.open);
      if (!result || result.status === "pending") return;
      attempt = null;
      if (result.status === "failed") {
        message.textContent = result.message;
        current.failure?.(result);
      }
      else {
        message.textContent = "";
        await current.complete(result);
      }
    } catch (error) {
      if (!current.receipt && error.data && !["busy", "unavailable"].includes(error.data.error)) attempt = null;
      message.textContent = error.message;
    } finally {
      busy = false;
      retry.hidden = !attempt;
      if (!attempt) current.trigger.disabled = false;
    }
  }

  return {
    body, message, dialog,
    send(trigger, payload, api = request, complete = finish, failure = null) {
      if (attempt || busy) return;
      trigger.disabled = true;
      attempt = { trigger, api, complete, failure, payload: { ...payload, request_id: crypto.randomUUID() } };
      void deliver();
    },
  };
}
