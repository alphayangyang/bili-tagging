/**
 * Client-side density window tests (contracts.md §5, uscript-only rule).
 */

import test from "node:test";
import assert from "node:assert/strict";

import {
  DensityController,
  applyDensity,
  collapsedText,
  defaultPrefs,
  mergePrefs,
  planSegment,
} from "../lib/policy.js";

/**
 * Build a service decision for one label.
 * @param {string} action
 * @param {string} label
 * @returns {{action: string, label: string, score: number, reason: string}}
 */
function decision(action, label) {
  return { action, label, score: 0.9, reason: "pref_" + action + "_" + label };
}

/**
 * Build a parsed danmaku item.
 * @param {string} dmid
 * @param {number} timeMs
 * @param {string} [text]
 * @returns {{dmid: string, text: string, time_ms: number, mode: number, uid: string, cid: number, id_str: string}}
 */
function item(dmid, timeMs, text) {
  return { dmid, text: text || ("danmaku-" + dmid), time_ms: timeMs, mode: 1, uid: "hash", cid: 1, id_str: dmid };
}

test("density leaves decisions alone below the visible limit", () => {
  const prefs = mergePrefs(defaultPrefs(), { density: { max_visible: 3 } });
  const controller = new DensityController(prefs.density);
  const first = controller.apply(decision("collapse", "humor"), 1000);
  const second = controller.apply(decision("hide", "attack"), 1100);
  assert.equal(first.action, "collapse");
  assert.equal(first.reason, "pref_collapse_humor");
  assert.equal(second.action, "hide");
  assert.equal(second.reason, "pref_hide_attack");
  assert.equal(controller.visibleCount, 1);
});

test("density folds visible decisions once the window is full", () => {
  const prefs = mergePrefs(defaultPrefs(), { density: { max_visible: 2, exceed: "collapse" } });
  const controller = new DensityController(prefs.density);
  assert.equal(controller.apply(decision("collapse", "humor"), 0).action, "collapse");
  assert.equal(controller.apply(decision("show", "humor"), 1).action, "show");
  const folded = controller.apply(decision("show", "humor"), 2);
  assert.equal(folded.action, "collapse");
  assert.equal(folded.reason, "density");
});

test("density keeps explicit per-label collapse decisions", () => {
  const prefs = mergePrefs(defaultPrefs(), { density: { max_visible: 1, exceed: "hide" } });
  const controller = new DensityController(prefs.density);
  assert.equal(controller.apply(decision("show", "humor"), 0).action, "show");
  const folded = controller.apply(decision("collapse", "irrelevant_repetition"), 100);
  assert.equal(folded.action, "collapse");
  assert.equal(folded.reason, "pref_collapse_irrelevant_repetition");
});

test("density removes the surplus show decisions by default", () => {
  const prefs = mergePrefs(defaultPrefs(), { density: { max_visible: 0 } });
  const controller = new DensityController(prefs.density);
  for (let index = 0; index < 5; index += 1) {
    const result = controller.apply(decision("show", "humor"), index * 100);
    assert.equal(result.action, "hide");
    assert.equal(result.reason, "density");
  }
});

test("hidden danmaku are never touched by density", () => {
  const prefs = mergePrefs(defaultPrefs(), { density: { max_visible: 0 } });
  const controller = new DensityController(prefs.density);
  const attack = controller.apply(decision("hide", "attack"), 0);
  assert.equal(attack.action, "hide");
  assert.equal(attack.reason, "pref_hide_attack");
});

test("only information is protected from density folding", () => {
  const prefs = mergePrefs(defaultPrefs(), { density: { max_visible: 0, exceed: "collapse" } });
  const controller = new DensityController(prefs.density);
  assert.equal(controller.apply(decision("show", "information"), 0).action, "show");
  assert.equal(controller.apply(decision("show", "other"), 10).reason, "density");
});

test("timestamps outside the window stop counting towards density", () => {
  const prefs = mergePrefs(defaultPrefs(), { density: { max_visible: 1, window_seconds: 10 } });
  const controller = new DensityController(prefs.density);
  assert.equal(controller.apply(decision("show", "humor"), 0).action, "show");
  const full = controller.apply(decision("show", "humor"), 1000);
  assert.equal(full.reason, "density");
  const afterWindow = controller.apply(decision("show", "humor"), 12000);
  assert.equal(afterWindow.reason, "pref_show_humor");
  assert.equal(controller.visibleCount, 1);
});

test("density can be disabled from prefs", () => {
  const prefs = mergePrefs(defaultPrefs(), { density: { enabled: false, max_visible: 0 } });
  const controller = new DensityController(prefs.density);
  const result = controller.apply(decision("hide", "attack"), 0);
  assert.equal(result.action, "hide");
  assert.equal(result.reason, "pref_hide_attack");
});

test("hidden danmaku do not occupy a visible slot", () => {
  const prefs = mergePrefs(defaultPrefs(), { density: { max_visible: 1, exceed: "collapse" } });
  const controller = new DensityController(prefs.density);
  controller.apply(decision("hide", "attack"), 0);
  controller.apply(decision("hide", "spoiler"), 10);
  assert.equal(controller.visibleCount, 0);
  assert.equal(controller.apply(decision("show", "humor"), 20).action, "show");
  assert.equal(controller.apply(decision("show", "humor"), 30).reason, "density");
});

test("max_visible zero plus exceed hide keeps explicit collapse decisions", () => {
  const prefs = mergePrefs(defaultPrefs(), { density: { max_visible: 0, exceed: "hide" } });
  const controller = new DensityController(prefs.density);
  const result = controller.apply(decision("collapse", "attack"), 0);
  assert.equal(result.action, "collapse");
  assert.equal(result.reason, "pref_collapse_attack");
});

test("applyDensity maps a time-ordered decision list", () => {
  const prefs = mergePrefs(defaultPrefs(), { density: { max_visible: 1 } });
  const results = applyDensity(
    [
      { time_ms: 0, decision: decision("show", "humor") },
      { time_ms: 1, decision: decision("show", "humor") },
    ],
    prefs
  );
  assert.equal(results[0].action, "show");
  assert.equal(results[1].action, "hide");
  assert.equal(results[1].reason, "density");
});

test("planSegment turns decisions into drop and collapse arguments", () => {
  const items = [item("1", 5000), item("2", 1000), item("3", 3000), item("4", 4000)];
  const decisions = {
    "1": decision("hide", "attack"),
    "2": decision("collapse", "irrelevant_repetition"),
    "3": decision("show", "information"),
  };
  const plan = planSegment(items, decisions, defaultPrefs());
  assert.deepEqual(Array.from(plan.drop), ["1"]);
  assert.deepEqual(Array.from(plan.collapse.keys()), ["2"]);
  assert.equal(plan.collapse.get("2"), collapsedText("irrelevant_repetition"));
  assert.equal(plan.entries.length, 2);
  assert.deepEqual(plan.entries.map((entry) => entry.dmid), ["2", "1"]);
  assert.equal(plan.entries[0].time_ms, 1000);
  assert.equal(plan.entries[1].time_ms, 5000);
  assert.equal(plan.actions.get("3").action, "show");
  assert.equal(plan.actions.get("4").action, "show");
  assert.equal(plan.actions.get("4").reason, "not_tagged");
});

test("planSegment applies time-ordered density to the whole segment", () => {
  const prefs = mergePrefs(defaultPrefs(), { density: { max_visible: 1 } });
  // Unsorted input: the first show at 1000ms claims the only visible slot, so
  // the two later shows are removed by the density window.
  const items = [item("1", 3000), item("2", 1000), item("3", 2000)];
  const decisions = {
    "1": decision("show", "humor"),
    "2": decision("show", "humor"),
    "3": decision("show", "humor"),
  };
  const plan = planSegment(items, decisions, prefs);
  assert.equal(plan.entries.length, 2);
  assert.deepEqual(plan.entries.map((entry) => entry.dmid), ["3", "1"]);
  assert.equal(plan.entries[0].time_ms, 2000);
  assert.equal(plan.entries[1].time_ms, 3000);
  assert.equal(plan.entries[0].action, "hide");
  assert.equal(plan.entries[1].action, "hide");
  assert.equal(plan.entries[0].reason, "density");
  assert.equal(plan.entries[1].reason, "density");
  assert.equal(plan.drop.size, 2);
  assert.equal(plan.collapse.size, 0);
  assert.equal(plan.actions.get("2").action, "show");
});

test("showOnce overrides a hide decision for the session", () => {
  const items = [item("1", 1000)];
  const decisions = { "1": decision("hide", "attack") };
  const plan = planSegment(items, decisions, defaultPrefs(), { showOnce: new Set(["1"]) });
  assert.equal(plan.drop.size, 0);
  assert.equal(plan.collapse.size, 0);
  assert.equal(plan.entries.length, 0);
  assert.equal(plan.actions.get("1").action, "show");
  assert.equal(plan.actions.get("1").reason, "user_show");
});

test("planSegment applies local tag overrides before density", () => {
  const items = [item("1", 1000)];
  const overrides = new Map([["1", "attack"]]);
  const plan = planSegment(items, {}, defaultPrefs(), { overrides });
  assert.deepEqual(Array.from(plan.drop), ["1"]);
  const entry = plan.entries[0];
  assert.equal(entry.label, "attack");
  assert.equal(entry.score, 1);
  assert.equal(entry.reason, "pref_hide_attack");
});

test("planSegment treats unknown dmids as show and tolerates null decisions", () => {
  const items = [item("1", 1000), item("2", 2000)];
  const plan = planSegment(items, null, defaultPrefs());
  assert.equal(plan.drop.size, 0);
  assert.equal(plan.collapse.size, 0);
  assert.equal(plan.entries.length, 0);
  assert.equal(plan.actions.size, 2);
});
