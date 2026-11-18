/**
 * Contract tests for the per-item presentation policy (contracts.md §5).
 */

import test from "node:test";
import assert from "node:assert/strict";

import {
  ACTIONS,
  DENSITY_PROTECTED_LABELS,
  DEFAULT_PREFS,
  LABELS_ZH,
  TYPES,
  collapsedText,
  decide,
  defaultPrefs,
  itemLabel,
  itemScore,
  labelName,
  mergePrefs,
  normalizePrefs,
  resolveItem,
} from "../lib/policy.js";

/**
 * Build a tagged item as the service cache stores it.
 * @param {string} label
 * @param {number} score
 * @param {object} [extra]
 * @returns {object}
 */
function item(label, score, extra) {
  return { text: "sample", time_ms: 1000, dominant: label, tags: { [label]: score }, score, ...extra };
}

test("DEFAULT_PREFS matches the frozen contract", () => {
  assert.equal(DEFAULT_PREFS.version, 1);
  assert.equal(DEFAULT_PREFS.enabled, true);
  assert.equal(DEFAULT_PREFS.min_confidence, 0.3);
  assert.deepEqual(DEFAULT_PREFS.per_type, {
    information: "show",
    humor: "show",
    emotional_resonance: "show",
    irrelevant_repetition: "collapse",
    attack: "hide",
    conflict: "hide",
    spoiler: "hide",
    political: "show",
    inflammatory: "hide",
    other: "show",
  });
  assert.equal(DEFAULT_PREFS.density.window_seconds, 10);
  assert.equal(DEFAULT_PREFS.density.max_visible, 30);
  assert.equal(DEFAULT_PREFS.density.exceed, "hide");
  assert.equal(Object.isFrozen(DEFAULT_PREFS), true);
  assert.equal(Object.isFrozen(DEFAULT_PREFS.per_type), true);
});

test("defaultPrefs returns an independent mutable copy", () => {
  const copy = defaultPrefs();
  copy.per_type.attack = "show";
  copy.density.max_visible = 99;
  assert.equal(DEFAULT_PREFS.per_type.attack, "hide");
  assert.equal(DEFAULT_PREFS.density.max_visible, 30);
  assert.deepEqual(copy, {
    version: 1,
    enabled: true,
    fail_open: true,
    min_confidence: 0.3,
    per_type: { ...DEFAULT_PREFS.per_type, attack: "show" },
    density: { ...DEFAULT_PREFS.density, max_visible: 99 },
  });
});

test("taxonomy and display names cover the ten labels", () => {
  assert.equal(TYPES.length, 10);
  assert.deepEqual(Array.from(TYPES), Object.keys(LABELS_ZH));
  assert.deepEqual(Array.from(ACTIONS), ["show", "hide", "collapse"]);
  assert.deepEqual(Array.from(DENSITY_PROTECTED_LABELS), ["information"]);
  assert.equal(labelName("attack"), "攻击");
  assert.equal(labelName("unknown-label"), "其他");
});

test("collapsedText renders the Chinese placeholder for every label", () => {
  const expected = {
    information: "【已折叠·信息】",
    humor: "【已折叠·幽默】",
    emotional_resonance: "【已折叠·共鸣】",
    irrelevant_repetition: "【已折叠·无意义重复】",
    attack: "【已折叠·攻击】",
    conflict: "【已折叠·争论】",
    spoiler: "【已折叠·剧透】",
    political: "【已折叠·政治】",
    inflammatory: "【已折叠·引战】",
    other: "【已折叠·其他】",
  };
  for (const label of TYPES) assert.equal(collapsedText(label), expected[label]);
  assert.equal(collapsedText("bogus"), "【已折叠·其他】");
});

test("decide returns not_tagged for a missing item", () => {
  assert.deepEqual(decide(null, DEFAULT_PREFS), {
    action: "show",
    label: "other",
    score: 0,
    reason: "not_tagged",
  });
  assert.deepEqual(decide(undefined, DEFAULT_PREFS).reason, "not_tagged");
});

test("decide honors the disabled switch before any per-type preference", () => {
  const prefs = mergePrefs(DEFAULT_PREFS, { enabled: false });
  assert.deepEqual(decide(item("attack", 0.9), prefs), {
    action: "show",
    label: "attack",
    score: 0.9,
    reason: "disabled",
  });
});

test("decide shows low-confidence items", () => {
  assert.deepEqual(decide(item("attack", 0.2), DEFAULT_PREFS), {
    action: "show",
    label: "attack",
    score: 0.2,
    reason: "low_confidence",
  });
  const stricter = mergePrefs(DEFAULT_PREFS, { min_confidence: 0.8 });
  assert.equal(decide(item("attack", 0.7), stricter).reason, "low_confidence");
  assert.equal(decide(item("attack", 0.8), stricter).action, "hide");
});

test("decide applies the confidence boundary inclusively", () => {
  assert.deepEqual(decide(item("attack", 0.6), DEFAULT_PREFS), {
    action: "hide",
    label: "attack",
    score: 0.6,
    reason: "pref_hide_attack",
  });
});

test("decide maps per-type actions to reasons", () => {
  assert.deepEqual(decide(item("information", 0.9), DEFAULT_PREFS), {
    action: "show",
    label: "information",
    score: 0.9,
    reason: "default_show",
  });
  assert.deepEqual(decide(item("spoiler", 0.9), DEFAULT_PREFS), {
    action: "hide",
    label: "spoiler",
    score: 0.9,
    reason: "pref_hide_spoiler",
  });
  assert.deepEqual(decide(item("irrelevant_repetition", 0.9), DEFAULT_PREFS), {
    action: "collapse",
    label: "irrelevant_repetition",
    score: 0.9,
    reason: "pref_collapse_irrelevant_repetition",
  });
  const custom = mergePrefs(DEFAULT_PREFS, { per_type: { humor: "hide" } });
  assert.equal(decide(item("humor", 0.9), custom).reason, "pref_hide_humor");
});

test("decide does not mutate its arguments", () => {
  const tagged = item("attack", 0.9);
  const prefs = defaultPrefs();
  const taggedCopy = JSON.parse(JSON.stringify(tagged));
  const prefsCopy = JSON.parse(JSON.stringify(prefs));
  decide(tagged, prefs);
  assert.deepEqual(tagged, taggedCopy);
  assert.deepEqual(prefs, prefsCopy);
});

test("itemLabel and itemScore tolerate partial cache entries", () => {
  assert.equal(itemLabel({ dominant: "attack", score: 0.8 }), "attack");
  assert.equal(itemLabel({ tags: { humor: 0.2, spoiler: 0.7 } }), "spoiler");
  assert.equal(itemLabel({ dominant: "bogus", tags: {} }), "other");
  assert.equal(itemLabel(null), "other");
  assert.equal(itemScore({ score: 0.42, tags: { attack: 0.9 } }, "attack"), 0.42);
  assert.equal(itemScore({ tags: { attack: 0.9 } }, "attack"), 0.9);
  assert.equal(itemScore({}, "attack"), 0);
  assert.equal(itemScore(null, "attack"), 0);
});

test("resolveItem applies a user override without mutating the item", () => {
  const original = item("humor", 0.7);
  const copy = JSON.parse(JSON.stringify(original));
  const resolved = resolveItem(original, { label: "attack" });
  assert.deepEqual(original, copy);
  assert.equal(resolved.dominant, "attack");
  assert.deepEqual(resolved.tags, { attack: 1 });
  assert.equal(resolved.score, 1);
  assert.equal(resolved.source, "user");
  assert.equal(resolved.evidence, "user correction");
  assert.equal(resolveItem(original, null), original);
  assert.equal(resolveItem(original, {}), original);
  assert.equal(resolveItem(original, { label: "bogus" }), original);
});

test("normalizePrefs drops unknown labels and clamps values", () => {
  const normalized = normalizePrefs({
    version: 3,
    enabled: false,
    min_confidence: 2,
    per_type: { attack: "collapse", made_up_label: "hide" },
    density: { window_seconds: -5, max_visible: 2.9, exceed: "bogus" },
  });
  assert.equal(normalized.version, 3);
  assert.equal(normalized.enabled, false);
  assert.equal(normalized.min_confidence, 1);
  assert.equal(normalized.per_type.attack, "collapse");
  assert.equal(Object.prototype.hasOwnProperty.call(normalized.per_type, "made_up_label"), false);
  assert.equal(normalized.per_type.spoiler, "hide");
  assert.equal(normalized.density.window_seconds, 0.1);
  assert.equal(normalized.density.max_visible, 2);
  assert.equal(normalized.density.exceed, "hide");
  assert.deepEqual(normalizePrefs(null), defaultPrefs());
  assert.deepEqual(normalizePrefs("garbage"), defaultPrefs());
});

test("normalizePrefs falls back to the default action of a label", () => {
  const normalized = normalizePrefs({ per_type: { attack: "bogus", information: 7 } });
  assert.equal(normalized.per_type.attack, "hide");
  assert.equal(normalized.per_type.information, "show");
});

test("mergePrefs deep-merges partial patches and keeps untouched fields", () => {
  const base = defaultPrefs();
  const merged = mergePrefs(base, { enabled: false, per_type: { humor: "collapse" } });
  assert.equal(merged.enabled, false);
  assert.equal(merged.per_type.humor, "collapse");
  assert.equal(merged.per_type.attack, "hide");
  assert.equal(base.enabled, true);
  const densityMerge = mergePrefs(base, { density: { max_visible: 5 } });
  assert.equal(densityMerge.density.max_visible, 5);
  assert.equal(densityMerge.density.window_seconds, 10);
  assert.equal(densityMerge.density.exceed, "hide");
  assert.equal(densityMerge.density.enabled, true);
  assert.deepEqual(mergePrefs(base, null), defaultPrefs());
});

test("fail_open can be disabled so explicit hide prefs win over low scores", () => {
  const strict = mergePrefs(defaultPrefs(), { fail_open: false, per_type: { attack: "hide" }, min_confidence: 0.6 });
  const decision = decide({ text: "x", dominant: "attack", score: 0.2 }, strict);
  assert.equal(decision.action, "hide");
  assert.equal(decision.reason, "pref_hide_attack");
  const lenient = decide({ text: "x", dominant: "attack", score: 0.2 }, defaultPrefs());
  assert.equal(lenient.action, "show");
  assert.equal(lenient.reason, "low_confidence");
});
