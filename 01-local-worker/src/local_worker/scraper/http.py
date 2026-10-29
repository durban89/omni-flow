from __future__ import annotations

import logging
import time
from typing import Self
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import httpx
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from local_worker.errors import ScrapeError

logger = logging.getLogger("local_worker.http")


def _is_retryable_error(exc: BaseException) -> bool:
    if isinstance(exc, (httpx.TimeoutException, httpx.NetworkError)):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in {429, 500, 502, 503, 504}
    return False


class HttpClient:
    def __init__(
        self,
        user_agent: str,
        *,
        timeout_seconds: float = 20.0,
        delay_ms: int = 1500,
        respect_robots: bool = True,
        transport: httpx.BaseTransport | None = None,
        max_retries: int = 3,
    ) -> None:
        self.user_agent = user_agent
        self.delay_seconds = max(0, delay_ms) / 1000
        self.respect_robots = respect_robots
        self.max_retries = max(1, max_retries)
        self._last_request: float | None = None
        self._robots: dict[str, RobotFileParser] = {}
        self._client = httpx.Client(
            headers={"User-Agent": user_agent},
            timeout=timeout_seconds,
            follow_redirects=True,
            transport=transport,
        )

    def __enter__(self) -> Self:
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    @retry(
        retry=retry_if_exception(_is_retryable_error),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=0.5, min=0.5, max=5.0),
        reraise=True,
    )
    def _fetch_url(self, url: str) -> httpx.Response:
        response = self._client.get(url)
        response.raise_for_status()
        return response

    def get(self, url: str, *, check_robots: bool = True) -> httpx.Response:
        if not _is_http_url(url):
            raise ScrapeError(f"invalid http url: {url}")
        if check_robots and not self.can_fetch(url):
            raise ScrapeError(f"robots.txt disallows: {url}")

        self._wait()
        try:
            return self._fetch_url(url)
        except httpx.HTTPError as exc:
            logger.warning("Request failed for %s after retries: %s", url, exc)
            raise ScrapeError(f"request failed for {url}: {exc}") from exc

    def can_fetch(self, url: str) -> bool:
        if not self.respect_robots:
            return True

        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return False

        origin = f"{parsed.scheme}://{parsed.netloc}"
        parser = self._robots.get(origin)
        if parser is None:
            parser = self._load_robots(origin)
            self._robots[origin] = parser
        return parser.can_fetch(self.user_agent, url)

    def _load_robots(self, origin: str) -> RobotFileParser:
        parser = RobotFileParser()
        try:
            response = self._client.get(f"{origin}/robots.txt")
        except httpx.HTTPError:
            parser.parse(["User-agent: *", "Disallow: /"])
            return parser

        if response.status_code == 404:
            parser.parse([])
        elif response.status_code >= 400:
            parser.parse(["User-agent: *", "Disallow: /"])
        else:
            parser.parse(response.text.splitlines())
        return parser

    def _wait(self) -> None:
        if self.delay_seconds and self._last_request is not None:
            elapsed = time.monotonic() - self._last_request
            remaining = self.delay_seconds - elapsed
            if remaining > 0:
                time.sleep(remaining)
        self._last_request = time.monotonic()


def _is_http_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)
