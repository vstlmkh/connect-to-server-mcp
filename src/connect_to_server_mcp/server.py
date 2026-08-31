"""MCP tool surface: connect to, inspect, and operate remote servers."""

from __future__ import annotations

import shlex
from typing import Annotated

from mcp.server.mcpserver import MCPServer
from pydantic import Field

from .config import ServerConfig, config_path, load_config
from .connection import ConnectionManager
from .safety import AuditLog, PolicyError, check_command, check_write_allowed

mcp = MCPServer(
    "connect-to-server",
    instructions=(
        "Tools for operating remote servers over SSH. Call list_hosts first to see what "
        "is reachable and which hosts are read_only. Prefer the specific tools "
        "(read_file, tail_log, service_control, system_overview) over raw run_command "
        "when one fits — they are safer and their output is easier to reason about."
    ),
)

_config: ServerConfig = load_config()
_connections = ConnectionManager(_config)
_audit = AuditLog(_config.audit_log)


def reload_config() -> ServerConfig:
    """Re-read the inventory from disk (used at startup and by reload_hosts)."""
    global _config, _connections, _audit
    _config = load_config()
    _connections = ConnectionManager(_config)
    _audit = AuditLog(_config.audit_log)
    return _config


@mcp.tool()
async def list_hosts() -> str:
    """List every configured host with its access mode and description."""
    if not _config.hosts:
        return (
            f"No hosts configured. Create {config_path()} (see docs/examples/hosts.yaml) "
            "or set CONNECT_MCP_CONFIG to point at an inventory file."
        )
    lines = ["Configured hosts:"]
    for host in _config.hosts:
        via = f", via {host.jump_host}" if host.jump_host else ""
        note = f" — {host.description}" if host.description else ""
        lines.append(
            f"  {host.name}: {host.username}@{host.hostname}:{host.port} "
            f"[{host.mode}{via}]{note}"
        )
    return "\n".join(lines)


@mcp.tool()
async def reload_hosts() -> str:
    """Reload the inventory file from disk and drop all open connections."""
    await _connections.close_all()
    config = reload_config()
    return f"Reloaded {len(config.hosts)} host(s) from {config_path()}."


@mcp.tool()
async def check_connection(
    host: Annotated[str, Field(description="Configured host name from list_hosts")],
) -> str:
    """Open (or reuse) the SSH connection to a host and report basic identity."""
    result = await _connections.run(host, "uname -a; uptime")
    _audit.record(host, "check_connection", exit_status=result.exit_status)
    return result.as_text()


@mcp.tool()
async def run_command(
    host: Annotated[str, Field(description="Configured host name")],
    command: Annotated[str, Field(description="Shell command to execute on the host")],
    working_dir: Annotated[str | None, Field(description="Directory to cd into first")] = None,
    timeout: Annotated[
        int | None, Field(description="Seconds before the command is killed")
    ] = None,
) -> str:
    """Run a shell command on a host, subject to that host's access policy.

    Read-only hosts accept only inspection commands; anything that appears to
    modify state is refused before it reaches the server.
    """
    host_config = _config.host(host)
    try:
        check_command(command, host_config, _config)
    except PolicyError as exc:
        _audit.record(host, "run_command", command=command, refused=str(exc))
        return f"Refused: {exc}"

    result = await _connections.run(host, command, timeout=timeout, working_dir=working_dir)
    _audit.record(host, "run_command", command=command, exit_status=result.exit_status)
    return result.as_text()


@mcp.tool()
async def read_file(
    host: Annotated[str, Field(description="Configured host name")],
    path: Annotated[str, Field(description="Absolute path of the file to read")],
    max_bytes: Annotated[int, Field(description="Maximum number of bytes to return")] = 100_000,
) -> str:
    """Read a remote file over SFTP."""
    sftp = await _connections.sftp(host)
    async with sftp.open(path, "rb") as fh:
        data: bytes = await fh.read(max_bytes + 1)
    _audit.record(host, "read_file", path=path, bytes=len(data))
    text = data[:max_bytes].decode("utf-8", errors="replace")
    suffix = "\n[truncated]" if len(data) > max_bytes else ""
    return f"{path} ({len(data)} bytes read):\n{text}{suffix}"


@mcp.tool()
async def write_file(
    host: Annotated[str, Field(description="Configured host name")],
    path: Annotated[str, Field(description="Absolute path of the file to write")],
    content: Annotated[str, Field(description="Full new contents of the file")],
    backup: Annotated[bool, Field(description="Keep a .bak copy of the previous version")] = True,
) -> str:
    """Overwrite a remote file. Refused on read-only hosts."""
    host_config = _config.host(host)
    try:
        check_write_allowed(host_config, f"writing {path}")
    except PolicyError as exc:
        _audit.record(host, "write_file", path=path, refused=str(exc))
        return f"Refused: {exc}"

    sftp = await _connections.sftp(host)
    if backup:
        try:
            await sftp.stat(path)
            await _connections.run(host, f"cp {shlex.quote(path)} {shlex.quote(path + '.bak')}")
        except FileNotFoundError:
            pass
    async with sftp.open(path, "w") as fh:
        await fh.write(content)
    _audit.record(host, "write_file", path=path, bytes=len(content))
    return f"Wrote {len(content)} bytes to {host}:{path}."


@mcp.tool()
async def list_directory(
    host: Annotated[str, Field(description="Configured host name")],
    path: Annotated[str, Field(description="Directory to list")] = ".",
) -> str:
    """List a remote directory with sizes and permissions."""
    result = await _connections.run(host, f"ls -lah --time-style=long-iso {shlex.quote(path)}")
    _audit.record(host, "list_directory", path=path)
    return result.as_text()


@mcp.tool()
async def tail_log(
    host: Annotated[str, Field(description="Configured host name")],
    source: Annotated[str, Field(description="Log file path, or 'unit:<name>' for a systemd unit")],
    lines: Annotated[int, Field(description="How many trailing lines to return")] = 200,
    grep: Annotated[
        str | None, Field(description="Only return lines matching this pattern")
    ] = None,
) -> str:
    """Tail a log file or a systemd unit's journal."""
    if source.startswith("unit:"):
        unit = shlex.quote(source.split(":", 1)[1])
        command = f"journalctl -u {unit} -n {int(lines)} --no-pager"
    else:
        command = f"tail -n {int(lines)} {shlex.quote(source)}"
    if grep:
        command += f" | grep -E {shlex.quote(grep)}"
    result = await _connections.run(host, command)
    _audit.record(host, "tail_log", source=source, lines=lines)
    return result.as_text()


@mcp.tool()
async def service_status(
    host: Annotated[str, Field(description="Configured host name")],
    unit: Annotated[str, Field(description="systemd unit name, e.g. nginx")],
) -> str:
    """Show the status of a systemd unit."""
    result = await _connections.run(host, f"systemctl status {shlex.quote(unit)} --no-pager")
    _audit.record(host, "service_status", unit=unit)
    return result.as_text()


@mcp.tool()
async def service_control(
    host: Annotated[str, Field(description="Configured host name")],
    unit: Annotated[str, Field(description="systemd unit name")],
    action: Annotated[
        str, Field(description="One of: start, stop, restart, reload, enable, disable")
    ],
) -> str:
    """Start, stop, restart, reload, enable or disable a systemd unit."""
    allowed = {"start", "stop", "restart", "reload", "enable", "disable"}
    if action not in allowed:
        return f"Refused: action must be one of {sorted(allowed)}."

    host_config = _config.host(host)
    try:
        check_write_allowed(host_config, f"systemctl {action} {unit}")
    except PolicyError as exc:
        _audit.record(host, "service_control", unit=unit, action=action, refused=str(exc))
        return f"Refused: {exc}"

    result = await _connections.run(host, f"sudo systemctl {action} {shlex.quote(unit)}")
    _audit.record(host, "service_control", unit=unit, action=action, exit_status=result.exit_status)
    return result.as_text()


@mcp.tool()
async def system_overview(
    host: Annotated[str, Field(description="Configured host name")],
) -> str:
    """Collect a health snapshot: load, memory, disk, top processes, failed units."""
    command = (
        "echo '## uptime'; uptime; "
        "echo '## memory'; free -h; "
        "echo '## disk'; df -h -x tmpfs -x devtmpfs; "
        "echo '## top processes'; ps -eo pid,comm,%cpu,%mem --sort=-%cpu | head -11; "
        "echo '## failed units'; systemctl --failed --no-pager 2>/dev/null || true"
    )
    result = await _connections.run(host, command)
    _audit.record(host, "system_overview")
    return result.as_text()


@mcp.tool()
async def disconnect(
    host: Annotated[str, Field(description="Configured host name, or 'all'")],
) -> str:
    """Close the SSH connection to one host, or to every host."""
    if host == "all":
        await _connections.close_all()
        return "Closed all connections."
    await _connections.close(host)
    return f"Closed connection to {host}."
