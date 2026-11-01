import { clone, wire } from "./model.js";

// RU: Одна запись за раз. Повтор после обрыва связи отправляет тот же снимок и request_id.
// EN: Serialize writes; a late acknowledgement never replaces newer local edits.
export class DraftPersistence {
  constructor(version, entries, send, onSaved, onError, defaultPause = 5) {
    this.version = version;
    this.current = wire(entries);
    this.defaultPause = defaultPause;
    this.baseline = this.snapshot();
    this.send = send;
    this.onSaved = onSaved;
    this.onError = onError;
    this.pending = null;
    this.inflight = null;
    this.timer = null;
    this.blocked = false;
    this.paused = false;
  }
  dirty() {
    return (
      this.pending !== null || this.snapshot() !== this.baseline
    );
  }
  snapshot() {
    return JSON.stringify([this.current, this.defaultPause]);
  }
  setEntries(entries) {
    this.current = wire(entries);
    this.schedule();
  }
  setDraft(entries, defaultPause) {
    this.current = wire(entries);
    this.defaultPause = defaultPause;
    this.schedule();
  }
  pause() {
    this.paused = true;
    clearTimeout(this.timer);
  }
  resume() {
    this.paused = false;
    this.schedule();
  }
  schedule(delay = 1200) {
    clearTimeout(this.timer);
    if (this.paused || this.blocked || !this.dirty()) return;
    this.timer = setTimeout(async () => {
      let retry = 1200;
      try {
        await this.saveOnce();
      } catch {
        retry = 5000;
      }
      this.schedule(retry);
    }, delay);
  }
  async flush() {
    clearTimeout(this.timer);
    while (this.dirty()) await this.saveOnce();
  }
  saveOnce() {
    if (this.inflight) return this.inflight;
    if (!this.dirty()) return Promise.resolve();
    if (this.blocked) return Promise.reject(this.lastError);
    this.pending ??= {
      version: this.version,
      entries: clone(this.current),
      default_pause_seconds: this.defaultPause,
      request_id: crypto.randomUUID(),
    };
    const sent = this.pending;
    this.inflight = this.send(sent)
      .then((saved) => {
        const unchanged =
          this.snapshot() === JSON.stringify([sent.entries, sent.default_pause_seconds]);
        this.version = saved.version;
        const gap = saved.default_pause_seconds ?? sent.default_pause_seconds;
        this.baseline = JSON.stringify([wire(saved.entries), gap]);
        if (unchanged) {
          this.current = wire(saved.entries);
          this.defaultPause = gap;
        }
        this.pending = null;
        this.lastError = null;
        this.onSaved(saved, unchanged);
      })
      .catch((error) => {
        this.lastError = error;
        if (
          error.data?.error === "conflict" ||
          error.data?.error === "selection_catalog_changed"
        )
          this.blocked = true;
        this.onError(error);
        throw error;
      })
      .finally(() => {
        this.inflight = null;
      });
    return this.inflight;
  }
}
