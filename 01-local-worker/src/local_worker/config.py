from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from local_worker.errors import ConfigError


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
