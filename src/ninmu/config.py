"""Configuration and settings."""

from __future__ import annotations

import os
from dataclasses import dataclass

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass


class ConfigError(Exception):
    """Configuration error with a user-friendly message."""


@dataclass
class Config:
    """Server configuration loaded from environment."""

    vikunja_url: str
    vikunja_token: str | None
    notediscovery_url: str
    notediscovery_api_key: str | None
    mcp_auth_token: str | None
    list_default_limit: int
    list_max_limit: int

    def validate(self) -> None:
        """Require the Vikunja token and both URLs (fail-fast).

        ``NOTEDISCOVERY_API_KEY`` is optional: a NoteDiscovery instance may
        run without authentication, in which case the client sends no
        ``X-API-Key`` header.
        """
        if not self.vikunja_token:
            raise ConfigError("Upstream credentials incomplete, missing: VIKUNJA_TOKEN")
        if not self.vikunja_url:
            raise ConfigError("VIKUNJA_URL is required")
        if not self.notediscovery_url:
            raise ConfigError("NOTEDISCOVERY_URL is required")


def _get_env_var(name: str) -> str | None:
    """Get environment variable, returning None if empty or unset."""
    value = os.getenv(name)
    return value if value else None


def load_config() -> Config:
    """Load configuration from environment variables."""
    return Config(
        vikunja_url=_get_env_var("VIKUNJA_URL") or "",
        vikunja_token=_get_env_var("VIKUNJA_TOKEN"),
        notediscovery_url=_get_env_var("NOTEDISCOVERY_URL") or "",
        notediscovery_api_key=_get_env_var("NOTEDISCOVERY_API_KEY"),
        mcp_auth_token=_get_env_var("NINMU_AUTH_TOKEN"),
        list_default_limit=max(1, int(os.getenv("LIST_DEFAULT_LIMIT", "20"))),
        list_max_limit=max(
            int(os.getenv("LIST_DEFAULT_LIMIT", "20")),
            int(os.getenv("LIST_MAX_LIMIT", "100")),
        ),
    )
