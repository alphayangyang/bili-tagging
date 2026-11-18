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
