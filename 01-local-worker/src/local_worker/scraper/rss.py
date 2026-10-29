from __future__ import annotations

import re
from datetime import datetime
from typing import Any
from xml.etree import ElementTree as ET

from local_worker.errors import ScrapeError
from local_worker.models import RawItem, optional_str, parse_datetime, string_list
from local_worker.scraper.base import SourceContext

ATOM = "{http://www.w3.org/2005/Atom}"
CONTENT = "{http://purl.org/rss/1.0/modules/content/}"
DC = "{http://purl.org/dc/elements/1.1/}"


class RssSource:
    def __init__(self, source_id: str, config: dict[str, Any], context: SourceContext) -> None:
        self.source_id = source_id
        self.config = config
        self.context = context

    def fetch(self) -> list[RawItem]:
        url = optional_str(self.config.get("url"))
        if not url:
            raise ScrapeError(f"{self.source_id}: rss source requires url")

        response = self.context.client.get(url)
        items = parse_feed(
            response.content,
            self.source_id,
            self.config,
            self.context.retrieved_at,
        )
        max_items = int(self.config.get("max_items") or 0)
        return items[:max_items] if max_items else items


def parse_feed(
    data: bytes,
    source_id: str,
    config: dict[str, Any],
    retrieved_at: datetime,
) -> list[RawItem]:
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise ScrapeError(f"{source_id}: invalid feed XML: {exc}") from exc

    if root.tag == f"{ATOM}feed":
        return _parse_atom(root, source_id, config, retrieved_at)

    if root.tag == "rss" or root.find("channel") is not None:
        return _parse_rss(root, source_id, config, retrieved_at)

    raise ScrapeError(f"{source_id}: unsupported feed format")


def _parse_rss(
    root: ET.Element,
    source_id: str,
    config: dict[str, Any],
    retrieved_at: datetime,
) -> list[RawItem]:
    channel = root.find("channel")
    if channel is None:
        return []

    feed_title = _text(channel.find("title"))
    feed_license = optional_str(_text(channel.find("copyright"))) or optional_str(
        config.get("license")
    )
    items: list[RawItem] = []

    for entry in channel.findall("item"):
        title = _text(entry.find("title"))
        link = _text(entry.find("link"))
        if not link:
            guid = _text(entry.find("guid"))
            if guid.startswith(("http://", "https://")):
                link = guid

        summary = _text(entry.find("description"))
        body = _text(entry.find(f"{CONTENT}encoded")) or summary
        published_at = parse_datetime(
            _text(entry.find("pubDate")) or _text(entry.find(f"{DC}date"))
        )
        tags = tuple(
            value for value in (_text(category) for category in entry.findall("category")) if value
        )
        publisher = _text(entry.find(f"{DC}creator")) or feed_title

        item = _build_item(
            source_id=source_id,
            config=config,
            retrieved_at=retrieved_at,
            title=title,
            link=link,
            summary=summary,
            body=body,
            body_format="html",
            published_at=published_at,
            tags=tags,
            publisher=publisher,
            license=feed_license,
        )
        if item is not None:
            items.append(item)

    return items


def _parse_atom(
    root: ET.Element,
    source_id: str,
    config: dict[str, Any],
    retrieved_at: datetime,
) -> list[RawItem]:
    feed_title = _text(root.find(f"{ATOM}title"))
    feed_license = optional_str(_text(root.find(f"{ATOM}rights"))) or optional_str(
        config.get("license")
    )
    items: list[RawItem] = []

    for entry in root.findall(f"{ATOM}entry"):
        title = _text(entry.find(f"{ATOM}title"))
        link = ""
        for link_element in entry.findall(f"{ATOM}link"):
            if link_element.get("rel", "alternate") == "alternate" and link_element.get("href"):
                link = link_element.get("href", "")
                break

        summary = _text(entry.find(f"{ATOM}summary"))
        content_element = entry.find(f"{ATOM}content")
        body = _text(content_element) or summary
        content_type = content_element.get("type", "text") if content_element is not None else "text"
        body_format = "html" if content_type in {"html", "xhtml"} else "text"

        published_at = parse_datetime(
            _text(entry.find(f"{ATOM}published"))
            or _text(entry.find(f"{ATOM}updated"))
        )
        tags = tuple(
            value
            for value in (
                category.get("term", "") for category in entry.findall(f"{ATOM}category")
            )
            if value
        )
        publisher = _text(entry.find(f"{ATOM}author/{ATOM}name")) or feed_title

        item = _build_item(
            source_id=source_id,
            config=config,
            retrieved_at=retrieved_at,
            title=title,
            link=link,
            summary=summary,
            body=body,
            body_format=body_format,
            published_at=published_at,
            tags=tags,
            publisher=publisher,
            license=feed_license,
        )
        if item is not None:
            items.append(item)

    return items


def _build_item(
    *,
    source_id: str,
    config: dict[str, Any],
    retrieved_at: datetime,
    title: str,
    link: str,
    summary: str,
    body: str,
    body_format: str,
    published_at: datetime | None,
    tags: tuple[str, ...],
    publisher: str | None,
    license: str | None,
) -> RawItem | None:
    title = title.strip()
    link = link.strip()
    if not title or not link:
        return None

    default_tags = string_list(config.get("default_tags"))
    return RawItem(
        source_id=source_id,
        source_url=link,
        title=title,
        summary=summary,
        body=body or summary or title,
        body_format=body_format,
        retrieved_at=retrieved_at,
        category=str(config.get("default_category") or "general"),
        tags=string_list(list(default_tags) + list(tags)),
        keywords=string_list(config.get("default_keywords")),
        published_at=published_at,
        publisher=publisher,
        license=license or optional_str(config.get("license")),
        affiliate_slug=optional_str(config.get("affiliate_slug")),
    )


def _text(element: ET.Element | None) -> str:
    if element is None:
        return ""
    return re.sub(r"\s+", " ", "".join(element.itertext())).strip()
