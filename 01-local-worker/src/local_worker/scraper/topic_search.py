from __future__ import annotations

import logging
import os
import re
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

# Words that carry no topic signal in English headlines. Headline similarity is
# part of lead dedupe, so "How to Choose the Right Condom Size Chart" and
# "Condom Size Chart: How It Matters" need to collapse to the same topic.
_TITLE_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from",
    "how", "in", "is", "it", "of", "on", "or", "that", "the", "this",
    "to", "was", "were", "what", "when", "where", "which", "who", "will",
    "with", "you", "your",
}


def _query_key(value: str) -> str:
    return " ".join(value.casefold().split())


def _title_tokens(value: str) -> frozenset[str]:
    words = re.findall(r"[a-z0-9]+", value.casefold())
    return frozenset(word for word in words if len(word) > 2 and word not in _TITLE_STOPWORDS)


def _is_similar_title(title: str, known_titles: list[str]) -> bool:
    tokens = _title_tokens(title)
    if len(tokens) < 2:
        return False
    for known in known_titles:
        other = _title_tokens(known)
        if len(other) < 2:
            continue
        overlap = len(tokens & other)
        if overlap < 2:
            continue
        containment = overlap / min(len(tokens), len(other))
        jaccard = overlap / len(tokens | other)
        if containment >= 0.6 or jaccard >= 0.5:
            return True
    return False


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
        limit = self.context.max_new_items or max_items
        # A query is a topic, not a feed. Taking many results for one query is
        # what produced several nearly identical "condom size chart" articles.
        per_query_limit = int(self.config.get("max_items_per_query") or 0)
        # Bound the number of search API calls so a source full of known URLs
        # cannot keep querying forever. Defaults to one attempt per query.
        max_attempts = int(self.config.get("max_attempts") or len(queries))
        attempts = 0
        used_queries = {_query_key(query) for query in self.context.exclude_queries}
        known_titles = list(self.context.exclude_titles)

        # A query that already produced an article is only deprioritised, not
        # dropped: search engines return new results over time, and blocking the
        # query forever leaves a small query list with nothing to collect. Fresh
        # queries are searched first; used ones are retried afterwards, with URL
        # and title dedupe still deciding what counts as a new lead.
        fresh_queries = [query for query in queries if _query_key(query) not in used_queries]
        retry_queries = [query for query in queries if _query_key(query) in used_queries]
        if not fresh_queries and retry_queries:
            logger.info(
                "[topic_search] %s: all %d configured queries were used before; "
                "retrying them for new leads",
                self.source_id,
                len(retry_queries),
            )

        items: list[RawItem] = []
        seen_urls: set[str] = set()
        for query in [*fresh_queries, *retry_queries]:
            if attempts >= max_attempts:
                break
            attempts += 1
            query_items = 0
            for result in self._search(endpoint, api_key, query, num_results):
                item = self._to_raw_item(result, query)
                if item is None:
                    continue
                key = canonical_url(item.source_url)
                if key in seen_urls or key in self.context.exclude_urls:
                    continue
                if _is_similar_title(item.title, known_titles):
                    logger.info("[topic_search] %s: skip similar topic %r", self.source_id, item.title)
                    continue
                seen_urls.add(key)
                known_titles.append(item.title)
                items.append(item)
                query_items += 1
                if per_query_limit and query_items >= per_query_limit:
                    break
                if limit and len(items) >= limit:
                    logger.info(
                        "[topic_search] %s: %d new topic(s) from %d query attempt(s)",
                        self.source_id,
                        len(items),
                        attempts,
                    )
                    return items
            if limit and len(items) >= limit:
                logger.info(
                    "[topic_search] %s: %d new topic(s) from %d query attempt(s)",
                    self.source_id,
                    len(items),
                    attempts,
                )
                return items

        logger.info(
            "[topic_search] %s: %d new topic(s) from %d query attempt(s) (max %d)",
            self.source_id,
            len(items),
            attempts,
            max_attempts,
        )
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
