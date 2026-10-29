from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from local_worker.content.text import (
    body_to_markdown,
    clean_summary,
    count_words,
    truncate,
)
from local_worker.errors import ContentError
from local_worker.models import (
    SEO,
    ContentItem,
    RawItem,
    SourceRef,
    canonical_url,
    content_id,
    slugify,
    utc_now,
)


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def unique_slug(base: str, used: set[str]) -> str:
    candidate = base
    counter = 2
    while candidate in used:
        candidate = f"{base}-{counter}"
        counter += 1
    used.add(candidate)
    return candidate


def normalize_raw_items(
    items: Iterable[RawItem],
    settings: dict[str, Any],
) -> list[ContentItem]:
    app = _mapping(settings.get("app"))
    pipeline = _mapping(settings.get("pipeline"))
    compliance = _mapping(settings.get("compliance"))

    locale = str(app.get("default_locale") or "en")
    content_rating = str(compliance.get("default_content_rating") or "general")
    max_items = int(pipeline.get("max_items_per_run") or 20)

    now = utc_now()
    seen_urls: set[str] = set()
    used_slugs: set[str] = set()
    results: list[ContentItem] = []

    for raw in items:
        errors = raw.validate()
        if errors:
            raise ContentError(f"{raw.source_id}: {'; '.join(errors)}")

        url = canonical_url(raw.source_url)
        if not url or url in seen_urls:
            continue
        seen_urls.add(url)

        body = body_to_markdown(raw.body, raw.body_format)
        if not body:
            continue

        title = raw.title.strip()
        summary = clean_summary(raw.summary, body)
        item_id = content_id(url, title)
        base_slug = slugify(title) or f"item-{item_id[:8]}"
        slug = unique_slug(base_slug, used_slugs)

        item = ContentItem(
            id=item_id,
            slug=slug,
            title=title,
            summary=summary,
            body_markdown=body,
            word_count=count_words(body),
            locale=locale,
            category=raw.category.strip() or "general",
            tags=raw.tags,
            keywords=raw.keywords,
            content_rating=content_rating,
            affiliate_slug=raw.affiliate_slug,
            sources=(
                SourceRef(
                    url=url,
                    title=title,
                    publisher=raw.publisher,
                    license=raw.license,
                    retrieved_at=raw.retrieved_at,
                ),
            ),
            status="draft",
            reviewed=False,
            updated_at=now,
            seo=SEO(
                title=title,
                description=truncate(summary, 160),
                canonical=None,
                noindex=True,
            ),
        )
        item.ensure_valid()
        results.append(item)

        if len(results) >= max_items:
            break

    return results
