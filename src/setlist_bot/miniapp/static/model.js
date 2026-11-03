// RU: Только локальная работа с порядком. EN: The server owns permissions and persistence.
export const clone = (value) => JSON.parse(JSON.stringify(value));
export function clock(seconds) {
  const n = Math.max(0, Math.trunc(seconds));
  return `${Math.floor(n / 60)
    .toString()
    .padStart(2, "0")}:${(n % 60).toString().padStart(2, "0")}`;
}
export function total(entries, gap) {
  return entries.reduce(
    (sum, item, index) =>
      sum +
      item.duration_seconds +
      (item.kind === "pause"
        ? 0
        : (item.pause_before ??
          (index === 0 || entries[index - 1].kind === "pause" ? 0 : gap))),
    0,
  );
}
export function numbered(entries) {
  let n = 0;
  const width =
    entries.filter((item) => item.kind !== "pause").length >= 10 ? 2 : 1;
  return entries.map((item) =>
    item.kind === "pause" ? "" : String(++n).padStart(width, "0"),
  );
}
export function wire(entries) {
  return entries.map((item) =>
    item.kind === "pause"
      ? { kind: "pause", duration_seconds: item.duration_seconds,
          ...(item.pause_label != null ? {pause_label: item.pause_label} : {}) }
      : {
          kind: "song",
          song_id: item.song_id,
          version: item.version,
          pause_before: item.pause_before ?? null,
        },
  );
}
export function move(entries, from, to) {
  const out = clone(entries);
  const [item] = out.splice(from, 1);
  out.splice(Math.max(0, Math.min(to, out.length)), 0, item);
  return out;
}
export function asText(entries) {
  return entries
    .map((item) =>
      item.kind === "pause"
        ? clock(item.duration_seconds) + (item.pause_label ? `  ${item.pause_label}` : "")
        : item.title,
    )
    .join("\n");
}
