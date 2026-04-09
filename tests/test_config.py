import pytest

from lrx_cli.config import AppConfig, CredentialConfig, WatchConfig, load_config


def test_missing_file_returns_defaults(tmp_path):
    assert load_config(tmp_path / "nonexistent.toml") == AppConfig()


def test_empty_file_returns_defaults(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text("")
    assert load_config(p) == AppConfig()


def test_partial_section_keeps_other_defaults(tmp_path):
    p = tmp_path / "config.toml"
    p.write_bytes(b"[watch]\ndebounce_ms = 200\n")
    cfg = load_config(p)
    assert cfg.watch.debounce_ms == 200
    assert cfg.watch.calibration_interval_s == WatchConfig().calibration_interval_s


def test_credentials_roundtrip(tmp_path):
    p = tmp_path / "config.toml"
    p.write_bytes(
        b"[credentials]\n"
        b'spotify_sp_dc = "abc"\n'
        b'qq_music_api_url = "http://localhost:3000"\n'
    )
    assert load_config(p).credentials == CredentialConfig(
        spotify_sp_dc="abc", qq_music_api_url="http://localhost:3000"
    )


def test_int_coerced_to_float(tmp_path):
    p = tmp_path / "config.toml"
    p.write_bytes(b"[general]\nhttp_timeout = 5\n")
    assert load_config(p).general.http_timeout == 5.0


def test_unknown_key_raises(tmp_path):
    p = tmp_path / "config.toml"
    p.write_bytes(b"[general]\ntypo_key = 1\n")
    with pytest.raises(ValueError, match="Unknown config keys"):
        load_config(p)


def test_wrong_type_raises(tmp_path):
    p = tmp_path / "config.toml"
    p.write_bytes(b"[watch]\ndebounce_ms = true\n")
    with pytest.raises(ValueError, match="expected int"):
        load_config(p)


def test_app_config_is_frozen():
    cfg = AppConfig()
    with pytest.raises(Exception):
        cfg.general = None  # type: ignore[misc]
