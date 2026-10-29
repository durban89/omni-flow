from __future__ import annotations

from typing import Any

from local_worker.errors import ConfigError
from local_worker.scraper.base import SourceAdapter, SourceContext
from local_worker.scraper.json_file import JsonFileSource
from local_worker.scraper.rss import RssSource


def build_sources(
    settings: dict[str, Any],
    context: SourceContext,
) -> list[SourceAdapter]:
    scraper_config = settings.get("scraper")
    if not isinstance(scraper_config, dict):
        return []

    sources = scraper_config.get("sources") or []
    if not isinstance(sources, list):
        raise ConfigError("scraper.sources must be a list")

    adapters: list[SourceAdapter] = []
    for entry in sources:
        if not isinstance(entry, dict):
            raise ConfigError("each scraper source must be a mapping")
        if not bool(entry.get("enabled", True)):
            continue

        source_id = str(entry.get("id") or "").strip()
        source_type = str(entry.get("type") or "").strip()
        if not source_id:
            raise ConfigError("scraper source requires id")
        if not source_type:
            raise ConfigError(f"scraper source '{source_id}' requires type")

        if source_type == "json_file":
            adapters.append(JsonFileSource(source_id, entry, context))
        elif source_type == "rss":
            adapters.append(RssSource(source_id, entry, context))
        else:
            raise ConfigError(f"unsupported scraper source type: {source_type}")

    return adapters


__all__ = ["SourceAdapter", "SourceContext", "build_sources"]
