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
