import pytest

from latodash.config import ConfigError, load_config

GOOD = """
[ssh]
host = "YOUR_SERVER_HOST"
user = "YOUR_SSH_USER"
key_file = "keys/latodash_ed25519"
known_hosts = "keys/known_hosts"

[app]
latochan_dir = "../latochan"
"""


def test_relative_paths_resolve_next_to_config(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text(GOOD)
    cfg = load_config(p)
    assert cfg.ssh.key_file == tmp_path / "keys" / "latodash_ed25519"
    assert cfg.latochan_dir == (tmp_path / ".." / "latochan").resolve()
    assert cfg.listen_host == "127.0.0.1"


def test_unknown_key_rejected(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text(GOOD + "\nbogus = 1\n")
    with pytest.raises(ConfigError):
        load_config(p)


def test_non_loopback_refused(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text(GOOD + 'listen_host = "0.0.0.0"\n')
    with pytest.raises(ConfigError):
        load_config(p)
