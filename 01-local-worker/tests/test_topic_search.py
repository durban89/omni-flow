import json
from urllib.parse import quote

import httpx
import pytest

from local_worker.errors import ScrapeError
from local_worker.scraper.base import SourceContext
from local_worker.scraper.http import HttpClient
from local_worker.scraper.topic_search import DEFAULT_ENDPOINT, TopicSearchSource


def _context(handler: httpx.MockTransport) -> SourceContext:
    return SourceContext(
        client=HttpClient(user_agent="TestBot/1.0", transport=handler, delay_ms=0)
    )


def _exa_handler(requests: list[httpx.Request]) -> httpx.MockTransport:
    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        payload = json.loads(request.content)
        query = payload["query"]
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "title": f"Topic for {query}",
                        "url": f"https://news.example.com/{quote(query)}",
                        "text": "A short search snippet about the topic.",
                    },
                    {
                        "title": "Duplicate topic",
                        "url": "https://news.example.com/shared",
                        "text": "Shared across queries.",
                    },
                ]
            },
        )

    return httpx.MockTransport(handle)


def test_topic_search_maps_results_without_crawling(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EXA_API_KEY", "test-key")
    requests: list[httpx.Request] = []
    source = TopicSearchSource(
        "topic_health_zh",
        {
            "queries": ["condom sizing", "latex allergy"],
            "num_results": 5,
            "default_category": "health",
            "default_tags": ["health"],
        },
        _context(_exa_handler(requests)),
    )

    items = source.fetch()

    paths = [str(request.url) for request in requests]
    assert paths and all(path == DEFAULT_ENDPOINT for path in paths)
    assert not any("robots.txt" in path for path in paths)
    assert [request.headers["x-api-key"] for request in requests] == ["test-key", "test-key"]
    assert json.loads(requests[0].content)["numResults"] == 5

    # Two queries, but the shared URL is deduplicated.
    assert len(items) == 3
    first = items[0]
    assert first.category == "health"
    assert "topic" in first.tags
    assert first.keywords == ("condom sizing",)
    assert "condom sizing" in first.body
    assert first.source_url.startswith("https://news.example.com/")
    assert first.body.startswith("Topic lead: ")


def test_topic_search_requires_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("EXA_API_KEY", raising=False)
    source = TopicSearchSource("topic_health_zh", {"query": "condoms"}, _context(_exa_handler([])))

    with pytest.raises(ScrapeError) as excinfo:
        source.fetch()

    assert "EXA_API_KEY" in str(excinfo.value)


def test_topic_search_requires_query(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EXA_API_KEY", "test-key")
    source = TopicSearchSource("topic_health_zh", {}, _context(_exa_handler([])))

    with pytest.raises(ScrapeError) as excinfo:
        source.fetch()

    assert "query" in str(excinfo.value)
