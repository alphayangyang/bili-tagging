/**
 * Study-mode support for the userscript: condition presets, a bounded event
 * recorder, and the JSON export shape used by the controlled experiment.
 *
 * Mirror of docs/study/protocol.md (absent in this checkout, so task-7's
 * description is authoritative). Everything here is a pure function or a
 * plain data object: there is no DOM, storage, or network access, which keeps
 * the logic unit-testable in Node and lets `build.mjs` strip the `export`
 * keywords into the userscript IIFE.
 */

/** localStorage key an examiner can use to override the URL parameters. */
export const STUDY_STORAGE_KEY = "dmf.study";

/** URL parameter selecting the study. */
export const STUDY_ID_PARAM = "dmf_study";

/** URL parameter selecting the experimental condition. */
export const CONDITION_PARAM = "dmf_cond";

/** URL parameter selecting the density cell: high (original) or low (thinned). */
export const DENSITY_PARAM = "dmf_density";

/** Density cells understood by the study harness. */
export const STUDY_DENSITIES = Object.freeze(["high", "low"]);

/** Visible comments per minute in the low-density cell. */
export const LOW_DENSITY_PER_MIN = 30;

/** Conditions understood by the study harness. */
export const STUDY_CONDITIONS = Object.freeze(["all_on", "keyword", "prototype", "sham"]);
// The protocol and the questionnaire use A/B/C/D letters; exports carry both
// the canonical id and the letter so the analysis script and humans agree.
export const CONDITION_LETTERS = Object.freeze({
  all_on: "A",
  keyword: "B",
  prototype: "C",
  sham: "D",
});

/**
 * @param {string} condition canonical condition id
 * @returns {string} A/B/C letter ("" when unknown)
 */
export function conditionLetter(condition) {
  const key = typeof condition === "string" ? condition.trim().toLowerCase() : "";
  return CONDITION_LETTERS[key] || "";
}

/** Upper bound of retained events; the oldest event is dropped first. */
export const MAX_STUDY_EVENTS = 5000;

/** Chinese labels shown to the examiner. */
export const CONDITION_LABELS = Object.freeze({
  all_on: "全部开启（仅记录）",
  keyword: "仅关键词（仅记录）",
  prototype: "原型策略",
  sham: "安慰剂（不过滤）",
});

/**
 * The pre-set strategy of the prototype condition: hide the hostile labels,
 * collapse spoilers, show everything else, no density control.
 */
const PROTOTYPE_PER_TYPE = Object.freeze({
  information: "show",
  humor: "show",
  emotional_resonance: "show",
  political: "show",
  irrelevant_repetition: "hide",
  attack: "hide",
  conflict: "hide",
  inflammatory: "hide",
  spoiler: "collapse",
  other: "show",
});

/** Record-only conditions apply no per-label action at all. */
const RECORD_ONLY_PER_TYPE = Object.freeze({
  information: "show",
  humor: "show",
  emotional_resonance: "show",
  political: "show",
  irrelevant_repetition: "show",
  attack: "show",
  conflict: "show",
  inflammatory: "show",
  spoiler: "show",
  other: "show",
});

/**
 * Copy a JSON-ish value; primitives are returned as-is.
 * @param {unknown} value
 * @returns {unknown}
 */
function cloneJson(value) {
  if (value === null || typeof value !== "object") return value;
  try {
    return JSON.parse(JSON.stringify(value));
  } catch (error) {
    return null;
  }
}

/**
 * @param {unknown} value
 * @returns {number}
 */
function finiteNumber(value, fallback) {
  const number = Number(value);
  return Number.isFinite(number) ? number : fallback;
}

/**
 * Keep the study id usable as a filename stem; empty means "no study".
 * @param {unknown} value
 * @returns {string}
 */
export function cleanStudyId(value) {
  if (typeof value !== "string") return "";
  return value.trim().slice(0, 100);
}

/**
 * Normalize a density cell name; empty means "not specified".
 * @param {unknown} value
 * @returns {"high"|"low"|""}
 */
export function normalizeDensity(value) {
  if (typeof value !== "string") return "";
  const text = value.trim().toLowerCase();
  return STUDY_DENSITIES.includes(text) ? text : "";
}

/**
 * Normalize a condition name, case-insensitively; returns "" when unknown.
 * @param {unknown} value
 * @returns {"all_on"|"keyword"|"prototype"|"sham"|""}
 */
export function normalizeCondition(value) {
  if (typeof value !== "string") return "";
  const text = value.trim().toLowerCase();
  if (STUDY_CONDITIONS.includes(text)) return text;
  // The lab protocol writes conditions as A / B / C / D in the URL.
  if (text === "a" || text === "1") return "all_on";
  if (text === "b" || text === "2") return "keyword";
  if (text === "c" || text === "3") return "prototype";
  if (text === "d" || text === "4") return "sham";
  return "";
}

/**
 * The declarative description of one condition.
 * @param {unknown} condition
 * @returns {{condition: string, label: string, record_only: boolean,
 *            enabled: boolean, per_type: object, density: object}|null}
 */
export function conditionSpec(condition) {
  const key = normalizeCondition(condition);
  if (!key) return null;
  const prototype = key === "prototype";
  // The sham condition is interactive too: it prefetches the whole pool and
  // keeps the badge/panel visible like prototype, but its per-type preset is
  // all "show", so the player stream is never rewritten.
  const interactive = prototype || key === "sham";
  return {
    condition: key,
    label: CONDITION_LABELS[key],
    record_only: !interactive,
    enabled: interactive,
    per_type: prototype ? { ...PROTOTYPE_PER_TYPE } : { ...RECORD_ONLY_PER_TYPE },
    density: { enabled: false },
  };
}

/**
 * Deep-merge one condition's policy patch onto a base prefs object.
 * The result is a plain object; callers normalize it with `normalizePrefs`
 * before using it so unknown fields and invalid actions are dropped.
 *
 * @param {unknown} condition
 * @param {object|null|undefined} basePrefs
 * @returns {object|null}
 */
export function prefsForCondition(condition, basePrefs) {
  const spec = conditionSpec(condition);
  if (!spec) return null;
  const base = basePrefs && typeof basePrefs === "object" ? basePrefs : {};
  const basePerType = base.per_type && typeof base.per_type === "object" ? base.per_type : {};
  const baseDensity = base.density && typeof base.density === "object" ? base.density : {};
  return {
    version: Number.isFinite(Number(base.version)) ? Number(base.version) : 1,
    enabled: spec.enabled,
    min_confidence: finiteNumber(base.min_confidence, 0.3),
    per_type: { ...basePerType, ...spec.per_type },
    density: { ...baseDensity, enabled: false },
  };
}

/**
 * The policy used to *count* would-be actions in every study condition.
 *
 * Record-only conditions run with `enabled: false` so they never touch the
 * danmaku; counting still uses the prototype policy, otherwise the service
 * would answer "show" for every comment and the recorded counts would be zero
 * in every condition. The sham condition applies no per-type action at all, so
 * its analysis prefs are all-show as well; it is the no-filtering control.
 *
 * @param {unknown} condition
 * @param {object|null|undefined} basePrefs
 * @returns {object|null}
 */
export function analysisPrefsForCondition(condition, basePrefs) {
  const key = normalizeCondition(condition);
  if (!key) return null;
  return prefsForCondition(key === "sham" ? "sham" : "prototype", basePrefs);
}

/**
 * Parse the stored override blob. Supported shapes:
 *  - JSON object {"study_id": "...", "condition": "prototype"}
 *  - JSON string / plain string "studyId|condition"
 *  - a plain study id
 * @param {unknown} stored
 * @returns {{study_id: string, condition: string}|null}
 */
function parseStudyOverride(stored) {
  if (stored === null || stored === undefined || stored === "") return null;
  if (typeof stored === "object") {
    const id = stored.study_id !== undefined ? stored.study_id : stored.studyId;
    return { study_id: id, condition: stored.condition };
  }
  if (typeof stored !== "string") return null;
  const text = stored.trim();
  if (!text) return null;
  if (text.charAt(0) === "{") {
    try {
      return parseStudyOverride(JSON.parse(text));
    } catch (error) {
      return null;
    }
  }
  const parts = text.split("|");
  if (parts.length >= 2) {
    return { study_id: parts[0], condition: parts[1] };
  }
  // A lone condition name only overrides an existing URL study; any other
  // lone string is a study id that activates on its own.
  if (normalizeCondition(text)) return { study_id: "", condition: text };
  return { study_id: text, condition: "" };
}

/**
 * Resolve the active study from the page query string plus the localStorage
 * override. Returns null when no valid study id + condition is available.
 *
 * @param {string} search location.search (leading "?" optional)
 * @param {unknown} stored raw localStorage["dmf.study"] value (or an object)
 * @returns {{studyId: string, condition: string, source: string}|null}
 */
export function parseStudyConfig(search, stored) {
  let params = null;
  try {
    params = new URLSearchParams(typeof search === "string" ? search : "");
  } catch (error) {
    params = null;
  }
  let studyId = params ? cleanStudyId(params.get(STUDY_ID_PARAM)) : "";
  let condition = params ? normalizeCondition(params.get(CONDITION_PARAM)) : "";
  let density = params ? normalizeDensity(params.get(DENSITY_PARAM)) : "";
  let source = studyId ? "url" : "";
  const override = parseStudyOverride(stored);
  if (override) {
    const overrideId = cleanStudyId(override.study_id);
    const overrideCondition = normalizeCondition(override.condition);
    const overrideDensity = normalizeDensity(override.density);
    if (overrideId) {
      studyId = overrideId;
      source = "storage";
      if (overrideCondition) condition = overrideCondition;
      if (overrideDensity) density = overrideDensity;
    } else if (studyId && overrideCondition) {
      condition = overrideCondition;
      source = "url+storage";
      if (overrideDensity) density = overrideDensity;
    }
  }
  if (!studyId || !condition) return null;
  return { studyId, condition, density: density || "high", source };
}

/**
 * @param {unknown} studyId
 * @returns {string} download filename for the exported JSON
 */
export function studyDownloadName(studyId) {
  const safe = cleanStudyId(studyId).replace(/[^0-9A-Za-z._-]+/g, "_") || "session";
  return "dmf-study-" + safe + ".json";
}

/**
 * Count final actions (including "show") by action and by label.
 * @param {Map<string, object>|Record<string, object>|null|undefined} actions
 * @returns {{shown: number, hidden: number, collapsed: number, by_label: object}}
 */
export function countActions(actions) {
  const counts = { shown: 0, hidden: 0, collapsed: 0, by_label: {} };
  if (!actions) return counts;
  const entries = actions instanceof Map ? actions.entries() : Object.entries(actions);
  for (const pair of entries) {
    const decision = pair[1];
    if (!decision || typeof decision !== "object") continue;
    const action = decision.action === "hide" || decision.action === "collapse" ? decision.action : "show";
    if (action === "hide") counts.hidden += 1;
    else if (action === "collapse") counts.collapsed += 1;
    else counts.shown += 1;
    const label = typeof decision.label === "string" && decision.label ? decision.label : "other";
    counts.by_label[label] = (counts.by_label[label] || 0) + 1;
  }
  return counts;
}

/**
 * Flatten a prefs object to "section.field" scalar leaves.
 * @param {unknown} prefs
 * @returns {Map<string, unknown>}
 */
function flattenPrefs(prefs) {
  const flat = new Map();
  if (!prefs || typeof prefs !== "object") return flat;
  for (const entry of Object.entries(prefs)) {
    const key = entry[0];
    const value = entry[1];
    if (value && typeof value === "object" && !Array.isArray(value)) {
      for (const inner of Object.entries(value)) {
        const innerValue = inner[1];
        if (innerValue && typeof innerValue === "object") continue;
        flat.set(key + "." + inner[0], innerValue);
      }
    } else {
      flat.set(key, value);
    }
  }
  return flat;
}

/**
 * Diff two prefs snapshots into flat key changes, sorted by key.
 * @param {object|null|undefined} before
 * @param {object|null|undefined} after
 * @returns {Array<{key: string, from: unknown, to: unknown}>}
 */
export function diffPrefs(before, after) {
  const previous = flattenPrefs(before);
  const next = flattenPrefs(after);
  const keys = new Set([...previous.keys(), ...next.keys()]);
  const changes = [];
  for (const key of Array.from(keys).sort()) {
    const from = previous.has(key) ? previous.get(key) : null;
    const to = next.has(key) ? next.get(key) : null;
    if (JSON.stringify(from) === JSON.stringify(to)) continue;
    changes.push({ key: key, from: from, to: to });
  }
  return changes;
}

/**
 * Create a study session recorder. Events are kept in memory (bounded by
 * MAX_STUDY_EVENTS) and exported as the protocol's JSON document.
 *
 * @param {{studyId: string, condition: string, prefs?: object,
 *          scriptVersion?: string, ua?: string, now?: () => number}} options
 * @returns {object}
 */
export function createStudySession(options) {
  const opts = options && typeof options === "object" ? options : {};
  const clock = typeof opts.now === "function" ? opts.now : () => Date.now();
  const startedAtMs = clock();
  const iso = (timeMs) => {
    try {
      return new Date(timeMs).toISOString();
    } catch (error) {
      return "";
    }
  };
  const events = [];
  const segments = new Map();
  let droppedEvents = 0;
  let prefs = cloneJson(opts.prefs || null);
  const session = {
    studyId: cleanStudyId(opts.studyId),
    condition: normalizeCondition(opts.condition),
    scriptVersion: typeof opts.scriptVersion === "string" ? opts.scriptVersion : "dev",
    ua: typeof opts.ua === "string" ? opts.ua : "",
    startedAtMs: startedAtMs,
    events: events,

    /**
     * Append one bounded event.
     * @param {object} event
     */
    push(event) {
      const record = { t: clock(), ...event };
      if (events.length >= MAX_STUDY_EVENTS) {
        events.shift();
        droppedEvents += 1;
      }
      events.push(record);
      return record;
    },

    /**
     * @param {{bvid?: string, url?: string, segment_index?: number,
     *          shown?: number, hidden?: number, collapsed?: number,
     *          by_label?: object, latency_ms?: number, online?: boolean,
     *          applied?: boolean}} fields
     */
    recordSegment(fields) {
      const input = fields && typeof fields === "object" ? fields : {};
      const byLabel = {};
      const source = input.by_label && typeof input.by_label === "object" ? input.by_label : {};
      for (const entry of Object.entries(source)) {
        const value = finiteNumber(entry[1], -1);
        if (value >= 0) byLabel[entry[0]] = Math.floor(value);
      }
      const cleanCounts = (source) => {
        const value = source && typeof source === "object" ? source : {};
        const labels = {};
        const rawLabels = value.by_label && typeof value.by_label === "object" ? value.by_label : {};
        for (const entry of Object.entries(rawLabels)) {
          const count = finiteNumber(entry[1], -1);
          if (count >= 0) labels[entry[0]] = Math.floor(count);
        }
        return {
          shown: Math.max(0, Math.floor(finiteNumber(value.shown, 0))),
          hidden: Math.max(0, Math.floor(finiteNumber(value.hidden, 0))),
          collapsed: Math.max(0, Math.floor(finiteNumber(value.collapsed, 0))),
          by_label: labels,
        };
      };
      const record = {
        type: "segment",
        bvid: typeof input.bvid === "string" ? input.bvid : "",
        url: typeof input.url === "string" ? input.url : "",
        segment_index: finiteNumber(input.segment_index, 0),
        shown: Math.max(0, Math.floor(finiteNumber(input.shown, 0))),
        hidden: Math.max(0, Math.floor(finiteNumber(input.hidden, 0))),
        collapsed: Math.max(0, Math.floor(finiteNumber(input.collapsed, 0))),
        thinned: Math.max(0, Math.floor(finiteNumber(input.thinned, 0))),
        by_label: byLabel,
        latency_ms: Math.max(0, Math.round(finiteNumber(input.latency_ms, 0))),
        online: input.online === true,
        applied: input.applied !== false,
      };
      // Record-only conditions (A/B) keep the counterfactual policy result in
      // `would` so the applied counts stay true to what the viewer saw.
      if (input.would && typeof input.would === "object") record.would = cleanCounts(input.would);
      // The player re-requests a segment when it pauses/resumes; replace the
      // earlier record so per-segment counts are not double counted.
      const key = record.bvid + "|" + record.segment_index;
      const existing = segments.get(key);
      if (existing) {
        const firstSeen = existing.t;
        // A re-request can arrive after an earlier pass already thinned the bytes,
        // which would make the replacement look emptier; keep the fuller counts.
        const previous = {};
        for (const field of ["shown", "hidden", "collapsed", "thinned", "latency_ms"]) {
          previous[field] = existing[field] || 0;
        }
        const previouslyApplied = Boolean(existing.applied);
        const previousWould = existing.would || null;
        Object.assign(existing, record);
        for (const field of ["shown", "hidden", "collapsed", "thinned", "latency_ms"]) {
          existing[field] = Math.max(previous[field], record[field] || 0);
        }
        if (previousWould && existing.would) {
          for (const field of ["shown", "hidden", "collapsed"]) {
            existing.would[field] = Math.max(previousWould[field] || 0, existing.would[field] || 0);
          }
        } else if (previousWould) {
          existing.would = previousWould;
        }
        existing.applied = Boolean(previouslyApplied || record.applied);
        existing.t = firstSeen;
        return existing;
      }
      const stored = session.push(record);
      segments.set(key, stored);
      return stored;
    },

    /**
     * @param {string} key flat pref key, e.g. "per_type.attack"
     * @param {unknown} from
     * @param {unknown} to
     */
    recordPref(key, from, to) {
      return session.push({
        type: "pref",
        key: String(key || ""),
        from: cloneJson(from),
        to: cloneJson(to),
      });
    },

    /**
     * @param {{dmid?: string, text?: string, from?: string, to?: string}} fields
     */
    recordCorrection(fields) {
      const input = fields && typeof fields === "object" ? fields : {};
      return session.push({
        type: "correction",
        dmid: String(input.dmid || ""),
        text: String(input.text || ""),
        from: String(input.from || ""),
        to: String(input.to || ""),
      });
    },

    /**
     * @param {{dmid?: string, label?: string}} fields
     */
    recordRestore(fields) {
      const input = fields && typeof fields === "object" ? fields : {};
      return session.push({
        type: "restore",
        dmid: String(input.dmid || ""),
        label: String(input.label || ""),
      });
    },

    /**
     * @param {object} [fields]
     */
    recordExpand(fields) {
      const input = fields && typeof fields === "object" ? fields : {};
      const event = { type: "expand" };
      if (typeof input.panel === "string") event.panel = input.panel;
      return session.push(event);
    },

    /** @param {object|null|undefined} nextPrefs */
    setPrefs(nextPrefs) {
      prefs = cloneJson(nextPrefs || null);
      return prefs;
    },

    /** @returns {object} aggregate counters for the export */
    counts() {
      const totals = {
        segments: 0,
        shown: 0,
        hidden: 0,
        collapsed: 0,
        thinned: 0,
        would_shown: 0,
        would_hidden: 0,
        would_collapsed: 0,
        prefs: 0,
        corrections: 0,
        restores: 0,
        expands: 0,
        dropped_events: droppedEvents,
      };
      for (const event of events) {
        if (event.type === "segment") {
          totals.segments += 1;
          totals.shown += finiteNumber(event.shown, 0);
          totals.hidden += finiteNumber(event.hidden, 0);
          totals.collapsed += finiteNumber(event.collapsed, 0);
          totals.thinned += finiteNumber(event.thinned, 0);
          const would = event.would && typeof event.would === "object" ? event.would : {};
          totals.would_shown += finiteNumber(would.shown, 0);
          totals.would_hidden += finiteNumber(would.hidden, 0);
          totals.would_collapsed += finiteNumber(would.collapsed, 0);
        } else if (event.type === "pref") totals.prefs += 1;
        else if (event.type === "correction") totals.corrections += 1;
        else if (event.type === "restore") totals.restores += 1;
        else if (event.type === "expand") totals.expands += 1;
      }
      return totals;
    },

    /**
     * Build the exported JSON document (protocol §7 shape).
     * @returns {{study_id: string, condition: string, started_at: string,
     *            ended_at: string, counts: object, events: object[],
     *            script_version: string, prefs: object|null, ua: string}}
     */
    export() {
      return {
        study_id: session.studyId,
        condition: session.condition,
        condition_label: conditionLetter(session.condition),
        started_at: iso(startedAtMs),
        ended_at: iso(clock()),
        counts: session.counts(),
        events: events.slice(),
        script_version: session.scriptVersion,
        prefs: cloneJson(prefs),
        ua: session.ua,
      };
    },
  };
  return session;
}
