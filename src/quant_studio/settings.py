"""Per-request explicit settings; never mutate a running process's environment."""

import os
from contextlib import contextmanager
from contextvars import ContextVar

_settings: ContextVar[dict | None] = ContextVar("studio_settings", default=None)


def current_settings() -> dict | None:
    return _settings.get()


def setting(name: str) -> str | None:
    profile = current_settings()
    chosen = profile["environment"].get(name) if profile is not None else None
    return chosen or os.environ.get(name)


def subprocess_environment() -> dict[str, str]:
    profile = current_settings()
    selected = profile["environment"] if profile is not None else {}
    return {**os.environ, **selected, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}


@contextmanager
def use_settings(profile: dict | None):
    token = _settings.set(profile)
    try:
        yield
    finally:
        _settings.reset(token)
