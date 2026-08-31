#!/usr/bin/env node
"use strict";

/**
 * Launcher and installer for connect-to-server-mcp.
 *
 *   npx connect-to-server-mcp             start the MCP server on stdio
 *   npx connect-to-server-mcp install     register it with Claude Code / Claude Desktop
 *   npx connect-to-server-mcp doctor      report what the launcher would use
 *
 * The server itself is Python; this wrapper only finds (or provisions) a Python
 * runtime that has the package installed, then hands stdio over to it.
 */

const { spawnSync } = require("node:child_process");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

const PKG_ROOT = path.resolve(__dirname, "..");
const PKG = require(path.join(PKG_ROOT, "package.json"));
const SERVER_NAME = "connect-to-server";
const BIN_NAME = "connect-to-server-mcp";
const IS_WINDOWS = process.platform === "win32";

function which(command) {
  const probe = IS_WINDOWS
    ? spawnSync("where", [command], { encoding: "utf8" })
    : spawnSync("command", ["-v", command], { encoding: "utf8", shell: true });
  if (probe.status !== 0 || !probe.stdout) return null;
  return probe.stdout.split(/\r?\n/)[0].trim() || null;
}

/** True when running from a git checkout rather than an installed npm tarball. */
function isSourceCheckout() {
  return (
    fs.existsSync(path.join(PKG_ROOT, "pyproject.toml")) &&
    fs.existsSync(path.join(PKG_ROOT, "src", "connect_to_server_mcp", "server.py"))
  );
}

/** What pip/uv should install: the local tree if we have one, else PyPI. */
function installSpec() {
  if (process.env.CONNECT_MCP_SOURCE) return process.env.CONNECT_MCP_SOURCE;
  if (isSourceCheckout()) return PKG_ROOT;
  return `connect-to-server-mcp==${PKG.version}`;
}

function cacheDir() {
  const base = IS_WINDOWS
    ? process.env.LOCALAPPDATA || path.join(os.homedir(), "AppData", "Local")
    : path.join(os.homedir(), ".cache");
  return path.join(base, "connect-to-server-mcp");
}

function venvPython(dir) {
  return IS_WINDOWS
    ? path.join(dir, "Scripts", "python.exe")
    : path.join(dir, "bin", "python");
}

function findSystemPython() {
  for (const candidate of ["python3", "python", "python3.12", "python3.11", "python3.10"]) {
    const found = which(candidate);
    if (!found) continue;
    const version = spawnSync(found, ["-c", "import sys; print(sys.version_info >= (3, 10))"], {
      encoding: "utf8",
    });
    if (version.stdout && version.stdout.trim() === "True") return found;
  }
  return null;
}

/** Create (or refresh) a private venv holding the server and its dependencies. */
function ensureManagedVenv() {
  const dir = path.join(cacheDir(), "venv");
  const python = venvPython(dir);
  const spec = installSpec();
  const stampPath = path.join(dir, ".install-stamp");
  const stamp = `${spec}\n${PKG.version}\n`;

  if (fs.existsSync(python) && fs.existsSync(stampPath) && fs.readFileSync(stampPath, "utf8") === stamp) {
    return python;
  }

  const system = findSystemPython();
  if (!system) {
    fail(
      "No Python 3.10+ found. Install Python (or uv, which is faster: https://docs.astral.sh/uv/), " +
        "or point CONNECT_MCP_PYTHON at an interpreter that already has connect-to-server-mcp installed."
    );
  }

  if (!fs.existsSync(python)) {
    fs.mkdirSync(path.dirname(dir), { recursive: true });
    log(`Creating Python environment in ${dir} …`);
    const created = spawnSync(system, ["-m", "venv", dir], { stdio: "inherit" });
    if (created.status !== 0) fail("Failed to create the Python virtual environment.");
  }

  log(`Installing ${spec} …`);
  const installed = spawnSync(python, ["-m", "pip", "install", "--quiet", "--upgrade", spec], {
    stdio: "inherit",
  });
  if (installed.status !== 0) fail(`Failed to install ${spec}.`);

  fs.writeFileSync(stampPath, stamp);
  return python;
}

/**
 * Decide how to start the server, cheapest option first:
 *   1. an interpreter the user pinned via CONNECT_MCP_PYTHON
 *   2. uv / uvx, which resolves dependencies without a persistent install
 *   3. a managed venv under the user's cache directory
 */
function resolveRuntime({ allowInstall = true } = {}) {
  const pinned = process.env.CONNECT_MCP_PYTHON;
  if (pinned) {
    return { how: "CONNECT_MCP_PYTHON", command: pinned, args: ["-m", "connect_to_server_mcp"] };
  }

  const uvx = which("uvx");
  if (uvx) {
    const spec = installSpec();
    return {
      how: `uvx (${spec === PKG_ROOT ? "bundled source" : spec})`,
      command: uvx,
      args: ["--from", spec, "connect-to-server-mcp"],
    };
  }

  if (!allowInstall) {
    const python = venvPython(path.join(cacheDir(), "venv"));
    return {
      how: fs.existsSync(python) ? "managed venv" : "managed venv (not created yet)",
      command: python,
      args: ["-m", "connect_to_server_mcp"],
    };
  }

  return {
    how: "managed venv",
    command: ensureManagedVenv(),
    args: ["-m", "connect_to_server_mcp"],
  };
}

function startServer(extraArgs) {
  const runtime = resolveRuntime();
  const result = spawnSync(runtime.command, [...runtime.args, ...extraArgs], {
    stdio: "inherit",
    env: process.env,
  });
  process.exit(result.status === null ? 1 : result.status);
}

/* ------------------------------------------------------------------ install */

function hostsConfigPath() {
  if (process.env.CONNECT_MCP_CONFIG) return path.resolve(process.env.CONNECT_MCP_CONFIG);
  return path.join(os.homedir(), ".config", "connect-to-server-mcp", "hosts.yaml");
}

/** Copy the example inventory into place the first time, so the server has something to read. */
function ensureHostsConfig() {
  const target = hostsConfigPath();
  if (fs.existsSync(target)) return { path: target, created: false };
  const example = path.join(PKG_ROOT, "docs", "examples", "hosts.yaml");
  fs.mkdirSync(path.dirname(target), { recursive: true });
  fs.writeFileSync(
    target,
    fs.existsSync(example)
      ? fs.readFileSync(example, "utf8")
      : "# See https://github.com/vstlmkh/connect-to-server-mcp#configure\nhosts: []\n"
  );
  return { path: target, created: true };
}

/**
 * npm refuses to `npx` a package from inside a checkout of that same package —
 * it assumes the local project already provides the binary and looks for a
 * node_modules/.bin link that a plain clone does not have. A globally installed
 * binary has no such problem, so prefer it whenever one is on PATH.
 */
function serverEntry(configPath) {
  const global = which(BIN_NAME);
  const launcher = global && !global.includes("/_npx/")
    ? { command: global, args: [] }
    : { command: "npx", args: ["-y", `connect-to-server-mcp@${PKG.version}`] };
  return { ...launcher, env: { CONNECT_MCP_CONFIG: configPath } };
}

function claudeDesktopConfigPath() {
  const home = os.homedir();
  if (process.platform === "darwin") {
    return path.join(home, "Library", "Application Support", "Claude", "claude_desktop_config.json");
  }
  if (IS_WINDOWS) {
    return path.join(process.env.APPDATA || path.join(home, "AppData", "Roaming"), "Claude", "claude_desktop_config.json");
  }
  return path.join(home, ".config", "Claude", "claude_desktop_config.json");
}

/** Merge our server into a `{ mcpServers: {...} }` JSON file, keeping a .bak of the original. */
function writeJsonConfig(configFile, entry) {
  let existing = {};
  if (fs.existsSync(configFile)) {
    const raw = fs.readFileSync(configFile, "utf8");
    try {
      existing = raw.trim() ? JSON.parse(raw) : {};
    } catch (error) {
      fail(`${configFile} is not valid JSON (${error.message}); fix or move it, then retry.`);
    }
    fs.copyFileSync(configFile, `${configFile}.bak`);
  } else {
    fs.mkdirSync(path.dirname(configFile), { recursive: true });
  }

  existing.mcpServers = existing.mcpServers || {};
  const replaced = Boolean(existing.mcpServers[SERVER_NAME]);
  existing.mcpServers[SERVER_NAME] = entry;
  fs.writeFileSync(configFile, `${JSON.stringify(existing, null, 2)}\n`);
  return { replaced };
}

function installClaudeCode(configPath, scope) {
  const claude = which("claude");
  if (!claude) return { ok: false, reason: "the `claude` CLI is not on PATH" };

  spawnSync(claude, ["mcp", "remove", SERVER_NAME, "--scope", scope], { stdio: "ignore" });
  const entry = serverEntry(configPath);
  const added = spawnSync(
    claude,
    [
      "mcp", "add", SERVER_NAME,
      "--scope", scope,
      "--env", `CONNECT_MCP_CONFIG=${configPath}`,
      "--", entry.command, ...entry.args,
    ],
    { stdio: "inherit" }
  );
  return added.status === 0
    ? { ok: true, detail: `registered with the claude CLI (scope: ${scope})` }
    : { ok: false, reason: "`claude mcp add` failed" };
}

function installClaudeDesktop(configPath) {
  const file = claudeDesktopConfigPath();
  const appInstalled = fs.existsSync(file) || fs.existsSync(path.dirname(file));
  if (!appInstalled) return { ok: false, reason: "Claude Desktop config directory not found" };
  const { replaced } = writeJsonConfig(file, serverEntry(configPath));
  return { ok: true, detail: `${replaced ? "updated" : "added"} in ${file}` };
}

function runInstall(argv) {
  const targets = argv.filter((a) => !a.startsWith("-"));
  const scopeIndex = argv.indexOf("--scope");
  const scope = scopeIndex !== -1 ? argv[scopeIndex + 1] : "user";
  const fileIndex = argv.indexOf("--config-file");
  const customFile = fileIndex !== -1 ? path.resolve(argv[fileIndex + 1]) : null;

  const config = ensureHostsConfig();
  const wanted = targets.length ? targets : ["claude-code", "claude-desktop"];
  const results = [];

  if (customFile) {
    const { replaced } = writeJsonConfig(customFile, serverEntry(config.path));
    results.push({ target: customFile, ok: true, detail: replaced ? "updated" : "added" });
  } else {
    for (const target of wanted) {
      if (target === "claude-code" || target === "claude-cli" || target === "cli") {
        results.push({ target: "claude-code", ...installClaudeCode(config.path, scope) });
      } else if (target === "claude-desktop" || target === "claude" || target === "desktop") {
        results.push({ target: "claude-desktop", ...installClaudeDesktop(config.path) });
      } else {
        results.push({ target, ok: false, reason: "unknown target" });
      }
    }
  }

  log("");
  for (const result of results) {
    log(result.ok ? `  ✓ ${result.target}: ${result.detail}` : `  · ${result.target}: skipped — ${result.reason}`);
  }

  log("");
  log(config.created ? `Created host inventory at ${config.path}` : `Host inventory: ${config.path}`);
  if (config.created) {
    log("Edit it to add your servers — every host starts in read_only mode.");
  }
  if (!results.some((r) => r.ok)) {
    log("");
    log("Nothing was registered automatically. Add this to your client's MCP config:");
    log(JSON.stringify({ mcpServers: { [SERVER_NAME]: serverEntry(config.path) } }, null, 2));
  }
  log("");
  log("Restart your Claude client to pick up the change.");
}

/* ------------------------------------------------------------------- doctor */

function runDoctor() {
  const runtime = resolveRuntime({ allowInstall: false });
  const config = hostsConfigPath();
  log(`connect-to-server-mcp ${PKG.version}`);
  log(`  node:            ${process.version} (${process.platform})`);
  log(`  uv:              ${which("uv") || "not found"}`);
  log(`  python:          ${findSystemPython() || "not found (3.10+ required)"}`);
  log(`  claude CLI:      ${which("claude") || "not found"}`);
  log(`  launch via:      ${runtime.how}`);
  log(`  command:         ${runtime.command} ${runtime.args.join(" ")}`);
  log(`  install spec:    ${installSpec()}`);
  log(`  host inventory:  ${config}${fs.existsSync(config) ? "" : " (missing — run `install`)"}`);
  log(`  desktop config:  ${claudeDesktopConfigPath()}`);
  if (cwdIsOwnCheckout()) {
    log("");
    log("  note: the working directory is a checkout of this package itself, so");
    log("        `npx connect-to-server-mcp` cannot resolve it (npm looks for a local");
    log("        node_modules/.bin link). Register the server with a global install");
    log("        (npm i -g connect-to-server-mcp) or run `node bin/cli.js` directly.");
  }
}

/** True when the current directory's package.json is this very package. */
function cwdIsOwnCheckout() {
  try {
    const local = JSON.parse(fs.readFileSync(path.join(process.cwd(), "package.json"), "utf8"));
    return local.name === PKG.name;
  } catch {
    return false;
  }
}

/* --------------------------------------------------------------------- main */

function log(message) {
  process.stderr.write(`${message}\n`);
}

function fail(message) {
  log(`connect-to-server-mcp: ${message}`);
  process.exit(1);
}

function usage() {
  log(`connect-to-server-mcp ${PKG.version}

Usage:
  npx connect-to-server-mcp                      Start the MCP server on stdio (default)
  npx connect-to-server-mcp install [targets]    Register with claude-code and/or claude-desktop
  npx connect-to-server-mcp doctor               Show the runtime and config the launcher will use

Install targets:   claude-code (alias: claude-cli)   claude-desktop (alias: claude)
Install options:   --scope user|project|local        --config-file <path/to/mcp.json>

Environment:
  CONNECT_MCP_CONFIG   Path to the host inventory YAML
  CONNECT_MCP_PYTHON   Interpreter to run the server with, bypassing uv/venv discovery
  CONNECT_MCP_SOURCE   pip/uv spec to install instead of the published package`);
}

function main() {
  const [command, ...rest] = process.argv.slice(2);
  switch (command) {
    case undefined:
    case "start":
    case "serve":
      return startServer(rest);
    case "install":
    case "setup":
      return runInstall(rest);
    case "doctor":
      return runDoctor();
    case "-h":
    case "--help":
    case "help":
      return usage();
    case "-v":
    case "--version":
      return log(PKG.version);
    default:
      log(`Unknown command: ${command}`);
      usage();
      return process.exit(1);
  }
}

main();
