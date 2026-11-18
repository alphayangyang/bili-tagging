/**
 * Aggregator entry point for the test directory.
 *
 * Node's test runner resolves a directory argument such as
 * `node --test extension/test/` to a single entry module. Without this file
 * that lookup fails ("Cannot find module .../extension/test"), so the module
 * simply imports every test file and lets them register with node:test.
 *
 * Keep this list in sync when adding test files.
 */

import "./dmproto.test.js";
import "./policy.test.js";
import "./density.test.js";
import "./linear.test.js";
import "./study.test.js";
import "./pool.test.js";
