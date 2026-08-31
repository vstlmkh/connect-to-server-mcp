"""Command policy checks and audit logging."""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

from .config import HostConfig, ServerConfig

# Commands that only read state. Used to decide what a read_only host may run
# when it has no explicit allowlist.
READ_ONLY_BINARIES = {
    "cat", "head", "tail", "less", "ls", "ll", "stat", "file", "find", "grep", "egrep",
    "rg", "awk", "sed", "wc", "du", "df", "free", "uptime", "who", "w", "id", "uname",
    "hostname", "hostnamectl", "ps", "top", "htop", "pgrep", "netstat", "ss", "ip",
    "ifconfig", "ping", "curl", "wget", "dig", "nslookup", "date", "env", "printenv",
    "which", "whereis", "journalctl", "dmesg", "docker", "kubectl", "git", "systemctl",
    "echo", "pwd", "readlink", "realpath", "md5sum", "sha256sum", "lsof", "vmstat",
    "iostat", "mount", "lsblk", "sensors", "nproc", "tree", "diff", "sort", "uniq", "jq",
}

# Subcommands of otherwise-read-only tools that mutate state.
MUTATING_SUBCOMMANDS = {
    "systemctl": {
        "start", "stop", "restart", "reload", "enable", "disable", "mask", "unmask", "set-property",
    },
    "docker": {
        "run", "rm", "rmi", "start", "stop", "restart", "kill", "exec", "build", "push", "pull",
        "prune", "compose",
    },
    "kubectl": {
        "apply", "delete", "create", "edit", "patch", "scale", "rollout", "exec", "drain", "cordon",
    },
    "git": {"push", "reset", "clean", "checkout", "merge", "rebase", "commit", "pull", "fetch"},
}


class PolicyError(PermissionError):
    """Raised when a command is refused before it ever reaches the host."""


def _first_binary(command: str) -> str:
    """Best-effort extraction of the leading executable name."""
    stripped = command.strip()
    for prefix in ("sudo ", "env ", "nohup ", "time "):
        if stripped.startswith(prefix):
            stripped = stripped[len(prefix) :].lstrip()
    token = stripped.split()[0] if stripped.split() else ""
    return Path(token).name


def _tokens(command: str) -> list[str]:
    return command.strip().split()


def is_read_only_command(command: str) -> bool:
    """True when every segment of a (possibly piped) command only reads state."""
    segments = re.split(r"&&|\|\||;|\|", command)
    for segment in segments:
        if not segment.strip():
            continue
        binary = _first_binary(segment)
        if binary not in READ_ONLY_BINARIES:
            return False
        mutating = MUTATING_SUBCOMMANDS.get(binary)
        if mutating:
            args = [t for t in _tokens(segment)[1:] if not t.startswith("-")]
            if args and args[0] in mutating:
                return False
        if re.search(r"(^|\s)>{1,2}\s*\S", segment):
            return False
    return True


def check_command(command: str, host: HostConfig, config: ServerConfig) -> None:
    """Raise :class:`PolicyError` if ``command`` is not permitted on ``host``."""
    if not command.strip():
        raise PolicyError("empty command")

    for pattern in [*config.default_denied_commands, *host.denied_commands]:
        if re.search(pattern, command):
            raise PolicyError(
                f"command blocked by deny rule {pattern!r} on host {host.name!r}"
            )

    if host.allowed_commands:
        if not any(re.search(p, command) for p in host.allowed_commands):
            raise PolicyError(
                f"command does not match any allow rule configured for host {host.name!r}"
            )
        return

    if host.read_only and not is_read_only_command(command):
        raise PolicyError(
            f"host {host.name!r} is in read_only mode; {command!r} appears to modify state. "
            "Set mode: full for this host, or add an explicit allowed_commands rule."
        )


def check_write_allowed(host: HostConfig, action: str) -> None:
    if host.read_only:
        raise PolicyError(f"host {host.name!r} is read_only; {action} is not permitted")


class AuditLog:
    """Append-only JSONL record of everything the agent attempted."""

    def __init__(self, path: str | None) -> None:
        self._path = Path(path).expanduser() if path else None
        if self._path:
            self._path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, host: str, event: str, **fields: Any) -> None:
        if not self._path:
            return
        entry = {"ts": time.time(), "host": host, "event": event, **fields}
        with self._path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
