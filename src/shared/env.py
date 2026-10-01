"""Environment variable helpers."""

from __future__ import annotations

import os

GROQ_API_KEY_ENV = "GROQ_API_KEY"
GROQ_KEY_ENV = "GROQ_KEY"


def env_first(*names: str) -> str:
    """Return the first non-empty stripped environment value among *names*."""
    for name in names:
        raw = os.environ.get(name)
        if raw is None:
            continue
        value = raw.strip()
        if value:
            return value
    return ""


def groq_api_key() -> str:
    """Canonical GROQ_API_KEY, else Cloud Agents GROQ_KEY alias."""
    return env_first(GROQ_API_KEY_ENV, GROQ_KEY_ENV)
