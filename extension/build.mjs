/**
 * Build the single-file Tampermonkey userscript.
 *
 * The lib modules are ESM so the node test runner can import them directly; here
 * their top-level export keywords and sibling import lines are stripped and the
 * files are concatenated in dependency order inside one strict-mode IIFE. The
 * output starts with the ==UserScript== metadata block.
 *
 * Usage: node extension/build.mjs
 */

import { readFile, writeFile, mkdir } from "node:fs/promises";
import { fileURLToPath } from "node:url";

const ROOT = new URL("./", import.meta.url);
const SOURCES = [
  "lib/dmproto.js",
  "lib/policy.js",
  "lib/linear.js",
  "lib/pool.js",
  "lib/poolweb.js",
  "lib/study.js",
  "src/main.js",
];
const OUTPUT = "dist/bili-danmaku-filter.user.js";

/**
 * Read a project-relative text file.
 * @param {string} relative
 * @returns {Promise<string>}
 */
async function readSource(relative) {
  return readFile(new URL(relative, ROOT), "utf8");
}

/**
 * Imports of sibling lib modules. Such modules are concatenated in dependency
 * order (pool.js after dmproto.js and policy.js), so the import lines are
 * dropped here while keeping the same file importable by `node --test`.
 */
const SIBLING_IMPORT = /^import\s*\{[^}]*\}\s*from\s*"\.\/[A-Za-z0-9_.-]+\.js";?[ \t]*$/gm;

/**
 * Strip top-level export keywords and sibling import statements so the
 * concatenated output is a plain classic script.
 * @param {string} relative
 * @param {string} source
 * @returns {string}
 */
function toClassicScript(relative, source) {
  const stripped = source.replace(SIBLING_IMPORT, "");
  if (/^\s*import\s/m.test(stripped)) {
    throw new Error(relative + " must only import sibling lib modules");
  }
  if (/^\s*export\s+(default|\{)/m.test(stripped)) {
    throw new Error(relative + " must only use inline export declarations");
  }
  return stripped.replace(/^export\s+/gm, "");
}

/**
 * Read the version from package.json.
 * @returns {Promise<string>}
 */
async function readVersion() {
  const manifest = JSON.parse(await readSource("package.json"));
  if (!manifest.version) throw new Error("package.json is missing a version");
  return String(manifest.version);
}

/**
 * Build the ==UserScript== metadata block.
 * @param {string} version
 * @returns {string}
 */
function metadataBlock(version) {
  return [
    "// ==UserScript==",
    "// @name         Bilibili 弹幕过滤（本地标签服务）",
    "// @namespace    https://github.com/bili-tagging",
    "// @version      " + version,
    "// @description  按本地标签服务过滤 B 站弹幕：默认隐藏攻击/争论/剧透，折叠无意义重复，并按密度折叠",
    "// @match        https://www.bilibili.com/video/*",
    "// @run-at       document-start",
    "// @grant        GM_xmlhttpRequest",
    "// @connect      127.0.0.1",
    "// @connect      api.bilibili.com",
    "// ==/UserScript==",
    "",
  ].join("\n");
}

/**
 * Build the userscript and write it to dist/.
 * @returns {Promise<string>} the output path
 */
async function build() {
  const version = await readVersion();
  const parts = [];
  for (const relative of SOURCES) {
    const source = await readSource(relative);
    parts.push("/* ---- " + relative + " ---- */\n" + toClassicScript(relative, source));
  }
  const body = [
    metadataBlock(version),
    "(() => {",
    "  'use strict';",
    "",
    parts.join("\n"),
    "",
    "})();",
    "",
  ].join("\n");
  const output = new URL(OUTPUT, ROOT);
  await mkdir(new URL("dist/", ROOT), { recursive: true });
  await writeFile(output, body, "utf8");
  return fileURLToPath(output);
}

const path = await build();
console.log("built " + path);
