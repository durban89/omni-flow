import pytest

from local_worker.errors import ScrapeError
from local_worker.scraper.base import SourceContext
from local_worker.scraper.http import HttpClient
from local_worker.scraper.playwright_source import PlaywrightSource


def test_playwright_source_missing_url() -> None:
    client = HttpClient("Bot/1.0", respect_robots=False)
    context = SourceContext(client=client)
    source = PlaywrightSource("pw", {}, context)
    with pytest.raises(ScrapeError, match="requires url"):
        source.fetch()
    client.close()


def test_playwright_source_disallowed_by_robots() -> None:
    client = HttpClient("Bot/1.0", respect_robots=True)
    # mock robots
    client._robots["https://disallowed.com"] = type(
        "MockParser", (), {"can_fetch": lambda *args: False}
    )()
    context = SourceContext(client=client)
    source = PlaywrightSource("pw", {"url": "https://disallowed.com/page"}, context)
    with pytest.raises(ScrapeError, match="robots.txt disallows"):
        source.fetch()
    client.close()
