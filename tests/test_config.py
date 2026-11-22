"""Tests for configuration loading and validation."""

from __future__ import annotations

import pytest

from ninmu.config import Config, ConfigError


def _make_config(
    vikunja_url: str = "http://vikunja:3456",
    vikunja_token: str | None = "vk-token",
    notediscovery_url: str = "http://notediscovery:8000",
    notediscovery_api_key: str | None = "notes-key",
) -> Config:
    """Build a valid baseline Config, overriding fields by name."""
    return Config(
        vikunja_url=vikunja_url,
        vikunja_token=vikunja_token,
        notediscovery_url=notediscovery_url,
        notediscovery_api_key=notediscovery_api_key,
        mcp_auth_token=None,
        list_default_limit=20,
        list_max_limit=100,
    )


def test_valid_config_passes() -> None:
    _make_config().validate()


def test_missing_vikunja_token_fails() -> None:
    config = _make_config(vikunja_token=None)
    with pytest.raises(ConfigError, match="VIKUNJA_TOKEN"):
        config.validate()


def test_missing_notediscovery_api_key_is_allowed() -> None:
    """NoteDiscovery may run without authentication; the key is optional."""
    config = _make_config(notediscovery_api_key=None)
    config.validate()


def test_missing_vikunja_url_fails() -> None:
    with pytest.raises(ConfigError, match="VIKUNJA_URL"):
        _make_config(vikunja_url="").validate()


def test_missing_notediscovery_url_fails() -> None:
    with pytest.raises(ConfigError, match="NOTEDISCOVERY_URL"):
        _make_config(notediscovery_url="").validate()
