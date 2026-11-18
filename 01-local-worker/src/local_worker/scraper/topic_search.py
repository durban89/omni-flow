from __future__ import annotations

import logging
import os
from typing import Any

from local_worker.errors import ScrapeError
from local_worker.models import (
    RawItem,
    canonical_url,
    optional_str,
    parse_datetime,
    string_list,
)
from local_worker.scraper.base import SourceContext

logger = logging.getLogger("local_worker.scraper.topic_search")

DEFAULT_ENDPOINT = "https://api.exa.ai/search"
DEFAULT_API_KEY_ENV = "EXA_API_KEY"
SNIPPET_CHARS = 400


class TopicSearchSource:
    """Discovers article topics through a search API instead of scraping pages.

    The adapter keeps only search metadata (title, URL, short snippet) as an
    editorial lead. The content stage writes the article from scratch, so no
    third-party text is republished and no page is crawled.
    """

    def __init__(self, source_id: str, config: dict[str, Any], context: SourceContext) -> None:
        self.source_id = source_id
        self.config = config
        self.context = context

    def fetch(self) -> list[RawItem]:
        api_key_env = str(self.config.get("api_key_env") or DEFAULT_API_KEY_ENV)
        api_key = os.environ.get(api_key_env, "").strip()
        if not api_key:
            raise ScrapeError(
                f"{self.source_id}: missing API key, set {api_key_env} in the environment or .env"
            )

        queries = self._queries()
        if not queries:
            raise ScrapeError(f"{self.source_id}: topic_search source requires query or queries")

        endpoint = str(self.config.get("endpoint") or DEFAULT_ENDPOINT)
        num_results = int(self.config.get("num_results") or 10)
        max_items = int(self.config.get("max_items") or 0)

        items: list[RawItem] = []
        seen_urls: set[str] = set()
        for query in queries:
            for result in self._search(endpoint, api_key, query, num_results):
                item = self._to_raw_item(result, query)
                if item is None:
                    continue
                key = canonical_url(item.source_url)
                if key in seen_urls:
                    continue
                seen_urls.add(key)
                items.append(item)
                if max_items and len(items) >= max_items:
                    return items

        logger.info("[topic_search] %s: %d topic(s) from %d query(ies)", self.source_id, len(items), len(queries))
        return items

    def _queries(self) -> list[str]:
        raw: list[Any] = []
        queries = self.config.get("queries")
        if isinstance(queries, list):
            raw.extend(queries)
        single = optional_str(self.config.get("query"))
        if single:
            raw.append(single)

        result: list[str] = []
        for entry in raw:
            text = str(entry).strip()
            if text and text not in result:
                result.append(text)
        return result

    def _search(
        self,
        endpoint: str,
        api_key: str,
        query: str,
        num_results: int,
    ) -> list[dict[str, Any]]:
        payload: dict[str, Any] = {
            "query": query,
            "numResults": max(1, num_results),
            "type": "auto",
            "contents": {"text": {"maxCharacters": SNIPPET_CHARS}},
        }
        if optional_str(self.config.get("include_domains")):
            payload["includeDomains"] = string_list(self.config.get("include_domains"))

        # The endpoint is a documented API, not a crawlable page, so robots.txt
        # does not apply; the provider's API terms do.
        response = self.context.client.post(
            endpoint,
            json=payload,
            headers={"x-api-key": api_key, "Content-Type": "application/json"},
            check_robots=False,
        )
        try:
            data = response.json()
        except ValueError as exc:
            raise ScrapeError(f"{self.source_id}: invalid JSON from {endpoint}: {exc}") from exc

        results = data.get("results") if isinstance(data, dict) else None
        if not isinstance(results, list):
            raise ScrapeError(f"{self.source_id}: unexpected response from {endpoint}")

        return [entry for entry in results if isinstance(entry, dict)]

    def _to_raw_item(self, result: dict[str, Any], query: str) -> RawItem | None:
        url = optional_str(result.get("url"))
        title = optional_str(result.get("title")) or url
        if not url or not title:
            return None

        snippet = str(result.get("text") or "").strip().replace("\n", " ")
        snippet = snippet[:SNIPPET_CHARS]

        # Body is a writing brief, not source text: it only tells the generator
        # what the topic is and which seed query produced it.
        brief_parts = [f"Topic lead: {title}", f"Seed query: {query}"]
        if snippet:
            brief_parts.append(f"Search context: {snippet}")

        default_tags = string_list(self.config.get("default_tags"))
        tags = list(dict.fromkeys([*default_tags, "topic"]))

        item = RawItem(
            source_id=self.source_id,
            source_url=url,
            title=title,
            summary=snippet,
            body="\n".join(brief_parts),
            body_format="text",
            retrieved_at=self.context.retrieved_at,
            category=str(self.config.get("default_category") or "general"),
            tags=tuple(tags),
            keywords=(query,),
            published_at=parse_datetime(result.get("publishedDate")),
            publisher=optional_str(result.get("author")),
        )
        errors = item.validate()
        if errors:
            logger.debug("[topic_search] skip %s: %s", url, "; ".join(errors))
            return None
        return item
