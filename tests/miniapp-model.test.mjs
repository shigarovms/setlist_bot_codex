import test from "node:test";
import assert from "node:assert/strict";
import {
  total,
  numbered,
  move,
  wire,
  clock,
  asText,
} from "../src/setlist_bot/miniapp/static/model.js";

test("timing preserves explicit pauses, first lead-in and zero overrides", () => {
  const song = {
    kind: "song",
    song_id: "a",
    version: 1,
    title: "A",
    duration_seconds: 100,
    pause_before: null,
  };
  const list = [
    { ...song, pause_before: 7 },
    { kind: "pause", duration_seconds: 180 },
    song,
    { ...song, pause_before: 0 },
    song,
  ];
  assert.equal(total(list, 5), 592);
  assert.deepEqual(numbered(list), ["1", "", "2", "3", "4"]);
  assert.deepEqual(numbered(Array.from({ length: 10 }, () => song)), [
    "01",
    "02",
    "03",
    "04",
    "05",
    "06",
    "07",
    "08",
    "09",
    "10",
  ]);
  assert.equal(clock(6003), "100:03");
});
test("moving does not merge repeated songs or discard lead-ins; wire exposes only save fields", () => {
  const list = [
    { kind: "song", song_id: "a", version: 1, pause_before: 3, key: "first" },
    { kind: "pause", duration_seconds: 120, key: "pause" },
    {
      kind: "song",
      song_id: "a",
      version: 1,
      pause_before: null,
      key: "second",
    },
  ];
  assert.deepEqual(
    move(list, 2, 0).map((x) => x.key),
    ["second", "first", "pause"],
  );
  assert.deepEqual(
    list.map((x) => x.key),
    ["first", "pause", "second"],
  );
  assert.deepEqual(wire(list)[0], {
    kind: "song",
    song_id: "a",
    version: 1,
    pause_before: 3,
  });
});

test("pause labels keep empty separate from default and survive move and wire", () => {
  const list = [
    {kind:"pause",duration_seconds:30,pause_label:"Крабы уносят микрофон"},
    {kind:"pause",duration_seconds:40,pause_label:""},
    {kind:"pause",duration_seconds:50},
  ];
  const saved = wire(move(list, 0, 2));
  assert.equal(saved[0].pause_label, "");
  assert.equal("pause_label" in saved[1], false);
  assert.equal(saved[2].pause_label, list[0].pause_label);
  assert.equal(total(saved, 5), 120);
  assert.equal(asText(list), "00:30  Крабы уносят микрофон\n00:40\n00:50");
});
