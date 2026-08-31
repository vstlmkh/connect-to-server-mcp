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

```bash
git clone git@common:vstlmkh/connect-to-server-mcp.git
cd connect-to-server-mcp
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
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

## Run

```bash
connect-to-server-mcp          # stdio transport
```

Register it with Claude Code:

```bash
claude mcp add connect-to-server -e CONNECT_MCP_CONFIG=~/.config/connect-to-server-mcp/hosts.yaml -- connect-to-server-mcp
```

For Claude Desktop, see [`docs/examples/claude_desktop_config.json`](docs/examples/claude_desktop_config.json).

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
