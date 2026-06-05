"""Persistent runtime config stored in /app/data/config.json."""

import json
import os
from pathlib import Path

_CONFIG_PATH = Path("/app/data/config.json")
_DEFAULTS = {"max_concurrent_downloads": int(os.environ.get("MAX_CONCURRENT_DOWNLOADS", "4"))}


def _load() -> dict:
    if _CONFIG_PATH.exists():
        try:
            return {**_DEFAULTS, **json.loads(_CONFIG_PATH.read_text())}
        except (json.JSONDecodeError, OSError):
            pass
    return dict(_DEFAULTS)


def _save(data: dict) -> None:
    _CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    _CONFIG_PATH.write_text(json.dumps(data, indent=2))


def get_all() -> dict:
    return _load()


def get(key: str):
    return _load().get(key, _DEFAULTS.get(key))


def set(key: str, value) -> dict:
    data = _load()
    data[key] = value
    _save(data)
    return data
