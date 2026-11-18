/**
 * Presentation policy shared by the userscript and its Node tests.
 *
 * Mirrors `danmaku_filter/policy.py` (contracts.md §5) for the per-item
 * decision, then adds the client-side density window and a segment planner
 * that turns decisions into `dmproto.filterSegment` arguments.
 *
 * This module has no imports: `extension/build.mjs` strips the `export`
 * keywords and concatenates it into the userscript IIFE.
 */

/** Taxonomy labels plus the fallback label. */
export const TYPES = Object.freeze([
  "information",
  "humor",
  "emotional_resonance",
  "irrelevant_repetition",
  "attack",
  "conflict",
  "spoiler",
  "political",
  "inflammatory",
  "other",
]);

/** Chinese display names for every label (used by the UI and collapse text). */
export const LABELS_ZH = Object.freeze({
  information: "信息",
  humor: "幽默",
  emotional_resonance: "共鸣",
  irrelevant_repetition: "无意义重复",
  attack: "攻击",
  conflict: "争论",
  spoiler: "剧透",
  political: "政治",
  inflammatory: "引战",
  other: "其他",
});

/**
 * Labels that density control must never fold or hide: information is the
 * category most easily mis-killed. Everything else (including the untagged
 * "other" filler) competes for the visible slots, otherwise a busy video can
 * never be thinned out.
 */
export const DENSITY_PROTECTED_LABELS = Object.freeze(["information"]);

/** Actions understood by policy.decide and the UI controls. */
export const ACTIONS = Object.freeze(["show", "hide", "collapse"]);

/** Frozen defaults from contracts.md §5. */
export const DEFAULT_PREFS = Object.freeze({
  version: 1,
  enabled: true,
  // Show danmaku whose label score falls below min_confidence instead of
  // applying the label preference (fail-open). Turn off when the viewer wants
  // "hide everything of this type" to be literal.
  fail_open: true,
  min_confidence: 0.3,
  per_type: Object.freeze({
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
  }),
  density: Object.freeze({
    enabled: true,
    window_seconds: 10,
    max_visible: 30,
    // Exceeding the window removes the surplus danmaku (they are still counted
    // in the panel); "collapse" would keep one placeholder line per surplus
    // comment, which does not lower the on-screen density at all.
    exceed: "hide",
  }),
});

/**
 * Return a fresh, mutable copy of DEFAULT_PREFS.
 * @returns {object}
 */
export function defaultPrefs() {
  return {
    version: DEFAULT_PREFS.version,
    enabled: DEFAULT_PREFS.enabled,
    fail_open: DEFAULT_PREFS.fail_open,
    min_confidence: DEFAULT_PREFS.min_confidence,
    per_type: { ...DEFAULT_PREFS.per_type },
    density: { ...DEFAULT_PREFS.density },
  };
}

/**
 * Clamp a number to [low, high]; non-finite values fall back.
 * @param {unknown} value
 * @param {number} fallback
 * @param {number} low
 * @param {number} high
 * @returns {number}
 */
function clampNumber(value, fallback, low, high) {
  const number = Number(value);
  if (!Number.isFinite(number)) return fallback;
  return Math.min(high, Math.max(low, number));
}

/**
 * Validate one per-type action, falling back to the default of that type.
 * @param {unknown} action
 * @param {string} label
 * @returns {"show"|"hide"|"collapse"}
 */
function normalizeAction(action, label) {
  return ACTIONS.includes(action) ? action : DEFAULT_PREFS.per_type[label];
}

/**
 * Normalize a density config, filling invalid fields from the defaults.
 * @param {unknown} density
 * @returns {{enabled: boolean, window_seconds: number, max_visible: number,
 *            exceed: "show"|"hide"|"collapse"}}
 */
function normalizeDensity(density) {
  const defaults = DEFAULT_PREFS.density;
  const source = density && typeof density === "object" ? density : {};
  const enabled = typeof source.enabled === "boolean" ? source.enabled : defaults.enabled;
  const windowSeconds = clampNumber(source.window_seconds, defaults.window_seconds, 0.1, 3600);
  const maxVisible = Math.floor(clampNumber(source.max_visible, defaults.max_visible, 0, 100000));
  const exceed = source.exceed === "hide" || source.exceed === "collapse" ? source.exceed : defaults.exceed;
  return { enabled, window_seconds: windowSeconds, max_visible: maxVisible, exceed };
}

/**
 * Validate and fill a prefs object. Unknown labels and actions are dropped;
 * missing fields come from DEFAULT_PREFS. Never mutates the input.
 * @param {object|null|undefined} prefs
 * @returns {object}
 */
export function normalizePrefs(prefs) {
  const normalized = defaultPrefs();
  if (!prefs || typeof prefs !== "object") return normalized;
  if (typeof prefs.enabled === "boolean") normalized.enabled = prefs.enabled;
  if (typeof prefs.fail_open === "boolean") normalized.fail_open = prefs.fail_open;
  normalized.min_confidence = clampNumber(prefs.min_confidence, normalized.min_confidence, 0, 1);
  if (Number.isFinite(Number(prefs.version))) normalized.version = Number(prefs.version);
  if (prefs.per_type && typeof prefs.per_type === "object") {
    for (const label of TYPES) {
      if (Object.prototype.hasOwnProperty.call(prefs.per_type, label)) {
        normalized.per_type[label] = normalizeAction(prefs.per_type[label], label);
      }
    }
  }
  if (prefs.density && typeof prefs.density === "object") {
    normalized.density = normalizeDensity(prefs.density);
  }
  return normalized;
}

/**
 * Recursively merge plain objects, returning a new object.
 * @param {object} base
 * @param {object} patch
 * @returns {object}
 */
function mergeObjects(base, patch) {
  const output = { ...base };
  for (const [key, value] of Object.entries(patch)) {
    const baseValue = base[key];
    if (
      value &&
      typeof value === "object" &&
      !Array.isArray(value) &&
      baseValue &&
      typeof baseValue === "object" &&
      !Array.isArray(baseValue)
    ) {
      output[key] = mergeObjects(baseValue, value);
    } else {
      output[key] = value;
    }
  }
  return output;
}

/**
 * Deep-merge a partial prefs patch onto a base prefs object, then validate.
 * @param {object|null|undefined} base
 * @param {object|null|undefined} patch
 * @returns {object}
 */
export function mergePrefs(base, patch) {
  const current = normalizePrefs(base);
  if (!patch || typeof patch !== "object") return current;
  return normalizePrefs(mergeObjects(current, patch));
}

/**
 * Resolve a cached tag item against a user override from data/overrides.
 * With an override the dominant label is forced and the score becomes 1.0;
 * without one the original item is returned untouched (contracts.md §2).
 * @param {object|null|undefined} item
 * @param {{label?: string}|null|undefined} override
 * @returns {object|null|undefined}
 */
export function resolveItem(item, override) {
  if (!item) return item;
  if (!override || typeof override !== "object") return item;
  const label = override.label;
  if (typeof label !== "string" || !LABELS_ZH[label]) return item;
  return {
    ...item,
    dominant: label,
    tags: { [label]: 1.0 },
    score: 1.0,
    source: "user",
    evidence: "user correction",
  };
}

/**
 * Pick the dominant label of an item, tolerating a missing field by taking the
 * highest-scored tag.
 * @param {object} item
 * @returns {string}
 */
export function itemLabel(item) {
  if (!item || typeof item !== "object") return "other";
  if (typeof item.dominant === "string" && LABELS_ZH[item.dominant]) return item.dominant;
  const tags = item.tags && typeof item.tags === "object" ? item.tags : {};
  let best = "other";
  let bestScore = -Infinity;
  for (const [label, score] of Object.entries(tags)) {
    if (!LABELS_ZH[label]) continue;
    const value = Number(score);
    if (Number.isFinite(value) && value > bestScore) {
      best = label;
      bestScore = value;
    }
  }
  return best;
}

/**
 * Pick the confidence of an item: explicit score first, then tags[dominant].
 * @param {object} item
 * @param {string} label
 * @returns {number}
 */
export function itemScore(item, label) {
  if (!item || typeof item !== "object") return 0;
  const direct = Number(item.score);
  if (Number.isFinite(direct)) return direct;
  const tags = item.tags && typeof item.tags === "object" ? item.tags : null;
  if (tags) {
    const tagged = Number(tags[label]);
    if (Number.isFinite(tagged)) return tagged;
  }
  return 0;
}

/**
 * Decide one danmaku item, in the exact order of contracts.md §5:
 * missing item → disabled → low confidence → per-type preference.
 *
 * @param {object|null|undefined} item
 * @param {object|null|undefined} prefs
 * @returns {{action: "show"|"hide"|"collapse", label: string, score: number, reason: string}}
 */
export function decide(item, prefs) {
  const settings = normalizePrefs(prefs);
  if (!item) {
    return { action: "show", label: "other", score: 0, reason: "not_tagged" };
  }
  const label = itemLabel(item);
  const score = itemScore(item, label);
  if (!settings.enabled) return { action: "show", label, score, reason: "disabled" };
  if (score < settings.min_confidence && settings.fail_open !== false) {
    return { action: "show", label, score, reason: "low_confidence" };
  }
  const preferred = settings.per_type[label] || "show";
  if (preferred === "show") return { action: "show", label, score, reason: "default_show" };
  return { action: preferred, label, score, reason: "pref_" + preferred + "_" + label };
}

/**
 * Build the collapse placeholder for a label: 【已折叠·中文标签】.
 * @param {string} label
 * @returns {string}
 */
export function collapsedText(label) {
  const name = LABELS_ZH[label] || LABELS_ZH.other;
  return "【已折叠·" + name + "】";
}

/**
 * Human display name of a label (Chinese), falling back to "其他".
 * @param {string} label
 * @returns {string}
 */
export function labelName(label) {
  return LABELS_ZH[label] || LABELS_ZH.other;
}

/**
 * Sliding-window density controller. Call `apply` for every danmaku of a
 * segment in ascending time order; it returns the (possibly downgraded)
 * decision and records whether the item ended up visible.
 *
 * Semantics (contracts.md §5): once at least `max_visible` danmaku are visible
 * inside `window_seconds`, every newly arriving show decision is handled by
 * `exceed` and gets reason `density`. Explicit collapse/hide decisions from the
 * per-label preferences are never rewritten, and information labels are never
 * folded by density.
 */
export class DensityController {
  /**
   * @param {object|null|undefined} density prefs.density (partial allowed)
   */
  constructor(density) {
    const settings = normalizeDensity(density);
    this.enabled = settings.enabled;
    this.windowMs = settings.window_seconds * 1000;
    this.maxVisible = settings.max_visible;
    this.exceed = settings.exceed;
    this.visibleTimes = [];
    this.head = 0;
  }

  /** Number of visible danmaku currently inside the window. */
  get visibleCount() {
    return this.visibleTimes.length - this.head;
  }

  /** Forget every recorded timestamp. */
  reset() {
    this.visibleTimes = [];
    this.head = 0;
  }

  /**
   * Evict timestamps that fell out of the window.
   * @param {number} now
   */
  prune(now) {
    while (
      this.head < this.visibleTimes.length &&
      this.visibleTimes[this.head] < now - this.windowMs
    ) {
      this.head += 1;
    }
    if (this.head > 1024) {
      this.visibleTimes = this.visibleTimes.slice(this.head);
      this.head = 0;
    }
  }

  /**
   * Apply density to one decision and update the window state.
   * @param {{action: string, label: string, score: number, reason: string}} decision
   * @param {number} timeMs
   * @returns {{action: string, label: string, score: number, reason: string}}
   */
  apply(decision, timeMs) {
    const now = Number.isFinite(timeMs) ? timeMs : 0;
    this.prune(now);
    const protectedLabel = DENSITY_PROTECTED_LABELS.includes(decision.label);
    let action = decision.action;
    let reason = decision.reason;
    const windowFull = this.visibleCount >= this.maxVisible;
    // Only show decisions compete for a slot: once the window is full every
    // further show is handled by the exceed action. Collapse keeps its
    // placeholder because the user asked for it explicitly, and hidden danmaku
    // free their slot immediately, which is what makes the volume knob work.
    if (this.enabled && !protectedLabel && windowFull && action === "show") {
      action = this.exceed;
      reason = "density";
    }
    if (action !== "hide") this.visibleTimes.push(now);
    if (action === decision.action && reason === decision.reason) return decision;
    return { ...decision, action, reason };
  }
}

/**
 * Convenience wrapper: apply density to a time-ordered list of
 * `{time_ms, decision}` entries.
 * @param {Array<{time_ms: number, decision: object}>} entries
 * @param {object|null|undefined} prefs
 * @returns {object[]}
 */
export function applyDensity(entries, prefs) {
  const settings = normalizePrefs(prefs);
  const controller = new DensityController(settings.density);
  return entries.map((entry) => controller.apply(entry.decision, entry.time_ms));
}

/**
 * Turn a segment's danmaku items plus the service decisions into final
 * actions, panel entries, and `filterSegment` arguments.
 *
 * Items without a decision are treated as show. Local label overrides and
 * session "show once" exceptions are applied before density.
 *
 * @param {Array<object>} items parsed danmaku items (dmproto.parseSegment)
 * @param {Record<string, object>|null|undefined} decisions dmid → decision
 * @param {object|null|undefined} prefs
 * @param {{showOnce?: Set<string>, overrides?: Map<string,string>}} [options]
 * @returns {{actions: Map<string, object>, entries: object[], drop: Set<string>,
 *            collapse: Map<string,string>}}
 */
export function planSegment(items, decisions, prefs, options) {
  const settings = normalizePrefs(prefs);
  const showOnce = options && options.showOnce instanceof Set ? options.showOnce : new Set();
  const overrides = options && options.overrides instanceof Map ? options.overrides : new Map();
  const controller = new DensityController(settings.density);
  const ordered = items.slice().sort((left, right) => {
    return (Number(left.time_ms) || 0) - (Number(right.time_ms) || 0);
  });
  const labels = options && options.labels && typeof options.labels === "object" ? options.labels : null;
  const actions = new Map();
  const entries = [];
  for (const item of ordered) {
    let decision = decisions && decisions[item.dmid] ? decisions[item.dmid] : null;
    if (overrides.has(item.dmid)) {
      const label = overrides.get(item.dmid);
      decision = decide(resolveItem({ text: item.text, time_ms: item.time_ms }, { label }), settings);
    }
    if (!decision) {
      // The service omits decisions for comments it leaves visible, but the
      // label is still useful: the panel shows the real mix instead of
      // counting every visible comment as "other".
      const known = labels && labels[item.dmid] ? labels[item.dmid] : null;
      decision = {
        action: "show",
        label: known && known.label ? known.label : "other",
        score: known && typeof known.score === "number" ? known.score : 0,
        reason: "not_tagged",
      };
    }
    if (showOnce.has(item.dmid)) {
      decision = { action: "show", label: decision.label, score: decision.score, reason: "user_show" };
    }
    const finalDecision = controller.apply(decision, Number(item.time_ms) || 0);
    actions.set(item.dmid, finalDecision);
    if (finalDecision.action === "show") continue;
    entries.push({
      dmid: item.dmid,
      text: item.text,
      time_ms: Number(item.time_ms) || 0,
      label: finalDecision.label,
      label_name: labelName(finalDecision.label),
      score: finalDecision.score,
      reason: finalDecision.reason,
      action: finalDecision.action,
      evidence: decision.evidence || "",
    });
  }
  const drop = new Set();
  const collapse = new Map();
  for (const entry of entries) {
    if (entry.action === "hide") drop.add(entry.dmid);
    else if (entry.action === "collapse") collapse.set(entry.dmid, collapsedText(entry.label));
  }
  return { actions, entries, drop, collapse };
}
