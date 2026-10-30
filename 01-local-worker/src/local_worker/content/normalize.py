from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import Any

from local_worker.content.generator import ContentGenerator
from local_worker.content.text import (
    body_to_markdown,  # Retained for fallback & non-LLM pipelines
    clean_summary,  # Retained for fallback & non-LLM pipelines
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
    parse_datetime,
    slugify,
    utc_now,
)

logger = logging.getLogger(__name__)


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
    master_db_path: str = "",
    *,
    reviews: dict[str, dict[str, Any]] | None = None,
) -> list[ContentItem]:
    app = _mapping(settings.get("app"))
    pipeline = _mapping(settings.get("pipeline"))
    compliance = _mapping(settings.get("compliance"))
    llm_settings = _mapping(settings.get("llm"))

    locale = str(app.get("default_locale") or "en")
    content_rating = str(compliance.get("default_content_rating") or "general")
    max_items = int(pipeline.get("max_items_per_run") or 20)
    auto_approve = bool(pipeline.get("auto_approve", False))

    # Initialize generator and check credentials/explicit activation flags
    generator = ContentGenerator(config=llm_settings)
    generator = generator.set_master_db_path(master_db_path)
    enable_llm = bool(llm_settings.get("enable", False)) and generator.has_llm_credentials

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

        title = raw.title.strip()
        item_id = content_id(url, title)

        existing_review = reviews.get(item_id) if reviews else None
            
        # ===== FIX: Check if LLM processing is active AND has valid credentials =====
        if enable_llm and not existing_review:
            logger.info("[generator] Processing content via LLM generator for item_id: %s", item_id)
            
            is_health_category = raw.category.strip().lower() in {"health", "medical", "wellness"}
            generator.set_health_mode(is_health_category)
            
            body, summary = generator.generate(raw)
        else:
            # Fallback path for unit tests or skipped items: Parse natively without template overruns
            body = body_to_markdown(raw.body, raw.body_format)
            if not body:
                continue
            summary = clean_summary(raw.summary, body)

        base_slug = slugify(title) or f"item-{item_id[:8]}"
        slug = unique_slug(base_slug, used_slugs)
        words = count_words(body)

        # Default draft state
        status = "draft"
        reviewed = False
        published_at = None
        noindex = True

        logger.info("[generator] Processing body: %s", body)
        logger.info("[generator] Processing summary: %s", summary)

        if existing_review:
            status = str(existing_review.get("status") or "draft")
            reviewed = bool(existing_review.get("reviewed", False))
            published_at = parse_datetime(existing_review.get("published_at"))
            noindex = status != "published"
        elif auto_approve and words >= 300:
            status = "published"
            reviewed = True
            published_at = now
            noindex = False

        item = ContentItem(
            id=item_id,
            slug=slug,
            title=title,
            summary=summary,
            body_markdown=body,
            word_count=words,
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
            status=status,
            reviewed=reviewed,
            published_at=published_at,
            updated_at=now,
            seo=SEO(
                title=title,
                description=truncate(summary, 160),
                canonical=None,
                noindex=noindex,
            ),
        )
        item.ensure_valid()
        results.append(item)

        if len(results) >= max_items:
            break

    return results
