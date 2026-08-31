<p align="center">
  <img src="https://raw.githubusercontent.com/vstlmkh/connect-to-server-mcp/master/docs/assets/banner.png" alt="connect-to-server-mcp — give your AI agent a safe shell on any server" width="900">
</p>

<p align="center">
  <a href="https://www.npmjs.com/package/connect-to-server-mcp"><img alt="npm version" src="https://img.shields.io/npm/v/connect-to-server-mcp?logo=npm&color=60a5fa"></a>
  <a href="https://www.npmjs.com/package/connect-to-server-mcp"><img alt="npm downloads" src="https://img.shields.io/npm/dm/connect-to-server-mcp?label=downloads&color=5eead4"></a>
  <img alt="python" src="https://img.shields.io/badge/python-3.10%2B-a78bfa">
  <a href="https://github.com/vstlmkh/connect-to-server-mcp/blob/master/LICENSE"><img alt="license" src="https://img.shields.io/badge/license-MIT-22c55e"></a>
</p>

<p align="center">
  <b><a href="https://www.npmjs.com/package/connect-to-server-mcp">📦 npmjs.com/package/connect-to-server-mcp</a></b>
  &nbsp;·&nbsp;
  <code>npx connect-to-server-mcp install</code>
</p>

# connect-to-server-mcp

**connect-to-server-mcp** is a Model Context Protocol server that turns any remote machine into something an AI agent can operate. Point it at a host — bare metal, VPS, or cloud instance — with whatever credentials you have (SSH key, password, agent forwarding, or a jump host), and the agent gains a structured toolset for real operations: executing commands, reading and editing files, tailing logs, managing systemd services, and inspecting resource usage. Every action goes through explicit, permission-scoped tools with full audit logging, so the agent's reach stays exactly as wide as you allow.

## Features

- **Any host, any auth** — SSH keys (with passphrase), passwords, the local SSH agent, and jump hosts / bastions.
- **Per-host access modes** — `read_only` refuses anything that looks like it mutates state; `full` unlocks writes. An explicit `allowed_commands` allowlist overrides both.
- **Global guard rails** — `rm -rf /`, `mkfs`, `dd of=/dev/…`, fork bombs and reboots are refused on every host regardless of mode.
- **Audit log** — every attempt, including refusals, appended as JSONL.
- **Purpose-built tools** — logs, services, and health snapshots have dedicated tools, so the agent rarely needs raw shell.

## Tools

| Tool | What it does |
| --- | --- |
| `list_hosts` | List configured hosts with access mode and description |
| `reload_hosts` | Re-read the inventory and drop open connections |
| `check_connection` | Open/reuse the SSH session and report host identity |
| `run_command` | Run a shell command, subject to the host's policy |
| `read_file` / `write_file` | Read or overwrite a remote file over SFTP (writes make a `.bak`) |
| `list_directory` | List a directory with sizes and permissions |
| `tail_log` | Tail a log file or a systemd unit's journal, optionally filtered |
| `service_status` / `service_control` | Inspect or start/stop/restart/enable a systemd unit |
| `system_overview` | Load, memory, disk, top processes, failed units in one call |
| `disconnect` | Close one connection or all of them |

## Install

Pick whichever fits your setup — all three end up running the same server.

### 1. One command (npx)

No Python setup, no clone. The launcher finds `uv` or Python 3.10+ on your machine, provisions what is missing, and registers the server with every Claude client it detects:

```bash
npx connect-to-server-mcp install
```

Targets can be named explicitly, and the scope for the CLI chosen:

```bash
npx connect-to-server-mcp install claude-code --scope user
npx connect-to-server-mcp install claude-desktop
npx connect-to-server-mcp install --config-file ./.mcp.json   # any MCP client
```

`install` also seeds a starter inventory at `~/.config/connect-to-server-mcp/hosts.yaml` if you do not have one. Restart the client afterwards, then run `npx connect-to-server-mcp doctor` if anything looks off — it prints the runtime, config paths, and the exact command the server will be started with.

### 2. Claude Code plugin (marketplace)

```
/plugin marketplace add vstlmkh/connect-to-server-mcp
/plugin install connect-to-server@connect-to-server
```

The plugin ships its own `.mcp.json`, so the server appears as soon as it is enabled.

> That `.mcp.json` sits at the repository root because a plugin's MCP config has to live there, and it refers to `${CLAUDE_PLUGIN_ROOT}` — a variable only Claude Code's plugin loader expands. When this repo is opened as an ordinary project the entry is therefore switched off through `.claude/settings.json`; develop against the server with `npx connect-to-server-mcp install` instead.

### 3. Manual client config

Add this to `claude_desktop_config.json` (Claude Desktop) or any other MCP client:

```json
{
  "mcpServers": {
    "connect-to-server": {
      "command": "npx",
      "args": ["-y", "connect-to-server-mcp"],
      "env": { "CONNECT_MCP_CONFIG": "~/.config/connect-to-server-mcp/hosts.yaml" }
    }
  }
}
```

Or register it with the Claude CLI directly:

```bash
claude mcp add connect-to-server -e CONNECT_MCP_CONFIG=~/.config/connect-to-server-mcp/hosts.yaml \
  -- npx -y connect-to-server-mcp
```

### From source

```bash
git clone https://github.com/vstlmkh/connect-to-server-mcp.git
cd connect-to-server-mcp
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
connect-to-server-mcp          # stdio transport
```

## Configure

Copy [`docs/examples/hosts.yaml`](docs/examples/hosts.yaml) to `~/.config/connect-to-server-mcp/hosts.yaml`, or point `CONNECT_MCP_CONFIG` at a file of your choosing. Any `${VAR}` in the file is expanded from the environment at load time — keep secrets out of version control.

```yaml
audit_log: ~/.local/state/connect-to-server-mcp/audit.jsonl

hosts:
  - name: prod-web
    hostname: 203.0.113.10
    username: deploy
    private_key: ~/.ssh/id_ed25519
    known_hosts: ~/.ssh/known_hosts
    mode: read_only

  - name: staging
    hostname: staging.internal
    username: root
    private_key: ~/.ssh/id_ed25519
    mode: full
    working_dir: /srv/app
```

Environment variables the launcher understands:

| Variable | Purpose |
| --- | --- |
| `CONNECT_MCP_CONFIG` | Path to the host inventory YAML |
| `CONNECT_MCP_PYTHON` | Interpreter to run the server with, bypassing uv/venv discovery |
| `CONNECT_MCP_SOURCE` | pip/uv spec to install instead of the published package |

## Safety model

The policy layer is a guard rail against an agent wandering, **not** a security boundary. The real boundary is the remote account's own permissions: give each host a dedicated user with the narrowest sudo rules that let it do its job, and start every host in `read_only` until you have a reason to widen it.

## Development

```bash
pytest          # tests
ruff check .    # lint
mypy src        # types
```

## License

MIT
