/**
 * Wire-codec tests against a real seg.so fixture (150 danmaku, first segment
 * of BV1GJ411x7h7), so parsing and rewriting are checked on production bytes.
 */

import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

import {
  encodeField,
  encodeVarint,
  filterSegment,
  iterElements,
  parseElement,
  parseSegment,
  replaceField,
} from "../lib/dmproto.js";
import { collapsedText } from "../lib/policy.js";

const FIXTURES = new URL("../../tests/fixtures/", import.meta.url);
const SAMPLE = new Uint8Array(readFileSync(new URL("seg_sample.bin", FIXTURES)));
const EXPECTED = JSON.parse(
  readFileSync(new URL("seg_sample.expected.json", FIXTURES), "utf8")
);

/**
 * Decode a protobuf varint from a byte array, mirroring the codec contract.
 * @param {Uint8Array} bytes
 * @returns {{value: bigint, length: number}}
 */
function decodeVarint(bytes) {
  let value = 0n;
  let shift = 0n;
  let length = 0;
  for (const byte of bytes) {
    length += 1;
    value |= BigInt(byte & 0x7f) << shift;
    if ((byte & 0x80) === 0) return { value, length };
    shift += 7n;
  }
  throw new Error("truncated varint");
}

/**
 * Collect the raw element payloads of a segment.
 * @param {Uint8Array} data
 * @returns {Uint8Array[]}
 */
function elementPayloads(data) {
  return Array.from(iterElements(data));
}

/**
 * Wrap raw element payloads into a top-level segment message.
 * @param {Uint8Array[]} payloads
 * @returns {Uint8Array}
 */
function wrapElements(payloads) {
  const chunks = payloads.map((payload) => encodeField(1, payload));
  const total = chunks.reduce((sum, chunk) => sum + chunk.length, 0);
  const output = new Uint8Array(total);
  let offset = 0;
  for (const chunk of chunks) {
    output.set(chunk, offset);
    offset += chunk.length;
  }
  return output;
}

test("fixture parses to the 150 expected elements", () => {
  const items = parseSegment(SAMPLE);
  assert.equal(items.length, 150);
  assert.equal(EXPECTED.elements.length, 150);
  items.forEach((item, index) => {
    const expected = EXPECTED.elements[index];
    assert.equal(item.id_str, expected.id_str);
    assert.equal(item.dmid, expected.id_str);
    assert.equal(BigInt(item.dmid), BigInt(expected.id_str));
    assert.equal(item.text, expected.content);
    assert.equal(item.time_ms, expected.progress);
    assert.equal(item.mode, expected.mode);
    assert.equal(item.uid, expected.mid_hash);
  });
});

test("dmid keeps 64-bit precision through BigInt", () => {
  const items = parseSegment(SAMPLE);
  for (const item of items) {
    assert.ok(Number(item.dmid) > Number.MAX_SAFE_INTEGER);
    assert.equal(BigInt(item.dmid), BigInt(item.id_str));
  }
  const unique = new Set(items.map((item) => item.dmid));
  assert.equal(unique.size, items.length);
});

test("iterElements yields one payload per top-level element", () => {
  const payloads = elementPayloads(SAMPLE);
  assert.equal(payloads.length, 150);
  assert.ok(payloads.every((payload) => payload instanceof Uint8Array));
  assert.equal(parseElement(payloads[0]).dmid, EXPECTED.elements[0].id_str);
});

test("empty drop and collapse sets leave bytes identical", () => {
  const result = filterSegment(SAMPLE, new Set(), new Map());
  assert.equal(result.hidden, 0);
  assert.equal(result.collapsed, 0);
  assert.deepEqual(Array.from(result.data), Array.from(SAMPLE));
  assert.equal(parseSegment(result.data).length, 150);
});

test("drop and collapse rewrite exactly the selected dmids", () => {
  const items = parseSegment(SAMPLE);
  const drop = new Set([items[0].dmid, items[3].dmid, items[20].dmid, items[77].dmid, items[149].dmid]);
  const labels = ["attack", "spoiler", "irrelevant_repetition", "conflict", "other"];
  const collapse = new Map([
    [items[1].dmid, labels[0]],
    [items[2].dmid, labels[1]],
    [items[10].dmid, labels[2]],
    [items[50].dmid, labels[3]],
    [items[100].dmid, labels[4]],
  ]);
  const collapseTexts = new Map(
    Array.from(collapse.entries()).map(([dmid, label]) => [dmid, collapsedText(label)])
  );
  const result = filterSegment(SAMPLE, drop, collapseTexts);
  assert.equal(result.hidden, 5);
  assert.equal(result.collapsed, 5);

  const rewritten = parseSegment(result.data);
  assert.equal(rewritten.length, 145);
  assert.equal(elementPayloads(result.data).length, 145);
  const byDmid = new Map(rewritten.map((item) => [item.dmid, item]));
  for (const dmid of drop) assert.equal(byDmid.has(dmid), false);
  for (const [dmid, text] of collapseTexts) assert.equal(byDmid.get(dmid).text, text);
  for (const item of items) {
    if (drop.has(item.dmid) || collapseTexts.has(item.dmid)) continue;
    const kept = byDmid.get(item.dmid);
    assert.ok(kept);
    assert.equal(kept.text, item.text);
    assert.equal(kept.time_ms, item.time_ms);
    assert.equal(kept.mode, item.mode);
    assert.equal(kept.uid, item.uid);
  }
});

test("collapse only changes field 7 and keeps the remaining fields", () => {
  const payloads = elementPayloads(SAMPLE);
  const original = parseElement(payloads[0]);
  const replaced = replaceField(payloads[0], 7, new TextEncoder().encode("【已折叠·攻击】"));
  const parsed = parseElement(replaced);
  assert.equal(parsed.text, "【已折叠·攻击】");
  assert.equal(parsed.dmid, original.dmid);
  assert.equal(parsed.time_ms, original.time_ms);
  assert.equal(parsed.mode, original.mode);
  assert.equal(parsed.uid, original.uid);
  assert.equal(parsed.cid, original.cid);
  assert.equal(parsed.id_str, original.id_str);
  // Other elements are untouched when only one payload is rewritten.
  const filtered = filterSegment(wrapElements(payloads), new Set(), new Map([[original.dmid, "x"]]));
  assert.equal(parseSegment(filtered.data).length, 150);
});

test("non-element top-level fields are preserved by filterSegment", () => {
  const prefix = encodeField(99, new Uint8Array([1, 2, 3]));
  const combined = new Uint8Array(prefix.length + SAMPLE.length);
  combined.set(prefix, 0);
  combined.set(SAMPLE, prefix.length);
  const items = parseSegment(combined);
  assert.equal(items.length, 150);
  const result = filterSegment(combined, new Set([items[0].dmid]), new Map());
  assert.deepEqual(Array.from(result.data.slice(0, prefix.length)), Array.from(prefix));
  assert.equal(parseSegment(result.data).length, 149);
});

test("encodeField and encodeVarint match the protobuf wire format", () => {
  assert.deepEqual(Array.from(encodeVarint(0)), [0]);
  assert.deepEqual(Array.from(encodeVarint(127)), [0x7f]);
  assert.deepEqual(Array.from(encodeVarint(128)), [0x80, 0x01]);
  assert.deepEqual(Array.from(encodeVarint(300)), [0xac, 0x02]);
  assert.deepEqual(Array.from(encodeField(1, new Uint8Array([0x61, 0x62, 0x63]))), [
    0x0a, 0x03, 0x61, 0x62, 0x63,
  ]);
  const big = 34576873522987015n;
  const encoded = encodeVarint(big);
  const decoded = decodeVarint(encoded);
  assert.equal(decoded.value, big);
  assert.equal(decoded.length, encoded.length);
  assert.equal(BigInt(parseSegment(SAMPLE)[0].dmid), big);
});

test("empty text elements are skipped by parseSegment", () => {
  const element = new Uint8Array([
    0x08, 0x01,
    0x10, 0x2a,
    0x3a, 0x00,
  ]);
  const segment = encodeField(1, element);
  assert.equal(parseSegment(segment).length, 0);
  assert.equal(iterElements(segment).next().done, false);
});

test("truncated data is rejected instead of producing garbage", () => {
  assert.throws(() => parseSegment(new Uint8Array([0x0a, 0x05, 0x01])), /truncated|invalid/);
  assert.throws(() => filterSegment(new Uint8Array([0x0a, 0x05, 0x01]), new Set(), new Map()), /truncated|invalid/);
});

test("accepts ArrayBuffer input as well as Uint8Array", () => {
  const buffer = SAMPLE.buffer.slice(SAMPLE.byteOffset, SAMPLE.byteOffset + SAMPLE.byteLength);
  assert.equal(parseSegment(buffer).length, 150);
  assert.equal(filterSegment(buffer, new Set(), new Map()).hidden, 0);
});
