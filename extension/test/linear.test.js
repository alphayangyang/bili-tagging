/**
 * Tests for the offline hashed TF-IDF linear scorer (task-4).
 *
 * The fixtures build tiny synthetic models with the same JSON shape Python
 * writes, so the contract (feature extraction, idf fallback, L2 norm, cosine
 * centroid + softmax) is pinned without depending on Python artefacts.
 */

import test from "node:test";
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { fileURLToPath } from "node:url";
import { join } from "node:path";

import {
  LinearScorer,
  crc32,
  loadModelFromJson,
  ngramCounts,
  ngramsOf,
  preprocess,
} from "../lib/linear.js";

const DIM = 10007;

/**
 * Convert a Map with numeric keys into the plain object JSON carries.
 * @param {Map<number, number>} vector
 * @returns {Record<string, number>}
 */
function mapToObject(vector) {
  const object = {};
  for (const [bucket, value] of vector) {
    object[String(bucket)] = value;
  }
  return object;
}

/**
 * Build one L2-normalised document vector with the documented feature code.
 * @param {string} text
 * @param {{dim?: number, ngram?: number[], idf?: Map<number, number>, defaultIdf?: number}} [options]
 * @returns {Map<number, number>}
 */
function docVector(text, options = {}) {
  const { dim = DIM, ngram = [1, 3], idf = new Map(), defaultIdf = 1 } = options;
  const counts = new Map();
  for (const [gram, count] of ngramCounts(text, ngram)) {
    const bucket = crc32(gram) % dim;
    counts.set(bucket, (counts.get(bucket) ?? 0) + count);
  }
  const raw = new Map();
  for (const [bucket, count] of counts) {
    raw.set(bucket, (1 + Math.log(count)) * (idf.get(bucket) ?? defaultIdf));
  }
  let squares = 0;
  for (const value of raw.values()) {
    squares += value * value;
  }
  const norm = Math.sqrt(squares);
  const normalised = new Map();
  if (norm > 0) {
    for (const [bucket, value] of raw) {
      normalised.set(bucket, value / norm);
    }
  }
  return normalised;
}

/**
 * Build a centroid model from one list of training texts per class.
 * @param {Record<string, string[]>} training
 * @param {{dim?: number, ngram?: number[], idf?: Map<number, number>, defaultIdf?: number}} [options]
 * @returns {object}
 */
function centroidModel(training, options = {}) {
  const { dim = DIM, ngram = [1, 3], idf = new Map(), defaultIdf = 1 } = options;
  const classes = Object.keys(training);
  const weights = {};
  for (const tag of classes) {
    const texts = training[tag];
    const total = new Map();
    for (const text of texts) {
      for (const [bucket, value] of docVector(text, { dim, ngram, idf, defaultIdf })) {
        total.set(bucket, (total.get(bucket) ?? 0) + value);
      }
    }
    const averaged = new Map();
    for (const [bucket, value] of total) {
      averaged.set(bucket, value / Math.max(texts.length, 1));
    }
    weights[tag] = mapToObject(averaged);
  }
  return {
    schema: 1,
    kind: "centroid",
    dim,
    ngram,
    classes,
    default_idf: defaultIdf,
    idf: mapToObject(idf),
    weights,
  };
}

/**
 * Stable softmax, used by tests to verify the scorer formula.
 * @param {number[]} logits
 * @returns {number[]}
 */
function softmax(logits) {
  const max = Math.max(...logits);
  const exps = logits.map((value) => Math.exp(value - max));
  const total = exps.reduce((sum, value) => sum + value, 0);
  return exps.map((value) => value / total);
}

/**
 * Deep-copy a model through JSON like the real artefact is loaded.
 * @param {object} model
 * @returns {object}
 */
function throughJson(model) {
  return JSON.parse(JSON.stringify(model));
}

/**
 * Dot product of a normalised document with one class weight vector.
 * @param {Map<number, number>} features
 * @param {Map<number, number>} weights
 * @returns {number}
 */
function dot(features, weights) {
  let total = 0;
  for (const [bucket, value] of features) {
    const weight = weights.get(bucket);
    if (weight !== undefined) {
      total += value * weight;
    }
  }
  return total;
}

test("crc32 matches zlib.crc32 reference values", () => {
  assert.equal(crc32(""), 0);
  assert.equal(crc32("hello"), 907060870);
  assert.equal(crc32("hello world"), 222957957);
  assert.equal(crc32("a"), 3904355907);
  assert.equal(crc32(" "), 3916222277);
  assert.equal(crc32("弹幕"), 1481486310);
  assert.equal(crc32("哈哈哈"), 1656376872);
  assert.equal(crc32(new TextEncoder().encode("hello")), 907060870);
  assert.equal(crc32([104, 101, 108, 108, 111]), 907060870);
  assert.throws(() => crc32(42), TypeError);
});

test("preprocess pads with spaces, trims and lowercases", () => {
  assert.equal(preprocess("  AbC	"), " abc ");
  assert.equal(preprocess(""), "  ");
  assert.equal(preprocess("弹幕"), " 弹幕 ");
});

test("ngramsOf emits every 1-3 gram in length-major order", () => {
  assert.deepEqual(ngramsOf("Ab"), [
    " ", "a", "b", " ",
    " a", "ab", "b ",
    " ab", "ab ",
  ]);
  assert.deepEqual(ngramsOf("Ab", [1, 1]), [" ", "a", "b", " "]);
  assert.deepEqual(ngramsOf("Ab", [2, 2]), [" a", "ab", "b "]);
});

test("ngramCounts counts repeated grams", () => {
  const counts = ngramCounts("Ab");
  assert.equal(counts.get(" "), 2);
  assert.equal(counts.get("a"), 1);
  assert.equal(counts.get("ab "), 1);
  assert.equal(counts.size, 8);
  let total = 0;
  for (const value of counts.values()) {
    total += value;
  }
  assert.equal(total, 9);
});

test("loadModelFromJson normalises buckets and applies defaults", () => {
  const loaded = loadModelFromJson({
    kind: "softmax",
    dim: 128,
    classes: ["a", "b"],
    default_idf: 2,
    idf: { "5": 1.5 },
    weights: { a: { "7": 0.5 }, b: {} },
  });
  assert.equal(loaded.kind, "softmax");
  assert.equal(loaded.dim, 128);
  assert.deepEqual(loaded.classes, ["a", "b"]);
  assert.equal(loaded.schema, 1);
  assert.equal(loaded.defaultIdf, 2);
  assert.deepEqual([...loaded.idf.entries()], [[5, 1.5]]);
  assert.deepEqual([...loaded.weights.get("a").entries()], [[7, 0.5]]);
  assert.deepEqual([...loaded.weights.get("b").entries()], []);
  assert.equal(loadModelFromJson({
    kind: "softmax",
    classes: ["a"],
    weights: { a: {} },
  }).dim, 262144);
});

test("loadModelFromJson rejects malformed models", () => {
  assert.throws(() => loadModelFromJson(null), TypeError);
  assert.throws(() => loadModelFromJson({ classes: ["a"], weights: { a: {} } }), TypeError);
  assert.throws(
    () => loadModelFromJson({ kind: "tree", classes: ["a"], weights: { a: {} } }),
    TypeError,
  );
  assert.throws(
    () => loadModelFromJson({ kind: "softmax", dim: 0, classes: ["a"], weights: { a: {} } }),
    TypeError,
  );
  assert.throws(
    () => loadModelFromJson({ kind: "softmax", classes: [], weights: {} }),
    TypeError,
  );
  assert.throws(
    () => loadModelFromJson({ kind: "softmax", classes: ["a", "a"], weights: { a: {} } }),
    TypeError,
  );
  assert.throws(
    () => loadModelFromJson({
      kind: "softmax",
      classes: ["a"],
      ngram: [3, 1],
      weights: { a: {} },
    }),
    TypeError,
  );
  assert.throws(
    () => loadModelFromJson({
      kind: "softmax",
      classes: ["a"],
      weights: { a: { "x": 1 } },
    }),
    TypeError,
  );
});

test("idf lookup falls back to default_idf and uses tf = 1 + ln(count)", () => {
  const bucketFor = (gram) => crc32(gram) % DIM;
  const spaceBucket = bucketFor(" ");
  const aBucket = bucketFor("a");
  const bBucket = bucketFor("b");
  assert.notEqual(aBucket, bBucket);
  assert.notEqual(aBucket, spaceBucket);
  assert.notEqual(bBucket, spaceBucket);
  const idf = new Map([[aBucket, 3.5]]);
  const loaded = loadModelFromJson({
    kind: "softmax",
    dim: DIM,
    ngram: [1, 1],
    classes: ["a"],
    default_idf: 0.25,
    idf: mapToObject(idf),
    weights: { a: {} },
  });
  const scorer = new LinearScorer(loaded);
  const weights = scorer.weights("ab");
  assert.equal(weights.get(aBucket), 3.5);
  assert.equal(weights.get(bBucket), 0.25);
  assert.equal(weights.get(spaceBucket), (1 + Math.log(2)) * 0.25);
});

test("bucket counts drive tf, so hash collisions share one tf term", () => {
  const model = {
    schema: 1,
    kind: "softmax",
    dim: 1,
    ngram: [1, 1],
    classes: ["a"],
    default_idf: 1,
    idf: {},
    weights: { a: {} },
  };
  const scorer = new LinearScorer(throughJson(model));
  // "ab" normalises to " ab " with 1-grams ' ', 'a', 'b', ' ' => bucket 0 has 4.
  assert.deepEqual([...scorer.weights("ab").entries()], [[0, 1 + Math.log(4)]]);
  assert.deepEqual([...scorer.features("ab").entries()], [[0, 1]]);
});

test("loadModelFromJson accepts Python's LinearTagger.to_dict shape", () => {
  const loaded = loadModelFromJson({
    schema: 1,
    kind: "centroid",
    labels: ["humor", "attack"],
    vectorizer: {
      dim: 1024,
      ngram: [1, 3],
      n_docs: 9,
      idf: { "5": 1.5 },
    },
    weights: { humor: { "7": 0.4 }, attack: { "9": -0.2 } },
  });
  assert.deepEqual(loaded.classes, ["humor", "attack"]);
  assert.equal(loaded.dim, 1024);
  assert.equal(loaded.defaultIdf, Math.log(1 + 9) + 1);
  assert.deepEqual([...loaded.idf.entries()], [[5, 1.5]]);
  assert.deepEqual([...loaded.weights.get("humor").entries()], [[7, 0.4]]);
  assert.deepEqual([...loaded.weights.get("attack").entries()], [[9, -0.2]]);
});

test("features are L2 normalised", () => {
  const model = centroidModel({ humor: ["哈哈哈"], attack: ["去死吧"] });
  const scorer = new LinearScorer(throughJson(model));
  const features = scorer.features("哈哈哈");
  let squares = 0;
  for (const value of features.values()) {
    squares += value * value;
  }
  assert.ok(Math.abs(squares - 1) < 1e-12, "squared norm is " + squares);
  const raw = scorer.weights("哈哈哈");
  let rawSquares = 0;
  for (const value of raw.values()) {
    rawSquares += value * value;
  }
  assert.ok(rawSquares > 1, "raw tf-idf vector should not be normalised");
  const emptyFeatures = scorer.features("");
  let emptySquares = 0;
  for (const value of emptyFeatures.values()) {
    emptySquares += value * value;
  }
  assert.ok(Math.abs(emptySquares - 1) < 1e-12);
});

test("centroid scoring ranks the matching class higher", () => {
  const model = centroidModel({
    humor: ["哈哈哈", "笑死我了", "哈哈哈哈"],
    attack: ["去死吧", "垃圾玩意", "你是个垃圾"],
  });
  const scorer = new LinearScorer(throughJson(model));
  const humorScores = scorer.scores("哈哈哈");
  assert.ok(humorScores.humor > humorScores.attack);
  assert.ok(humorScores.humor > 0.5);
  assert.equal(scorer.predict("哈哈哈").tag, "humor");
  const attackScores = scorer.scores("去死吧");
  assert.ok(attackScores.attack > attackScores.humor);
  assert.ok(attackScores.attack > 0.5);
  assert.equal(scorer.predict("去死吧").tag, "attack");
  assert.ok(scorer.scores("笑死").humor > scorer.scores("今天天气不错").humor);
});

test("centroid scores are softmax(dot * 10), like the Python scorer", () => {
  const model = centroidModel({
    humor: ["哈哈哈", "笑死我了"],
    attack: ["去死吧", "垃圾玩意"],
  });
  const scorer = new LinearScorer(throughJson(model));
  const text = "哈哈哈 笑死";
  const features = scorer.features(text);
  const logits = scorer.model.classes.map((tag) => dot(features, scorer.model.weights.get(tag)) * 10);
  const expected = softmax(logits);
  const actual = scorer.scores(text);
  scorer.model.classes.forEach((tag, index) => {
    assert.ok(Math.abs(actual[tag] - expected[index]) < 1e-12);
  });
  // Python keeps the raw dot product: a prototype's own length is not undone.
  const scaled = new LinearScorer(throughJson({
    ...model,
    weights: { humor: model.weights.humor, attack: model.weights.attack },
  }));
  assert.deepEqual(scaled.scores(text), actual);
});

test("softmax models use raw dot products as logits", () => {
  const xBucket = crc32("x") % DIM;
  const model = {
    schema: 1,
    kind: "softmax",
    dim: DIM,
    ngram: [1, 3],
    classes: ["a", "b"],
    default_idf: 1,
    idf: {},
    weights: {
      a: { [String(xBucket)]: 1.2 },
      b: { [String(xBucket)]: 0.1 },
    },
  };
  const scorer = new LinearScorer(throughJson(model));
  const features = scorer.features("x");
  const logits = ["a", "b"].map((tag) => dot(features, scorer.model.weights.get(tag)));
  const expected = softmax(logits);
  const actual = scorer.scores("x");
  assert.ok(Math.abs(actual.a - expected[0]) < 1e-12);
  assert.ok(Math.abs(actual.b - expected[1]) < 1e-12);
  assert.ok(actual.a > actual.b);
  assert.equal(scorer.predict("x").tag, "a");
});

test("empty text and empty weights do not crash", () => {
  const emptyModel = {
    schema: 1,
    kind: "centroid",
    dim: DIM,
    ngram: [1, 3],
    classes: ["a", "b"],
    default_idf: 1,
    idf: {},
    weights: { a: {}, b: {} },
  };
  const emptyScorer = new LinearScorer(throughJson(emptyModel));
  assert.deepEqual(emptyScorer.scores(""), { a: 0.5, b: 0.5 });
  assert.deepEqual(emptyScorer.predict(""), { tag: "a", score: 0.5 });
  const model = centroidModel({ humor: ["哈哈哈"], attack: ["去死吧"] });
  const scorer = new LinearScorer(throughJson(model));
  const scores = scorer.scores("");
  assert.ok(Math.abs(scores.humor + scores.attack - 1) < 1e-12);
  for (const value of Object.values(scores)) {
    assert.ok(value >= 0 && value <= 1);
  }
});

test("score-cli prints scores and top tag as JSON", () => {
  const model = centroidModel({
    humor: ["哈哈哈", "笑死我了"],
    attack: ["去死吧", "垃圾玩意"],
  });
  const directory = mkdtempSync(join(tmpdir(), "dmf-score-cli-"));
  const modelPath = join(directory, "model.json");
  writeFileSync(modelPath, JSON.stringify(model), "utf8");
  try {
    const cli = fileURLToPath(new URL("../tools/score-cli.mjs", import.meta.url));
    const result = spawnSync(process.execPath, [cli, modelPath, "哈哈哈"], {
      encoding: "utf8",
    });
    assert.equal(result.status, 0, result.stderr);
    const payload = JSON.parse(result.stdout);
    assert.deepEqual(Object.keys(payload.scores), ["humor", "attack"]);
    assert.equal(payload.top, "humor");
    const scorer = new LinearScorer(throughJson(model));
    const expected = scorer.scores("哈哈哈");
    assert.ok(Math.abs(payload.scores.humor - expected.humor) < 1e-12);
  } finally {
    rmSync(directory, { recursive: true, force: true });
  }
});

test("score-cli rejects a missing model file", () => {
  const cli = fileURLToPath(new URL("../tools/score-cli.mjs", import.meta.url));
  const result = spawnSync(process.execPath, [cli, "/nonexistent/model.json", "text"], {
    encoding: "utf8",
  });
  assert.notEqual(result.status, 0);
  assert.match(result.stderr, /model/i);
});
