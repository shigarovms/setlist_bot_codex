import test from "node:test";
import assert from "node:assert/strict";
import { DraftPersistence } from "../src/setlist_bot/miniapp/static/persistence.js";

const song = (id) => ({
  kind: "song",
  song_id: id,
  version: 1,
  pause_before: null,
});
const deferred = () => {
  let resolve, reject;
  const promise = new Promise((ok, no) => {
    resolve = ok;
    reject = no;
  });
  return { promise, resolve, reject };
};
test("autosave acknowledgement keeps edits made while request was in flight", async () => {
  const calls = [],
    first = deferred();
  const saver = new DraftPersistence(
    0,
    [],
    async (payload) => {
      calls.push(structuredClone(payload));
      return calls.length === 1
        ? first.promise
        : { version: 2, entries: payload.entries };
    },
    () => {},
    () => {},
  );
  saver.pause();
  saver.setEntries([song("A")]);
  const request = saver.saveOnce();
  saver.setEntries([song("A"), song("B")]);
  first.resolve({ version: 1, entries: [song("A")] });
  await request;
  assert.equal(saver.current.length, 2);
  assert.equal(saver.dirty(), true);
  await saver.flush();
  assert.deepEqual(
    calls.map((x) => x.version),
    [0, 1],
  );
  assert.equal(saver.dirty(), false);
});
test("lost response retries the exact same immutable request before newer edits", async () => {
  const calls = [];
  const saver = new DraftPersistence(
    0,
    [],
    async (payload) => {
      calls.push(structuredClone(payload));
      if (calls.length === 1) throw Error("offline");
      return { version: calls.length === 2 ? 1 : 2, entries: payload.entries };
    },
    () => {},
    () => {},
  );
  saver.pause();
  saver.setEntries([song("A")]);
  await assert.rejects(saver.saveOnce());
  saver.setEntries([song("B")]);
  await saver.flush();
  assert.deepEqual(calls[0], calls[1]);
  assert.notEqual(calls[1].request_id, calls[2].request_id);
  assert.equal(calls[2].entries[0].song_id, "B");
  assert.equal(calls[2].version, 1);
});
test("conflict blocks automatic overwrites and retains local entries", async () => {
  let calls = 0;
  const error = Object.assign(Error("conflict"), {
    data: { error: "conflict" },
  });
  const saver = new DraftPersistence(
    0,
    [],
    async () => {
      calls++;
      throw error;
    },
    () => {},
    () => {},
  );
  saver.pause();
  saver.setEntries([song("A")]);
  await assert.rejects(saver.flush());
  await assert.rejects(saver.flush());
  assert.equal(calls, 1);
  assert.equal(saver.current[0].song_id, "A");
  assert.equal(saver.blocked, true);
});

test("gap-only edit is saved and a late acknowledgement preserves a newer gap", async () => {
  const calls = [], first = deferred();
  const saver = new DraftPersistence(0, [song("A"), song("A")], async (payload) => {
    calls.push(structuredClone(payload));
    return calls.length === 1 ? first.promise : {version: 2, ...payload};
  }, () => {}, () => {}, 5);
  saver.pause();
  saver.setDraft([song("A"), song("A")], 30);
  assert.equal(saver.dirty(), true);
  const request = saver.saveOnce();
  saver.setDraft([song("A"), song("A")], 0);
  first.resolve({version: 1, entries: [song("A"), song("A")], default_pause_seconds: 30});
  await request;
  assert.equal(saver.defaultPause, 0);
  assert.equal(saver.dirty(), true);
  await saver.flush();
  assert.deepEqual(calls.map(p => p.default_pause_seconds), [30, 0]);
  assert.equal(saver.dirty(), false);
});

test("lost response retries the same gap before saving a newer list and gap", async () => {
  const calls = [];
  const saver = new DraftPersistence(0, [], async (payload) => {
    calls.push(structuredClone(payload));
    if (calls.length === 1) throw Error("offline");
    return {...payload, version: calls.length - 1};
  }, () => {}, () => {}, 5);
  saver.pause();
  saver.setDraft([song("A")], 20);
  await assert.rejects(saver.saveOnce());
  saver.setDraft([song("B")], 0);
  await saver.flush();
  assert.deepEqual(calls[0], calls[1]);
  assert.equal(calls[2].default_pause_seconds, 0);
  assert.equal(calls[2].entries[0].song_id, "B");
});
