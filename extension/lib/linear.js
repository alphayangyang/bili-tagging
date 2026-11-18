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
export function crc32(input) {
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
export function preprocess(text) {
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
export function ngramsOf(text, range = DEFAULT_NGRAM) {
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
export function ngramCounts(text, range = DEFAULT_NGRAM) {
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
export function loadModelFromJson(model) {
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
export class LinearScorer {
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
