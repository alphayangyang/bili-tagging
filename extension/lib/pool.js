/**
 * Whole-pool danmaku processing (v0.4): merge every seg.so segment of a video,
 * build one action table for every danmaku, and thin the pool per equal-width
 * time window.
 *
 * The module is pure logic shared by the userscript and its Node tests. It
 * imports only its sibling lib modules so `node --test` can exercise it
 * directly; `extension/build.mjs` concatenates those modules first and strips
 * the import lines before wrapping everything in the userscript IIFE.
 */

import { parseSegment } from "./dmproto.js";
import { DEFAULT_PREFS, LABELS_ZH, decide, normalizePrefs } from "./policy.js";

/** Seconds of video covered by one seg.so payload. */
const SECONDS_PER_SEGMENT = 360;

/** Hard cap on segments, matching the Bilibili player. */
const MAX_SEGMENTS = 60;

/** Label used for danmaku without a usable tag. */
const FALLBACK_LABEL = "other";

/** Score used for danmaku without a usable tag. */
const FALLBACK_SCORE = 0.5;

/**
 * Labels that are never thinned by density: information is the category most
 * easily mis-killed, and untagged filler keeps the "other" bucket honest.
 */
const DENSITY_EXEMPT_LABELS = Object.freeze(["information", "other"]);

/**
 * Number of seg.so payloads a video of this duration needs.
 * @param {number} durationSeconds
 * @returns {number} integer in [1, 60]
 */
export function segmentCount(durationSeconds) {
  const duration = Number(durationSeconds);
  if (!Number.isFinite(duration) || duration <= 0) return 1;
  return Math.min(MAX_SEGMENTS, Math.max(1, Math.ceil(duration / SECONDS_PER_SEGMENT)));
}

/**
 * Normalize the segment list to `{index, bytes}` entries sorted by index.
 * @param {Array<{index?: number, bytes?: unknown}>|null|undefined} segments
 * @returns {Array<{index: number, bytes: unknown}>}
 */
function orderSegments(segments) {
  const list = Array.isArray(segments) ? segments : [];
  return list
    .map((segment, position) => {
      const index = Number(segment && segment.index);
      return {
        index: Number.isFinite(index) ? index : position + 1,
        bytes: segment ? segment.bytes : null,
      };
    })
    .sort((left, right) => left.index - right.index);
}

/**
 * Merge the parsed danmaku of every segment into one time-ordered pool.
 *
 * Duplicates across segments (Bilibili repeats the last danmaku of a segment at
 * the start of the next one) keep their first occurrence in segment order.
 *
 * @param {Array<{index?: number, bytes?: unknown}>|null|undefined} segments
 * @returns {{items: Array<{dmid: string, text: string, time_ms: number,
 *            segment_index: number}>, bySegment: Map<number, string[]>,
 *            stats: {raw: number, unique: number, duplicates: number,
 *                    segments: number}}}
 */
export function mergePool(segments) {
  const ordered = orderSegments(segments);
  const seen = new Set();
  const items = [];
  const bySegment = new Map();
  let raw = 0;
  for (const segment of ordered) {
    let dmids = bySegment.get(segment.index);
    if (!dmids) {
      dmids = [];
      bySegment.set(segment.index, dmids);
    }
    let parsed = [];
    try {
      parsed = parseSegment(segment.bytes);
    } catch (error) {
      parsed = [];
    }
    for (const item of parsed) {
      raw += 1;
      const dmid = item && item.dmid ? String(item.dmid) : "";
      if (!dmid) continue;
      dmids.push(dmid);
      if (seen.has(dmid)) continue;
      seen.add(dmid);
      items.push({
        dmid,
        text: typeof item.text === "string" ? item.text : "",
        time_ms: Number.isFinite(Number(item.time_ms)) ? Number(item.time_ms) : 0,
        segment_index: segment.index,
      });
    }
  }
  // Array.prototype.sort is stable, so equal timestamps keep segment/element order.
  items.sort((left, right) => left.time_ms - right.time_ms);
  return {
    items,
    bySegment,
    stats: {
      raw,
      unique: items.length,
      duplicates: raw - items.length,
      segments: ordered.length,
    },
  };
}

/**
 * Look up one `{label, score}` tag entry, accepting a Map or a plain object.
 * @param {Map<string, object>|Record<string, object>|null|undefined} labels
 * @param {string} dmid
 * @returns {{label: string, score: number}}
 */
function lookupTag(labels, dmid) {
  let entry = null;
  if (labels instanceof Map) entry = labels.get(dmid) || null;
  else if (labels && typeof labels === "object" && Object.prototype.hasOwnProperty.call(labels, dmid)) {
    entry = labels[dmid];
  }
  if (!entry || typeof entry !== "object") return { label: FALLBACK_LABEL, score: FALLBACK_SCORE };
  const label = typeof entry.label === "string" && LABELS_ZH[entry.label] ? entry.label : FALLBACK_LABEL;
  const score = Number(entry.score);
  return { label, score: Number.isFinite(score) ? score : FALLBACK_SCORE };
}

/**
 * Look up one user label override, accepting a Map or a plain object.
 * @param {Map<string, string>|Record<string, string>|null|undefined} overrides
 * @param {string} dmid
 * @returns {string} a valid label, or the empty string when there is none
 */
function lookupOverride(overrides, dmid) {
  let label = "";
  if (overrides instanceof Map) label = overrides.get(dmid) || "";
  else if (overrides && typeof overrides === "object" && Object.prototype.hasOwnProperty.call(overrides, dmid)) {
    label = overrides[dmid];
  }
  return typeof label === "string" && LABELS_ZH[label] ? label : "";
}

/**
 * Resolve the density config passed by main.js, falling back to prefs.density.
 * Density is always off while filtering is paused, so a user who disables the
 * filter sees the untouched pool.
 * @param {object|null|undefined} density
 * @param {object|null|undefined} fallback prefs.density
 * @param {boolean} [filterEnabled] prefs.enabled
 * @returns {{enabled: boolean, window_seconds: number, max_visible: number}}
 */
function resolveDensity(density, fallback, filterEnabled) {
  const defaults = fallback && typeof fallback === "object" ? fallback : DEFAULT_PREFS.density;
  const source = density && typeof density === "object" ? density : defaults;
  const enabled =
    filterEnabled === false
      ? false
      : typeof source.enabled === "boolean"
        ? source.enabled
        : Boolean(defaults.enabled);
  const windowSecondsRaw = Number(source.window_seconds);
  const windowSeconds = Number.isFinite(windowSecondsRaw) && windowSecondsRaw > 0 ? windowSecondsRaw : 10;
  const maxVisibleRaw = Number(source.max_visible);
  const maxVisible = Number.isFinite(maxVisibleRaw) ? Math.max(0, Math.floor(maxVisibleRaw)) : 30;
  return { enabled, window_seconds: windowSeconds, max_visible: maxVisible };
}

/**
 * Apply the equal-width window thinning to the show rows of one pool.
 *
 * Windows start at 0 and are `window_seconds` wide. Inside a window at most
 * `max_visible` non-exempt show rows survive (highest score first, earliest
 * timestamp on ties); the rest become collapse decisions with reason
 * "density". Information and other rows always survive and never take a slot.
 *
 * @param {Array<object>} rows entries with `time_ms`, `score`, `order`, and
 *        a mutable `action`/`reason`
 * @param {{enabled: boolean, window_seconds: number, max_visible: number}} config
 */
function applyPoolDensity(rows, config) {
  if (!config.enabled) return;
  const windowMs = config.window_seconds * 1000;
  const groups = new Map();
  for (const row of rows) {
    if (row.action !== "show") continue;
    if (row.forced) continue;
    if (DENSITY_EXEMPT_LABELS.includes(row.label)) continue;
    const timeMs = Math.max(0, row.time_ms);
    const windowIndex = Math.floor(timeMs / windowMs);
    let group = groups.get(windowIndex);
    if (!group) {
      group = [];
      groups.set(windowIndex, group);
    }
    group.push(row);
  }
  for (const group of groups.values()) {
    group.sort((left, right) => {
      if (right.score !== left.score) return right.score - left.score;
      if (left.time_ms !== right.time_ms) return left.time_ms - right.time_ms;
      return left.order - right.order;
    });
    for (let position = config.max_visible; position < group.length; position += 1) {
      group[position].action = "collapse";
      group[position].reason = "density";
    }
  }
}

/**
 * Build the final action for every danmaku of a whole pool.
 *
 * @param {{items?: Array<object>, labels?: object|Map, prefs?: object,
 *          overrides?: object|Map,
 *          density?: {enabled?: boolean, window_seconds?: number,
 *                     max_visible?: number}}} options
 * @returns {{actions: Map<string, object>, counts: {shown: number,
 *            hidden: number, collapsed: number}, folds: object[]}}
 */
export function buildActionTable(options) {
  const source = options && typeof options === "object" ? options : {};
  const items = Array.isArray(source.items) ? source.items : [];
  const labels = source.labels;
  const overrides = source.overrides;
  const prefs = normalizePrefs(source.prefs);
  const density = resolveDensity(source.density, prefs.density, prefs.enabled);
  const rows = [];
  for (let position = 0; position < items.length; position += 1) {
    const item = items[position] || {};
    const dmid = item.dmid === undefined || item.dmid === null ? "" : String(item.dmid);
    const text = typeof item.text === "string" ? item.text : "";
    const timeMs = Number.isFinite(Number(item.time_ms)) ? Number(item.time_ms) : 0;
    const segmentIndex = Number.isFinite(Number(item.segment_index)) ? Number(item.segment_index) : 0;
    const tag = lookupTag(labels, dmid);
    const overrideLabel = lookupOverride(overrides, dmid);
    let decision;
    if (overrideLabel) {
      decision = decide({ text, time_ms: timeMs, dominant: overrideLabel, score: 1 }, prefs);
    } else {
      decision = decide({ text, time_ms: timeMs, dominant: tag.label, score: tag.score }, prefs);
    }
    const forced = lookupFlag(source.forceShow, dmid);
    rows.push({
      dmid,
      text,
      time_ms: timeMs,
      segment_index: segmentIndex,
      action: forced ? "show" : decision.action,
      label: decision.label,
      score: decision.score,
      reason: forced ? "user_show" : decision.reason,
      source: overrideLabel ? "user" : "auto",
      forced,
      order: position,
    });
  }
  rows.sort((left, right) => left.time_ms - right.time_ms || left.order - right.order);
  applyPoolDensity(rows, density);
  const actions = new Map();
  const counts = { shown: 0, hidden: 0, collapsed: 0 };
  const folds = [];
  for (const row of rows) {
    const entry = {
      action: row.action,
      label: row.label,
      score: row.score,
      reason: row.reason,
      text: row.text,
      time_ms: row.time_ms,
      segment_index: row.segment_index,
      source: row.source,
    };
    actions.set(row.dmid, entry);
    if (row.action === "hide") counts.hidden += 1;
    else if (row.action === "collapse") counts.collapsed += 1;
    else counts.shown += 1;
    if (row.action !== "show") folds.push({ dmid: row.dmid, ...entry });
  }
  folds.sort((left, right) => left.time_ms - right.time_ms);
  return { actions, counts, folds };
}

/**
 * Look up one action entry, accepting a Map or a plain object.
 * @param {Map<string, object>|Record<string, object>|null|undefined} actions
 * @param {string} dmid
 * @returns {object|null}
 */
/**
 * Test one dmid against a Set, Map or plain object of flags.
 * @param {Set<string>|Map<string, unknown>|Record<string, unknown>|null|undefined} collection
 * @param {string} dmid
 * @returns {boolean}
 */
function lookupFlag(collection, dmid) {
  if (!collection) return false;
  if (collection instanceof Set) return collection.has(dmid);
  if (collection instanceof Map) return Boolean(collection.get(dmid));
  return Boolean(collection[dmid]);
}

function lookupAction(actions, dmid) {
  if (!actions) return null;
  if (actions instanceof Map) return actions.get(dmid) || null;
  if (typeof actions === "object" && Object.prototype.hasOwnProperty.call(actions, dmid)) {
    return actions[dmid] || null;
  }
  return null;
}

/**
 * The dmids that must not be rendered at all: hide and collapse decisions.
 * Collapsed danmaku are listed in the panel instead of shown as placeholders.
 * @param {Map<string, object>|Record<string, object>|null|undefined} actions
 * @returns {Set<string>}
 */
export function dropSet(actions) {
  const result = new Set();
  if (!actions) return result;
  const entries = actions instanceof Map ? actions.entries() : Object.entries(actions);
  for (const [dmid, entry] of entries) {
    if (!entry) continue;
    if (entry.action === "hide" || entry.action === "collapse") result.add(String(dmid));
  }
  return result;
}

/**
 * Count the final actions of the dmids of one segment. Dmids without an action
 * entry are counted as shown (fail open, matching the player behaviour).
 * @param {Map<string, object>|Record<string, object>|null|undefined} actions
 * @param {string[]|null|undefined} dmids
 * @returns {{shown: number, hidden: number, collapsed: number,
 *            by_label: Record<string, number>}}
 */
export function countForSegment(actions, dmids) {
  const counts = { shown: 0, hidden: 0, collapsed: 0, by_label: {} };
  const list = Array.isArray(dmids) ? dmids : [];
  for (const value of list) {
    const entry = lookupAction(actions, String(value));
    if (!entry) {
      counts.shown += 1;
      continue;
    }
    if (entry.action === "hide") counts.hidden += 1;
    else if (entry.action === "collapse") counts.collapsed += 1;
    else counts.shown += 1;
    if (entry.action === "hide" || entry.action === "collapse") {
      const label = entry.label && LABELS_ZH[entry.label] ? entry.label : FALLBACK_LABEL;
      counts.by_label[label] = (counts.by_label[label] || 0) + 1;
    }
  }
  return counts;
}

/**
 * Aggregate pool statistics for the panel: how many danmaku exist, how many are
 * tagged, and the label distribution (untagged danmaku count as "other").
 * @param {Array<{dmid?: string}>|null|undefined} items
 * @param {Map<string, object>|Record<string, object>|null|undefined} labels
 * @returns {{total: number, tagged: number, by_label: Record<string, number>}}
 */
export function poolStats(items, labels) {
  const list = Array.isArray(items) ? items : [];
  const byLabel = {};
  let tagged = 0;
  for (const item of list) {
    const dmid = item && item.dmid !== undefined && item.dmid !== null ? String(item.dmid) : "";
    const tag = lookupTag(labels, dmid);
    const isTagged = tag.label !== FALLBACK_LABEL || hasTag(labels, dmid);
    if (isTagged) tagged += 1;
    const label = isTagged ? tag.label : FALLBACK_LABEL;
    byLabel[label] = (byLabel[label] || 0) + 1;
  }
  return { total: list.length, tagged, by_label: byLabel };
}

/**
 * Stable hash of a dmid, used for deterministic density thinning.
 * @param {string} dmid
 * @returns {number}
 */
function stableRank(dmid) {
  const text = String(dmid || "");
  let hash = 5381;
  for (let index = 0; index < text.length; index += 1) {
    hash = ((hash << 5) + hash + text.charCodeAt(index)) >>> 0;
  }
  return hash;
}

/**
 * Deterministic density thinning for the study harness: keep at most
 * `target_per_min` comments per minute, chosen by a stable hash so the same
 * participant sees the same thinned stream on replay. Returns the dmid set to
 * remove from the stream.
 *
 * @param {{dmid: string, time_ms?: number}[]} items
 * @param {{target_per_min?: number, window_ms?: number}} [options]
 * @returns {Set<string>}
 */
export function thinSegmentDrop(items, options) {
  const settings = options && typeof options === "object" ? options : {};
  const targetPerMin = Number(settings.target_per_min);
  const perMinute = Number.isFinite(targetPerMin) && targetPerMin > 0 ? targetPerMin : 30;
  const windowMs = Number(settings.window_ms) > 0 ? Number(settings.window_ms) : 10000;
  const quota = Math.max(1, Math.round((perMinute * windowMs) / 60000));
  const buckets = new Map();
  for (const item of Array.isArray(items) ? items : []) {
    const key = Math.floor(Math.max(0, Number(item.time_ms) || 0) / windowMs);
    if (!buckets.has(key)) buckets.set(key, []);
    buckets.get(key).push(item);
  }
  const drop = new Set();
  for (const bucket of buckets.values()) {
    if (bucket.length <= quota) continue;
    const ranked = bucket
      .slice()
      .sort((a, b) => stableRank(a.dmid) - stableRank(b.dmid));
    for (let index = quota; index < ranked.length; index += 1) {
      drop.add(String(ranked[index].dmid));
    }
  }
  return drop;
}

/**
 * Whether a labels collection contains an entry for this dmid.
 * @param {Map<string, object>|Record<string, object>|null|undefined} labels
 * @param {string} dmid
 * @returns {boolean}
 */
function hasTag(labels, dmid) {
  if (labels instanceof Map) return labels.has(dmid);
  return Boolean(
    labels && typeof labels === "object" && Object.prototype.hasOwnProperty.call(labels, dmid)
  );
}
