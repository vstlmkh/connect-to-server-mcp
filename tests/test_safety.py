import pytest

from connect_to_server_mcp.config import HostConfig, ServerConfig
from connect_to_server_mcp.safety import PolicyError, check_command, is_read_only_command


def host(**kwargs) -> HostConfig:
    base = {"name": "h", "hostname": "example.com", "username": "u"}
    return HostConfig(**{**base, **kwargs})


@pytest.mark.parametrize(
    "command",
    [
        "ls -la /var/log",
        "tail -n 100 /var/log/syslog | grep ERROR",
        "systemctl status nginx",
        "df -h",
    ],
)
def test_read_only_commands_pass(command: str) -> None:
    check_command(command, host(mode="read_only"), ServerConfig())


@pytest.mark.parametrize(
    "command",
    ["systemctl restart nginx", "rm -rf /var/log/old", "echo hi > /etc/motd", "docker run alpine"],
)
def test_mutating_commands_refused_on_read_only(command: str) -> None:
    with pytest.raises(PolicyError):
        check_command(command, host(mode="read_only"), ServerConfig())


def test_full_mode_allows_mutation() -> None:
    check_command("systemctl restart nginx", host(mode="full"), ServerConfig())


def test_global_deny_applies_even_in_full_mode() -> None:
    with pytest.raises(PolicyError):
        check_command("rm -rf /", host(mode="full"), ServerConfig())


def test_allowlist_overrides_mode() -> None:
    h = host(mode="read_only", allowed_commands=[r"^systemctl restart nginx$"])
    check_command("systemctl restart nginx", h, ServerConfig())
    with pytest.raises(PolicyError):
        check_command("ls /tmp", h, ServerConfig())


def test_is_read_only_command_handles_pipes() -> None:
    assert is_read_only_command("ps aux | grep python | wc -l")
    assert not is_read_only_command("ps aux | tee /tmp/out")
