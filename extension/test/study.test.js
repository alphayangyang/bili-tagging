/**
 * Study-mode tests: condition presets, URL/localStorage activation, the
 * bounded event recorder, and the protocol §7 export document.
 */

import test from "node:test";
import assert from "node:assert/strict";

import {
  MAX_STUDY_EVENTS,
  STUDY_CONDITIONS,
  analysisPrefsForCondition,
  cleanStudyId,
  conditionSpec,
  countActions,
  createStudySession,
  diffPrefs,
  normalizeCondition,
  normalizeDensity,
  parseStudyConfig,
  prefsForCondition,
  studyDownloadName,
} from "../lib/study.js";

import { defaultPrefs, normalizePrefs, TYPES } from "../lib/policy.js";

/** Deterministic clock: every call advances one second. */
function clock(startMs) {
  let current = typeof startMs === "number" ? startMs : 1700000000000;
  return () => {
    const value = current;
    current += 1000;
    return value;
  };
}

test("conditions are the three protocol conditions", () => {
  assert.deepEqual([...STUDY_CONDITIONS], ["all_on", "keyword", "prototype"]);
});

test("normalizeCondition accepts known conditions case-insensitively", () => {
  assert.equal(normalizeCondition("all_on"), "all_on");
  assert.equal(normalizeCondition(" KEYWORD "), "keyword");
  assert.equal(normalizeCondition("Prototype"), "prototype");
  assert.equal(normalizeCondition("A"), "all_on");
  assert.equal(normalizeCondition("b"), "keyword");
  assert.equal(normalizeCondition(" C "), "prototype");
  assert.equal(normalizeCondition("control"), "");
  assert.equal(normalizeCondition(null), "");
  assert.equal(normalizeCondition(42), "");
});

test("cleanStudyId trims and bounds the id", () => {
  assert.equal(cleanStudyId("  pilot-1  "), "pilot-1");
  assert.equal(cleanStudyId("x".repeat(200)).length, 100);
  assert.equal(cleanStudyId(null), "");
});

test("conditionSpec marks record-only conditions", () => {
  for (const condition of ["all_on", "keyword"]) {
    const spec = conditionSpec(condition);
    assert.equal(spec.condition, condition);
    assert.equal(spec.record_only, true);
    assert.equal(spec.enabled, false);
    assert.equal(spec.density.enabled, false);
    assert.equal(spec.per_type.attack, "show");
  }
  const prototype = conditionSpec("prototype");
  assert.equal(prototype.record_only, false);
  assert.equal(prototype.enabled, true);
  assert.equal(prototype.density.enabled, false);
  assert.equal(prototype.per_type.information, "show");
  assert.equal(prototype.per_type.humor, "show");
  assert.equal(prototype.per_type.emotional_resonance, "show");
  assert.equal(prototype.per_type.political, "show");
  assert.equal(prototype.per_type.other, "show");
  assert.equal(prototype.per_type.irrelevant_repetition, "hide");
  assert.equal(prototype.per_type.attack, "hide");
  assert.equal(prototype.per_type.conflict, "hide");
  assert.equal(prototype.per_type.inflammatory, "hide");
  assert.equal(prototype.per_type.spoiler, "collapse");
  assert.equal(conditionSpec("nope"), null);
});

test("prefsForCondition merges the preset onto the base without mutating it", () => {
  const base = defaultPrefs();
  const snapshot = JSON.stringify(base);
  const prototype = normalizePrefs(prefsForCondition("prototype", base));
  assert.equal(prototype.enabled, true);
  assert.equal(prototype.density.enabled, false);
  for (const label of TYPES) {
    assert.equal(typeof prototype.per_type[label], "string", "keeps every taxonomy label");
  }
  assert.equal(prototype.per_type.attack, "hide");
  assert.equal(prototype.per_type.spoiler, "collapse");
  assert.equal(prototype.per_type.information, "show");
  const recordOnly = normalizePrefs(prefsForCondition("all_on", base));
  assert.equal(recordOnly.enabled, false);
  assert.equal(recordOnly.density.enabled, false);
  assert.equal(recordOnly.per_type.attack, "show");
  assert.equal(JSON.stringify(base), snapshot, "base prefs stay untouched");
  assert.equal(normalizePrefs(prefsForCondition("keyword", base)).per_type.conflict, "show");
  assert.equal(prefsForCondition("nope", base), null);
});

test("analysis prefs always count with the prototype policy", () => {
  const base = defaultPrefs();
  for (const condition of STUDY_CONDITIONS) {
    const analysis = normalizePrefs(analysisPrefsForCondition(condition, base));
    assert.equal(analysis.enabled, true, condition + " still counts would-be actions");
    assert.equal(analysis.per_type.attack, "hide");
    assert.equal(analysis.per_type.spoiler, "collapse");
    assert.equal(analysis.density.enabled, false);
  }
  assert.equal(analysisPrefsForCondition("nope", base), null);
});

test("parseStudyConfig reads both URL parameters", () => {
  const config = parseStudyConfig("?dmf_study=pilot-a&dmf_cond=prototype", null);
  assert.deepEqual(config, {
    studyId: "pilot-a", condition: "prototype", density: "high", source: "url",
  });
  assert.deepEqual(parseStudyConfig("?dmf_study=pilot-a&dmf_cond=C&dmf_density=low", null), {
    studyId: "pilot-a", condition: "prototype", density: "low", source: "url",
  });
  assert.equal(normalizeDensity("LOW"), "low");
  assert.equal(normalizeDensity("medium"), "");
  assert.equal(parseStudyConfig("?dmf_cond=prototype", null), null);
  assert.equal(parseStudyConfig("?dmf_study=pilot-a", null), null);
  assert.equal(parseStudyConfig("?dmf_study=pilot-a&dmf_cond=nope", null), null);
  assert.equal(parseStudyConfig("", null), null);
  assert.equal(parseStudyConfig("", "pilot-b|all_on").studyId, "pilot-b");
});

test("parseStudyConfig applies the localStorage override", () => {
  const object = JSON.stringify({ study_id: "override-1", condition: "keyword" });
  assert.deepEqual(parseStudyConfig("?dmf_study=pilot-a&dmf_cond=prototype", object), {
    studyId: "override-1",
    condition: "keyword",
    density: "high",
    source: "storage",
  });
  // Storage may activate a study on its own (examiner convenience).
  assert.deepEqual(parseStudyConfig("", { studyId: "solo", condition: "all_on" }), {
    studyId: "solo",
    condition: "all_on",
    density: "high",
    source: "storage",
  });
  // A storage condition alone only overrides an existing URL study.
  assert.deepEqual(parseStudyConfig("?dmf_study=pilot-a&dmf_cond=all_on", "prototype"), {
    studyId: "pilot-a",
    condition: "prototype",
    density: "high",
    source: "url+storage",
  });
  assert.equal(parseStudyConfig("", "prototype"), null);
  assert.deepEqual(parseStudyConfig("?dmf_study=pilot-a&dmf_cond=all_on", "pilot-c"), {
    studyId: "pilot-c",
    condition: "all_on",
    density: "high",
    source: "storage",
  });
});

test("studyDownloadName sanitizes the study id", () => {
  assert.equal(studyDownloadName("pilot-1"), "dmf-study-pilot-1.json");
  assert.equal(studyDownloadName("a b/c"), "dmf-study-a_b_c.json");
  assert.equal(studyDownloadName(""), "dmf-study-session.json");
});

test("countActions counts every final action and label", () => {
  const actions = new Map([
    ["a", { action: "show", label: "humor" }],
    ["b", { action: "hide", label: "attack" }],
    ["c", { action: "collapse", label: "spoiler" }],
    ["d", { action: "hide", label: "attack" }],
  ]);
  assert.deepEqual(countActions(actions), {
    shown: 1,
    hidden: 2,
    collapsed: 1,
    by_label: { humor: 1, attack: 2, spoiler: 1 },
  });
  assert.deepEqual(countActions({ a: { action: "show", label: "other" } }).by_label, { other: 1 });
  assert.deepEqual(countActions(null), { shown: 0, hidden: 0, collapsed: 0, by_label: {} });
});

test("diffPrefs reports flat scalar changes only", () => {
  const before = defaultPrefs();
  const after = defaultPrefs();
  after.enabled = false;
  after.min_confidence = 0.75;
  after.per_type.attack = "collapse";
  after.density.enabled = false;
  const changes = diffPrefs(before, after);
  const keys = changes.map((change) => change.key);
  assert.deepEqual(keys, ["density.enabled", "enabled", "min_confidence", "per_type.attack"]);
  const attack = changes.find((change) => change.key === "per_type.attack");
  assert.equal(attack.from, "hide");
  assert.equal(attack.to, "collapse");
  assert.deepEqual(diffPrefs(before, defaultPrefs()), []);
});

test("session records segment events and aggregates counts", () => {
  const session = createStudySession({
    studyId: "pilot-1",
    condition: "prototype",
    prefs: defaultPrefs(),
    scriptVersion: "0.2.1",
    ua: "test-ua",
    now: clock(),
  });
  session.recordSegment({
    bvid: "BV1GJ411x7h7",
    url: "https://api.bilibili.com/x/v2/dm/web/seg.so?oid=1",
    segment_index: 2,
    shown: 10,
    hidden: 3,
    collapsed: 2,
    by_label: { attack: 3, spoiler: 2, humor: 10 },
    latency_ms: 123.6,
    online: true,
    applied: true,
  });
  session.recordSegment({ shown: 5, hidden: 0, collapsed: 0, thinned: 60 });
  const event = session.events[0];
  assert.equal(event.type, "segment");
  assert.equal(event.bvid, "BV1GJ411x7h7");
  assert.equal(event.segment_index, 2);
  assert.equal(event.latency_ms, 124);
  assert.equal(event.online, true);
  assert.equal(event.applied, true);
  assert.equal(session.events[1].thinned, 60);
  assert.deepEqual(session.counts(), {
    segments: 2,
    shown: 15,
    hidden: 3,
    collapsed: 2,
    thinned: 60,
    would_shown: 0,
    would_hidden: 0,
    would_collapsed: 0,
    prefs: 0,
    corrections: 0,
    restores: 0,
    expands: 0,
    dropped_events: 0,
  });
});

test("session records pref, correction, restore and expand events", () => {
  const session = createStudySession({
    studyId: "pilot-1",
    condition: "prototype",
    prefs: defaultPrefs(),
    now: clock(),
  });
  session.recordPref("per_type.attack", "hide", "show");
  session.recordCorrection({ dmid: "1", text: "hello", from: "attack", to: "humor" });
  session.recordRestore({ dmid: "2", label: "spoiler" });
  session.recordExpand({ panel: "open" });
  assert.deepEqual(
    session.events.map((event) => event.type),
    ["pref", "correction", "restore", "expand"]
  );
  assert.equal(session.events[0].key, "per_type.attack");
  assert.equal(session.events[0].from, "hide");
  assert.equal(session.events[0].to, "show");
  assert.equal(session.events[1].text, "hello");
  assert.equal(session.events[2].label, "spoiler");
  const counts = session.counts();
  assert.equal(counts.prefs, 1);
  assert.equal(counts.corrections, 1);
  assert.equal(counts.restores, 1);
  assert.equal(counts.expands, 1);
});

test("a re-request keeps the fuller segment counts", () => {
  const session = createStudySession({
    studyId: "pilot-1",
    condition: "all_on",
    prefs: defaultPrefs(),
    scriptVersion: "0.4.0",
    ua: "test-ua",
    now: clock(),
  });
  session.recordSegment({ bvid: "BV1", segment_index: 1, shown: 972, hidden: 19, thinned: 912, applied: true });
  session.recordSegment({ bvid: "BV1", segment_index: 1, shown: 60, hidden: 0, thinned: 0, applied: false });
  assert.equal(session.events.length, 1);
  assert.deepEqual(session.counts(), {
    segments: 1,
    shown: 972,
    hidden: 19,
    collapsed: 0,
    thinned: 912,
    would_shown: 0,
    would_hidden: 0,
    would_collapsed: 0,
    prefs: 0,
    corrections: 0,
    restores: 0,
    expands: 0,
    dropped_events: 0,
  });
  assert.equal(session.events[0].applied, true);
});

test("session bounds events and reports dropped ones", () => {
  const session = createStudySession({
    studyId: "pilot-1",
    condition: "all_on",
    prefs: defaultPrefs(),
    now: clock(),
  });
  for (let index = 0; index < MAX_STUDY_EVENTS + 2; index += 1) {
    session.recordPref("k", index, index + 1);
  }
  assert.equal(session.events.length, MAX_STUDY_EVENTS);
  assert.equal(session.counts().dropped_events, 2);
  // The two oldest events were evicted.
  assert.equal(session.events[0].from, 2);
  assert.equal(session.events[session.events.length - 1].from, MAX_STUDY_EVENTS + 1);
});

test("export returns the protocol document shape", () => {
  const session = createStudySession({
    studyId: "pilot-1",
    condition: "keyword",
    prefs: defaultPrefs(),
    scriptVersion: "0.2.1",
    ua: "test-ua",
    now: clock(1700000000000),
  });
  session.recordSegment({ shown: 4, hidden: 1, collapsed: 0, online: true });
  session.recordPref("enabled", false, true);
  const document = session.export();
  assert.deepEqual(Object.keys(document).sort(), [
    "condition",
    "condition_label",
    "counts",
    "ended_at",
    "events",
    "prefs",
    "script_version",
    "started_at",
    "study_id",
    "ua",
  ]);
  assert.equal(document.study_id, "pilot-1");
  assert.equal(document.condition, "keyword");
  assert.equal(document.condition_label, "B");
  assert.equal(document.started_at, "2023-11-14T22:13:20.000Z");
  assert.equal(document.ended_at, "2023-11-14T22:13:23.000Z");
  assert.equal(document.script_version, "0.2.1");
  assert.equal(document.ua, "test-ua");
  assert.equal(document.counts.segments, 1);
  assert.equal(document.counts.hidden, 1);
  assert.equal(document.events.length, 2);
  assert.equal(document.prefs.per_type.attack, "hide");
  // The exported event array is a snapshot of the event list.
  assert.notEqual(document.events, session.events);
});

test("setPrefs updates the exported snapshot", () => {
  const session = createStudySession({
    studyId: "pilot-1",
    condition: "prototype",
    prefs: defaultPrefs(),
    now: clock(),
  });
  const next = defaultPrefs();
  next.per_type.attack = "show";
  session.setPrefs(next);
  assert.equal(session.export().prefs.per_type.attack, "show");
});
test("record-only blocks keep applied counts and the would-be policy apart", () => {
  const session = createStudySession({
    studyId: "P01-A",
    condition: "all_on",
    prefs: defaultPrefs(),
    now: clock(),
  });
  // A/B never rewrite the stream: applied counts describe what the viewer saw,
  // while would_* keeps the prototype policy result for RQ2 comparisons.
  session.recordSegment({
    bvid: "BV1Ho4y1D7Wz",
    segment_index: 1,
    shown: 972,
    hidden: 0,
    collapsed: 0,
    thinned: 0,
    applied: false,
    would: { shown: 953, hidden: 19, collapsed: 0, by_label: { attack: 19 } },
  });
  const event = session.events[0];
  assert.equal(event.shown, 972);
  assert.equal(event.hidden, 0);
  assert.deepEqual(event.would, { shown: 953, hidden: 19, collapsed: 0, by_label: { attack: 19 } });
  const counts = session.counts();
  assert.equal(counts.would_shown, 953);
  assert.equal(counts.would_hidden, 19);
  assert.equal(counts.would_collapsed, 0);
  // A second pass over the same segment must not lose the richer would counts.
  session.recordSegment({ bvid: "BV1Ho4y1D7Wz", segment_index: 1, shown: 60, would: { shown: 60, hidden: 0 } });
  assert.equal(session.events.length, 1);
  assert.equal(session.events[0].would.hidden, 19);
  assert.equal(session.counts().would_hidden, 19);
});
