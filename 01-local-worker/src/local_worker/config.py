from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

from local_worker.errors import ConfigError

# Settings profile selector: test runs a single item, production runs the full batch.
MODE_ENV_VAR = "LOCAL_WORKER_MODE"
DEFAULT_MODE = "production"

# Keys that belong to LLM generation (shared by content:/llm: sections).
_LLM_KEYS = (
    "enable",
    "enabled",
    "model",
    "base_url",
    "temperature",
    "max_words",
    "timeout_seconds",
    "reasoning_max_tokens",
    "max_output_tokens",
)


def load_settings(path: Path, mode: str | None = None) -> dict[str, Any]:
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
    return apply_mode(data, mode)


def _as_mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def resolve_mode(settings: dict[str, Any]) -> str:
    """Pick the settings profile: explicit arg > env var > app.mode."""
    mode = (
        os.environ.get(MODE_ENV_VAR)
        or _as_mapping(settings.get("app")).get("mode")
        or DEFAULT_MODE
    )
    return str(mode).strip().lower()


def apply_mode(settings: dict[str, Any], mode: str | None = None) -> dict[str, Any]:
    """Overlay the selected `modes:<name>` block onto the base settings."""
    modes = _as_mapping(settings.get("modes"))
    selected = str(mode or resolve_mode(settings)).strip().lower()
    if not modes:
        return settings

    overrides = _as_mapping(modes.get(selected))
    if not overrides:
        raise ConfigError(
            f"unknown mode '{selected}'; available modes: {', '.join(sorted(modes))}"
        )

    merged = _deep_merge(settings, overrides)
    merged.pop("modes", None)
    app = dict(_as_mapping(merged.get("app")))
    app["mode"] = selected
    merged["app"] = app
    return merged


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
