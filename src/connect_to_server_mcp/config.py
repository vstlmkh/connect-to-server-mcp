"""Host inventory and safety policy loading.

Configuration is a single YAML file, located via ``CONNECT_MCP_CONFIG`` or, by
default, ``~/.config/connect-to-server-mcp/hosts.yaml``. Secrets are never
required to live in the file: any string value may use ``${ENV_VAR}`` and is
expanded from the environment at load time.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, model_validator

DEFAULT_CONFIG_PATH = Path.home() / ".config" / "connect-to-server-mcp" / "hosts.yaml"
ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")

# Patterns refused on every host regardless of mode. This is a guard rail against
# an agent wandering, not a security boundary — the real boundary is the remote
# account's own permissions.
DEFAULT_DENY = [
    r"\brm\s+(-[a-zA-Z]*\s+)*/(\s|$)",
    r"\bmkfs(\.|\s)",
    r"\bdd\s+.*\bof=/dev/",
    r":\(\)\s*\{.*\};\s*:",
    r"\bshutdown\b|\breboot\b|\bhalt\b|\bpoweroff\b",
    r"\bchmod\s+-R\s+777\s+/(\s|$)",
    r">\s*/dev/sd[a-z]",
]

AccessMode = Literal["read_only", "full"]


class HostConfig(BaseModel):
    """One reachable machine and the policy that applies to it."""

    name: str
    hostname: str
    port: int = 22
    username: str
    description: str = ""

    # Auth — any one of these; asyncssh falls back to the local agent if all are unset.
    private_key: str | None = None
    private_key_passphrase: str | None = None
    password: str | None = None
    jump_host: str | None = Field(
        default=None,
        description="Name of another configured host to tunnel through.",
    )
    known_hosts: str | None = Field(
        default=None,
        description="Path to a known_hosts file. Null disables host key checking.",
    )

    mode: AccessMode = "read_only"
    allowed_commands: list[str] = Field(default_factory=list)
    denied_commands: list[str] = Field(default_factory=list)
    working_dir: str | None = None
    command_timeout: int = 60
    env: dict[str, str] = Field(default_factory=dict)

    @property
    def read_only(self) -> bool:
        return self.mode == "read_only"


class ServerConfig(BaseModel):
    hosts: list[HostConfig] = Field(default_factory=list)
    audit_log: str | None = None
    max_output_bytes: int = 200_000
    default_denied_commands: list[str] = Field(default_factory=lambda: list(DEFAULT_DENY))

    @model_validator(mode="after")
    def _check_unique_names(self) -> ServerConfig:
        seen: set[str] = set()
        for host in self.hosts:
            if host.name in seen:
                raise ValueError(f"duplicate host name: {host.name}")
            seen.add(host.name)
        for host in self.hosts:
            if host.jump_host and host.jump_host not in seen:
                raise ValueError(
                    f"host {host.name!r} references unknown jump_host {host.jump_host!r}"
                )
        return self

    def host(self, name: str) -> HostConfig:
        for host in self.hosts:
            if host.name == name:
                return host
        known = ", ".join(h.name for h in self.hosts) or "<none configured>"
        raise KeyError(f"unknown host {name!r}; configured hosts: {known}")


def _expand(value: Any) -> Any:
    """Recursively substitute ``${VAR}`` references from the environment."""
    if isinstance(value, str):
        return ENV_PATTERN.sub(lambda m: os.environ.get(m.group(1), ""), value)
    if isinstance(value, dict):
        return {k: _expand(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand(v) for v in value]
    return value


def config_path() -> Path:
    override = os.environ.get("CONNECT_MCP_CONFIG")
    return Path(override).expanduser() if override else DEFAULT_CONFIG_PATH


def load_config(path: Path | None = None) -> ServerConfig:
    """Load the inventory. A missing file yields an empty (but usable) config."""
    target = path or config_path()
    if not target.exists():
        return ServerConfig()
    raw = yaml.safe_load(target.read_text()) or {}
    return ServerConfig.model_validate(_expand(raw))
