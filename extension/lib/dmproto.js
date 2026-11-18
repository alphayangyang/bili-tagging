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
export function encodeVarint(value) {
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
export function encodeField(number, payload) {
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
export function* iterElements(data) {
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
export function parseElement(raw) {
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
export function parseSegment(data) {
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
export function replaceField(raw, number, payload) {
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
export function filterSegment(data, drop, collapse) {
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
