from __future__ import annotations

import logging
from typing import Any

from local_worker.errors import ScrapeError
from local_worker.models import RawItem, optional_str, string_list
from local_worker.scraper.base import SourceContext

logger = logging.getLogger("local_worker.scraper.playwright")


class PlaywrightSource:
    """Dynamic browser scraper using Playwright."""

    def __init__(
        self,
        source_id: str,
        config: dict[str, Any],
        context: SourceContext,
    ) -> None:
        self.source_id = source_id
        self.config = config
        self.context = context

    def fetch(self) -> list[RawItem]:
        url = optional_str(self.config.get("url"))
        if not url:
            raise ScrapeError(f"{self.source_id}: playwright source requires url")

        if not self.context.client.can_fetch(url):
            raise ScrapeError(f"{self.source_id}: robots.txt disallows {url}")

        try:
            from playwright.sync_api import TimeoutError as PlaywrightTimeout
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise ScrapeError(
                f"{self.source_id}: Playwright is not installed. "
                "Install with: pip install playwright && playwright install chromium"
            ) from exc

        timeout_ms = int(self.config.get("timeout_ms") or 30000)
        wait_selector = optional_str(self.config.get("wait_selector"))

        logger.info("[%s] Fetching %s with Playwright", self.source_id, url)
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                page = browser.new_page(user_agent=self.context.client.user_agent)
                page.set_default_timeout(timeout_ms)

                page.goto(url, wait_until="domcontentloaded")
                if wait_selector:
                    page.wait_for_selector(wait_selector, timeout=timeout_ms)

                title = page.title() or ""
                summary = ""
                meta_desc = page.locator("meta[name='description']").first
                if meta_desc.count() > 0:
                    summary = meta_desc.get_attribute("content") or ""

                content_html = page.content()
                browser.close()
        except PlaywrightTimeout as exc:
            raise ScrapeError(f"{self.source_id}: Playwright timeout for {url}: {exc}") from exc
        except Exception as exc:
            raise ScrapeError(f"{self.source_id}: Playwright navigation failed for {url}: {exc}") from exc

        item = RawItem(
            source_id=self.source_id,
            source_url=url,
            title=title.strip() or url,
            summary=summary.strip(),
            body=content_html,
            body_format="html",
            retrieved_at=self.context.retrieved_at,
            category=str(self.config.get("default_category") or "general"),
            tags=string_list(self.config.get("default_tags")),
            keywords=string_list(self.config.get("default_keywords")),
            publisher=optional_str(self.config.get("publisher")),
            license=optional_str(self.config.get("license")),
            affiliate_slug=optional_str(self.config.get("affiliate_slug")),
        )

        errors = item.validate()
        if errors:
            raise ScrapeError(f"{self.source_id}: invalid scraped item: {'; '.join(errors)}")

        return [item]
