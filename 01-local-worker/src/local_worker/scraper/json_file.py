from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from local_worker.errors import ScrapeError
from local_worker.models import RawItem, optional_str, parse_datetime, string_list
from local_worker.scraper.base import SourceContext


class JsonFileSource:
    def __init__(self, source_id: str, config: dict[str, Any], context: SourceContext) -> None:
        self.source_id = source_id
        self.config = config
        self.context = context

    def fetch(self) -> list[RawItem]:
        path_value = optional_str(self.config.get("path"))
        url_value = optional_str(self.config.get("url"))
        if not path_value and not url_value:
            raise ScrapeError(f"{self.source_id}: json source requires path or url")

        if url_value:
            resp = self.context.client.get(url_value)
            try:
                data = resp.json()
            except (json.JSONDecodeError, ValueError) as exc:
                raise ScrapeError(f"{self.source_id}: invalid JSON from {url_value}: {exc}") from exc
            source_desc = url_value
        else:
            assert path_value is not None
            path = Path(path_value).expanduser()
            if not path.is_absolute():
                path = Path.cwd() / path

            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except FileNotFoundError as exc:
                raise ScrapeError(f"{self.source_id}: file not found: {path}") from exc
            except json.JSONDecodeError as exc:
                raise ScrapeError(f"{self.source_id}: invalid JSON in {path}: {exc}") from exc
            source_desc = str(path)

        entries = data.get("items") if isinstance(data, dict) else data
        if not isinstance(entries, list):
            raise ScrapeError(f"{self.source_id}: expected an items array in {source_desc}")

        default_category = str(self.config.get("default_category") or "general")
        default_tags = string_list(self.config.get("default_tags"))
        default_keywords = string_list(self.config.get("default_keywords"))
        default_publisher = optional_str(self.config.get("publisher"))
        default_license = optional_str(self.config.get("license"))
        default_affiliate = optional_str(self.config.get("affiliate_slug"))
        max_items = int(self.config.get("max_items") or 0)

        items: list[RawItem] = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue

            title = str(entry.get("title") or "").strip()
            url = str(entry.get("url") or entry.get("source_url") or "").strip()
            summary = str(entry.get("summary") or "")

            body = ""
            body_format = "markdown"
            if entry.get("body_html") is not None:
                body = str(entry["body_html"])
                body_format = "html"
            elif entry.get("body_markdown") is not None:
                body = str(entry["body_markdown"])
                body_format = "markdown"
            elif entry.get("body") is not None:
                body = str(entry["body"])
                body_format = str(entry.get("body_format") or "markdown")
            elif summary:
                body = summary
                body_format = "text"

            tags = string_list(list(default_tags) + list(string_list(entry.get("tags"))))
            keywords = string_list(
                list(default_keywords) + list(string_list(entry.get("keywords")))
            )

            item = RawItem(
                source_id=self.source_id,
                source_url=url,
                title=title,
                summary=summary,
                body=body,
                body_format=body_format,
                retrieved_at=self.context.retrieved_at,
                category=str(entry.get("category") or default_category),
                tags=tags,
                keywords=keywords,
                published_at=parse_datetime(entry.get("published_at")),
                publisher=optional_str(entry.get("publisher")) or default_publisher,
                license=optional_str(entry.get("license")) or default_license,
                affiliate_slug=optional_str(entry.get("affiliate_slug")) or default_affiliate,
            )
            errors = item.validate()
            if errors:
                raise ScrapeError(f"{self.source_id}: invalid item: {'; '.join(errors)}")

            items.append(item)
            if max_items and len(items) >= max_items:
                break

        return items
