/**
 * Print the local linear scorer output for one danmaku text.
 *
 * Usage::
 *
 *   node extension/tools/score-cli.mjs <model.json> '<text>'
 *
 * Prints {"scores": {"<tag>": <probability>, ...}, "top": "<tag>"} so the
 * JavaScript scorer can be compared with the Python one on the same model.
 * When the text argument is omitted the text is read from stdin.
 */

import { readFileSync } from "node:fs";

import { LinearScorer } from "../lib/linear.js";

/** @returns {string} standard input decoded as UTF-8 minus one trailing newline */
function readStdin() {
  const text = readFileSync(0, "utf8");
  return text.replace(/\r?\n$/, "");
}

/**
 * Score one text and print the JSON payload.
 *
 * @param {string[]} argv process.argv
 * @returns {number} process exit code
 */
function main(argv) {
  const modelPath = argv[2];
  if (!modelPath) {
    process.stderr.write("usage: node score-cli.mjs <model.json> '<text>'\n");
    return 2;
  }
  const text = argv[3] === undefined ? readStdin() : argv[3];
  const rawModel = JSON.parse(readFileSync(modelPath, "utf8"));
  const scorer = new LinearScorer(rawModel);
  const scores = scorer.scores(text);
  const top = scorer.predict(text).tag;
  process.stdout.write(JSON.stringify({ scores, top }) + "\n");
  return 0;
}

try {
  process.exitCode = main(process.argv);
} catch (error) {
  process.stderr.write("score-cli: " + String(error && error.message ? error.message : error) + "\n");
  process.exitCode = 1;
}
