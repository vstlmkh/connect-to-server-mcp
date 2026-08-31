"""SSH connection pooling and command execution."""

from __future__ import annotations

import asyncio
import shlex
from dataclasses import dataclass
from typing import Any

import asyncssh

from .config import HostConfig, ServerConfig


@dataclass(slots=True)
class CommandResult:
    host: str
    command: str
    exit_status: int
    stdout: str
    stderr: str
    truncated: bool = False

    def as_text(self) -> str:
        parts = [f"[{self.host}] $ {self.command}", f"exit status: {self.exit_status}"]
        if self.stdout.strip():
            parts.append(f"--- stdout ---\n{self.stdout.rstrip()}")
        if self.stderr.strip():
            parts.append(f"--- stderr ---\n{self.stderr.rstrip()}")
        if self.truncated:
            parts.append("[output truncated]")
        return "\n".join(parts)


class ConnectionManager:
    """Keeps one live SSH connection per configured host, opened lazily."""

    def __init__(self, config: ServerConfig) -> None:
        self._config = config
        self._connections: dict[str, asyncssh.SSHClientConnection] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def _lock(self, name: str) -> asyncio.Lock:
        return self._locks.setdefault(name, asyncio.Lock())

    def _connect_options(self, host: HostConfig) -> dict[str, Any]:
        options: dict[str, Any] = {
            "host": host.hostname,
            "port": host.port,
            "username": host.username,
            "known_hosts": host.known_hosts,
        }
        if host.private_key:
            options["client_keys"] = [host.private_key]
            if host.private_key_passphrase:
                options["passphrase"] = host.private_key_passphrase
        if host.password:
            options["password"] = host.password
        return options

    async def connect(self, name: str) -> asyncssh.SSHClientConnection:
        host = self._config.host(name)
        async with self._lock(name):
            existing = self._connections.get(name)
            if existing is not None and not existing.is_closed():
                return existing

            tunnel: asyncssh.SSHClientConnection | None = None
            if host.jump_host:
                tunnel = await self.connect(host.jump_host)

            options = self._connect_options(host)
            if tunnel is not None:
                options["tunnel"] = tunnel

            conn = await asyncssh.connect(**options)
            self._connections[name] = conn
            return conn

    async def run(
        self,
        name: str,
        command: str,
        *,
        timeout: int | None = None,
        working_dir: str | None = None,
    ) -> CommandResult:
        host = self._config.host(name)
        conn = await self.connect(name)

        cwd = working_dir or host.working_dir
        full = f"cd {shlex.quote(cwd)} && {command}" if cwd else command

        try:
            result = await asyncio.wait_for(
                conn.run(full, check=False, env=host.env or None),
                timeout=timeout or host.command_timeout,
            )
        except TimeoutError:
            return CommandResult(
                host=name,
                command=command,
                exit_status=124,
                stdout="",
                stderr=f"command timed out after {timeout or host.command_timeout}s",
            )

        limit = self._config.max_output_bytes
        stdout = str(result.stdout or "")
        stderr = str(result.stderr or "")
        truncated = len(stdout) > limit or len(stderr) > limit
        return CommandResult(
            host=name,
            command=command,
            exit_status=result.exit_status if result.exit_status is not None else -1,
            stdout=stdout[:limit],
            stderr=stderr[:limit],
            truncated=truncated,
        )

    async def sftp(self, name: str) -> asyncssh.SFTPClient:
        conn = await self.connect(name)
        return await conn.start_sftp_client()

    async def close(self, name: str) -> None:
        conn = self._connections.pop(name, None)
        if conn is not None and not conn.is_closed():
            conn.close()
            await conn.wait_closed()

    async def close_all(self) -> None:
        for name in list(self._connections):
            await self.close(name)
