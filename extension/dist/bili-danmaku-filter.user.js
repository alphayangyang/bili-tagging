// ==UserScript==
// @name         Bilibili 弹幕过滤（本地标签服务）
// @namespace    https://github.com/bili-tagging
// @version      0.4.2
// @description  按本地标签服务过滤 B 站弹幕：默认隐藏攻击/争论/剧透，折叠无意义重复，并按密度折叠
// @match        https://www.bilibili.com/video/*
// @run-at       document-start
// @grant        GM_xmlhttpRequest
// @connect      127.0.0.1
// @connect      api.bilibili.com
// ==/UserScript==

(() => {
  'use strict';

/* ---- lib/dmproto.js ---- */
/**
 * Minimal protobuf wire codec for Bilibili seg.so danmaku segments.
 *
 * The module is intentionally dependency free: `node extension/build.mjs`
 * strips the top-level `export` keywords and concatenates this file into the
 * userscript IIFE, where it runs in the page context. The same file can be
 * imported directly by `node --test` (ESM).
 *
 * Segment layout (observed from api.bilibili.com/x/v2/dm/web/seg.so):
 *   top level: field 1 (wire type 2) = DanmakuElem, repeated in order
 *   DanmakuElem: 1=dmid(varint, 64-bit) 2=progress(ms) 3=mode 4=fontsize
 *                5=color 6=midHash(str) 7=content(str) 8=ctime 9=weight
 *                12=idStr(str) 26=cid 27=type
 *
 * dmid is a 64-bit integer greater than 2^53, so it is always handled as a
 * decimal string key (field 12 idStr when present, otherwise the BigInt
 * decoding of field 1). Lengths and progress values are plain numbers.
 */

const WIRE_VARINT = 0;
const WIRE_FIXED64 = 1;
const WIRE_LENGTH = 2;
const WIRE_FIXED32 = 5;

const TOP_LEVEL_ELEMENT = 1;
const FIELD_DMID = 1;
const FIELD_PROGRESS = 2;
const FIELD_MODE = 3;
const FIELD_MID_HASH = 6;
const FIELD_CONTENT = 7;
const FIELD_ID_STR = 12;
const FIELD_CID = 26;

const TEXT_DECODER = new TextDecoder("utf-8", { fatal: false });
const TEXT_ENCODER = new TextEncoder();

/**
 * Raised when a byte sequence is not a valid protobuf message.
 * Internal to the codec; callers treat it as "unparseable, fail open".
 */
class ProtobufWireError extends Error {
  /**
   * @param {string} message
   */
  constructor(message) {
    super(message);
    this.name = "ProtobufWireError";
  }
}

/**
 * Normalize the accepted binary inputs to a Uint8Array view.
 * @param {Uint8Array|ArrayBuffer|ArrayBufferView|number[]} value
 * @returns {Uint8Array}
 */
function toBytes(value) {
  if (value instanceof Uint8Array) return value;
  if (value instanceof ArrayBuffer) return new Uint8Array(value);
  if (ArrayBuffer.isView(value)) {
    return new Uint8Array(value.buffer, value.byteOffset, value.byteLength);
  }
  if (Array.isArray(value)) return Uint8Array.from(value);
  throw new TypeError("expected Uint8Array, ArrayBuffer, or byte array");
}

/**
 * Concatenate byte chunks into one Uint8Array.
 * @param {Uint8Array[]} chunks
 * @returns {Uint8Array}
 */
function concatBytes(chunks) {
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
 * Encode a non-negative integer as a protobuf base-128 varint.
 * Accepts numbers (must be safe integers) and BigInts.
 * @param {number|bigint} value
 * @returns {Uint8Array}
 */
function encodeVarint(value) {
  let remaining;
  if (typeof value === "bigint") {
    remaining = value;
  } else if (Number.isSafeInteger(value)) {
    remaining = BigInt(value);
  } else {
    throw new RangeError("encodeVarint expects a non-negative safe integer or BigInt");
  }
  if (remaining < 0n) throw new RangeError("encodeVarint expects a non-negative value");
  const bytes = [];
  do {
    let byte = Number(remaining & 0x7fn);
    remaining >>= 7n;
    if (remaining > 0n) byte |= 0x80;
    bytes.push(byte);
  } while (remaining > 0n);
  return Uint8Array.from(bytes);
}

/**
 * Read one varint starting at offset. Returns the BigInt value and the offset
 * of the next field.
 * @param {Uint8Array} bytes
 * @param {number} offset
 * @returns {{value: bigint, offset: number}}
 */
function readVarint(bytes, offset) {
  let value = 0n;
  let shift = 0n;
  let cursor = offset;
  while (true) {
    if (cursor >= bytes.length) throw new ProtobufWireError("truncated varint");
    const byte = bytes[cursor];
    cursor += 1;
    value |= BigInt(byte & 0x7f) << shift;
    if ((byte & 0x80) === 0) return { value, offset: cursor };
    shift += 7n;
    if (shift > 63n) throw new ProtobufWireError("varint exceeds 64 bits");
  }
}

/**
 * Read a field tag (field number + wire type).
 * @param {Uint8Array} bytes
 * @param {number} offset
 * @returns {{field: number, wire: number, offset: number}}
 */
function readTag(bytes, offset) {
  const decoded = readVarint(bytes, offset);
  const field = Number(decoded.value >> 3n);
  const wire = Number(decoded.value & 7n);
  if (field <= 0) throw new ProtobufWireError("invalid field number");
  if (wire === 3 || wire === 4) throw new ProtobufWireError("protobuf groups are unsupported");
  return { field, wire, offset: decoded.offset };
}

/**
 * Read a length-delimited value.
 * @param {Uint8Array} bytes
 * @param {number} offset
 * @returns {{payload: Uint8Array, offset: number}}
 */
function readLengthDelimited(bytes, offset) {
  const decoded = readVarint(bytes, offset);
  const length = Number(decoded.value);
  if (!Number.isSafeInteger(length) || length < 0) {
    throw new ProtobufWireError("invalid length-delimited size");
  }
  const start = decoded.offset;
  const end = start + length;
  if (end > bytes.length) throw new ProtobufWireError("truncated length-delimited value");
  return { payload: bytes.subarray(start, end), offset: end };
}

/**
 * Skip the value of a field whose tag has already been consumed.
 * @param {Uint8Array} bytes
 * @param {number} offset
 * @param {number} wire
 * @returns {number} offset of the next field
 */
function skipValue(bytes, offset, wire) {
  if (wire === WIRE_VARINT) return readVarint(bytes, offset).offset;
  if (wire === WIRE_FIXED64) {
    if (offset + 8 > bytes.length) throw new ProtobufWireError("truncated fixed64");
    return offset + 8;
  }
  if (wire === WIRE_LENGTH) return readLengthDelimited(bytes, offset).offset;
  if (wire === WIRE_FIXED32) {
    if (offset + 4 > bytes.length) throw new ProtobufWireError("truncated fixed32");
    return offset + 4;
  }
  throw new ProtobufWireError("unsupported wire type: " + wire);
}

/**
 * Decode UTF-8 bytes leniently (invalid sequences become replacement chars).
 * @param {Uint8Array} bytes
 * @returns {string}
 */
function decodeUtf8(bytes) {
  return TEXT_DECODER.decode(bytes);
}

/**
 * Encode a length-delimited field (wire type 2).
 * @param {number} number field number, positive integer
 * @param {Uint8Array|ArrayBuffer|ArrayBufferView|number[]} payload
 * @returns {Uint8Array}
 */
function encodeField(number, payload) {
  if (!Number.isSafeInteger(number) || number <= 0) {
    throw new RangeError("encodeField expects a positive field number");
  }
  const bytes = toBytes(payload);
  return concatBytes([
    encodeVarint(number * 8 + WIRE_LENGTH),
    encodeVarint(bytes.length),
    bytes,
  ]);
}

/**
 * Yield the raw DanmakuElem payloads of a segment, preserving their order.
 * Non-element top-level fields are skipped.
 * @param {Uint8Array|ArrayBuffer|ArrayBufferView|number[]} data
 * @returns {Generator<Uint8Array>}
 */
function* iterElements(data) {
  const bytes = toBytes(data);
  let offset = 0;
  while (offset < bytes.length) {
    const tag = readTag(bytes, offset);
    offset = tag.offset;
    if (tag.field === TOP_LEVEL_ELEMENT && tag.wire === WIRE_LENGTH) {
      const decoded = readLengthDelimited(bytes, offset);
      yield decoded.payload;
      offset = decoded.offset;
    } else {
      offset = skipValue(bytes, offset, tag.wire);
    }
  }
}

/**
 * Decode one raw DanmakuElem payload.
 *
 * @param {Uint8Array|ArrayBuffer|ArrayBufferView|number[]} raw
 * @returns {{dmid: string, text: string, time_ms: number, mode: number,
 *            uid: string, cid: number, id_str: string}}
 */
function parseElement(raw) {
  const bytes = toBytes(raw);
  let dmidValue = 0n;
  let idStr = "";
  let text = "";
  let timeMs = 0;
  let mode = 1;
  let uid = "";
  let cid = 0;
  let offset = 0;
  while (offset < bytes.length) {
    const tag = readTag(bytes, offset);
    offset = tag.offset;
    if (tag.wire === WIRE_VARINT) {
      const decoded = readVarint(bytes, offset);
      offset = decoded.offset;
      if (tag.field === FIELD_DMID) dmidValue = decoded.value;
      else if (tag.field === FIELD_PROGRESS) timeMs = Number(decoded.value);
      else if (tag.field === FIELD_MODE) mode = Number(decoded.value);
      else if (tag.field === FIELD_CID) cid = Number(decoded.value);
    } else if (tag.wire === WIRE_LENGTH) {
      const decoded = readLengthDelimited(bytes, offset);
      offset = decoded.offset;
      if (tag.field === FIELD_MID_HASH) uid = decodeUtf8(decoded.payload);
      else if (tag.field === FIELD_CONTENT) text = decodeUtf8(decoded.payload);
      else if (tag.field === FIELD_ID_STR) idStr = decodeUtf8(decoded.payload);
    } else {
      offset = skipValue(bytes, offset, tag.wire);
    }
  }
  const dmid = idStr || (dmidValue > 0n ? dmidValue.toString() : "");
  return { dmid, text, time_ms: timeMs, mode, uid, cid, id_str: idStr };
}

/**
 * Decode a whole seg.so payload into danmaku items.
 * Elements with an empty text body are skipped, matching danmaku_filter/dmproto.py.
 * @param {Uint8Array|ArrayBuffer|ArrayBufferView|number[]} data
 * @returns {Array<{dmid: string, text: string, time_ms: number, mode: number,
 *                  uid: string, cid: number, id_str: string}>}
 */
function parseSegment(data) {
  const items = [];
  for (const raw of iterElements(data)) {
    let item;
    try {
      item = parseElement(raw);
    } catch (error) {
      continue;
    }
    if (!item.text) continue;
    items.push(item);
  }
  return items;
}

/**
 * Return a copy of raw with one length-delimited field replaced (or appended
 * when absent). All other fields, including unknown ones, are copied verbatim,
 * matching danmaku_filter/dmproto.py replace_field.
 * @param {Uint8Array|ArrayBuffer|ArrayBufferView|number[]} raw
 * @param {number} number field number to replace
 * @param {Uint8Array|ArrayBuffer|ArrayBufferView|number[]} payload
 * @returns {Uint8Array}
 */
function replaceField(raw, number, payload) {
  const bytes = toBytes(raw);
  const replacement = toBytes(payload);
  const chunks = [];
  let replaced = false;
  let offset = 0;
  while (offset < bytes.length) {
    const start = offset;
    const tag = readTag(bytes, offset);
    offset = tag.offset;
    if (tag.field === number) {
      if (tag.wire === WIRE_LENGTH) {
        offset = readLengthDelimited(bytes, offset).offset;
      } else {
        offset = skipValue(bytes, offset, tag.wire);
      }
      chunks.push(encodeField(number, replacement));
      replaced = true;
    } else if (tag.wire === WIRE_LENGTH) {
      offset = readLengthDelimited(bytes, offset).offset;
      chunks.push(bytes.subarray(start, offset));
    } else {
      offset = skipValue(bytes, offset, tag.wire);
      chunks.push(bytes.subarray(start, offset));
    }
  }
  if (!replaced) chunks.push(encodeField(number, replacement));
  return concatBytes(chunks);
}

/**
 * Coerce a drop collection into a Set of dmid strings.
 * @param {Set<string>|string[]|null|undefined} drop
 * @returns {Set<string>}
 */
function toDropSet(drop) {
  if (drop instanceof Set) {
    const result = new Set();
    for (const value of drop) result.add(String(value));
    return result;
  }
  if (Array.isArray(drop)) return new Set(drop.map(String));
  return new Set();
}

/**
 * Coerce collapse config (Map or plain object) into a Map of dmid → text.
 * @param {Map<string,string>|Record<string,string>|null|undefined} collapse
 * @returns {Map<string,string>}
 */
function toCollapseMap(collapse) {
  const result = new Map();
  if (collapse instanceof Map) {
    for (const [key, value] of collapse) result.set(String(key), String(value));
  } else if (collapse && typeof collapse === "object") {
    for (const [key, value] of Object.entries(collapse)) result.set(String(key), String(value));
  }
  return result;
}

/**
 * Rewrite a seg.so payload: drop whole elements for dmids in drop, and replace
 * the field 7 content of dmids in collapse, keeping every other field and the
 * original top-level field order.
 *
 * @param {Uint8Array|ArrayBuffer|ArrayBufferView|number[]} data
 * @param {Set<string>|string[]|null|undefined} drop
 * @param {Map<string,string>|Record<string,string>|null|undefined} collapse
 * @returns {{data: Uint8Array, hidden: number, collapsed: number}}
 */
function filterSegment(data, drop, collapse) {
  const bytes = toBytes(data);
  const dropSet = toDropSet(drop);
  const collapseMap = toCollapseMap(collapse);
  const chunks = [];
  let hidden = 0;
  let collapsed = 0;
  let offset = 0;
  while (offset < bytes.length) {
    const start = offset;
    const tag = readTag(bytes, offset);
    offset = tag.offset;
    if (tag.field !== TOP_LEVEL_ELEMENT || tag.wire !== WIRE_LENGTH) {
      offset = skipValue(bytes, offset, tag.wire);
      chunks.push(bytes.subarray(start, offset));
      continue;
    }
    const decoded = readLengthDelimited(bytes, offset);
    offset = decoded.offset;
    let element;
    try {
      element = parseElement(decoded.payload);
    } catch (error) {
      chunks.push(bytes.subarray(start, offset));
      continue;
    }
    if (dropSet.has(element.dmid)) {
      hidden += 1;
      continue;
    }
    const replacement = collapseMap.get(element.dmid);
    if (replacement === undefined) {
      chunks.push(bytes.subarray(start, offset));
      continue;
    }
    chunks.push(
      encodeField(TOP_LEVEL_ELEMENT, replaceField(decoded.payload, FIELD_CONTENT, TEXT_ENCODER.encode(replacement)))
    );
    collapsed += 1;
  }
  return { data: concatBytes(chunks), hidden, collapsed };
}

/* ---- lib/policy.js ---- */
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
const TYPES = Object.freeze([
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
const LABELS_ZH = Object.freeze({
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
const DENSITY_PROTECTED_LABELS = Object.freeze(["information"]);

/** Actions understood by policy.decide and the UI controls. */
const ACTIONS = Object.freeze(["show", "hide", "collapse"]);

/** Frozen defaults from contracts.md §5. */
const DEFAULT_PREFS = Object.freeze({
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
function defaultPrefs() {
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
function normalizePrefs(prefs) {
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
function mergePrefs(base, patch) {
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
function resolveItem(item, override) {
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
function itemLabel(item) {
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
function itemScore(item, label) {
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
function decide(item, prefs) {
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
function collapsedText(label) {
  const name = LABELS_ZH[label] || LABELS_ZH.other;
  return "【已折叠·" + name + "】";
}

/**
 * Human display name of a label (Chinese), falling back to "其他".
 * @param {string} label
 * @returns {string}
 */
function labelName(label) {
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
class DensityController {
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
function applyDensity(entries, prefs) {
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
function planSegment(items, decisions, prefs, options) {
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

/* ---- lib/linear.js ---- */
/**
 * Offline linear danmaku scorer.
 *
 * Pure ES module with no imports: it mirrors the Python hashed character
 * n-gram TF-IDF scorer bit for bit so the userscript can label a segment
 * without calling the local service.  Two JSON shapes are understood: the
 * frozen spec shape and the shape Python's LinearTagger.to_dict() emits::
 *
 *   spec:   {schema, kind, dim, ngram, classes, default_idf, idf, weights}
 *   python: {schema, kind, labels, vectorizer: {dim, ngram, n_docs, idf},
 *            weights}
 *
 * The Python artifact carries n_docs instead of default_idf; the unseen-bucket
 * fallback is then ln(1 + n_docs) + 1, exactly as HashingTfidf.transform uses.
 *
 * Feature extraction (must match Python exactly):
 *
 *   t = " " + text.trim().toLowerCase() + " "
 *   grams = every substring of t of length 1..3 (spaces included)
 *   count[gram] = occurrences of gram in t
 *   bucket = crc32(utf8(gram)) % dim
 *   weight[bucket] += (1 + ln(count)) * idf(bucket)
 *   vector = weight / ||weight||
 *
 * centroid models score cos(document, class_centroid) and pass the cosine
 * similarities through a softmax (cos * 10).  softmax models take the raw
 * dot products as logits and return the softmax probabilities.
 */

/** Standard IEEE CRC-32 lookup table (same polynomial as zlib.crc32). */
const CRC32_TABLE = (() => {
  const table = new Uint32Array(256);
  for (let n = 0; n < 256; n += 1) {
    let c = n;
    for (let k = 0; k < 8; k += 1) {
      c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
    }
    table[n] = c >>> 0;
  }
  return table;
})();

const DEFAULT_DIM = 262144;
const DEFAULT_NGRAM = [1, 3];

/**
 * Return the standard IEEE CRC-32 of a UTF-8 string or byte array.
 *
 * @param {string|Uint8Array|number[]} input
 * @returns {number} unsigned 32-bit checksum
 */
function crc32(input) {
  let bytes;
  if (typeof input === "string") {
    bytes = new TextEncoder().encode(input);
  } else if (input instanceof Uint8Array) {
    bytes = input;
  } else if (Array.isArray(input)) {
    bytes = Uint8Array.from(input);
  } else {
    throw new TypeError("crc32 expects a string or byte array");
  }
  let crc = 0xffffffff;
  for (let i = 0; i < bytes.length; i += 1) {
    crc = (crc >>> 8) ^ CRC32_TABLE[(crc ^ bytes[i]) & 0xff];
  }
  return (crc ^ 0xffffffff) >>> 0;
}

/**
 * Normalise a raw danmaku body exactly like the Python trainer.
 *
 * @param {string} text
 * @returns {string} " " + text.trim().toLowerCase() + " "
 */
function preprocess(text) {
  const value = typeof text === "string" ? text : String(text ?? "");
  return " " + value.trim().toLowerCase() + " ";
}

/**
 * Coerce a model n-gram range into [minLength, maxLength].
 *
 * @param {unknown} range
 * @returns {[number, number]}
 */
function normalizeNgramRange(range) {
  if (range === undefined || range === null) {
    return [...DEFAULT_NGRAM];
  }
  if (!Array.isArray(range) || range.length !== 2) {
    throw new TypeError("model.ngram must be [minLength, maxLength]");
  }
  const min = Number(range[0]);
  const max = Number(range[1]);
  if (!Number.isInteger(min) || !Number.isInteger(max) || min < 1 || max < min) {
    throw new TypeError("model.ngram must be two positive integers with min <= max");
  }
  return [min, max];
}

/**
 * Split normalised text into every n-gram of the configured lengths.
 *
 * The order is length-major (1-grams first, then 2-grams, ...), which is the
 * order the Python implementation uses.
 *
 * @param {string} text
 * @param {[number, number]} [range]
 * @returns {string[]}
 */
function ngramsOf(text, range = DEFAULT_NGRAM) {
  const [minLength, maxLength] = normalizeNgramRange(range);
  const value = preprocess(text);
  const grams = [];
  for (let length = minLength; length <= maxLength; length += 1) {
    for (let start = 0; start + length <= value.length; start += 1) {
      grams.push(value.slice(start, start + length));
    }
  }
  return grams;
}

/**
 * Count every n-gram occurrence in the normalised text.
 *
 * @param {string} text
 * @param {[number, number]} [range]
 * @returns {Map<string, number>} gram -> count
 */
function ngramCounts(text, range = DEFAULT_NGRAM) {
  const counts = new Map();
  for (const gram of ngramsOf(text, range)) {
    counts.set(gram, (counts.get(gram) ?? 0) + 1);
  }
  return counts;
}

/**
 * Parse a bucket key from a model JSON object key.
 *
 * @param {string} key
 * @param {string} what
 * @returns {number}
 */
function parseBucket(key, what) {
  const bucket = Number(key);
  if (!Number.isInteger(bucket) || bucket < 0) {
    throw new TypeError(what + " has a non-integer bucket: " + JSON.stringify(key));
  }
  return bucket;
}

/**
 * Copy one model sparse vector from a plain object into a Map.
 *
 * @param {unknown} source
 * @param {string} what
 * @returns {Map<number, number>} bucket -> weight
 */
function sparseVector(source, what) {
  const vector = new Map();
  if (source === undefined || source === null) {
    return vector;
  }
  if (typeof source !== "object" || Array.isArray(source)) {
    throw new TypeError(what + " must be an object of bucket -> weight");
  }
  for (const [key, rawValue] of Object.entries(source)) {
    const value = Number(rawValue);
    if (!Number.isFinite(value)) {
      throw new TypeError(what + " has a non-numeric weight for bucket " + key);
    }
    vector.set(parseBucket(key, what), value);
  }
  return vector;
}

/**
 * Validate a model description and convert its sparse objects to Maps.
 *
 * @param {object} model
 * @returns {object} normalised model
 */
function loadModelFromJson(model) {
  if (!model || typeof model !== "object" || Array.isArray(model)) {
    throw new TypeError("model must be an object");
  }
  // Already normalised by a previous call: keep the fast path idempotent.
  if (model.idf instanceof Map && model.weights instanceof Map) {
    return model;
  }
  const kind = model.kind;
  if (kind !== "centroid" && kind !== "softmax") {
    throw new TypeError("model.kind must be 'centroid' or 'softmax'");
  }
  const pythonShape =
    Array.isArray(model.labels) &&
    model.vectorizer !== null &&
    typeof model.vectorizer === "object";
  const vectorizer = pythonShape ? model.vectorizer : model;
  const dim = Number(vectorizer.dim ?? DEFAULT_DIM);
  if (!Number.isInteger(dim) || dim <= 0) {
    throw new TypeError("model.dim must be a positive integer");
  }
  const classes = pythonShape ? model.labels : model.classes;
  if (!Array.isArray(classes) || classes.length === 0) {
    throw new TypeError("model.classes must be a non-empty array");
  }
  const seen = new Set();
  for (const tag of classes) {
    if (typeof tag !== "string" || tag.length === 0) {
      throw new TypeError("model.classes entries must be non-empty strings");
    }
    if (seen.has(tag)) {
      throw new TypeError("model.classes contains a duplicate: " + tag);
    }
    seen.add(tag);
  }
  const ngram = normalizeNgramRange(vectorizer.ngram);
  let defaultIdf;
  if (pythonShape) {
    const nDocs = Number(vectorizer.n_docs ?? 0);
    defaultIdf = Math.log(1 + (Number.isFinite(nDocs) && nDocs > 0 ? nDocs : 0)) + 1;
  } else if (model.default_idf === undefined) {
    defaultIdf = 1;
  } else {
    defaultIdf = Number(model.default_idf);
  }
  if (!Number.isFinite(defaultIdf)) {
    throw new TypeError("model.default_idf must be a finite number");
  }
  const idf = sparseVector(vectorizer.idf, "model.idf");
  const rawWeights = model.weights;
  if (!rawWeights || typeof rawWeights !== "object" || Array.isArray(rawWeights)) {
    throw new TypeError("model.weights must be an object of class -> weights");
  }
  const weights = new Map();
  for (const tag of classes) {
    weights.set(tag, sparseVector(rawWeights[tag], "model.weights." + tag));
  }
  return {
    schema: model.schema === undefined ? 1 : model.schema,
    kind,
    dim,
    ngram,
    classes: [...classes],
    defaultIdf,
    idf,
    weights,
  };
}

/**
 * L2 norm of a sparse vector.
 *
 * @param {Map<number, number>} vector
 * @returns {number}
 */
function vectorNorm(vector) {
  let total = 0;
  for (const value of vector.values()) {
    total += value * value;
  }
  return Math.sqrt(total);
}

/**
 * Stable softmax over an array of logits.
 *
 * @param {number[]} logits
 * @returns {number[]}
 */
function softmax(logits) {
  let max = -Infinity;
  for (const value of logits) {
    if (value > max) {
      max = value;
    }
  }
  const exps = logits.map((value) => Math.exp(value - max));
  let total = 0;
  for (const value of exps) {
    total += value;
  }
  if (!(total > 0) || !Number.isFinite(total)) {
    return logits.map(() => 1 / logits.length);
  }
  return exps.map((value) => value / total);
}

/**
 * Hashed TF-IDF linear scorer.
 *
 * Usage::
 *
 *   const scorer = new LinearScorer(modelJson);
 *   scorer.scores("前方高能");   // {humor: 0.1, attack: 0.7, ...}
 *   scorer.predict("前方高能");  // {tag: "attack", score: 0.7}
 */
class LinearScorer {
  /**
   * @param {object} model parsed model JSON (see module docstring)
   */
  constructor(model) {
    /** @type {object} */
    this.model = loadModelFromJson(model);
  }

  /**
   * Unnormalised TF-IDF weights keyed by hash bucket.
   *
   * @param {string} text
   * @returns {Map<number, number>} bucket -> 1 + ln(count) times idf
   */
  weights(text) {
    const { dim, ngram, idf, defaultIdf } = this.model;
    // Count occurrences per bucket, not per gram: Python hashes first and
    // applies tf = 1 + ln(bucket count), so collisions share one tf term.
    const counts = new Map();
    for (const gram of ngramsOf(text, ngram)) {
      const bucket = crc32(gram) % dim;
      counts.set(bucket, (counts.get(bucket) ?? 0) + 1);
    }
    const vector = new Map();
    for (const [bucket, count] of counts) {
      const idfValue = idf.get(bucket);
      vector.set(bucket, (1 + Math.log(count)) * (idfValue === undefined ? defaultIdf : idfValue));
    }
    return vector;
  }

  /**
   * L2-normalised TF-IDF document vector keyed by hash bucket.
   *
   * @param {string} text
   * @returns {Map<number, number>} bucket -> normalised weight
   */
  features(text) {
    const vector = this.weights(text);
    const norm = vectorNorm(vector);
    if (!(norm > 0)) {
      return new Map();
    }
    const normalised = new Map();
    for (const [bucket, value] of vector) {
      normalised.set(bucket, value / norm);
    }
    return normalised;
  }

  /**
   * Score one text against every model class.
   *
   * @param {string} text
   * @returns {Record<string, number>} tag -> probability in [0, 1]
   */
  scores(text) {
    const features = this.features(text);
    const logits = this.model.classes.map((tag) => {
      const centroid = this.model.weights.get(tag);
      let dot = 0;
      for (const [bucket, value] of features) {
        const weight = centroid.get(bucket);
        if (weight !== undefined) {
          dot += value * weight;
        }
      }
      // Python scores centroid models with the raw dot product * 10 and
      // does not re-normalise prototypes after pruning, so parity requires
      // keeping plain dot here (an unpruned centroid is already unit norm).
      if (this.model.kind === "centroid") {
        return dot * 10;
      }
      return dot;
    });
    const probabilities = softmax(logits);
    const scores = {};
    this.model.classes.forEach((tag, index) => {
      scores[tag] = probabilities[index];
    });
    return scores;
  }

  /**
   * Return the highest-scoring class.
   *
   * @param {string} text
   * @returns {{tag: string, score: number}}
   */
  predict(text) {
    const scores = this.scores(text);
    let topTag = this.model.classes[0];
    let topScore = scores[topTag];
    for (const tag of this.model.classes) {
      if (scores[tag] > topScore) {
        topTag = tag;
        topScore = scores[tag];
      }
    }
    return { tag: topTag, score: topScore };
  }
}

/* ---- lib/pool.js ---- */
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
function segmentCount(durationSeconds) {
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
function mergePool(segments) {
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
function buildActionTable(options) {
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
function dropSet(actions) {
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
function countForSegment(actions, dmids) {
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
function poolStats(items, labels) {
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
function thinSegmentDrop(items, options) {
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

/* ---- lib/poolweb.js ---- */
/**
 * Whole-pool prefetch layer (v0.4).
 *
 * The player downloads segments lazily, so per-segment decisions can only be
 * made while playback is already running: a strategy change then takes effect
 * minutes later, density is computed per segment instead of per video, and the
 * same comment is counted once per download. This layer fetches every segment
 * up front, labels the whole pool (per-video linear model in the page, or
 * service batches as the fallback), builds one action table, and lets each
 * player request rewrite its bytes against that table with no extra latency.
 *
 * Concatenated after lib/pool.js and before src/main.js, so the globals from
 * build.mjs (state, parseSegment, postJson, ...) resolve at call time.
 */

const POOL_MAX_SEGMENTS = 24;
const POOL_MAX_ITEMS = 40000;
const POOL_WAIT_MS = 2500;
// Study sessions trade startup latency for a consistent prototype condition:
// wait for the whole-pool table instead of falling back to per-segment labels.
const POOL_STUDY_WAIT_MS = 15000;
const POOL_FETCH_CONCURRENCY = 3;
const POOL_LABEL_CHUNK = 300;
const POOL_LABEL_CONCURRENCY = 2;
const POOL_EMPTY_STREAK = 2;
const POOL_OVERLAY_ID = "dmf-pool-overlay";

/** @param {number} ms @returns {Promise<void>} */
function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/** @param {object} segment @returns {string} */
function poolKeyFor(segment) {
  return currentBvid() + "|" + segment.cid;
}

/** @param {object} segment @returns {object} */
function ensurePool(segment) {
  const key = poolKeyFor(segment);
  if (state.pool && state.pool.key === key) return state.pool;
  state.pool = {
    key,
    bvid: currentBvid(),
    cid: segment.cid,
    aid: segment.aid,
    ready: false,
    loading: null,
    items: [],
    labels: null,
    actions: new Map(),
    counts: { shown: 0, hidden: 0, collapsed: 0 },
    folds: [],
    progress: { done: 0, total: 0, items: 0, phase: "fetch" },
    pausedVideo: null,
    resumed: false,
    error: "",
    startedAt: Date.now(),
    loadMs: 0,
    localLabels: 0,
  };
  return state.pool;
}

/** The prefs the pool table is built with (study analysis prefs in a session). */
function poolPrefs() {
  if (state.study && state.study.analysisPrefs) return state.study.analysisPrefs;
  return state.prefs;
}

/** @param {object} segment @param {number} index @returns {string} */
function poolSegmentUrl(segment, index) {
  const cid = encodeURIComponent(segment.cid);
  const aid = encodeURIComponent(segment.aid || 0);
  return (
    "https://api.bilibili.com/x/v2/dm/web/seg.so?type=1&oid=" +
    cid +
    "&pid=" +
    aid +
    "&segment_index=" +
    index
  );
}

/** Best-effort video duration in seconds. */
function pageDurationSeconds() {
  try {
    const data = PAGE.__INITIAL_STATE__;
    if (data && data.videoData && Number.isFinite(Number(data.videoData.duration))) {
      return Number(data.videoData.duration);
    }
  } catch (error) {
    /* ignore */
  }
  try {
    const video = document.querySelector("video");
    if (video && Number.isFinite(video.duration) && video.duration > 0) return video.duration;
  } catch (error) {
    /* ignore */
  }
  try {
    const player = PAGE.player;
    if (player && typeof player.getDuration === "function") {
      const value = Number(player.getDuration());
      if (Number.isFinite(value) && value > 0) return value;
    }
  } catch (error) {
    /* ignore */
  }
  return 0;
}

/** The small fixed progress pill shown while the pool is being prepared. */
function poolOverlay() {
  let node = document.getElementById(POOL_OVERLAY_ID);
  if (node) return node;
  node = document.createElement("div");
  node.id = POOL_OVERLAY_ID;
  node.style.cssText = [
    "position:fixed",
    "left:50%",
    "top:14px",
    "transform:translateX(-50%)",
    "z-index:2147483000",
    "display:none",
    "padding:7px 14px",
    "border-radius:999px",
    "background:rgba(20,22,28,.92)",
    "border:1px solid rgba(251,114,153,.45)",
    "color:#f6f7fb",
    "font:12px/1.5 -apple-system,BlinkMacSystemFont,Segoe UI,sans-serif",
    "box-shadow:0 6px 20px rgba(0,0,0,.35)",
    "pointer-events:none",
  ].join(";");
  const text = document.createElement("span");
  text.textContent = "正在整理弹幕池…";
  node.appendChild(text);
  node.__dmfText = text;
  (document.body || document.documentElement).appendChild(node);
  return node;
}

/** @param {object} pool */
function updatePoolOverlay(pool) {
  const node = document.getElementById(POOL_OVERLAY_ID);
  if (!node || !node.__dmfText || !pool) return;
  const progress = pool.progress;
  let text = "正在整理弹幕池";
  if (progress.total) text += " " + progress.done + "/" + progress.total + " 段";
  if (pool.items.length) text += " · " + pool.items.length + " 条";
  if (progress.phase === "label") text += " · 打分中";
  node.__dmfText.textContent = text;
}

/** Pause playback so the first frame is already filtered. */
function pauseForPool(pool) {
  try {
    const video = document.querySelector("video");
    if (video && !video.paused && !video.ended) {
      pool.pausedVideo = video;
      video.pause();
    }
  } catch (error) {
    /* ignore */
  }
}

/** Resume playback only when this layer paused it. */
function resumeAfterPool(pool) {
  const video = pool ? pool.pausedVideo : null;
  if (pool) pool.pausedVideo = null;
  if (!video || !pool || pool.resumed) return;
  pool.resumed = true;
  try {
    const promise = video.play();
    if (promise && typeof promise.catch === "function") promise.catch(() => {});
  } catch (error) {
    /* ignore */
  }
}

/** @param {object} pool */
function showPoolOverlay(pool) {
  const node = poolOverlay();
  node.style.display = "block";
  updatePoolOverlay(pool);
  pauseForPool(pool);
}

/** @param {object|null} pool */
function hidePoolOverlay(pool) {
  const node = document.getElementById(POOL_OVERLAY_ID);
  if (node) node.style.display = "none";
  resumeAfterPool(pool);
}

/**
 * Label the whole pool: local linear model when the video has one, service
 * batches otherwise. Returns a plain object dmid -> {label, score}.
 * @param {object} pool
 * @returns {Promise<object>}
 */
async function labelPool(pool) {
  const prefs = poolPrefs();
  const labels = {};
  let payload = null;
  try {
    payload = await getJson("/api/v1/model?bvid=" + encodeURIComponent(pool.bvid), 4000);
  } catch (error) {
    log("pool model fetch failed", error);
  }
  const source = payload && payload.ok === true ? payload.source : null;
  if (source === "video" && payload.model) {
    try {
      const scorer = new LinearScorer(payload.model);
      for (const item of pool.items) {
        if (!item.text) continue;
        const prediction = scorer.predict(item.text);
        labels[item.dmid] = {
          label: prediction && prediction.tag ? prediction.tag : "other",
          score:
            prediction && Number.isFinite(Number(prediction.score))
              ? Number(prediction.score)
              : 0.5,
        };
      }
      log("pool labelled locally", pool.items.length);
    } catch (error) {
      log("pool local scoring failed", error);
    }
    pool.localLabels = Object.keys(labels).length;
  }
  const missing = pool.items.filter((item) => !labels[item.dmid]);
  if (!missing.length) return labels;
  const chunks = [];
  for (let i = 0; i < missing.length; i += POOL_LABEL_CHUNK) {
    chunks.push(missing.slice(i, i + POOL_LABEL_CHUNK));
  }
  let cursor = 0;
  const labelChunk = async () => {
    while (cursor < chunks.length) {
      const chunk = chunks[cursor];
      cursor += 1;
      const dmids = chunk.map((item) => item.dmid);
      const texts = {};
      for (const item of chunk) {
        if (item.text) texts[item.dmid] = item.text;
      }
      try {
        const response = await postJson(
          "/api/v1/decisions",
          { bvid: pool.bvid, dmids, texts, prefs, ephemeral: true, labels: true },
          8000
        );
        if (response && response.ok === true) {
          const knownLabels =
            response.labels && typeof response.labels === "object" ? response.labels : {};
          const knownDecisions =
            response.decisions && typeof response.decisions === "object" ? response.decisions : {};
          for (const dmid of dmids) {
            const known = knownLabels[dmid] || knownDecisions[dmid];
            if (!known) continue;
            labels[dmid] = {
              label: typeof known.label === "string" ? known.label : "other",
              score: Number.isFinite(Number(known.score)) ? Number(known.score) : 0.5,
            };
          }
        }
      } catch (error) {
        log("pool label chunk failed", error);
      }
      pool.progress.items += chunk.length;
      updatePoolOverlay(pool);
    }
  };
  const workers = [];
  for (let i = 0; i < Math.min(POOL_LABEL_CONCURRENCY, chunks.length); i += 1) {
    workers.push(labelChunk());
  }
  await Promise.all(workers);
  pool.progress.items = pool.items.length;
  return labels;
}

/**
 * Mirror the pool's fold list into the panel so the folded comments are visible
 * from the first second, before any segment has been served.
 */
function seedPoolPanel(pool) {
  if (!pool || !pool.actions || !state.entries) return;
  const limit = typeof MAX_PANEL_ROWS === "number" ? MAX_PANEL_ROWS : 200;
  if (!pool.seeded) pool.seeded = new Set();
  if (!pool.poolCounted) pool.poolCounted = new Set();
  let added = 0;
  for (const fold of pool.folds) {
    if (added >= limit) break;
    if (pool.seeded.has(fold.dmid) || state.entries.has(fold.dmid)) continue;
    pool.seeded.add(fold.dmid);
    state.entries.set(fold.dmid, {
      dmid: fold.dmid,
      text: fold.text,
      time_ms: fold.time_ms,
      label: fold.label,
      label_name: labelName(fold.label),
      score: fold.score,
      reason: fold.reason,
      action: fold.action,
      evidence: fold.source === "user" ? "user correction" : "",
    });
    added += 1;
  }
  let counted = 0;
  for (const [dmid, action] of pool.actions) {
    const key = "pool|" + dmid;
    if (pool.poolCounted.has(key) || state.labelCounted.has(key)) continue;
    pool.poolCounted.add(key);
    state.labelCounted.add(key);
    state.labelCounts.set(action.label, (state.labelCounts.get(action.label) || 0) + 1);
    counted += 1;
  }
  pool.seedStats = { added, counted, entries: state.entries.size };
  log("pool seed", JSON.stringify(pool.seedStats));
  renderEntries();
  syncTypeCounts();
  updateStats();
  updateBadge();
}

/** Recompute the pool action table from labels plus the given preferences. */
/** Normalise comment text so list.so and seg.so copies of a comment can meet. */
function poolTextKey(text) {
  return String(text || "")
    .replace(/\s+/g, " ")
    .trim();
}

/**
 * list.so and seg.so do not always return the same dmid set for a video, so
 * the pool table is also indexed by normalised text. A comment is served from
 * the pool when either key matches.
 */
function rebuildPoolTextIndex(pool) {
  const index = new Map();
  if (!pool.items || !pool.actions) return index;
  for (const item of pool.items) {
    const key = poolTextKey(item.text);
    if (!key) continue;
    const entry = pool.actions.get(item.dmid);
    if (!entry) continue;
    const previous = index.get(key);
    if (!previous || entry.action === "hide") index.set(key, entry);
  }
  return index;
}

function rebuildPoolActions(pool, prefs) {
  const settings = prefs || poolPrefs();
  const overrides = {};
  for (const [dmid, label] of state.overrides.entries()) overrides[dmid] = label;
  const table = buildActionTable({
    items: pool.items,
    labels: pool.labels || {},
    prefs: settings,
    overrides,
    forceShow: state.showOnce,
    density: settings.density,
  });
  pool.actions = table.actions;
  pool.counts = table.counts;
  pool.folds = table.folds;
  pool.byText = rebuildPoolTextIndex(pool);
  seedPoolPanel(pool);
  return table;
}

/** Decode a list.so payload: plain XML, raw deflate, zlib, or gzip. */
async function decodePoolXml(buffer) {
  if (!buffer) return "";
  const bytes = new Uint8Array(buffer);
  if (bytes.length && bytes[0] === 0x3c) {
    return new TextDecoder("utf-8").decode(bytes);
  }
  for (const format of ["deflate-raw", "deflate", "gzip"]) {
    try {
      const stream = new Blob([bytes]).stream().pipeThrough(new DecompressionStream(format));
      return await new Response(stream).text();
    } catch (error) {
      /* try the next format */
    }
  }
  return new TextDecoder("utf-8").decode(bytes);
}

/** Parse list.so XML ("<d p=\"...\">text</d>") into pool items. */
function parsePoolXml(xml) {
  const items = [];
  const pattern = /<d p="([^"]*)"[^>]*>([\s\S]*?)<\/d>/g;
  let match = pattern.exec(xml);
  while (match) {
    const fields = match[1].split(",");
    const seconds = Number(fields[0]);
    const dmid = fields.length > 7 ? fields[7] : "";
    const text = match[2]
      .replace(/&lt;/g, "<")
      .replace(/&gt;/g, ">")
      .replace(/&quot;/g, '"')
      .replace(/&#39;/g, "'")
      .replace(/&amp;/g, "&")
      .trim();
    if (dmid && text) {
      items.push({
        dmid,
        text,
        time_ms: Number.isFinite(seconds) ? Math.round(seconds * 1000) : 0,
        segment_index: 0,
      });
    }
    match = pattern.exec(xml);
  }
  return items;
}

/**
 * The whole-video pool in one request: list.so returns up to a few thousand
 * comments with absolute timestamps, which is far cheaper than crawling
 * segments (seg.so answers 304 for every segment after the first without the
 * player's signed request).
 */
async function fetchPoolList(segment, pool) {
  const url = "https://api.bilibili.com/x/v1/dm/list.so?oid=" + encodeURIComponent(segment.cid);
  const buffer = await fetchSegmentBytes(url);
  const xml = await decodePoolXml(buffer);
  if (!xml || xml.indexOf("<d ") === -1) return [];
  pool.progress.done = 1;
  pool.progress.total = 1;
  updatePoolOverlay(pool);
  return parsePoolXml(xml);
}

/** Fallback: crawl protobuf segments until two consecutive ones come back empty. */
async function fetchPoolSegments(segment, pool) {
  const limit = POOL_MAX_SEGMENTS;
  pool.progress.total = limit;
  const segments = [];
  let cursor = 0;
  let stop = false;
  let emptyStreak = 0;
  const fetchWorker = async () => {
    while (!stop && cursor < limit) {
      const index = cursor + 1;
      cursor += 1;
      let parsed = 0;
      try {
        const bytes = await fetchSegmentBytes(poolSegmentUrl(segment, index));
        if (bytes && bytes.byteLength) {
          parsed = parseSegment(bytes).length;
          if (parsed) segments.push({ index, bytes });
        }
      } catch (error) {
        log("pool fetch failed", index, error);
      }
      if (parsed) {
        emptyStreak = 0;
      } else {
        emptyStreak += 1;
        if (emptyStreak >= POOL_EMPTY_STREAK) stop = true;
      }
      if (!stop) pool.progress.done += 1;
      updatePoolOverlay(pool);
    }
  };
  const workers = [];
  for (let i = 0; i < Math.min(POOL_FETCH_CONCURRENCY, limit); i += 1) {
    workers.push(fetchWorker());
  }
  await Promise.all(workers);
  return mergePool(segments).items;
}

/** Fetch every segment, label the pool, and build the action table. */
async function loadPool(segment, pool) {
  const started = Date.now();
  showPoolOverlay(pool);
  try {
    let items = [];
    try {
      items = await fetchPoolList(segment, pool);
      log("pool list items", items.length);
    } catch (error) {
      log("pool list failed", error);
    }
    if (!items.length) {
      items = await fetchPoolSegments(segment, pool);
      log("pool segment items", items.length);
    }
    pool.items = items.slice(0, POOL_MAX_ITEMS);
    pool.progress.items = pool.items.length;
    pool.progress.phase = "label";
    updatePoolOverlay(pool);
    pool.labels = await labelPool(pool);
    rebuildPoolActions(pool, poolPrefs());
    pool.loadMs = Date.now() - pool.startedAt;
    pool.ready = true;
    log("pool ready", pool.items.length, "items in", Date.now() - started, "ms");
    renderEntries();
    updateBadge();
  } catch (error) {
    pool.error = describeError(error);
    log("pool load failed", error);
  } finally {
    hidePoolOverlay(pool);
  }
  return pool;
}

/** Start (or join) the pool load and wait at most POOL_WAIT_MS for it. */
async function poolFor(segment) {
  const pool = ensurePool(segment);
  if (pool.ready) return pool;
  if (!pool.loading) pool.loading = loadPool(segment, pool);
  const waitMs = state.study && !state.study.recordOnly ? POOL_STUDY_WAIT_MS : POOL_WAIT_MS;
  await Promise.race([pool.loading.catch(() => {}), sleep(waitMs)]);
  return pool;
}

/**
 * Serve one segment from the finished pool table (no network round trip).
 * @returns {Uint8Array|null} rewritten bytes, or null to leave them untouched
 */
async function serveSegmentFromPool(bytes, segment, pool, items, extraDrop) {
  const started = Date.now();
  try {
    const actions = new Map();
    const entries = [];
    const unmatched = [];
    for (const item of items) {
      let entry = pool.actions.get(item.dmid);
      if (!entry && pool.byText) entry = pool.byText.get(poolTextKey(item.text));
      if (!entry) {
        unmatched.push(item);
        continue;
      }
      const mapped = {
        action: entry.action,
        label: entry.label,
        score: entry.score,
        reason: entry.reason,
        evidence: entry.source === "user" ? "user correction" : "",
      };
      actions.set(item.dmid, mapped);
      if (mapped.action !== "show") {
        entries.push({
          dmid: item.dmid,
          text: item.text,
          time_ms: item.time_ms,
          label: mapped.label,
          label_name: labelName(mapped.label),
          score: mapped.score,
          reason: mapped.reason,
          action: mapped.action,
          evidence: mapped.evidence,
        });
      }
    }
    // list.so and seg.so do not expose exactly the same comment set, so any
    // comment missing from the pool table still goes through the normal
    // per-segment decision path; the pool remains an optimisation, never a gap.
    if (unmatched.length) {
      try {
        const cache = await decisionsFor(segment, unmatched);
        if (cache) {
          const extra = planSegment(unmatched, cache.decisions, poolPrefs(), {
            showOnce: state.showOnce,
            overrides: state.overrides,
            labels: cache.labels,
          });
          for (const pair of extra.actions) actions.set(pair[0], pair[1]);
          for (const entry of extra.entries) entries.push(entry);
        }
      } catch (error) {
        log("pool fallback failed", error);
      }
    }
    const plan = { actions, entries };
    const drop = dropSet(actions);
    if (extraDrop && extraDrop.size) {
      for (const dmid of extraDrop) drop.add(dmid);
    }
    pool.lastServe = {
      at: Date.now(),
      ready: pool.ready === true,
      tableSize: pool.actions ? pool.actions.size : 0,
      items: items.length,
      unmatched: unmatched.length,
      matched: actions.size,
      drop: drop.size,
    };
    if (state.study) {
      const counts = countForSegment(
        actions,
        items.map((item) => item.dmid)
      );
      counts.thinned = extraDrop ? extraDrop.size : 0;
      if (extraDrop && extraDrop.size) {
        let thinnedShown = 0;
        for (const dmid of extraDrop) {
          const decision = actions.get(String(dmid));
          if (decision && decision.action === "show") thinnedShown += 1;
        }
        counts.shown = Math.max(0, counts.shown - thinnedShown);
      }
      studyRecordSegment(segment, counts, Date.now() - started, true, drop.size > 0);
    }
    recordPlan(segment, plan);
    if (!drop.size) return null;
    return filterSegment(bytes, drop, new Map());
  } catch (error) {
    log("pool segment failed", error);
    return null;
  }
}

/* ---- lib/study.js ---- */
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
const STUDY_STORAGE_KEY = "dmf.study";

/** URL parameter selecting the study. */
const STUDY_ID_PARAM = "dmf_study";

/** URL parameter selecting the experimental condition. */
const CONDITION_PARAM = "dmf_cond";

/** URL parameter selecting the density cell: high (original) or low (thinned). */
const DENSITY_PARAM = "dmf_density";

/** Density cells understood by the study harness. */
const STUDY_DENSITIES = Object.freeze(["high", "low"]);

/** Visible comments per minute in the low-density cell. */
const LOW_DENSITY_PER_MIN = 30;

/** Conditions understood by the study harness. */
const STUDY_CONDITIONS = Object.freeze(["all_on", "keyword", "prototype"]);
// The protocol and the questionnaire use A/B/C letters; exports carry both the
// canonical id and the letter so the analysis script and humans agree.
const CONDITION_LETTERS = Object.freeze({ all_on: "A", keyword: "B", prototype: "C" });

/**
 * @param {string} condition canonical condition id
 * @returns {string} A/B/C letter ("" when unknown)
 */
function conditionLetter(condition) {
  const key = typeof condition === "string" ? condition.trim().toLowerCase() : "";
  return CONDITION_LETTERS[key] || "";
}

/** Upper bound of retained events; the oldest event is dropped first. */
const MAX_STUDY_EVENTS = 5000;

/** Chinese labels shown to the examiner. */
const CONDITION_LABELS = Object.freeze({
  all_on: "全部开启（仅记录）",
  keyword: "仅关键词（仅记录）",
  prototype: "原型策略",
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
function cleanStudyId(value) {
  if (typeof value !== "string") return "";
  return value.trim().slice(0, 100);
}

/**
 * Normalize a density cell name; empty means "not specified".
 * @param {unknown} value
 * @returns {"high"|"low"|""}
 */
function normalizeDensity(value) {
  if (typeof value !== "string") return "";
  const text = value.trim().toLowerCase();
  return STUDY_DENSITIES.includes(text) ? text : "";
}

/**
 * Normalize a condition name, case-insensitively; returns "" when unknown.
 * @param {unknown} value
 * @returns {"all_on"|"keyword"|"prototype"|""}
 */
function normalizeCondition(value) {
  if (typeof value !== "string") return "";
  const text = value.trim().toLowerCase();
  if (STUDY_CONDITIONS.includes(text)) return text;
  // The lab protocol writes conditions as A / B / C in the URL.
  if (text === "a" || text === "1") return "all_on";
  if (text === "b" || text === "2") return "keyword";
  if (text === "c" || text === "3") return "prototype";
  return "";
}

/**
 * The declarative description of one condition.
 * @param {unknown} condition
 * @returns {{condition: string, label: string, record_only: boolean,
 *            enabled: boolean, per_type: object, density: object}|null}
 */
function conditionSpec(condition) {
  const key = normalizeCondition(condition);
  if (!key) return null;
  const prototype = key === "prototype";
  return {
    condition: key,
    label: CONDITION_LABELS[key],
    record_only: !prototype,
    enabled: prototype,
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
function prefsForCondition(condition, basePrefs) {
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
 * in every condition.
 *
 * @param {unknown} condition
 * @param {object|null|undefined} basePrefs
 * @returns {object|null}
 */
function analysisPrefsForCondition(condition, basePrefs) {
  if (!normalizeCondition(condition)) return null;
  return prefsForCondition("prototype", basePrefs);
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
function parseStudyConfig(search, stored) {
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
function studyDownloadName(studyId) {
  const safe = cleanStudyId(studyId).replace(/[^0-9A-Za-z._-]+/g, "_") || "session";
  return "dmf-study-" + safe + ".json";
}

/**
 * Count final actions (including "show") by action and by label.
 * @param {Map<string, object>|Record<string, object>|null|undefined} actions
 * @returns {{shown: number, hidden: number, collapsed: number, by_label: object}}
 */
function countActions(actions) {
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
function diffPrefs(before, after) {
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
function createStudySession(options) {
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

/* ---- src/main.js ---- */
/**
 * Bilibili danmaku filter userscript entry point.
 *
 * Runs at document-start and intercepts the player's requests to
 * /x/v2/dm/web/seg.so or /x/v2/dm/wbi/web/seg.so (XHR and fetch), asks the local service at
 * http://127.0.0.1:8765 for per-danmaku decisions, rewrites the raw protobuf
 * segment with dmproto.filterSegment, and hands the bytes back to the player.
 * Every failure path is fail-open: the original request proceeds untouched.
 *
 * This file is concatenated after lib/dmproto.js and lib/policy.js into one
 * IIFE by extension/build.mjs, so it relies on those globals instead of imports.
 */

/**
 * Player segment endpoints. Bilibili moved the plain endpoint to a signed
 * wbi path in 2023; both are recognised so the hook keeps working on old
 * and new player builds.
 */
const SEG_PATHS = ["/x/v2/dm/web/seg.so", "/x/v2/dm/wbi/web/seg.so"];
const DEFAULT_SERVICE = "http://127.0.0.1:8765";
const PREFS_KEY = "dmf.prefs.v1";
const SERVICE_KEY = "dmf.service.v1";
const DECISION_TIMEOUT_MS = 1500;
const SEGMENT_TIMEOUT_MS = 5000;
const FAILURE_TTL_MS = 10000;
const MAX_PANEL_ROWS = 200;
const BADGE_ID = "dmf-badge";
const PANEL_ID = "dmf-panel";

/** Page realm, so hooks affect the player rather than a userscript sandbox. */
const PAGE =
  typeof unsafeWindow !== "undefined" && unsafeWindow ? unsafeWindow : window;

/** Mutable runtime state shared by the hooks and the UI. */
const state = {
  prefs: null,
  // Study-mode session (controlled experiment); null in normal browsing.
  study: null,
  service: DEFAULT_SERVICE,
  connected: null,
  hidden: 0,
  collapsed: 0,
  entries: new Map(),
  counted: new Set(),
  decisionsCache: new Map(),
  failures: new Map(),
  showOnce: new Set(),
  overrides: new Map(),
  shown: 0,
  labelCounts: new Map(),
  labelCounted: new Set(),
  logEntries: [],
  tab: "strategy",
  xhrInstalled: false,
  fetchInstalled: false,
  uiReady: false,
  lastBvid: "",
  // Runtime diagnostics shown in the advanced section: these make a silent
  // fail-open visible instead of leaving the page looking unfiltered.
  diag: { intercepts: 0, ok: 0, fail: 0, online: 0, found: null, lastError: "" },
  debug: false,
  debugLogs: [],
  onlineNotified: false,
  // Rendered-danmaku bookkeeping for the live DOM safety net.
  dom: null,
};

const ui = {
  badge: null,
  badgeText: null,
  badgeStatus: null,
  toggle: null,
  panel: null,
  statusDot: null,
  statusText: null,
  stats: null,
  tabs: new Map(),
  views: new Map(),
  typeRows: new Map(),
  totalCount: null,
  foldedList: null,
  logList: null,
  minConfidence: null,
  minConfValue: null,
  densityEnabled: null,
  densityWindow: null,
  densityMax: null,
  densityExceed: null,
  serviceInput: null,
  diag: null,
  popover: null,
  popoverDocHandler: null,
  toast: null,
  toastTimer: 0,
};

/**
 * Debug logging, enabled with localStorage["dmf.debug"] === "1".
 * @param {...unknown} args
 */
function log(...args) {
  try {
    if (!(window.localStorage && localStorage.getItem("dmf.debug") === "1")) return;
    state.debug = true;
    let line = "";
    try {
      line = args.map((value) => (typeof value === "string" ? value : JSON.stringify(value))).join(" ");
    } catch (error) {
      line = String(args);
    }
    state.debugLogs.push(Date.now() + " " + line);
    if (state.debugLogs.length > 200) state.debugLogs.shift();
    console.debug("[dmf]", ...args);
  } catch (error) {
    /* logging must never throw */
  }
}

/**
 * @param {string} tag
 * @param {string} [className]
 * @returns {HTMLElement}
 */
function el(tag, className) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  return node;
}

/**
 * Read and validate prefs from localStorage.
 * @returns {object}
 */
function loadPrefs() {
  try {
    const raw = localStorage.getItem(PREFS_KEY);
    if (!raw) return normalizePrefs(null);
    return normalizePrefs(JSON.parse(raw));
  } catch (error) {
    log("prefs load failed", error);
    return normalizePrefs(null);
  }
}

/** Persist prefs to localStorage (best effort). */
function savePrefs() {
  try {
    localStorage.setItem(PREFS_KEY, JSON.stringify(state.prefs));
  } catch (error) {
    log("prefs save failed", error);
  }
}

/**
 * Read the service base URL from localStorage.
 * @returns {string}
 */
function loadService() {
  try {
    const stored = localStorage.getItem(SERVICE_KEY);
    if (stored && /^https?:\/\//.test(stored)) return stored.replace(/\/+$/, "");
  } catch (error) {
    /* storage may be unavailable */
  }
  return DEFAULT_SERVICE;
}

/** Save the service base URL (best effort). */
function saveService() {
  try {
    localStorage.setItem(SERVICE_KEY, state.service);
  } catch (error) {
    /* ignore */
  }
}

/**
 * Build a full URL for a local service endpoint.
 * @param {string} path
 * @returns {string}
 */
function serviceEndpoint(path) {
  return state.service.replace(/\/+$/, "") + path;
}

/**
 * @returns {boolean} whether the page has GM_xmlhttpRequest
 */
function hasGm() {
  return typeof GM_xmlhttpRequest === "function";
}

/**
 * Promise wrapper around GM_xmlhttpRequest.
 * @param {object} options
 * @returns {Promise<any>}
 */
function gmRequest(options) {
  return new Promise((resolve, reject) => {
    if (!hasGm()) {
      reject(new Error("GM_xmlhttpRequest is unavailable"));
      return;
    }
    try {
      GM_xmlhttpRequest({
        timeout: options.timeout || DECISION_TIMEOUT_MS,
        ...options,
        onload: (response) => resolve(response),
        onerror: () => reject(new Error("GM request failed")),
        ontimeout: () => reject(new Error("GM request timed out")),
        onabort: () => reject(new Error("GM request aborted")),
      });
    } catch (error) {
      reject(error);
    }
  });
}

/**
 * Extract response text from a GM response.
 * @param {any} response
 * @returns {string}
 */
function gmResponseText(response) {
  if (!response) return "";
  if (typeof response.response === "string") return response.response;
  if (response.response instanceof ArrayBuffer) {
    try {
      return new TextDecoder("utf-8", { fatal: false }).decode(new Uint8Array(response.response));
    } catch (error) {
      return "";
    }
  }
  return "";
}

/**
 * GET JSON from the local service.
 * @param {string} path
 * @param {number} [timeout]
 * @returns {Promise<object>}
 */
async function getJson(path, timeout) {
  const url = serviceEndpoint(path);
  if (hasGm()) {
    const response = await gmRequest({ method: "GET", url, timeout: timeout || DECISION_TIMEOUT_MS });
    if (response.status < 200 || response.status >= 300) throw new Error("HTTP " + response.status);
    const text = gmResponseText(response);
    if (!text) throw new Error("empty response");
    return JSON.parse(text);
  }
  const limitedFetch = await pageFetchWithTimeout(url, { method: "GET" }, timeout || DECISION_TIMEOUT_MS);
  if (!limitedFetch.ok) throw new Error("HTTP " + limitedFetch.status);
  return limitedFetch.json();
}

/**
 * POST JSON to the local service.
 * @param {string} path
 * @param {object} payload
 * @param {number} [timeout]
 * @returns {Promise<object>}
 */
async function postJson(path, payload, timeout) {
  const url = serviceEndpoint(path);
  const body = JSON.stringify(payload || {});
  if (hasGm()) {
    const response = await gmRequest({
      method: "POST",
      url,
      data: body,
      headers: { "Content-Type": "application/json" },
      timeout: timeout || DECISION_TIMEOUT_MS,
    });
    if (response.status < 200 || response.status >= 300) throw new Error("HTTP " + response.status);
    const text = gmResponseText(response);
    if (!text) throw new Error("empty response");
    return JSON.parse(text);
  }
  const limitedFetch = await pageFetchWithTimeout(
    url,
    { method: "POST", headers: { "Content-Type": "application/json" }, body },
    timeout || DECISION_TIMEOUT_MS
  );
  if (!limitedFetch.ok) throw new Error("HTTP " + limitedFetch.status);
  return limitedFetch.json();
}

/**
 * fetch with an explicit timeout, used only when GM_xmlhttpRequest is absent.
 * @param {string} url
 * @param {object} init
 * @param {number} timeout
 * @returns {Promise<Response>}
 */
async function pageFetchWithTimeout(url, init, timeout) {
  const controller = typeof AbortController === "function" ? new AbortController() : null;
  const timer = controller ? setTimeout(() => controller.abort(), timeout) : null;
  try {
    const options = { ...init };
    if (controller) options.signal = controller.signal;
    // Use the fetch captured before our hook was installed: calling the hooked
    // fetch would run a seg.so response through the filter twice and hand the
    // thinned bytes back to the caller as if they were the original stream.
    const doFetch = typeof state.nativeFetch === "function" ? state.nativeFetch : fetch;
    return await doFetch(url, options);
  } finally {
    if (timer) clearTimeout(timer);
  }
}

/**
 * Fetch the raw seg.so bytes through GM_xmlhttpRequest (cross-origin safe).
 * @param {string} url
 * @returns {Promise<ArrayBuffer>}
 */
async function fetchSegmentBytes(url) {
  if (!hasGm()) {
    // Tampermonkey normally provides GM_xmlhttpRequest; this fallback keeps the
    // hook usable in plain page context (and makes the failure explicit).
    const response = await pageFetchWithTimeout(
      url,
      { method: "GET", credentials: "include", headers: { Accept: "*/*" } },
      SEGMENT_TIMEOUT_MS
    );
    if (!response.ok) throw new Error("seg.so HTTP " + response.status);
    const buffer = await response.arrayBuffer();
    if (!buffer || buffer.byteLength === 0) throw new Error("seg.so returned an empty body");
    return buffer;
  }
  const response = await gmRequest({
    method: "GET",
    url,
    responseType: "arraybuffer",
    timeout: SEGMENT_TIMEOUT_MS,
    anonymous: false,
    headers: { Referer: location.origin + "/" },
  });
  if (response.status < 200 || response.status >= 300) {
    throw new Error("seg.so HTTP " + response.status);
  }
  if (!response.response || response.response.byteLength === 0) {
    throw new Error("seg.so returned an empty body");
  }
  return response.response;
}

/**
 * Parse the current bvid from the page URL (SSR state and a local fallback are
 * also accepted). Returns "" when it cannot be resolved, which makes the caller
 * fail open.
 * @returns {string}
 */
function currentBvid() {
  try {
    const match = /\/video\/(BV[0-9A-Za-z]{10})/.exec(location.pathname);
    if (match) return match[1];
    const hrefMatch = /(BV[0-9A-Za-z]{10})/.exec(location.href);
    if (hrefMatch) return hrefMatch[1];
    if (PAGE.__INITIAL_STATE__ && typeof PAGE.__INITIAL_STATE__.bvid === "string") {
      return PAGE.__INITIAL_STATE__.bvid;
    }
    const stored = localStorage.getItem("dmf.bvid");
    if (stored && /^BV[0-9A-Za-z]{10}$/.test(stored)) return stored;
  } catch (error) {
    log("bvid parse failed", error);
  }
  return "";
}

/**
 * Recognize and parse a seg.so URL.
 * @param {string} rawUrl
 * @returns {{href: string, cid: string, aid: string, index: number}|null}
 */
function parseSegUrl(rawUrl) {
  if (!rawUrl) return null;
  try {
    const parsed = new URL(rawUrl, location.href);
    if (!SEG_PATHS.some((suffix) => parsed.pathname.indexOf(suffix) !== -1)) return null;
    const cid = parsed.searchParams.get("oid") || "";
    const aid = parsed.searchParams.get("pid") || "";
    const rawIndex = parsed.searchParams.get("segment_index");
    const index = rawIndex === null ? 1 : Number(rawIndex);
    return { href: parsed.href, cid, aid, index: Number.isFinite(index) ? index : 1 };
  } catch (error) {
    return null;
  }
}

/**
 * Extract a URL from a fetch() input argument.
 * @param {RequestInfo|URL|string} input
 * @returns {string}
 */
function requestUrl(input) {
  if (typeof input === "string") return input;
  if (typeof URL === "function" && input instanceof URL) return input.href;
  if (input && typeof input.url === "string") return input.url;
  return "";
}

/**
 * @param {object} segment
 * @returns {string}
 */
function segmentKey(segment) {
  return currentBvid() + "|" + segment.cid + "|" + segment.index;
}

/**
 * Ask the local service for decisions on the given items, caching per
 * (bvid, cid, segment_index) so repeated player requests avoid extra POSTs.
 *
 * The comment texts are sent along with the ids: for videos without an
 * offline tag cache the service classifies them on the fly with the rule
 * baseline instead of answering "miss" and leaving the page unfiltered.
 * @param {object} segment
 * @param {Array<{dmid: string, text: string}>} items
 * @returns {Promise<{decisions: object, miss: string[], found: boolean}|null>}
 */
async function decisionsFor(segment, items) {
  const dmids = items.map((item) => item.dmid);
  const texts = {};
  for (const item of items) {
    if (item && item.dmid && typeof item.text === "string") texts[item.dmid] = item.text;
  }
  // Record-only study conditions keep prefs.enabled=false for the player but
  // still need server decisions to count what the prototype policy would do.
  const requestPrefs = state.study ? state.study.analysisPrefs : state.prefs;
  if (!requestPrefs || !requestPrefs.enabled) return null;
  const bvid = currentBvid();
  if (!bvid || !segment.cid) return null;
  const key = segmentKey(segment);
  const cached = state.decisionsCache.get(key);
  if (cached) return cached;
  const failedAt = state.failures.get(key);
  if (failedAt && Date.now() - failedAt < FAILURE_TTL_MS) return null;
  const requestStarted = Date.now();
  let payload;
  try {
    payload = await postJson("/api/v1/decisions", {
      bvid,
      dmids,
      texts,
      prefs: requestPrefs,
      labels: true,
    });
  } catch (error) {
    log("decisions request failed", error);
    state.diag.fail += 1;
    state.diag.lastError = describeError(error);
    state.failures.set(key, Date.now());
    setConnected(false);
    return null;
  }
  if (!payload || payload.ok !== true) {
    log("decisions response invalid", payload);
    state.diag.fail += 1;
    state.diag.lastError = "决策响应无效";
    state.failures.set(key, Date.now());
    setConnected(false);
    return null;
  }
  state.diag.ok += 1;
  setConnected(true);
  const decisions = payload.decisions && typeof payload.decisions === "object" ? payload.decisions : {};
  const miss = Array.isArray(payload.miss) ? payload.miss.map(String) : [];
  const found = payload.found === true;
  state.diag.found = found;
  state.diag.online += Number(payload.online || 0);
  if (!found && !state.onlineNotified) {
    state.onlineNotified = true;
    toast("该视频未离线打标 · 已用规则实时过滤并缓存");
  }
  const labels = payload.labels && typeof payload.labels === "object" ? payload.labels : {};
  const result = { decisions, miss, found, labels, latency: Date.now() - requestStarted };
  state.decisionsCache.set(key, result);
  if (miss.length) {
    postJson("/api/v1/miss", { bvid, dmids: miss }).catch((error) => log("miss post failed", error));
  }
  return result;
}

/**
 * Parse a segment, fetch decisions, and rewrite the protobuf bytes.
 * Returns null whenever the request must be left untouched (fail open).
 * @param {ArrayBuffer|Uint8Array} bytes
 * @param {object} segment
 * @returns {Promise<{data: Uint8Array, hidden: number, collapsed: number}|null>}
 */
async function applySegment(bytes, segment) {
  try {
    const recordOnly = Boolean(state.study && state.study.recordOnly);
    if (!recordOnly && (!state.prefs || !state.prefs.enabled)) return null;
    const items = parseSegment(bytes);
    if (!items.length) return null;
    state.diag.intercepts += 1;
    const started = Date.now();
    const prefs = recordOnly ? state.study.analysisPrefs : state.prefs;
    // Density is an experimental factor in the study: the low cell keeps only a
    // fixed quota per time window (stable hash) so every condition sees the same
    // thinning, otherwise H1c would be untestable on real, dense streams.
    const thinDrop =
      state.study && state.study.density === "low"
        ? thinSegmentDrop(items, { target_per_min: LOW_DENSITY_PER_MIN })
        : new Set();
    if (!recordOnly) {
      const pool = await poolFor(segment);
      if (pool && pool.ready) {
        return await serveSegmentFromPool(bytes, segment, pool, items, thinDrop);
      }
    }
    const cache = await decisionsFor(segment, items);
    log("segment", segment.index, "items", items.length,
      "found", cache ? cache.found : null,
      "decisions", cache ? Object.keys(cache.decisions).length : 0,
      "miss", cache ? cache.miss.length : 0);
    if (!cache) {
      if (recordOnly) {
        studyRecordSegment(segment, {
          shown: items.length - thinDrop.size,
          hidden: 0,
          collapsed: 0,
          thinned: thinDrop.size,
          by_label: {},
        }, (cache && cache.latency) || Date.now() - started, thinDrop.size > 0, thinDrop.size > 0);
        if (thinDrop.size) return filterSegment(bytes, thinDrop, new Map());
      }
      return null;
    }
    const plan = planSegment(items, cache.decisions, prefs, {
      showOnce: recordOnly ? undefined : state.showOnce,
      overrides: recordOnly ? undefined : state.overrides,
      labels: cache.labels,
    });
    const counts = countActions(plan.actions);
    if (recordOnly) {
      // Record-only: the viewer sees every comment the stream delivered (minus
      // the density manipulation), so the applied counts are the whole segment.
      // What the prototype policy would have done goes to `would`; without this
      // split A/B look like they filtered when nothing was touched, and RQ2's
      // behaviour numbers would compare counterfactuals with reality.
      const applied = {
        shown: Math.max(0, items.length - thinDrop.size),
        hidden: 0,
        collapsed: 0,
        thinned: thinDrop.size,
        by_label: {},
        would: {
          shown: Math.max(0, counts.shown - thinnedShowCount(plan.actions, thinDrop)),
          hidden: counts.hidden,
          collapsed: counts.collapsed,
          by_label: counts.by_label,
        },
      };
      studyRecordSegment(segment, applied,
        (cache && cache.latency) || Date.now() - started, true, thinDrop.size > 0);
      if (!thinDrop.size) return null;
      return filterSegment(bytes, thinDrop, new Map());
    }
    if (state.study) {
      const visible = Math.max(0, counts.shown - thinnedShowCount(plan.actions, thinDrop));
      studyRecordSegment(segment, { ...counts, shown: visible, thinned: thinDrop.size }, Date.now() - started, true, true);
    }
    const drop = new Set(plan.drop);
    for (const dmid of thinDrop) drop.add(dmid);
    log("plan drop", drop.size, "collapse", plan.collapse.size, "thin", thinDrop.size);
    if (!drop.size && !plan.collapse.size) return null;
    const filtered = filterSegment(bytes, drop, plan.collapse);
    recordPlan(segment, plan);
    return filtered;
  } catch (error) {
    log("segment processing failed", error);
    return null;
  }
}

/**
 * Record filtered entries for the badge, panel, and per-segment counters.
 * @param {object} segment
 * @param {{entries: object[]}} plan
 */
function recordPlan(segment, plan) {
  const key = segmentKey(segment);
  for (const [dmid, decision] of plan.actions || []) {
    const labelKey = key + "|" + dmid;
    if (state.labelCounted.has(labelKey)) continue;
    state.labelCounted.add(labelKey);
    const label = decision && decision.label ? decision.label : "other";
    state.labelCounts.set(label, (state.labelCounts.get(label) || 0) + 1);
    if (decision && decision.action === "show") state.shown += 1;
  }
  for (const entry of plan.entries) {
    const countKey = key + "|" + entry.dmid;
    let recorded = state.entries.get(entry.dmid);
    if (!recorded) {
      recorded = {
        dmid: entry.dmid,
        text: entry.text,
        time_ms: entry.time_ms,
        label: entry.label,
        label_name: entry.label_name,
        score: entry.score,
        reason: entry.reason,
        action: entry.action,
        evidence: entry.evidence,
        density: entry.reason === "density",
        countKey,
        counted: false,
        countedAction: "",
      };
      state.entries.set(entry.dmid, recorded);
      trimEntries();
    } else {
      recorded.action = entry.action;
      recorded.reason = entry.reason;
      recorded.evidence = entry.evidence;
      if (!recorded.counted) recorded.countKey = countKey;
    }
    if (state.counted.has(countKey)) {
      recorded.counted = true;
      recorded.countedAction = recorded.action;
      continue;
    }
    state.counted.add(countKey);
    recorded.counted = true;
    recorded.countedAction = entry.action;
    if (entry.action === "hide") state.hidden += 1;
    else if (entry.action === "collapse") state.collapsed += 1;
  }
  updateBadge();
  renderEntries();
}

/** Keep the panel list bounded, dropping the oldest entries first. */
function trimEntries() {
  while (state.entries.size > MAX_PANEL_ROWS) {
    const oldest = state.entries.keys().next();
    if (oldest.done) break;
    state.entries.delete(oldest.value);
  }
}

/**
 * Undo the session counter contribution of one panel entry.
 * @param {object} entry
 */
function undoEntry(entry) {
  if (!entry || !entry.counted) return;
  entry.counted = false;
  if (entry.countedAction === "hide") state.hidden = Math.max(0, state.hidden - 1);
  else if (entry.countedAction === "collapse") state.collapsed = Math.max(0, state.collapsed - 1);
  state.counted.delete(entry.countKey);
  entry.countedAction = "";
}

/**
 * Recount an entry after a user correction changed its action.
 * @param {object} entry
 * @param {string} action
 */
function recountEntry(entry, action) {
  undoEntry(entry);
  entry.action = action;
  if (action === "hide" || action === "collapse") {
    state.counted.add(entry.countKey);
    entry.counted = true;
    entry.countedAction = action;
    if (action === "hide") state.hidden += 1;
    else state.collapsed += 1;
  }
  updateBadge();
}

/**
 * POST a feedback event; failures are ignored on purpose.
 * @param {string} kind label_correction | policy_event | prefs
 * @param {object} payload
 * @param {string} [dmid]
 * @returns {Promise<object|null>}
 */
function sendFeedback(kind, payload, dmid) {
  logFeedback(kind, payload, dmid);
  const bvid = currentBvid();
  if (!bvid) return Promise.resolve(null);
  const body = { bvid, kind, payload };
  if (dmid) body.dmid = dmid;
  return postJson("/api/v1/feedback", body).catch((error) => {
    log("feedback failed", error);
    return null;
  });
}

/**
 * Persist prefs after a user change and notify the service.
 * @param {string} reason
 */
function persistPrefs(reason) {
  const before = state.prefs;
  state.prefs = normalizePrefs(state.prefs);
  // In study mode the condition must stay reproducible: record the change but
  // keep the participant's own dmf.prefs.v1 untouched.
  if (state.study) studyRecordPrefs(before, state.prefs);
  else savePrefs();
  state.decisionsCache.clear();
  state.failures.clear();
  updateBadge();
  syncSettings();
  sendFeedback("prefs", { prefs: state.prefs });
  pushLog("prefs", "偏好更新", reason);
  log("prefs updated", reason, state.prefs);
}

/* ------------------------------------------------------------------ */
/* Study mode (controlled experiment)                                  */
/*                                                                     */
/* Activated by ?dmf_study=<id>&dmf_cond=<all_on|keyword|prototype> or */
/* by the examiner localStorage override. Conditions are fixed presets; */
/* user edits are only recorded. Events are capped and exported as the */
/* protocol §7 JSON document.                                          */
/* ------------------------------------------------------------------ */

/**
 * Read the study configuration from the URL and the examiner override.
 * @returns {object|null}
 */
function initStudy() {
  try {
    let stored = null;
    try {
      stored = localStorage.getItem(STUDY_STORAGE_KEY);
    } catch (error) {
      stored = null;
    }
    const config = parseStudyConfig(String(location.search || ""), stored);
    if (!config) return null;
    const spec = conditionSpec(config.condition);
    if (!spec) return null;
    // Controlled conditions ignore whatever prefs this browser stored.
    const base = defaultPrefs();
    const prefs = normalizePrefs(prefsForCondition(config.condition, base));
    const analysisPrefs = normalizePrefs(analysisPrefsForCondition(config.condition, base));
    const density = config.density || "high";
    const session = createStudySession({
      studyId: config.studyId,
      condition: config.condition,
      prefs,
      scriptVersion: scriptVersion(),
      ua: String(navigator.userAgent || ""),
    });
    log("study mode", config.studyId, config.condition, density, config.source);
    return {
      id: config.studyId,
      condition: config.condition,
      density,
      source: config.source,
      spec,
      recordOnly: spec.record_only === true,
      prefs,
      analysisPrefs,
      session,
      uiVisible: false,
    };
  } catch (error) {
    log("study init failed", error);
    return null;
  }
}

/**
 * Append one segment event for the study log.
 * @param {object} segment
 * @param {{shown: number, hidden: number, collapsed: number, by_label: object}} counts
 * @param {number} latencyMs
 * @param {boolean} online
 * @param {boolean} applied
 */
function studyRecordSegment(segment, counts, latencyMs, online, applied) {
  if (!state.study || !state.study.session) return;
  try {
    state.study.session.recordSegment({
      bvid: currentBvid(),
      url: (segment && segment.href) || "",
      segment_index: segment && segment.index ? segment.index : 1,
      shown: counts.shown,
      hidden: counts.hidden,
      collapsed: counts.collapsed,
      thinned: counts.thinned,
      by_label: counts.by_label,
      latency_ms: latencyMs,
      online: online === true,
      applied: applied === true,
    });
  } catch (error) {
    log("study segment event failed", error);
  }
}

/**
 * Count thinned comments that the policy would have shown. Density thinning
 * only affects visible comments, so the study counters subtract these from
 * "shown" instead of inflating it.
 * @param {Map<string, object>} actions
 * @param {Set<string>} thinDrop
 * @returns {number}
 */
function thinnedShowCount(actions, thinDrop) {
  if (!actions || !thinDrop || !thinDrop.size) return 0;
  let count = 0;
  for (const dmid of thinDrop) {
    const decision = actions.get(String(dmid));
    if (decision && decision.action === "show") count += 1;
  }
  return count;
}

/**
 * Record the flat scalar prefs difference of one user edit.
 * @param {object} before
 * @param {object} after
 */
function studyRecordPrefs(before, after) {
  if (!state.study || !state.study.session) return;
  try {
    for (const change of diffPrefs(before, after)) {
      state.study.session.recordPref(change.key, change.from, change.to);
    }
    state.study.session.setPrefs(after);
  } catch (error) {
    log("study prefs event failed", error);
  }
}

/** @param {string} dmid @param {string} label */
function studyRecordRestore(dmid, label) {
  if (!state.study || !state.study.session) return;
  try {
    state.study.session.recordRestore({ dmid, label });
  } catch (error) {
    log("study restore event failed", error);
  }
}

/** @param {string} dmid @param {string} text @param {string} from @param {string} to */
function studyRecordCorrection(dmid, text, from, to) {
  if (!state.study || !state.study.session) return;
  try {
    state.study.session.recordCorrection({ dmid, text, from, to });
  } catch (error) {
    log("study correction event failed", error);
  }
}

/** @param {string} panel */
function studyRecordExpand(panel) {
  if (!state.study || !state.study.session) return;
  try {
    state.study.session.recordExpand({ panel });
  } catch (error) {
    log("study expand event failed", error);
  }
}

/**
 * Build the protocol §7 export document.
 * @returns {object|null}
 */
function studyExport() {
  if (!state.study || !state.study.session) return null;
  try {
    state.study.session.setPrefs(state.prefs);
    return state.study.session.export();
  } catch (error) {
    log("study export failed", error);
    return null;
  }
}

/** Download the study log as dmf-study-<id>.json. */
function studyDownload() {
  const payload = studyExport();
  if (!payload) return null;
  try {
    const BlobCtor = PAGE.Blob || Blob;
    const UrlCtor = PAGE.URL || URL;
    const text = JSON.stringify(payload, null, 2);
    const url = UrlCtor.createObjectURL(new BlobCtor([text], { type: "application/json" }));
    const link = document.createElement("a");
    link.href = url;
    link.download = studyDownloadName(payload.study_id);
    link.style.display = "none";
    document.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(() => {
      try {
        UrlCtor.revokeObjectURL(url);
      } catch (error) {
        /* ignore */
      }
    }, 1000);
    return payload;
  } catch (error) {
    log("study download failed", error);
    return payload;
  }
}

/** Show the badge and panel while the study session is active. */
function studyShowUi() {
  if (!state.study) return;
  state.study.uiVisible = true;
  ensureUi();
  if (ui.badge) ui.badge.style.display = "";
  togglePanel(true);
}

/** Hide the study UI again (the default state). */
function studyHideUi() {
  if (!state.study) return;
  state.study.uiVisible = false;
  if (ui.badge) ui.badge.style.display = "none";
  if (ui.panel) ui.panel.style.display = "none";
}

/**
 * @param {boolean|null} connected
 */
function setConnected(connected) {
  state.connected = connected;
  updateBadge();
  syncStatus();
}

/**
 * Probe GET /api/v1/health to drive the "service connected" indicator.
 */
async function probeService() {
  try {
    const payload = await getJson("/api/v1/health", DECISION_TIMEOUT_MS);
    setConnected(Boolean(payload && payload.ok === true));
  } catch (error) {
    setConnected(false);
  }
}

/** Accent colour of every label, matching the prototype style sheet. */
const LABEL_COLORS = {
  information: "#5aa9ff",
  humor: "#ffcc5c",
  emotional_resonance: "#ff9f6e",
  irrelevant_repetition: "#8f9bb3",
  attack: "#ff6b81",
  conflict: "#ffa94d",
  spoiler: "#b197fc",
  political: "#38d9a9",
  inflammatory: "#ff8787",
  other: "#8b93a7",
};

/** Chinese names for the feedback kinds shown in the log tab. */
const LOG_KINDS = {
  label_correction: "标签纠正",
  policy_event: "策略动作",
  prefs: "偏好更新",
};

/** Update the fixed badge and the header statistics. */
function updateBadge() {
  if (!ui.badgeText) return;
  if (state.prefs && state.prefs.enabled === false) {
    ui.badgeText.textContent = "已暂停 · 点控制台开启过滤";
  } else {
    const pool = state.pool;
    const wide = pool && pool.ready && pool.counts ? pool.counts : null;
    ui.badgeText.textContent =
      "已隐藏 " + (wide ? wide.hidden : state.hidden) + " · 折叠 " + (wide ? wide.collapsed : state.collapsed);
  }
  if (ui.badgeStatus) {
    ui.badgeStatus.classList.toggle("is-on", state.connected === true);
    ui.badgeStatus.classList.toggle("is-off", state.connected === false);
  }
  updateStats();
  updateDiag();
}

/** Human readable form of a thrown value. */
function describeError(error) {
  if (!error) return "未知错误";
  if (typeof error === "string") return error;
  if (typeof error.message === "string" && error.message) return error.message;
  return String(error);
}

/** Refresh the diagnostics line in the advanced section. */
function updateDiag() {
  if (!ui.diag) return;
  const d = state.diag;
  const parts = [
    "拦截 " + d.intercepts,
    "决策成功 " + d.ok,
    "失败 " + d.fail,
    d.found === null ? "缓存 未知" : d.found ? "缓存 命中" : "缓存 未命中",
  ];
  if (d.online) parts.push("在线打标 " + d.online);
  if (state.dom && (state.dom.hidden || state.dom.collapsed)) {
    parts.push("DOM 兜底 隐藏 " + state.dom.hidden + " · 折叠 " + state.dom.collapsed);
  }
  if (state.pool && (state.pool.ready || state.pool.progress.total)) {
    const progress = state.pool.progress;
    parts.push(
      state.pool.ready
        ? "弹幕池 " + state.pool.items.length + " 条"
        : "弹幕池 " + progress.done + "/" + progress.total + " 段"
    );
  }
  let text = parts.join(" · ");
  if (d.lastError) text += " · 最近错误: " + d.lastError;
  ui.diag.textContent = text;
}

/** Refresh the three session counters. */
function updateStats() {
  if (!ui.stats) return;
  const pool = state.pool;
  const wide = pool && pool.ready && pool.counts ? pool.counts : null;
  if (ui.stats.show) ui.stats.show.textContent = String(wide ? wide.shown : state.shown);
  if (ui.stats.collapse) ui.stats.collapse.textContent = String(wide ? wide.collapsed : state.collapsed);
  if (ui.stats.hide) ui.stats.hide.textContent = String(wide ? wide.hidden : state.hidden);
}

/** Version of the running userscript, so the panel proves which build loaded. */
function scriptVersion() {
  try {
    if (typeof GM_info === "object" && GM_info && GM_info.script && GM_info.script.version) {
      return String(GM_info.script.version);
    }
  } catch (error) {
    /* GM_info is optional */
  }
  return "dev";
}

/** Refresh the per-label processed counts inside the strategy tab. */
function syncTypeCounts() {
  if (!ui.typeRows) return;
  let total = 0;
  for (const [label, row] of ui.typeRows) {
    const count = state.labelCounts.get(label) || 0;
    total += count;
    if (row.count) row.count.textContent = String(count);
  }
  if (ui.totalCount) ui.totalCount.textContent = String(total);
}

/** Refresh the service connection indicator. */
function syncStatus() {
  if (ui.statusDot) {
    ui.statusDot.classList.toggle("is-on", state.connected === true);
    ui.statusDot.classList.toggle("is-off", state.connected === false);
  }
  if (ui.statusText) {
    ui.statusText.textContent =
      state.connected === true
        ? "本地服务已连接"
        : state.connected === false
        ? "未连接 · 弹幕原样显示"
        : "正在检测服务";
    ui.statusText.title = state.service;
  }
  if (ui.serviceInput && ui.serviceInput.value !== state.service) {
    ui.serviceInput.value = state.service;
  }
}

/** Sync every settings control from state.prefs. */
function syncSettings() {
  if (!ui.panel) return;
  if (ui.toggle) ui.toggle.checked = state.prefs.enabled;
  for (const [label, row] of ui.typeRows) {
    const action = state.prefs.per_type[label] || "show";
    for (const key of ACTIONS) {
      const button = row.buttons[key];
      if (button) button.classList.toggle("is-on-" + key, key === action);
    }
  }
  if (ui.minConfidence) {
    ui.minConfidence.value = String(state.prefs.min_confidence);
    if (ui.minConfValue) ui.minConfValue.textContent = Number(state.prefs.min_confidence).toFixed(2);
  }
  if (ui.densityEnabled) ui.densityEnabled.checked = state.prefs.density.enabled !== false;
  if (ui.failOpen) ui.failOpen.checked = state.prefs.fail_open !== false;
  if (ui.densityWindow) ui.densityWindow.value = String(state.prefs.density.window_seconds);
  if (ui.densityMax) ui.densityMax.value = String(state.prefs.density.max_visible);
  if (ui.densityExceed) ui.densityExceed.value = state.prefs.density.exceed;
  syncStatus();
  updateBadge();
}

/**
 * Format milliseconds as mm:ss for the folded list.
 * @param {number} timeMs
 * @returns {string}
 */
function formatTime(timeMs) {
  const total = Math.max(0, Math.round((Number(timeMs) || 0) / 1000));
  const minutes = Math.floor(total / 60);
  const seconds = total % 60;
  return (minutes < 10 ? "0" : "") + minutes + ":" + (seconds < 10 ? "0" : "") + seconds;
}

/**
 * Format a timestamp as HH:MM:SS for the feedback log.
 * @param {number} timeMs
 * @returns {string}
 */
function clockTime(timeMs) {
  const date = new Date(Number(timeMs) || Date.now());
  const pad = (value) => (value < 10 ? "0" : "") + value;
  return pad(date.getHours()) + ":" + pad(date.getMinutes()) + ":" + pad(date.getSeconds());
}

/**
 * Human explanation of a policy reason code.
 * @param {string} reason
 * @returns {string}
 */
function reasonText(reason) {
  const value = String(reason || "");
  if (value === "density") return "密度超限";
  if (value === "not_tagged") return "未打标";
  if (value === "low_confidence") return "低置信度";
  if (value === "disabled") return "过滤已关闭";
  if (value === "user_show") return "手动恢复显示";
  if (value === "default_show") return "默认显示";
  const pref = /^pref_(hide|collapse)_(.+)$/.exec(value);
  if (pref) {
    return "策略：" + (pref[1] === "hide" ? "隐藏" : "折叠") + (LABELS_ZH[pref[2]] || pref[2]);
  }
  return value || "—";
}

/** Inject the panel stylesheet once. */
function injectStyles() {
  if (document.getElementById("dmf-style")) return;
  const style = document.createElement("style");
  style.id = "dmf-style";
  style.textContent = [
    "#dmf-badge{position:fixed;right:16px;bottom:16px;z-index:2147483647;display:flex;align-items:center;",
    "gap:9px;padding:7px 10px 7px 7px;border-radius:12px;background:rgba(15,17,23,.95);",
    "border:1px solid rgba(255,255,255,.09);color:#e8e8ea;box-shadow:0 10px 30px rgba(0,0,0,.45);",
    'font:12px/1.4 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;',
    "user-select:none;backdrop-filter:blur(8px);}",
    "#dmf-badge .dmf-brand{width:22px;height:22px;border-radius:7px;display:grid;place-items:center;",
    "background:linear-gradient(135deg,#fb7299,#ff9db8);color:#fff;font-size:12px;font-weight:700;",
    "cursor:pointer;flex:none;}",
    "#dmf-badge .dmf-badge-text{color:#cfd3d8;font-variant-numeric:tabular-nums;cursor:pointer;}",
    "#dmf-badge .dmf-badge-status{width:7px;height:7px;border-radius:50%;background:#f0b429;flex:none;}",
    "#dmf-badge .dmf-badge-status.is-on{background:#2fbf71;box-shadow:0 0 0 3px rgba(47,191,113,.16);}",
    "#dmf-badge .dmf-badge-status.is-off{background:#ff6b81;box-shadow:0 0 0 3px rgba(255,107,129,.14);}",
    "#dmf-badge .dmf-open{appearance:none;border:1px solid rgba(255,255,255,.14);background:rgba(255,255,255,.05);",
    "color:#e8e8ea;border-radius:7px;padding:2px 8px;font:inherit;font-size:11px;cursor:pointer;}",
    "#dmf-badge .dmf-open:hover{background:rgba(255,255,255,.12);}",
    "#dmf-badge .dmf-switch{position:relative;width:32px;height:17px;flex:none;display:inline-block;}",
    "#dmf-badge .dmf-switch input{position:absolute;inset:0;width:100%;height:100%;margin:0;opacity:0;",
    "cursor:pointer;z-index:2;}",
    "#dmf-badge .dmf-switch-track{position:absolute;inset:0;border-radius:999px;background:#2a2f3d;",
    "transition:background .16s ease;}",
    "#dmf-badge .dmf-switch-track::after{content:\"\";position:absolute;top:2px;left:2px;width:13px;height:13px;",
    "border-radius:50%;background:#fff;transition:left .16s ease;}",
    "#dmf-badge .dmf-switch input:checked+.dmf-switch-track{background:rgba(251,114,153,.9);}",
    "#dmf-badge .dmf-switch input:checked+.dmf-switch-track::after{left:17px;}",

    "#dmf-panel{position:fixed;right:16px;bottom:64px;z-index:2147483647;width:min(412px,92vw);",
    "display:flex;flex-direction:column;max-height:76vh;border-radius:14px;background:#14161c;",
    "border:1px solid rgba(255,255,255,.08);color:#e8e8ea;overflow:hidden;",
    'box-shadow:0 20px 54px rgba(0,0,0,.6);font:12px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",',
    '"PingFang SC","Microsoft YaHei",sans-serif;}',
    "#dmf-panel .dmf-head{display:flex;align-items:center;gap:9px;padding:12px 14px 10px;",
    "border-bottom:1px solid rgba(255,255,255,.06);flex:none;}",
    "#dmf-panel .dmf-brand-sm{width:24px;height:24px;border-radius:8px;display:grid;place-items:center;",
    "background:linear-gradient(135deg,#fb7299,#ff9db8);color:#fff;font-size:12px;font-weight:700;flex:none;}",
    "#dmf-panel .dmf-title{font-size:13px;font-weight:600;color:#fff;letter-spacing:.2px;}",
    "#dmf-panel .dmf-sub{font-size:10.5px;color:#697083;margin-top:1px;}",
    "#dmf-panel .dmf-status{margin-left:auto;display:flex;align-items:center;gap:5px;font-size:11px;",
    "color:#8b93a7;max-width:150px;overflow:hidden;white-space:nowrap;text-overflow:ellipsis;}",
    "#dmf-panel .dmf-status .dmf-dot{width:7px;height:7px;border-radius:50%;background:#f0b429;flex:none;}",
    "#dmf-panel .dmf-status .dmf-dot.is-on{background:#2fbf71;box-shadow:0 0 0 3px rgba(47,191,113,.16);}",
    "#dmf-panel .dmf-status .dmf-dot.is-off{background:#ff6b81;}",
    "#dmf-panel .dmf-close{appearance:none;border:0;background:transparent;color:#697083;font-size:16px;",
    "line-height:1;cursor:pointer;padding:2px 0 2px 6px;}",
    "#dmf-panel .dmf-close:hover{color:#fff;}",
    "#dmf-panel .dmf-stats{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;padding:10px 14px 2px;flex:none;}",
    "#dmf-panel .dmf-stat{padding:7px 10px;border-radius:9px;background:#1a1d25;border:1px solid rgba(255,255,255,.05);}",
    "#dmf-panel .dmf-stat b{display:block;font-size:16px;line-height:1.25;color:#fff;font-weight:650;",
    "font-variant-numeric:tabular-nums;}",
    "#dmf-panel .dmf-stat span{font-size:11px;color:#8b93a7;}",
    "#dmf-panel .dmf-tabs{display:flex;gap:18px;padding:10px 14px 0;border-bottom:1px solid rgba(255,255,255,.06);flex:none;}",
    "#dmf-panel .dmf-tab{appearance:none;border:0;background:transparent;color:#8b93a7;font:inherit;font-size:12px;",
    "padding:0 0 8px;cursor:pointer;position:relative;}",
    "#dmf-panel .dmf-tab:hover{color:#cfd3d8;}",
    "#dmf-panel .dmf-tab.is-active{color:#fff;font-weight:600;}",
    "#dmf-panel .dmf-tab.is-active::after{content:\"\";position:absolute;left:0;right:0;bottom:-1px;height:2px;",
    "border-radius:2px;background:#fb7299;}",
    "#dmf-panel .dmf-body{overflow:auto;flex:1;min-height:220px;padding:4px 14px 14px;}",
    "#dmf-panel .dmf-view{display:none;padding-top:6px;}",
    "#dmf-panel .dmf-view.is-active{display:block;}",
    "#dmf-panel .dmf-tag-row{display:grid;grid-template-columns:1fr auto auto;align-items:center;gap:8px;",
    "padding:6px 0;border-bottom:1px solid rgba(255,255,255,.04);}",
    "#dmf-panel .dmf-tag-name{display:flex;align-items:center;gap:7px;min-width:0;}",
    "#dmf-panel .dmf-tag-name i{width:8px;height:8px;border-radius:2px;flex:none;}",
    "#dmf-panel .dmf-tag-zh{color:#e8e8ea;font-size:12px;}",
    "#dmf-panel .dmf-tag-en{color:#697083;font-size:10px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}",
    "#dmf-panel .dmf-tag-count{color:#697083;font-size:11px;font-variant-numeric:tabular-nums;min-width:18px;",
    "text-align:right;}",
    "#dmf-panel .dmf-seg{display:flex;border:1px solid rgba(255,255,255,.08);border-radius:7px;overflow:hidden;}",
    "#dmf-panel .dmf-seg button{appearance:none;border:0;background:transparent;color:#9aa2b4;font:inherit;",
    "font-size:11px;padding:3px 7px;cursor:pointer;line-height:1.45;}",
    "#dmf-panel .dmf-seg button:hover{color:#fff;background:rgba(255,255,255,.06);}",
    "#dmf-panel .dmf-seg button.is-on-show{background:rgba(47,191,113,.18);color:#7ee2a8;}",
    "#dmf-panel .dmf-seg button.is-on-collapse{background:rgba(240,180,41,.2);color:#ffd98a;}",
    "#dmf-panel .dmf-seg button.is-on-hide{background:rgba(255,107,129,.2);color:#ffb3bd;}",
  ".dmf-live-collapsed{opacity:.4;filter:blur(.3px);}",
    "#dmf-panel .dmf-section{margin:14px 0 4px;font-size:11px;letter-spacing:.4px;color:#697083;text-transform:uppercase;}",
    "#dmf-panel .dmf-field{display:flex;align-items:center;gap:10px;padding:6px 0;color:#cfd3d8;}",
    "#dmf-panel .dmf-field>span:first-child{min-width:86px;color:#aab1c0;}",
    "#dmf-panel .dmf-field input[type=range]{flex:1;accent-color:#fb7299;height:16px;}",
    "#dmf-panel .dmf-field input[type=number],#dmf-panel .dmf-field input[type=text]{width:74px;",
    "background:#1a1d25;border:1px solid rgba(255,255,255,.1);color:#e8e8ea;border-radius:6px;padding:3px 6px;",
    "font:inherit;}",
    "#dmf-panel .dmf-field select{background:#1a1d25;border:1px solid rgba(255,255,255,.1);color:#e8e8ea;",
    "border-radius:6px;padding:3px 6px;font:inherit;}",
    "#dmf-panel .dmf-field .dmf-value{min-width:34px;text-align:right;color:#e8e8ea;font-variant-numeric:tabular-nums;}",
    "#dmf-panel .dmf-field label{display:flex;align-items:center;gap:6px;cursor:pointer;}",
    "#dmf-panel .dmf-field input[type=checkbox]{accent-color:#fb7299;}",
    "#dmf-panel .dmf-diag{margin-top:5px;color:#8b93a7;font-size:11px;line-height:1.5;overflow-wrap:anywhere;}",
    "#dmf-panel details.dmf-advanced{margin-top:12px;border-top:1px solid rgba(255,255,255,.06);padding-top:8px;}",
    "#dmf-panel details.dmf-advanced summary{cursor:pointer;color:#8b93a7;font-size:11px;outline:none;}",
    "#dmf-panel details.dmf-advanced summary:hover{color:#cfd3d8;}",
    "#dmf-panel .dmf-card{padding:9px 10px;border:1px solid rgba(255,255,255,.06);border-radius:10px;",
    "background:#1a1d25;margin:8px 0;}",
    "#dmf-panel .dmf-card-head{display:flex;align-items:center;gap:6px;font-size:11px;color:#8b93a7;}",
    "#dmf-panel .dmf-chip{display:inline-flex;align-items:center;gap:4px;padding:1px 6px;border-radius:5px;",
    "background:rgba(255,255,255,.07);color:#cfd3d8;font-size:10.5px;}",
    "#dmf-panel .dmf-chip.is-hide{background:rgba(255,107,129,.18);color:#ffb3bd;}",
    "#dmf-panel .dmf-chip.is-collapse{background:rgba(240,180,41,.18);color:#ffd98a;}",
    "#dmf-panel .dmf-chip.is-show{background:rgba(47,191,113,.16);color:#7ee2a8;}",
    "#dmf-panel .dmf-card-head .dmf-score{margin-left:auto;color:#697083;font-variant-numeric:tabular-nums;}",
    "#dmf-panel .dmf-card-text{margin:6px 0 7px;color:#e8e8ea;overflow-wrap:anywhere;line-height:1.5;}",
    "#dmf-panel .dmf-card-meta{display:flex;flex-wrap:wrap;gap:5px;color:#7c8394;font-size:11px;}",
    "#dmf-panel .dmf-card-actions{display:flex;gap:6px;margin-top:8px;}",
    "#dmf-panel .dmf-btn{appearance:none;border:1px solid rgba(255,255,255,.12);background:rgba(255,255,255,.05);",
    "color:#cfd3d8;border-radius:6px;padding:3px 9px;font:inherit;font-size:11px;cursor:pointer;}",
    "#dmf-panel .dmf-btn:hover{background:rgba(255,255,255,.12);color:#fff;}",
    "#dmf-panel .dmf-btn.is-accent{border-color:rgba(251,114,153,.45);color:#ffb7cb;}",
    "#dmf-panel .dmf-empty{color:#697083;text-align:center;padding:26px 10px;line-height:1.7;}",
    "#dmf-panel .dmf-log{display:grid;grid-template-columns:auto auto 1fr;gap:8px;padding:6px 0;",
    "border-bottom:1px solid rgba(255,255,255,.04);font-size:11px;align-items:baseline;}",
    "#dmf-panel .dmf-log-time{color:#697083;font-variant-numeric:tabular-nums;}",
    "#dmf-panel .dmf-log-kind{padding:0 6px;border-radius:4px;background:rgba(251,114,153,.14);color:#ffb7cb;}",
    "#dmf-panel .dmf-log-text{color:#aab1c0;overflow-wrap:anywhere;}",
    "#dmf-popover{position:fixed;z-index:2147483647;width:214px;padding:8px;border-radius:10px;background:#1c1f28;",
    "border:1px solid rgba(255,255,255,.12);box-shadow:0 16px 40px rgba(0,0,0,.55);display:grid;",
    "grid-template-columns:1fr 1fr;gap:4px;",
    'font:12px/1.4 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;}',
    "#dmf-popover button{appearance:none;border:0;background:#232733;color:#cfd3d8;border-radius:6px;padding:5px 7px;",
    "font:inherit;font-size:11px;text-align:left;cursor:pointer;display:flex;align-items:center;gap:5px;}",
    "#dmf-popover button:hover{background:#2c3140;color:#fff;}",
    "#dmf-popover button i{width:7px;height:7px;border-radius:2px;flex:none;}",
    "#dmf-panel .dmf-toast{position:fixed;right:16px;bottom:112px;max-width:320px;padding:7px 12px;border-radius:9px;",
    "background:rgba(251,114,153,.92);color:#fff;font-size:11px;text-align:center;opacity:0;",
    "transform:translateY(4px);transition:opacity .18s ease,transform .18s ease;pointer-events:none;}",
    "#dmf-panel .dmf-toast.is-visible{opacity:1;transform:none;}",
  ].join("");
  (document.head || document.documentElement).appendChild(style);
}

/** Build the fixed badge with the enable switch and console button. */
function buildBadge() {
  const badge = el("div");
  badge.id = BADGE_ID;
  const brand = el("span", "dmf-brand");
  brand.textContent = "Z";
  brand.title = "打开过滤控制台";
  const text = el("span", "dmf-badge-text");
  const status = el("span", "dmf-badge-status");
  status.title = "本地服务状态";
  const open = document.createElement("button");
  open.type = "button";
  open.className = "dmf-open";
  open.textContent = "控制台";
  open.addEventListener("click", () => togglePanel());
  const label = el("label", "dmf-switch");
  label.title = "开启 / 关闭过滤";
  const checkbox = document.createElement("input");
  checkbox.type = "checkbox";
  checkbox.checked = state.prefs.enabled;
  const track = el("span", "dmf-switch-track");
  label.append(checkbox, track);
  checkbox.addEventListener("change", () => {
    state.prefs = mergePrefs(state.prefs, { enabled: checkbox.checked });
    persistPrefs("enabled");
    renderEntries();
  });
  brand.addEventListener("click", () => togglePanel());
  text.addEventListener("click", () => togglePanel());
  badge.append(brand, text, status, open, label);
  document.body.appendChild(badge);
  ui.badge = badge;
  ui.badgeText = text;
  ui.badgeStatus = status;
  ui.toggle = checkbox;
}

/**
 * Build one label row: colour, names, processed count, show/collapse/hide switch.
 * @param {string} label
 * @returns {HTMLElement}
 */
function buildTypeRow(label) {
  const row = el("div", "dmf-tag-row");
  const name = el("div", "dmf-tag-name");
  const dot = el("i");
  dot.style.background = LABEL_COLORS[label] || LABEL_COLORS.other;
  const zh = el("span", "dmf-tag-zh");
  zh.textContent = labelName(label);
  const en = el("span", "dmf-tag-en");
  en.textContent = label;
  name.append(dot, zh, en);
  const count = el("span", "dmf-tag-count");
  count.textContent = "0";
  count.title = "本次会话处理过的条数";
  const seg = el("div", "dmf-seg");
  const buttons = {};
  for (const action of ACTIONS) {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = action === "show" ? "显示" : action === "hide" ? "隐藏" : "折叠";
    button.title = "把「" + labelName(label) + "」设为" + button.textContent;
    button.addEventListener("click", () => {
      state.prefs = mergePrefs(state.prefs, { per_type: { [label]: action } });
      persistPrefs("per_type:" + label);
      reapplyPrefs();
    });
    buttons[action] = button;
    seg.appendChild(button);
  }
  row.append(name, count, seg);
  ui.typeRows.set(label, { buttons, count });
  return row;
}

/** Build the global threshold and density controls. */
function buildGlobalSettings() {
  const box = el("div");

  const thresholdTitle = el("div", "dmf-section");
  thresholdTitle.textContent = "全局参数";
  box.appendChild(thresholdTitle);

  const thresholdRow = el("div", "dmf-field");
  const thresholdName = el("span");
  thresholdName.textContent = "置信度阈值";
  const confidence = document.createElement("input");
  confidence.type = "range";
  confidence.min = "0";
  confidence.max = "1";
  confidence.step = "0.05";
  confidence.value = String(state.prefs.min_confidence);
  const confidenceValue = el("span", "dmf-value");
  confidenceValue.textContent = Number(state.prefs.min_confidence).toFixed(2);
  confidence.addEventListener("input", () => {
    confidenceValue.textContent = Number(confidence.value).toFixed(2);
  });
  confidence.addEventListener("change", () => {
    state.prefs = mergePrefs(state.prefs, { min_confidence: Number(confidence.value) });
    persistPrefs("min_confidence");
    reapplyPrefs();
  });
  thresholdRow.append(thresholdName, confidence, confidenceValue);
  ui.minConfidence = confidence;
  ui.minConfValue = confidenceValue;
  box.appendChild(thresholdRow);

  const windowRow = el("div", "dmf-field");
  const windowName = el("span");
  windowName.textContent = "密度窗口";
  const windowInput = document.createElement("input");
  windowInput.type = "number";
  windowInput.min = "1";
  windowInput.max = "3600";
  windowInput.step = "1";
  windowInput.value = String(state.prefs.density.window_seconds);
  windowInput.addEventListener("change", () => {
    state.prefs = mergePrefs(state.prefs, { density: { window_seconds: Number(windowInput.value) } });
    persistPrefs("density.window_seconds");
  });
  const windowUnit = el("span", "dmf-value");
  windowUnit.textContent = "秒";
  windowRow.append(windowName, windowInput, windowUnit);
  ui.densityWindow = windowInput;
  box.appendChild(windowRow);

  const maxRow = el("div", "dmf-field");
  const maxName = el("span");
  maxName.textContent = "窗口内上限";
  const maxInput = document.createElement("input");
  maxInput.type = "number";
  maxInput.min = "0";
  maxInput.max = "100000";
  maxInput.step = "1";
  maxInput.value = String(state.prefs.density.max_visible);
  maxInput.addEventListener("change", () => {
    state.prefs = mergePrefs(state.prefs, { density: { max_visible: Number(maxInput.value) } });
    persistPrefs("density.max_visible");
  });
  const maxUnit = el("span", "dmf-value");
  maxUnit.textContent = "条";
  maxRow.append(maxName, maxInput, maxUnit);
  ui.densityMax = maxInput;
  box.appendChild(maxRow);

  const exceedRow = el("div", "dmf-field");
  const exceedName = el("span");
  exceedName.textContent = "超限动作";
  const exceedSelect = document.createElement("select");
  for (const value of ["collapse", "hide"]) {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = value === "collapse" ? "折叠" : "隐藏";
    exceedSelect.appendChild(option);
  }
  exceedSelect.value = state.prefs.density.exceed;
  exceedSelect.addEventListener("change", () => {
    state.prefs = mergePrefs(state.prefs, { density: { exceed: exceedSelect.value } });
    persistPrefs("density.exceed");
  });
  exceedRow.append(exceedName, exceedSelect);
  ui.densityExceed = exceedSelect;
  box.appendChild(exceedRow);

  const enabledRow = el("div", "dmf-field");
  const enabledName = el("span");
  enabledName.textContent = "密度控制";
  const densityToggleLabel = document.createElement("label");
  const densityCheckbox = document.createElement("input");
  densityCheckbox.type = "checkbox";
  densityCheckbox.checked = state.prefs.density.enabled !== false;
  densityCheckbox.addEventListener("change", () => {
    state.prefs = mergePrefs(state.prefs, { density: { enabled: densityCheckbox.checked } });
    persistPrefs("density.enabled");
  });
  densityToggleLabel.append(densityCheckbox, document.createTextNode(" 启用"));
  enabledRow.append(enabledName, densityToggleLabel);
  ui.densityEnabled = densityCheckbox;
  box.appendChild(enabledRow);

  const failRow = el("div", "dmf-field");
  const failName = el("span");
  failName.textContent = "低置信放行";
  const failLabel = document.createElement("label");
  const failCheckbox = document.createElement("input");
  failCheckbox.type = "checkbox";
  failCheckbox.checked = state.prefs.fail_open !== false;
  failCheckbox.title = "关闭后，即使模型对该条标签的置信度很低，也会按标签策略隐藏或折叠";
  failCheckbox.addEventListener("change", () => {
    state.prefs = mergePrefs(state.prefs, { fail_open: failCheckbox.checked });
    persistPrefs("fail_open");
    reapplyPrefs();
  });
  failLabel.append(failCheckbox, document.createTextNode(" 启用"));
  failRow.append(failName, failLabel);
  ui.failOpen = failCheckbox;
  box.appendChild(failRow);

  return box;
}

/** Build the collapsed advanced block (service address and preference reset). */
function buildAdvanced() {
  const details = document.createElement("details");
  details.className = "dmf-advanced";
  const summary = document.createElement("summary");
  summary.textContent = "高级 · 本地服务";
  details.appendChild(summary);

  const serviceRow = el("div", "dmf-field");
  const serviceName = el("span");
  serviceName.textContent = "服务地址";
  const serviceInput = document.createElement("input");
  serviceInput.type = "text";
  serviceInput.value = state.service;
  serviceInput.addEventListener("change", () => {
    const value = serviceInput.value.trim();
    state.service = /^https?:\/\//.test(value) ? value.replace(/\/+$/, "") : DEFAULT_SERVICE;
    saveService();
    state.decisionsCache.clear();
    state.failures.clear();
    syncStatus();
    probeService();
  });
  serviceRow.append(serviceName, serviceInput);
  ui.serviceInput = serviceInput;
  details.appendChild(serviceRow);

  const diagRow = el("div", "dmf-diag");
  diagRow.id = "dmf-diag";
  ui.diag = diagRow;
  details.appendChild(diagRow);
  updateDiag();

  const resetRow = el("div", "dmf-card-actions");
  const resetButton = document.createElement("button");
  resetButton.type = "button";
  resetButton.className = "dmf-btn";
  resetButton.textContent = "恢复默认策略";
  resetButton.addEventListener("click", () => {
    const before = state.prefs;
    if (!state.study) {
      try {
        localStorage.removeItem(PREFS_KEY);
      } catch (error) {
        /* ignore */
      }
    }
    state.prefs = normalizePrefs(null);
    if (state.study) studyRecordPrefs(before, state.prefs);
    state.decisionsCache.clear();
    state.failures.clear();
    syncSettings();
    reapplyPrefs();
    pushLog("prefs", "偏好更新", "恢复默认策略");
    sendFeedback("prefs", { prefs: state.prefs });
  });
  resetRow.appendChild(resetButton);
  details.appendChild(resetRow);

  const hint = el("div");
  hint.style.color = "#697083";
  hint.style.fontSize = "10.5px";
  hint.style.marginTop = "6px";
  hint.textContent = "服务不可用时脚本自动放行原弹幕（fail-open）。";
  details.appendChild(hint);

  return details;
}

/** Build the three-tab console panel (hidden until opened). */
function buildPanel() {
  const panel = el("div");
  panel.id = PANEL_ID;
  panel.style.display = "none";

  const head = el("div", "dmf-head");
  const brand = el("span", "dmf-brand-sm");
  brand.textContent = "Z";
  const headText = el("div");
  const title = el("div", "dmf-title");
  title.textContent = "过滤控制台";
  const sub = el("div", "dmf-sub");
  sub.textContent = "v" + scriptVersion() + " · 修改立即生效";
  headText.append(title, sub);
  const status = el("div", "dmf-status");
  const statusDot = el("span", "dmf-dot");
  const statusText = el("span");
  statusText.textContent = "正在检测服务";
  status.append(statusDot, statusText);
  const close = document.createElement("button");
  close.type = "button";
  close.className = "dmf-close";
  close.textContent = "×";
  close.title = "关闭";
  close.addEventListener("click", () => togglePanel(false));
  head.append(brand, headText, status, close);
  ui.statusDot = statusDot;
  ui.statusText = statusText;

  const stats = el("div", "dmf-stats");
  const statShow = el("b");
  const statCollapse = el("b");
  const statHide = el("b");
  const statSpecs = [
    [statShow, "显示"],
    [statCollapse, "折叠"],
    [statHide, "隐藏"],
  ];
  for (const [value, name] of statSpecs) {
    const cell = el("div", "dmf-stat");
    const label = el("span");
    label.textContent = name;
    value.textContent = "0";
    cell.append(value, label);
    stats.appendChild(cell);
  }
  ui.stats = { show: statShow, collapse: statCollapse, hide: statHide };

  const tabs = el("div", "dmf-tabs");
  const tabDefs = [
    ["strategy", "标签策略"],
    ["folded", "折叠记录"],
    ["log", "反馈日志"],
  ];
  for (const [key, name] of tabDefs) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "dmf-tab";
    button.dataset.view = key;
    button.textContent = name;
    button.addEventListener("click", () => setTab(key));
    tabs.appendChild(button);
    ui.tabs.set(key, button);
  }

  const body = el("div", "dmf-body");

  const strategyView = el("div", "dmf-view");
  const typeSection = el("div", "dmf-section");
  typeSection.textContent = "每个标签的展示方式";
  strategyView.appendChild(typeSection);
  for (const label of TYPES) strategyView.appendChild(buildTypeRow(label));
  strategyView.appendChild(buildGlobalSettings());
  strategyView.appendChild(buildAdvanced());

  const foldedView = el("div", "dmf-view");
  const foldedList = el("div");
  foldedView.appendChild(foldedList);
  ui.foldedList = foldedList;

  const logView = el("div", "dmf-view");
  const logList = el("div");
  logView.appendChild(logList);
  ui.logList = logList;

  ui.views.set("strategy", strategyView);
  ui.views.set("folded", foldedView);
  ui.views.set("log", logView);
  body.append(strategyView, foldedView, logView);

  const toast = el("div", "dmf-toast");
  ui.toast = toast;

  panel.append(head, stats, tabs, body, toast);
  document.body.appendChild(panel);
  ui.panel = panel;
  setTab(state.tab);
  syncSettings();
  renderEntries();
  renderLog();
}

/**
 * Switch the panel between the strategy, folded and log tabs.
 * @param {"strategy"|"folded"|"log"} tab
 */
function setTab(tab) {
  state.tab = tab;
  for (const [key, button] of ui.tabs) button.classList.toggle("is-active", key === tab);
  for (const [key, view] of ui.views) view.classList.toggle("is-active", key === tab);
}

/**
 * Open or close the panel.
 * @param {boolean} [force]
 */
function togglePanel(force) {
  if (!ui.panel) return;
  const show = typeof force === "boolean" ? force : ui.panel.style.display === "none";
  ui.panel.style.display = show ? "flex" : "none";
  if (show) {
    studyRecordExpand("panel");
    renderEntries();
    renderLog();
    syncSettings();
    probeService();
  } else {
    closePopover();
  }
}

/**
 * Append one feedback event to the in-memory log.
 * @param {string} kind
 * @param {string} title
 * @param {string} detail
 */
function pushLog(kind, title, detail) {
  state.logEntries.unshift({
    kind,
    title,
    detail: String(detail || ""),
    at: Date.now(),
  });
  if (state.logEntries.length > 60) state.logEntries.length = 60;
  renderLog();
}

/**
 * Translate one outgoing feedback event into a log row.
 * @param {string} kind
 * @param {object} payload
 * @param {string} [dmid]
 */
function logFeedback(kind, payload, dmid) {
  if (kind === "prefs") return;
  const title = LOG_KINDS[kind] || kind;
  let detail = "";
  if (kind === "label_correction" && payload) {
    detail = labelName(payload.old_label) + " → " + labelName(payload.new_label) + " · " + (payload.text || "");
  } else if (kind === "policy_event" && payload) {
    detail = payload.action === "show_once" ? "恢复显示 · " + labelName(payload.label) : String(payload.action || "");
  }
  pushLog(kind, title, detail || dmid || "");
}

/** Render the feedback log tab. */
function renderLog() {
  if (!ui.logList) return;
  ui.logList.textContent = "";
  if (!state.logEntries.length) {
    const empty = el("div", "dmf-empty");
    empty.textContent = "还没有反馈事件。展开某条弹幕、改标签或调整策略后会记录在这里。";
    ui.logList.appendChild(empty);
    return;
  }
  for (const item of state.logEntries) {
    const row = el("div", "dmf-log");
    const time = el("span", "dmf-log-time");
    time.textContent = clockTime(item.at);
    const kind = el("span", "dmf-log-kind");
    kind.textContent = item.title;
    const text = el("span", "dmf-log-text");
    text.textContent = item.detail || "—";
    row.append(time, kind, text);
    ui.logList.appendChild(row);
  }
}

/**
 * Build one folded-list card.
 * @param {object} entry
 * @returns {HTMLElement}
 */
function entryCard(entry) {
  const card = el("div", "dmf-card");

  const head = el("div", "dmf-card-head");
  const time = el("span");
  time.textContent = formatTime(entry.time_ms);
  const actionChip = el("span", "dmf-chip");
  actionChip.textContent =
    entry.action === "hide" ? "已隐藏" : entry.action === "collapse" ? "已折叠" : "已显示";
  actionChip.classList.add(
    entry.action === "hide" ? "is-hide" : entry.action === "collapse" ? "is-collapse" : "is-show"
  );
  const labelChip = el("span", "dmf-chip");
  const dot = el("i");
  dot.style.background = LABEL_COLORS[entry.label] || LABEL_COLORS.other;
  labelChip.append(dot, document.createTextNode(entry.label_name + " · " + entry.label));
  const score = el("span", "dmf-score");
  score.textContent = Number(entry.score || 0).toFixed(2);
  head.append(time, actionChip, labelChip, score);

  const text = el("div", "dmf-card-text");
  text.textContent = entry.text;

  const meta = el("div", "dmf-card-meta");
  const reason = el("span");
  reason.textContent = "原因：" + reasonText(entry.reason);
  meta.appendChild(reason);
  if (entry.evidence && entry.evidence !== "user correction") {
    const evidence = el("span");
    evidence.textContent = "证据：" + entry.evidence;
    meta.appendChild(evidence);
  }

  const actions = el("div", "dmf-card-actions");
  if (entry.action !== "show") {
    const restore = document.createElement("button");
    restore.type = "button";
    restore.className = "dmf-btn";
    restore.textContent = "恢复显示";
    restore.title = "本条弹幕下次加载该片段时不再隐藏";
    restore.addEventListener("click", () => restoreEntry(entry));
    actions.append(restore);
  }
  const relabel = document.createElement("button");
  relabel.type = "button";
  relabel.className = "dmf-btn is-accent";
  relabel.textContent = "改标签";
  relabel.addEventListener("click", () => openPopover(entry, relabel));
  actions.append(relabel);

  card.append(head, text, meta, actions);
  return card;
}

/** Render the folded list from state.entries (newest first). */
function renderEntries() {
  syncTypeCounts();
  if (!ui.foldedList) return;
  ui.foldedList.textContent = "";
  const pool = state.pool;
  const limit = typeof MAX_PANEL_ROWS === "number" ? MAX_PANEL_ROWS : 200;
  const all = [];
  if (pool && pool.actions) {
    const seen = new Set();
    for (const fold of pool.folds) {
      if (all.length >= limit) break;
      if (!fold || !fold.dmid || seen.has(fold.dmid)) continue;
      seen.add(fold.dmid);
      const override = state.entries.get(fold.dmid);
      const row = override || fold;
      if (!row.label_name && typeof labelName === "function") row.label_name = labelName(row.label);
      all.push(row);
    }
    for (const entry of Array.from(state.entries.values()).reverse()) {
      if (all.length >= limit) break;
      if (!entry || !entry.dmid || seen.has(entry.dmid)) continue;
      seen.add(entry.dmid);
      all.push(entry);
    }
  } else {
    all.push(...Array.from(state.entries.values()).reverse());
  }
  const densityCount = all.filter((entry) => entry.density).length;
  const entries = all.filter((entry) => !entry.density);
  if (densityCount) {
    const summary = el("div", "dmf-empty");
    summary.textContent =
      "密度超限：" + densityCount + " 条弹幕因超出「窗口内上限」被" +
      (state.prefs.density.exceed === "hide" ? "隐藏" : "折叠") +
      "（不逐条列出）。可在「全局参数」里调大窗口上限或关闭密度控制。";
    ui.foldedList.appendChild(summary);
  }
  if (!entries.length) {
    const empty = el("div", "dmf-empty");
    empty.textContent = "暂无过滤记录。播放视频时会实时记录被隐藏或折叠的弹幕，可在这里恢复或改标签。";
    ui.foldedList.appendChild(empty);
    return;
  }
  for (const entry of entries) ui.foldedList.appendChild(entryCard(entry));
}

/**
 * Restore one filtered entry for this session.
 * @param {object} entry
 */
function restoreEntry(entry) {
  studyRecordRestore(entry.dmid, entry.label);
  state.showOnce.add(entry.dmid);
  if (state.pool && state.pool.actions) rebuildPoolActions(state.pool, poolPrefs());
  state.decisionsCache.clear();
  undoEntry(entry);
  entry.action = "show";
  entry.reason = "user_show";
  sendFeedback("policy_event", { action: "show_once", label: entry.label, reason: "user_show" }, entry.dmid);
  renderEntries();
  updateBadge();
  toast("已恢复 · 该片段下次加载时显示");
}

/**
 * Re-run the per-item decision for every recorded entry after a pref change.
 */
function reapplyPrefs() {
  for (const entry of state.entries.values()) {
    const decision = decide(
      resolveItem({ text: entry.text, time_ms: entry.time_ms }, { label: entry.label }),
      state.prefs
    );
    entry.reason = decision.reason;
    recountEntry(entry, decision.action);
  }
  if (state.pool && state.pool.ready) rebuildPoolActions(state.pool, poolPrefs());
  syncSettings();
  renderEntries();
  updateBadge();
  liveTick();
}

/**
 * Apply a corrected label to one entry and send it as feedback.
 * @param {object} entry
 * @param {string} newLabel
 */
function applyCorrection(entry, newLabel) {
  if (!newLabel || newLabel === entry.label) return;
  const oldLabel = entry.label;
  studyRecordCorrection(entry.dmid, entry.text, oldLabel, newLabel);
  state.overrides.set(entry.dmid, newLabel);
  state.decisionsCache.clear();
  sendFeedback(
    "label_correction",
    { text: entry.text, old_label: oldLabel, new_label: newLabel },
    entry.dmid
  );
  const decision = decide(
    resolveItem({ text: entry.text, time_ms: entry.time_ms }, { label: newLabel }),
    state.prefs
  );
  entry.label = decision.label;
  entry.label_name = labelName(decision.label);
  entry.score = decision.score;
  entry.reason = decision.reason;
  entry.evidence = "user correction";
  recountEntry(entry, decision.action);
  renderEntries();
  updateBadge();
  toast("已改为「" + labelName(newLabel) + "」");
}

/** Close the label correction popover if it is open. */
function closePopover() {
  if (ui.popover) {
    ui.popover.remove();
    ui.popover = null;
  }
  if (ui.popoverDocHandler) {
    document.removeEventListener("mousedown", ui.popoverDocHandler, true);
    ui.popoverDocHandler = null;
  }
}

/**
 * Open the label correction popover anchored to a button.
 * @param {object} entry
 * @param {HTMLElement} anchor
 */
function openPopover(entry, anchor) {
  closePopover();
  const pop = document.createElement("div");
  pop.id = "dmf-popover";
  for (const label of TYPES) {
    const button = document.createElement("button");
    button.type = "button";
    const dot = document.createElement("i");
    dot.style.background = LABEL_COLORS[label] || LABEL_COLORS.other;
    button.append(dot, document.createTextNode(labelName(label)));
    button.title = entry.label === label ? "当前标签" : "改为「" + labelName(label) + "」";
    button.addEventListener("click", () => {
      closePopover();
      applyCorrection(entry, label);
    });
    pop.appendChild(button);
  }
  document.body.appendChild(pop);
  const rect = anchor.getBoundingClientRect();
  const width = 214;
  const height = 132;
  const left = Math.max(8, Math.min(window.innerWidth - width - 8, rect.right - width));
  let top = rect.bottom + 6;
  if (top + height > window.innerHeight - 8) top = Math.max(8, rect.top - height - 6);
  pop.style.left = Math.round(left) + "px";
  pop.style.top = Math.round(top) + "px";
  ui.popover = pop;
  ui.popoverDocHandler = (event) => {
    if (!pop.contains(event.target)) closePopover();
  };
  setTimeout(() => {
    if (ui.popoverDocHandler) document.addEventListener("mousedown", ui.popoverDocHandler, true);
  }, 0);
}

/**
 * Show a short confirmation inside the panel.
 * @param {string} message
 */
function toast(message) {
  if (!ui.toast) return;
  ui.toast.textContent = message;
  ui.toast.classList.add("is-visible");
  if (ui.toastTimer) clearTimeout(ui.toastTimer);
  ui.toastTimer = setTimeout(() => {
    if (ui.toast) ui.toast.classList.remove("is-visible");
  }, 2200);
}

/** Create the UI once document.body exists. */
function ensureUi() {
  try {
    if (state.uiReady || !document.body) return;
    injectStyles();
    buildBadge();
    buildPanel();
    state.uiReady = true;
    updateBadge();
  } catch (error) {
    log("UI init failed", error);
  }
}

/**
 * Run a callback as soon as document.body is available.
 * @param {() => void} callback
 */
function whenBodyReady(callback) {
  if (document.body) {
    callback();
    return;
  }
  try {
    const observer = new MutationObserver(() => {
      if (document.body) {
        observer.disconnect();
        callback();
      }
    });
    observer.observe(document.documentElement || document, { childList: true, subtree: true });
  } catch (error) {
    /* fall back to DOMContentLoaded below */
  }
  document.addEventListener("DOMContentLoaded", callback, { once: true });
}

/**
 * Decode bytes into a byte-preserving latin-1 string so that text-mode XHR
 * consumers still see one character per protobuf byte.
 * @param {Uint8Array} bytes
 * @returns {string}
 */
function bytesToLatin1(bytes) {
  let out = "";
  const chunkSize = 8192;
  for (let start = 0; start < bytes.length; start += chunkSize) {
    out += String.fromCharCode.apply(null, bytes.subarray(start, start + chunkSize));
  }
  return out;
}

/**
 * Deliver rewritten bytes as a completed XHR: define the response properties,
 * provide response headers, and fire readystatechange/load/loadend.
 * @param {XMLHttpRequest} xhr
 * @param {{data: Uint8Array}} filtered
 * @param {object} segment
 */
function deliverXhr(xhr, filtered, segment) {
  const bytes = filtered.data;
  const responseType = String(xhr.responseType || "").toLowerCase();
  const buffer =
    bytes.byteOffset === 0 && bytes.byteLength === bytes.buffer.byteLength
      ? bytes.buffer
      : bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength);
  const headers =
    "content-type: application/octet-stream\r\ncontent-length: " +
    buffer.byteLength +
    "\r\n";
  const PageEvent = PAGE.Event || Event;
  try {
    Object.defineProperty(xhr, "readyState", { configurable: true, get: () => 4 });
    Object.defineProperty(xhr, "status", { configurable: true, get: () => 200 });
    Object.defineProperty(xhr, "statusText", { configurable: true, get: () => "OK" });
    Object.defineProperty(xhr, "responseURL", {
      configurable: true,
      get: () => (segment && segment.href) || "",
    });
    Object.defineProperty(xhr, "response", {
      configurable: true,
      get: () => {
        if (responseType === "arraybuffer") return buffer;
        if (responseType === "blob") {
          try {
            return new (PAGE.Blob || Blob)([bytes]);
          } catch (error) {
            return buffer;
          }
        }
        if (responseType === "json") return null;
        // responseType "" / "text": mimic a real XHR text response while
        // keeping every byte recoverable for protobuf parsers.
        return bytesToLatin1(bytes);
      },
    });
    Object.defineProperty(xhr, "responseText", {
      configurable: true,
      get: () =>
        responseType === "arraybuffer" || responseType === "blob"
          ? ""
          : bytesToLatin1(bytes),
    });
    xhr.getAllResponseHeaders = () => headers;
    xhr.getResponseHeader = (name) => {
      const key = String(name || "").toLowerCase();
      if (key === "content-type") return "application/octet-stream";
      if (key === "content-length") return String(buffer.byteLength);
      return null;
    };
    if (xhr.__dmfRequest) xhr.__dmfRequest.delivered = true;
  } catch (error) {
    log("xhr delivery failed", error);
    return;
  }
  for (const type of ["readystatechange", "load", "loadend"]) {
    try {
      xhr.dispatchEvent(new PageEvent(type));
    } catch (error) {
      try {
        const handler = xhr["on" + type];
        if (typeof handler === "function") handler.call(xhr, new PageEvent(type));
      } catch (inner) {
        log("xhr event dispatch failed", type, inner);
      }
    }
  }
}

/**
 * Handle one intercepted XHR send: fetch bytes via GM, filter, then either
 * deliver a synthetic response or fall back to the original send.
 * @param {XMLHttpRequest} xhr
 * @param {object} segment
 * @param {Function} originalSend
 * @param {IArguments|any[]} sendArgs
 */
function startXhrIntercept(xhr, segment, originalSend, sendArgs) {
  const request = xhr.__dmfRequest || {};
  let finished = false;
  const fallback = () => {
    if (finished || request.aborted) return;
    finished = true;
    try {
      originalSend.apply(xhr, sendArgs);
    } catch (error) {
      log("xhr fallback failed", error);
    }
  };
  // Record-only study conditions still intercept to count decisions, but the
  // original request is replayed so playback is never altered.
  const recordOnly = Boolean(state.study && state.study.recordOnly);
  if ((!recordOnly && (!state.prefs || !state.prefs.enabled)) || !hasGm()) {
    fallback();
    return;
  }
  (async () => {
    try {
      const bytes = await fetchSegmentBytes(segment.href);
      if (request.aborted) return;
      const filtered = await applySegment(bytes, segment);
      if (request.aborted) return;
      if (!filtered) {
        fallback();
        return;
      }
      finished = true;
      deliverXhr(xhr, filtered, segment);
    } catch (error) {
      log("xhr interception failed", error);
      fallback();
    }
  })();
}

/** Patch XMLHttpRequest.open/send/abort once, on the page realm. */
function installXhrHook() {
  if (state.xhrInstalled) return;
  const proto = PAGE.XMLHttpRequest && PAGE.XMLHttpRequest.prototype;
  if (!proto) return;
  state.xhrInstalled = true;
  const originalOpen = proto.open;
  const originalSend = proto.send;
  const originalAbort = proto.abort;
  proto.open = function open(method, url) {
    try {
      const raw = String(url);
      this.__dmfRequest = {
        method: String(method || "GET"),
        url: new URL(raw, location.href).href,
        aborted: false,
        delivered: false,
      };
    } catch (error) {
      this.__dmfRequest = { method: String(method || "GET"), url: String(url), aborted: false };
    }
    return originalOpen.apply(this, arguments);
  };
  proto.send = function send() {
    const request = this.__dmfRequest;
    const segment = request && request.url ? parseSegUrl(request.url) : null;
    if (!segment) return originalSend.apply(this, arguments);
    // json/document responses cannot be synthesized byte-exactly: fail open.
    const responseType = String(this.responseType || "").toLowerCase();
    if (responseType === "json" || responseType === "document") {
      log("xhr responseType unsupported, passing through", responseType);
      return originalSend.apply(this, arguments);
    }
    startXhrIntercept(this, segment, originalSend, arguments);
    return undefined;
  };
  proto.abort = function abort() {
    try {
      if (this.__dmfRequest) this.__dmfRequest.aborted = true;
    } catch (error) {
      /* ignore */
    }
    return originalAbort.apply(this, arguments);
  };
}

/**
 * Handle a fetch response for seg.so.
 * @param {Response} response
 * @param {object} segment
 * @returns {Promise<Response>}
 */
async function processFetchResponse(response, segment) {
  try {
    const buffer = await response.clone().arrayBuffer();
    const filtered = await applySegment(buffer, segment);
    if (!filtered) return response;
    const PageResponse = PAGE.Response || Response;
    return new PageResponse(filtered.data, {
      status: response.status,
      statusText: response.statusText,
      headers: response.headers,
    });
  } catch (error) {
    log("fetch interception failed", error);
    return response;
  }
}

/** Patch fetch once, on the page realm. */
function installFetchHook() {
  if (state.fetchInstalled) return;
  const target = PAGE;
  const originalFetch = target.fetch;
  if (typeof originalFetch !== "function") return;
  state.fetchInstalled = true;
  // Keep the unpatched fetch for the script's own requests (see pageFetchWithTimeout).
  try {
    state.nativeFetch = originalFetch.bind(target);
  } catch (error) {
    state.nativeFetch = null;
  }
  const wrappedFetch = function fetch(input, init) {
    let segment = null;
    try {
      segment = parseSegUrl(requestUrl(input));
    } catch (error) {
      segment = null;
    }
    const recordOnly = Boolean(state.study && state.study.recordOnly);
    if (!segment || (!recordOnly && (!state.prefs || !state.prefs.enabled))) {
      return originalFetch.apply(this, arguments);
    }
    return originalFetch.apply(this, arguments).then((response) => {
      if (!response || !response.ok) return response;
      return processFetchResponse(response, segment);
    });
  };
  try {
    target.fetch = wrappedFetch;
  } catch (error) {
    log("fetch hook install failed", error);
    return;
  }
  try {
    if (window.fetch !== wrappedFetch && typeof window.fetch === "function") {
      window.fetch = wrappedFetch;
    }
  } catch (error) {
    /* the page proxy may be read-only */
  }
}

/** Reset per-video state when the SPA navigates to another video. */
function resetVideoState() {
  state.entries.clear();
  state.counted.clear();
  state.decisionsCache.clear();
  state.failures.clear();
  state.showOnce.clear();
  state.overrides.clear();
  state.labelCounts.clear();
  state.labelCounted.clear();
  state.logEntries = [];
  if (state.pool && state.pool.bvid !== currentBvid()) {
    state.pool = null;
    hidePoolOverlay(null);
  }
  state.shown = 0;
  state.hidden = 0;
  state.collapsed = 0;
  if (state.pool && state.pool.ready) seedPoolPanel(state.pool);
  updateBadge();
  renderEntries();
  syncStatus();
}

/* ---------- live DOM safety net ----------
 * The segment hook only reaches danmaku the player has not downloaded yet:
 * a long video keeps its next segments queued in memory, so changing a
 * strategy mid-video would otherwise show no effect for minutes. This layer
 * watches the rendered danmaku DOM and applies the same policy to what is
 * already on screen, re-asking the service for labels it has not seen yet.
 */
const DM_NODE_CLASS = "bili-danmaku-x-dm";
const DM_ROOT_SELECTOR = ".bpx-player-row-dm-wrap, .bpx-player-dm-wrap";
const LIVE_TEXT_LIMIT = 800;
const LIVE_TICK_MS = 300;

/** Lazily create the live-filter bookkeeping. */
function ensureLiveState() {
  if (!state.dom) {
    state.dom = { labels: new Map(), seen: new WeakMap(), hidden: 0, collapsed: 0, seq: 0, running: false };
  }
  return state.dom;
}

/** @returns {Element[]} every rendered danmaku container currently on the page. */
function liveRoots() {
  const roots = [];
  try {
    for (const root of document.querySelectorAll(DM_ROOT_SELECTOR)) roots.push(root);
  } catch (error) {
    /* ignore */
  }
  return roots;
}

/** @returns {Element[]} every rendered danmaku node. */
function liveNodes() {
  const nodes = [];
  for (const root of liveRoots()) {
    try {
      for (const node of root.querySelectorAll("." + DM_NODE_CLASS)) nodes.push(node);
    } catch (error) {
      /* ignore */
    }
  }
  return nodes;
}

/**
 * Read the comment text of a rendered node, remembering the original text
 * while the node is collapsed by us.
 * @param {Element} node
 * @returns {string}
 */
function liveNodeText(node) {
  const stored = node.__dmfLiveText || "";
  const current = (node.textContent || "").trim();
  if (node.classList && node.classList.contains("dmf-live-collapsed")) return stored;
  if (current && current !== stored) {
    node.__dmfLiveText = current;
    return current;
  }
  return stored;
}

/**
 * Apply the current strategy to one rendered node.
 * @param {Element} node
 * @param {string} label
 * @param {number} score
 */
function applyLiveNode(node, label, score) {
  const live = ensureLiveState();
  const decision = decide({ label, score: typeof score === "number" ? score : 0.8 }, state.prefs);
  const action = state.prefs && state.prefs.enabled ? decision.action : "show";
  const previous = live.seen.get(node) || { action: "show", original: "" };
  if (action === "show") {
    if (previous.action === "collapse" && previous.original) {
      node.textContent = previous.original;
      node.classList.remove("dmf-live-collapsed");
    }
    live.seen.set(node, { action, original: previous.original });
    return;
  }
  if (action === "collapse") {
    if (previous.action !== "collapse") {
      const original = (node.textContent || "").trim();
      live.seen.set(node, { action, original });
      node.textContent = "已折叠·" + (LABELS_ZH[label] || "其他");
      node.classList.add("dmf-live-collapsed");
      live.collapsed += 1;
      updateBadge();
    }
    return;
  }
  if (previous.action !== "hide") live.hidden += 1;
  live.seen.set(node, { action, original: previous.original });
  node.remove();
  updateBadge();
}

/**
 * Ask the service for labels of texts the live layer has not seen yet.
 * @param {string[]} texts
 * @returns {Promise<boolean>} true when new labels arrived
 */
async function liveLabelTexts(texts) {
  const live = ensureLiveState();
  const missing = [];
  for (const text of texts) {
    if (text && !live.labels.has(text) && missing.indexOf(text) === -1) missing.push(text);
  }
  if (!missing.length) return false;
  const bvid = currentBvid();
  if (!bvid) return false;
  const dmids = [];
  const byDmid = {};
  for (const text of missing.slice(0, 120)) {
    live.seq += 1;
    const dmid = "live" + live.seq;
    dmids.push(dmid);
    byDmid[dmid] = text;
  }
  let payload;
  try {
    payload = await postJson("/api/v1/decisions", {
      bvid,
      dmids,
      texts: byDmid,
      prefs: state.prefs,
      ephemeral: true,
      labels: true,
    });
  } catch (error) {
    log("live labels failed", error);
    return false;
  }
  if (!payload || payload.ok !== true) return false;
  const labels = payload.labels && typeof payload.labels === "object" ? payload.labels : {};
  for (const dmid of dmids) {
    const entry = labels[dmid] || {};
    live.labels.set(byDmid[dmid], {
      label: typeof entry.label === "string" ? entry.label : "other",
      score: typeof entry.score === "number" ? entry.score : 0.5,
    });
  }
  while (live.labels.size > LIVE_TEXT_LIMIT) {
    const first = live.labels.keys().next();
    if (first.done) break;
    live.labels.delete(first.value);
  }
  return true;
}

/** One pass: label unknown rendered comments, then apply the strategy. */
async function liveTick() {
  // Record-only study conditions must never modify the rendered danmaku.
  if (state.study && state.study.recordOnly) return;
  const live = ensureLiveState();
  if (live.running) return;
  live.running = true;
  try {
    const nodes = liveNodes();
    if (!nodes.length) return;
    const unknown = [];
    for (const node of nodes) {
      const text = liveNodeText(node);
      if (!text) continue;
      const entry = live.labels.get(text);
      if (entry) applyLiveNode(node, entry.label, entry.score);
      else if (unknown.indexOf(text) === -1) unknown.push(text);
    }
    if (unknown.length && (await liveLabelTexts(unknown))) {
      for (const node of liveNodes()) {
        const text = liveNodeText(node);
        const entry = text ? live.labels.get(text) : null;
        if (entry) applyLiveNode(node, entry.label, entry.score);
      }
    }
  } catch (error) {
    log("live tick failed", error);
  } finally {
    live.running = false;
  }
}

/** Start the polling loop that keeps rendered danmaku in line with prefs. */
function startLiveFilter() {
  if (state.study && state.study.recordOnly) {
    log("live filter skipped: record-only study");
    return;
  }
  ensureLiveState();
  setInterval(() => {
    liveTick();
  }, LIVE_TICK_MS);
  log("live filter started");
}
/** Watch SPA navigation (Bilibili swaps videos without a full reload). */
function watchNavigation() {
  state.lastBvid = currentBvid();
  setInterval(() => {
    try {
      const bvid = currentBvid();
      // The player rewrites the query string while loading, so compare the
      // video identity instead of the full href, or every load wipes the
      // counters that were just recorded for this video.
      if (bvid && bvid !== state.lastBvid) {
        state.lastBvid = bvid;
        resetVideoState();
      }
    } catch (error) {
      /* ignore */
    }
  }, 1000);
}

/** Boot the userscript: load prefs, install hooks, then build the UI. */
function start() {
  try {
    if (PAGE.__dmfFilterStarted) return;
    PAGE.__dmfFilterStarted = true;
    state.study = initStudy();
    state.prefs = state.study ? state.study.prefs : loadPrefs();
    state.service = loadService();
    installXhrHook();
    installFetchHook();
    whenBodyReady(() => {
      // Study mode hides the UI until the examiner calls study.showUi().
      if (state.study && !state.study.uiVisible) return;
      ensureUi();
    });
    watchNavigation();
    startLiveFilter();
    probeService();
    try {
      PAGE.__dmfFilter = {
        state,
        parseSegment,
        planSegment,
        applySegment,
        currentBvid,
        parseSegUrl,
        liveTick,
        pool: () => state.pool,
        rebuildPoolActions,
        study: state.study
          ? {
              id: state.study.id,
              condition: state.study.condition,
              recordOnly: state.study.recordOnly,
              session: state.study.session,
              showUi: studyShowUi,
              hideUi: studyHideUi,
              export: studyExport,
              download: studyDownload,
            }
          : null,
        /** Test hook: merge a partial prefs patch, persist it, and re-apply. */
        setPrefs(patch) {
          state.prefs = mergePrefs(state.prefs, patch || {});
          persistPrefs("debug");
          reapplyPrefs();
          return state.prefs;
        },
      };
    } catch (error) {
      /* ignore */
    }
    log("started", state.prefs);
  } catch (error) {
    log("startup failed", error);
  }
}

start();


})();
