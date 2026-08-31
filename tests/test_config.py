import pytest

from connect_to_server_mcp.config import ServerConfig, load_config


def test_env_expansion_and_jump_host(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("SECRET_PW", "hunter2")
    cfg = tmp_path / "hosts.yaml"
    cfg.write_text(
        "hosts:\n"
        "  - name: bastion\n    hostname: b.example.com\n    username: u\n"
        "  - name: db\n    hostname: 10.0.0.5\n    username: dba\n"
        "    password: ${SECRET_PW}\n    jump_host: bastion\n"
    )
    loaded = load_config(cfg)
    assert loaded.host("db").password == "hunter2"
    assert loaded.host("db").jump_host == "bastion"


def test_unknown_jump_host_rejected() -> None:
    with pytest.raises(ValueError):
        ServerConfig.model_validate(
            {"hosts": [{"name": "a", "hostname": "h", "username": "u", "jump_host": "nope"}]}
        )


def test_missing_file_yields_empty_config(tmp_path) -> None:
    assert load_config(tmp_path / "absent.yaml").hosts == []
