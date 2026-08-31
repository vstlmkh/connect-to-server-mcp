#!/usr/bin/env node
"use strict";

/**
 * Plugin entry point.
 *
 * The plugin cache only ever contains this directory, so everything the plugin
 * needs must live here — it starts the published npm package rather than any
 * path inside the repository.
 *
 * The spawn deliberately runs from a neutral directory: npm refuses to resolve
 * `npx connect-to-server-mcp` while the working directory is a checkout of that
 * same package (it assumes a local node_modules/.bin link that a clone does not
 * have), which would otherwise break the plugin for anyone working inside this
 * project. stdio is inherited untouched so the MCP stream stays byte-for-byte.
 */

const { spawn } = require("node:child_process");
const os = require("node:os");

const PACKAGE = "connect-to-server-mcp@0.1.3";
const npx = process.platform === "win32" ? "npx.cmd" : "npx";

const child = spawn(npx, ["-y", PACKAGE], {
  cwd: os.tmpdir(),
  stdio: "inherit",
  env: process.env,
  shell: process.platform === "win32",
});

child.on("error", (error) => {
  process.stderr.write(
    `connect-to-server plugin: could not start ${PACKAGE} via ${npx} (${error.message}).\n` +
      "Node.js 18+ and npm must be on PATH.\n"
  );
  process.exit(1);
});

child.on("exit", (code, signal) => {
  process.exit(signal ? 1 : code ?? 1);
});

for (const signal of ["SIGINT", "SIGTERM"]) {
  process.on(signal, () => child.kill(signal));
}
