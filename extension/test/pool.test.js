/**
 * Tests for extension/lib/pool.js.
 *
 * Every fixture is a synthetic protobuf segment assembled from the dmproto
 * encoder helpers, so the tests never depend on the cached data/ snapshots.
 */

import test from "node:test";
import assert from "node:assert/strict";

import { encodeField, encodeVarint } from "../lib/dmproto.js";
import { TYPES } from "../lib/policy.js";
import {
  buildActionTable,
  countForSegment,
  dropSet,
  mergePool,
  poolStats,
  segmentCount,
  thinSegmentDrop,
} from "../lib/pool.js";

const ENCODER = new TextEncoder();

/**
 * Concatenate byte chunks.
 * @param {Uint8Array[]} chunks
 * @returns {Uint8Array}
 */
function concat(chunks) {
  let total = 0;
  for (const chunk of chunks) total += chunk.length;
  const output = new Uint8Array(total);
  let offset = 0;
  for (const chunk of chunks) {
    output.set(chunk, offset);
    offset += chunk.length;
  }
  return output;
}

/**
 * Encode a varint (wire type 0) field.
 * @param {number} fieldNumber
 * @param {number} value
 * @returns {Uint8Array}
 */
function varintField(fieldNumber, value) {
  return concat([encodeVarint(fieldNumber * 8), encodeVarint(value)]);
}

/**
 * Encode one DanmakuElem payload.
 * @param {{id: string|number, text?: string, time?: number, mode?: number}} options
 * @returns {Uint8Array}
 */
function element(options) {
  return concat([
    encodeField(12, ENCODER.encode(String(options.id))),
    encodeField(7, ENCODER.encode(options.text === undefined ? "弹幕" + options.id : options.text)),
    varintField(2, options.time === undefined ? 0 : options.time),
    varintField(3, options.mode === undefined ? 1 : options.mode),
  ]);
}

/**
 * Encode a whole seg.so payload from element definitions.
 * @param {Array<object>} elements
 * @returns {Uint8Array}
 */
function segment(elements) {
  return concat(elements.map((item) => encodeField(1, element(item))));
}

/**
 * Build a prefs object whose per-type action is `action` for every label.
 * @param {"show"|"hide"|"collapse"} action
 * @param {object} [extra]
 * @returns {object}
 */
function prefsWith(action, extra) {
  const perType = {};
  for (const label of TYPES) perType[label] = action;
  return {
    version: 1,
    enabled: true,
    min_confidence: 0.3,
    per_type: perType,
    density: { enabled: false, window_seconds: 10, max_visible: 30, exceed: "hide" },
    ...(extra || {}),
  };
}

/**
 * Build the labels map for a list of `[dmid, label, score]` triples.
 * @param {Array<[string, string, number]>} triples
 * @returns {Record<string, {label: string, score: number}>}
 */
function labelsOf(triples) {
  const labels = {};
  for (const [dmid, label, score] of triples) labels[dmid] = { label, score };
  return labels;
}

/**
 * Build one pool item.
 * @param {string} dmid
 * @param {number} timeMs
 * @returns {{dmid: string, text: string, time_ms: number, segment_index: number}}
 */
function item(dmid, timeMs) {
  return { dmid, text: "文本" + dmid, time_ms: timeMs, segment_index: 1 };
}

test("segmentCount rounds up to 360s segments and clamps to [1, 60]", () => {
  assert.equal(segmentCount(0), 1);
  assert.equal(segmentCount(-10), 1);
  assert.equal(segmentCount(NaN), 1);
  assert.equal(segmentCount(1), 1);
  assert.equal(segmentCount(360), 1);
  assert.equal(segmentCount(361), 2);
  assert.equal(segmentCount(720), 2);
  assert.equal(segmentCount(213), 1);
  assert.equal(segmentCount(3600), 10);
  assert.equal(segmentCount(21600), 60);
  assert.equal(segmentCount(100000), 60);
});

test("mergePool dedupes across segments, keeps first occurrence, sorts by time", () => {
  const first = segment([
    { id: "2", time: 300 },
    { id: "3", time: 400 },
  ]);
  const second = segment([
    { id: "3", time: 400 },
    { id: "1", time: 100 },
  ]);
  const pool = mergePool([
    { index: 2, bytes: second },
    { index: 1, bytes: first },
  ]);
  assert.deepEqual(
    pool.items.map((entry) => [entry.dmid, entry.time_ms, entry.segment_index]),
    [
      ["1", 100, 2],
      ["2", 300, 1],
      ["3", 400, 1],
    ]
  );
  assert.deepEqual(pool.stats, { raw: 4, unique: 3, duplicates: 1, segments: 2 });
  assert.deepEqual(pool.bySegment.get(1), ["2", "3"]);
  assert.deepEqual(pool.bySegment.get(2), ["3", "1"]);
});

test("mergePool accepts ArrayBuffer segments and keeps empty segments in stats", () => {
  const bytes = segment([{ id: "7", time: 700 }]);
  const arrayBuffer = bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength);
  const pool = mergePool([
    { index: 1, bytes: arrayBuffer },
    { index: 2, bytes: segment([]) },
  ]);
  assert.equal(pool.items.length, 1);
  assert.equal(pool.items[0].segment_index, 1);
  assert.deepEqual(pool.stats, { raw: 1, unique: 1, duplicates: 0, segments: 2 });
  assert.deepEqual(pool.bySegment.get(2), []);
});

test("mergePool tolerates missing or malformed input", () => {
  assert.deepEqual(mergePool(null).stats, { raw: 0, unique: 0, duplicates: 0, segments: 0 });
  const pool = mergePool([{ index: 1, bytes: new Uint8Array([0xff, 0xff, 0xff]) }]);
  assert.deepEqual(pool.stats, { raw: 0, unique: 0, duplicates: 0, segments: 1 });
  assert.equal(pool.bySegment.get(1).length, 0);
});

test("buildActionTable keeps every show decision and sorts actions by time", () => {
  const table = buildActionTable({
    items: [item("2", 200), item("1", 100), item("3", 300)],
    labels: labelsOf([
      ["1", "humor", 0.9],
      ["2", "information", 0.8],
      ["3", "other", 0.5],
    ]),
    prefs: prefsWith("show"),
  });
  assert.deepEqual(table.counts, { shown: 3, hidden: 0, collapsed: 0 });
  assert.deepEqual(table.folds, []);
  assert.deepEqual([...table.actions.keys()], ["1", "2", "3"]);
  const entry = table.actions.get("2");
  assert.equal(entry.action, "show");
  assert.equal(entry.label, "information");
  assert.equal(entry.score, 0.8);
  assert.equal(entry.reason, "default_show");
  assert.equal(entry.text, "文本2");
  assert.equal(entry.time_ms, 200);
  assert.equal(entry.segment_index, 1);
  assert.equal(entry.source, "auto");
});

test("buildActionTable hides labels whose per-type preference is hide", () => {
  const table = buildActionTable({
    items: [item("1", 100)],
    labels: labelsOf([["1", "attack", 0.95]]),
    prefs: prefsWith("show", {
      per_type: { ...prefsWith("show").per_type, attack: "hide" },
    }),
  });
  assert.deepEqual(table.counts, { shown: 0, hidden: 1, collapsed: 0 });
  const entry = table.actions.get("1");
  assert.equal(entry.action, "hide");
  assert.equal(entry.reason, "pref_hide_attack");
  assert.equal(table.folds.length, 1);
  assert.equal(table.folds[0].dmid, "1");
  assert.equal(table.folds[0].action, "hide");
});

test("buildActionTable folds labels whose per-type preference is collapse", () => {
  const table = buildActionTable({
    items: [item("1", 100)],
    labels: labelsOf([["1", "irrelevant_repetition", 0.7]]),
    prefs: prefsWith("show", {
      per_type: { ...prefsWith("show").per_type, irrelevant_repetition: "collapse" },
    }),
  });
  assert.deepEqual(table.counts, { shown: 0, hidden: 0, collapsed: 1 });
  assert.equal(table.actions.get("1").action, "collapse");
  assert.equal(table.folds[0].action, "collapse");
});

test("buildActionTable keeps low-confidence danmaku visible", () => {
  const prefs = prefsWith("hide", { min_confidence: 0.6 });
  const table = buildActionTable({
    items: [item("1", 100)],
    labels: labelsOf([["1", "attack", 0.2]]),
    prefs,
  });
  assert.deepEqual(table.counts, { shown: 1, hidden: 0, collapsed: 0 });
  assert.equal(table.actions.get("1").reason, "low_confidence");
  assert.equal(table.actions.get("1").action, "show");
});

test("buildActionTable keeps everything visible when prefs are disabled", () => {
  const prefs = prefsWith("hide", { enabled: false });
  const table = buildActionTable({
    items: [item("1", 100)],
    labels: labelsOf([["1", "attack", 0.99]]),
    prefs,
  });
  assert.deepEqual(table.counts, { shown: 1, hidden: 0, collapsed: 0 });
  assert.equal(table.actions.get("1").reason, "disabled");
});

test("buildActionTable applies label overrides and marks the source as user", () => {
  const table = buildActionTable({
    items: [item("1", 100)],
    labels: labelsOf([["1", "attack", 0.95]]),
    overrides: { 1: "humor" },
    prefs: prefsWith("show"),
  });
  const entry = table.actions.get("1");
  assert.equal(entry.label, "humor");
  assert.equal(entry.action, "show");
  assert.equal(entry.score, 1);
  assert.equal(entry.source, "user");
});

test("buildActionTable resolves overrides through the per-type preference", () => {
  const prefs = prefsWith("show", {
    per_type: { ...prefsWith("show").per_type, attack: "hide" },
  });
  const table = buildActionTable({
    items: [item("1", 100)],
    labels: labelsOf([["1", "humor", 0.9]]),
    overrides: new Map([["1", "attack"]]),
    prefs,
  });
  assert.equal(table.actions.get("1").action, "hide");
  assert.equal(table.actions.get("1").source, "user");
  assert.deepEqual(table.counts, { shown: 0, hidden: 1, collapsed: 0 });
});

test("buildActionTable falls back to other / 0.5 without a tag", () => {
  const table = buildActionTable({
    items: [item("1", 100)],
    labels: {},
    prefs: prefsWith("show"),
  });
  const entry = table.actions.get("1");
  assert.equal(entry.label, "other");
  assert.equal(entry.score, 0.5);
  assert.equal(entry.action, "show");
});

test("density keeps the highest score per window and folds the rest", () => {
  const table = buildActionTable({
    items: [item("1", 1000), item("2", 2000), item("3", 3000)],
    labels: labelsOf([
      ["1", "humor", 0.7],
      ["2", "humor", 0.9],
      ["3", "humor", 0.5],
    ]),
    prefs: prefsWith("show"),
    density: { enabled: true, window_seconds: 10, max_visible: 1 },
  });
  assert.deepEqual(table.counts, { shown: 1, hidden: 0, collapsed: 2 });
  assert.equal(table.actions.get("2").action, "show");
  for (const dmid of ["1", "3"]) {
    assert.equal(table.actions.get(dmid).action, "collapse");
    assert.equal(table.actions.get(dmid).reason, "density");
  }
  assert.deepEqual(
    table.folds.map((fold) => [fold.dmid, fold.time_ms]),
    [
      ["1", 1000],
      ["3", 3000],
    ]
  );
});

test("density never folds information or other and they take no slot", () => {
  const table = buildActionTable({
    items: [item("1", 0), item("2", 1000), item("3", 2000), item("4", 3000)],
    labels: labelsOf([
      ["1", "information", 0.4],
      ["2", "other", 0.4],
      ["3", "humor", 0.9],
      ["4", "humor", 0.8],
    ]),
    prefs: prefsWith("show"),
    density: { enabled: true, window_seconds: 10, max_visible: 1 },
  });
  assert.equal(table.actions.get("1").action, "show");
  assert.equal(table.actions.get("2").action, "show");
  assert.equal(table.actions.get("3").action, "show");
  assert.equal(table.actions.get("4").action, "collapse");
  assert.deepEqual(table.counts, { shown: 3, hidden: 0, collapsed: 1 });
});

test("density windows are equal width and start at zero", () => {
  const table = buildActionTable({
    items: [item("1", 0), item("2", 9000), item("3", 10000), item("4", 12000)],
    labels: labelsOf([
      ["1", "humor", 0.9],
      ["2", "humor", 0.8],
      ["3", "humor", 0.7],
      ["4", "humor", 0.95],
    ]),
    prefs: prefsWith("show"),
    density: { enabled: true, window_seconds: 10, max_visible: 1 },
  });
  // 0 and 9000 share window 0; 10000 starts window 1 and 12000 shares it.
  assert.equal(table.actions.get("1").action, "show");
  assert.equal(table.actions.get("2").action, "collapse");
  assert.equal(table.actions.get("3").action, "collapse");
  assert.equal(table.actions.get("4").action, "show");
  assert.deepEqual(table.counts, { shown: 2, hidden: 0, collapsed: 2 });
});

test("density stays off while the filter is disabled", () => {
  const prefs = prefsWith("show", {
    enabled: false,
    density: { enabled: true, window_seconds: 10, max_visible: 0, exceed: "collapse" },
  });
  const table = buildActionTable({
    items: [item("1", 0), item("2", 100)],
    labels: labelsOf([
      ["1", "humor", 0.9],
      ["2", "humor", 0.8],
    ]),
    prefs,
    density: { enabled: true, window_seconds: 10, max_visible: 0 },
  });
  assert.deepEqual(table.counts, { shown: 2, hidden: 0, collapsed: 0 });
  assert.equal(table.actions.get("1").reason, "disabled");
});

test("density disabled leaves every show decision alone", () => {
  const table = buildActionTable({
    items: [item("1", 0), item("2", 100), item("3", 200)],
    labels: labelsOf([
      ["1", "humor", 0.9],
      ["2", "humor", 0.8],
      ["3", "humor", 0.7],
    ]),
    prefs: prefsWith("show"),
    density: { enabled: false, window_seconds: 10, max_visible: 1 },
  });
  assert.deepEqual(table.counts, { shown: 3, hidden: 0, collapsed: 0 });
  assert.deepEqual(table.folds, []);
});

test("density tie-break keeps the earliest danmaku", () => {
  const table = buildActionTable({
    items: [item("late", 5000), item("early", 1000)],
    labels: labelsOf([
      ["late", "humor", 0.8],
      ["early", "humor", 0.8],
    ]),
    prefs: prefsWith("show"),
    density: { enabled: true, window_seconds: 10, max_visible: 1 },
  });
  assert.equal(table.actions.get("early").action, "show");
  assert.equal(table.actions.get("late").action, "collapse");
});

test("density with max_visible 0 folds every non-exempt show", () => {
  const table = buildActionTable({
    items: [item("1", 0), item("2", 100)],
    labels: labelsOf([
      ["1", "humor", 0.9],
      ["2", "information", 0.4],
    ]),
    prefs: prefsWith("show"),
    density: { enabled: true, window_seconds: 10, max_visible: 0 },
  });
  assert.equal(table.actions.get("1").action, "collapse");
  assert.equal(table.actions.get("1").reason, "density");
  assert.equal(table.actions.get("2").action, "show");
  assert.deepEqual(table.counts, { shown: 1, hidden: 0, collapsed: 1 });
});

test("dropSet contains exactly the hide and collapse dmids", () => {
  const table = buildActionTable({
    items: [item("1", 100), item("2", 200), item("3", 300)],
    labels: labelsOf([
      ["1", "attack", 0.9],
      ["2", "irrelevant_repetition", 0.9],
      ["3", "humor", 0.9],
    ]),
    prefs: prefsWith("show", {
      per_type: {
        ...prefsWith("show").per_type,
        attack: "hide",
        irrelevant_repetition: "collapse",
      },
    }),
  });
  assert.deepEqual([...dropSet(table.actions)].sort(), ["1", "2"]);
  assert.deepEqual([...dropSet(null)], []);
});

test("countForSegment counts actions and treats unknown dmids as shown", () => {
  const actions = new Map([
    ["1", { action: "hide", label: "attack" }],
    ["2", { action: "collapse", label: "irrelevant_repetition" }],
    ["3", { action: "show", label: "humor" }],
  ]);
  const counts = countForSegment(actions, ["1", "2", "3", "4"]);
  assert.deepEqual(counts, {
    shown: 2,
    hidden: 1,
    collapsed: 1,
    by_label: { attack: 1, irrelevant_repetition: 1 },
  });
  assert.deepEqual(countForSegment(actions, null), {
    shown: 0,
    hidden: 0,
    collapsed: 0,
    by_label: {},
  });
});

test("poolStats reports totals, tagged count, and label distribution", () => {
  const items = [item("1", 0), item("2", 100), item("3", 200)];
  const stats = poolStats(
    items,
    labelsOf([
      ["1", "humor", 0.9],
      ["2", "other", 0.6],
    ])
  );
  assert.equal(stats.total, 3);
  assert.equal(stats.tagged, 2);
  assert.deepEqual(stats.by_label, { humor: 1, other: 2 });
  assert.deepEqual(poolStats(null, null), { total: 0, tagged: 0, by_label: {} });
});

test("buildActionTable falls back to prefs.density when density is omitted", () => {
  const prefs = prefsWith("show", {
    density: { enabled: true, window_seconds: 10, max_visible: 1, exceed: "collapse" },
  });
  const table = buildActionTable({
    items: [item("1", 0), item("2", 1000)],
    labels: labelsOf([
      ["1", "humor", 0.9],
      ["2", "humor", 0.8],
    ]),
    prefs,
  });
  assert.equal(table.actions.get("1").action, "show");
  assert.equal(table.actions.get("2").reason, "density");
});

test("buildActionTable sorts folds by time and reports exact counts", () => {
  const table = buildActionTable({
    items: [item("1", 3000), item("2", 1000), item("3", 500)],
    labels: labelsOf([
      ["1", "attack", 0.9],
      ["2", "irrelevant_repetition", 0.9],
      ["3", "humor", 0.9],
    ]),
    prefs: prefsWith("show", {
      per_type: {
        ...prefsWith("show").per_type,
        attack: "hide",
        irrelevant_repetition: "collapse",
      },
    }),
  });
  assert.deepEqual(
    table.folds.map((fold) => [fold.dmid, fold.time_ms, fold.action]),
    [
      ["2", 1000, "collapse"],
      ["1", 3000, "hide"],
    ]
  );
  assert.deepEqual(table.counts, { shown: 1, hidden: 1, collapsed: 1 });
});

test("thinSegmentDrop keeps the per-window quota and is deterministic", () => {
  const items = [];
  for (let index = 0; index < 60; index += 1) {
    items.push({ dmid: String(index + 1), time_ms: 1000 + index * 100 });
  }
  const first = thinSegmentDrop(items, { target_per_min: 30, window_ms: 10000 });
  const second = thinSegmentDrop(items, { target_per_min: 30, window_ms: 10000 });
  // 60 comments in one 10 s window at 30/min -> keep 5, drop 55.
  assert.equal(first.size, 55);
  assert.deepEqual([...first], [...second]);
  assert.equal(first.has("1"), first.has("1"));
});

test("thinSegmentDrop leaves sparse windows untouched", () => {
  const items = [
    { dmid: "a", time_ms: 0 },
    { dmid: "b", time_ms: 2000 },
    { dmid: "c", time_ms: 4000 },
  ];
  const drop = thinSegmentDrop(items, { target_per_min: 30, window_ms: 10000 });
  assert.equal(drop.size, 0);
});

test("thinSegmentDrop applies the quota per window, not globally", () => {
  const items = [];
  for (let index = 0; index < 20; index += 1) {
    items.push({ dmid: "w1-" + index, time_ms: index * 100 });
    items.push({ dmid: "w2-" + index, time_ms: 10000 + index * 100 });
  }
  const drop = thinSegmentDrop(items, { target_per_min: 30, window_ms: 10000 });
  assert.equal(drop.size, 30); // 15 kept per window
});
