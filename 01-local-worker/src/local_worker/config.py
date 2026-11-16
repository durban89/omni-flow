from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from local_worker.errors import ConfigError

# Keys that belong to LLM generation (shared by content:/llm: sections).
_LLM_KEYS = (
    "enable",
    "enabled",
    "model",
    "base_url",
    "temperature",
    "max_words",
    "timeout_seconds",
)


def load_settings(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ConfigError(
            f"settings file not found: {path}; "
            "copy config/settings.example.yaml to config/settings.yaml"
        )

    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid YAML in {path}: {exc}") from exc

    if not isinstance(data, dict):
        raise ConfigError(f"settings root must be a mapping: {path}")
    return data


def _as_mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def resolve_llm_settings(settings: dict[str, Any]) -> dict[str, Any]:
    """
    Merge legacy `content:` and canonical `llm:` sections into one LLM config.

    Precedence: `llm` overlays `content`. `enabled` is normalized to `enable`.
    """
    content = _as_mapping(settings.get("content"))
    llm = _as_mapping(settings.get("llm"))

    merged: dict[str, Any] = {}
    for key in _LLM_KEYS:
        if key in content:
            merged[key] = content[key]
    for key in _LLM_KEYS:
        if key in llm:
            merged[key] = llm[key]

    if "enable" not in merged and "enabled" in merged:
        merged["enable"] = bool(merged["enabled"])
    elif "enable" in merged:
        merged["enable"] = bool(merged["enable"])
    else:
        merged["enable"] = False

    return merged
