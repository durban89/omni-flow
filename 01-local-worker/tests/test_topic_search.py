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


def test_topic_search_skips_existing_and_continues_queries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("EXA_API_KEY", "test-key")
    existing_url = "https://news.example.com/already-used"
    requests: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        query = json.loads(request.content)["query"]
        results = {
            "first query": [
                {"title": "Already used", "url": existing_url, "text": "Old lead."}
            ],
            "second query": [
                {"title": "Fresh topic", "url": "https://news.example.com/fresh", "text": "New lead."}
            ],
        }[query]
        return httpx.Response(200, json={"results": results})

    source = TopicSearchSource(
        "topic_health_en",
        {"queries": ["first query", "second query"], "max_items": 5, "max_attempts": 3},
        SourceContext(
            client=HttpClient(user_agent="TestBot/1.0", transport=httpx.MockTransport(handle), delay_ms=0),
            exclude_urls=frozenset({existing_url}),
            max_new_items=1,
        ),
    )

    items = source.fetch()

    assert [item.source_url for item in items] == ["https://news.example.com/fresh"]
    assert len(requests) == 2


def test_topic_search_stops_at_max_attempts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EXA_API_KEY", "test-key")
    existing_url = "https://news.example.com/already-used"
    requests: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={"results": [{"title": "Old", "url": existing_url, "text": "Old lead."}]},
        )

    source = TopicSearchSource(
        "topic_health_en",
        {
            "queries": ["first query", "second query", "third query"],
            "max_attempts": 2,
        },
        SourceContext(
            client=HttpClient(user_agent="TestBot/1.0", transport=httpx.MockTransport(handle), delay_ms=0),
            exclude_urls=frozenset({existing_url}),
            max_new_items=1,
        ),
    )

    assert source.fetch() == []
    assert len(requests) == 2


def test_topic_search_skips_used_queries(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EXA_API_KEY", "test-key")
    requests: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        query = json.loads(request.content)["query"]
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "title": f"Fresh article for {query}",
                        "url": f"https://news.example.com/{quote(query)}",
                        "text": "New lead.",
                    }
                ]
            },
        )

    source = TopicSearchSource(
        "topic_health_en",
        {"queries": ["used query", "fresh query"], "max_items": 5},
        SourceContext(
            client=HttpClient(user_agent="TestBot/1.0", transport=httpx.MockTransport(handle), delay_ms=0),
            exclude_queries=frozenset({"used query"}),
            max_new_items=1,
        ),
    )

    items = source.fetch()

    assert [item.keywords for item in items] == [("fresh query",)]
    assert len(requests) == 1
    assert json.loads(requests[0].content)["query"] == "fresh query"


def test_topic_search_retries_used_queries_for_new_leads(monkeypatch: pytest.MonkeyPatch) -> None:
    """An exhausted query list must still be searched instead of returning nothing."""
    monkeypatch.setenv("EXA_API_KEY", "test-key")
    requests: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        query = json.loads(request.content)["query"]
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "title": f"A new angle on {query}",
                        "url": f"https://news.example.com/{quote(query)}/new",
                        "text": "A lead that was not collected before.",
                    }
                ]
            },
        )

    source = TopicSearchSource(
        "topic_health_en",
        {"queries": ["used query"], "max_items": 5},
        SourceContext(
            client=HttpClient(user_agent="TestBot/1.0", transport=httpx.MockTransport(handle), delay_ms=0),
            exclude_queries=frozenset({"used query"}),
            max_new_items=1,
        ),
    )

    items = source.fetch()

    assert [item.keywords for item in items] == [("used query",)]
    assert len(requests) == 1


def test_topic_search_limits_results_per_query(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EXA_API_KEY", "test-key")

    def handle(request: httpx.Request) -> httpx.Response:
        query = json.loads(request.content)["query"]
        titles = {
            "first query": ("Alpha sizing guide", "Alpha sizing notes"),
            "second query": ("Beta latex guide", "Beta latex notes"),
        }[query]
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "title": titles[0],
                        "url": f"https://news.example.com/{quote(query)}/one",
                        "text": "First lead.",
                    },
                    {
                        "title": titles[1],
                        "url": f"https://news.example.com/{quote(query)}/two",
                        "text": "Second lead.",
                    },
                ]
            },
        )

    source = TopicSearchSource(
        "topic_health_en",
        {"queries": ["first query", "second query"], "max_items": 10, "max_items_per_query": 1},
        SourceContext(
            client=HttpClient(user_agent="TestBot/1.0", transport=httpx.MockTransport(handle), delay_ms=0),
            max_new_items=10,
        ),
    )

    items = source.fetch()

    assert [item.source_url for item in items] == [
        "https://news.example.com/first%20query/one",
        "https://news.example.com/second%20query/one",
    ]


def test_topic_search_skips_similar_existing_title(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EXA_API_KEY", "test-key")
    requests: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        query = json.loads(request.content)["query"]
        if query == "sizing":
            results = [
                {
                    "title": "Condom Size Chart: How (and Why) It Matters",
                    "url": "https://news.example.com/duplicate-sizing",
                    "text": "Same topic.",
                }
            ]
        else:
            results = [
                {
                    "title": "Latex allergy: polyurethane condom alternatives",
                    "url": "https://news.example.com/latex",
                    "text": "Different topic.",
                }
            ]
        return httpx.Response(200, json={"results": results})

    source = TopicSearchSource(
        "topic_health_en",
        {"queries": ["sizing", "latex"], "max_items": 5},
        SourceContext(
            client=HttpClient(user_agent="TestBot/1.0", transport=httpx.MockTransport(handle), delay_ms=0),
            exclude_titles=("Condom size chart: how to measure for the right fit",),
            max_new_items=1,
        ),
    )

    items = source.fetch()

    assert [item.source_url for item in items] == ["https://news.example.com/latex"]
    assert len(requests) == 2


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
