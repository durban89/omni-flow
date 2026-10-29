import httpx
import pytest

from local_worker.errors import ScrapeError
from local_worker.scraper.http import HttpClient


def test_http_client_invalid_url() -> None:
    client = HttpClient("TestBot/1.0", respect_robots=False)
    with pytest.raises(ScrapeError, match="invalid http url"):
        client.get("not-a-valid-url")
    client.close()


def test_http_client_retries_and_fails() -> None:
    # Use a custom transport that returns 500
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, request=request)

    transport = httpx.MockTransport(handler)
    client = HttpClient("TestBot/1.0", respect_robots=False, transport=transport, delay_ms=0)
    with pytest.raises(ScrapeError, match="request failed"):
        client.get("https://example.com/test")
    client.close()


def test_http_client_success_mock() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="hello world", request=request)

    transport = httpx.MockTransport(handler)
    client = HttpClient("TestBot/1.0", respect_robots=False, transport=transport, delay_ms=0)
    res = client.get("https://example.com/ok")
    assert res.status_code == 200
    assert res.text == "hello world"
    client.close()
