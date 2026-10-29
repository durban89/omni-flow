from datetime import UTC, datetime
from typing import Any

from local_worker.models import (
    SEO,
    ContentItem,
    SourceRef,
    parse_datetime,
    slugify,
    utc_now,
)


def make_content(**overrides: Any) -> ContentItem:
    values: dict[str, Any] = {
        "id": "abc123",
        "slug": "sample-post",
        "title": "Sample Post",
        "summary": "A short summary for the sample post.",
        "body_markdown": "word " * 320,
        "word_count": 320,
        "locale": "en",
        "category": "guides",
        "content_rating": "general",
        "status": "published",
        "reviewed": True,
        "updated_at": utc_now(),
        "seo": SEO(
            title="Sample Post",
            description="A short summary for the sample post.",
            noindex=False,
        ),
        "sources": (SourceRef(url="https://example.com/post"),),
        "published_at": utc_now(),
    }
    values.update(overrides)
    return ContentItem(**values)


def test_content_item_roundtrip() -> None:
    item = make_content()

    assert item.validate() == []
    assert ContentItem.from_dict(item.to_dict()) == item


def test_published_content_requires_review() -> None:
    item = make_content(reviewed=False)

    errors = item.validate()

    assert any("reviewed" in error for error in errors)


def test_draft_content_must_be_noindex() -> None:
    item = make_content(
        status="draft",
        reviewed=False,
        published_at=None,
        seo=SEO(
            title="Sample Post",
            description="A short summary.",
            noindex=False,
        ),
    )

    errors = item.validate()

    assert any("noindex" in error for error in errors)


def test_parse_datetime_accepts_rfc822() -> None:
    value = parse_datetime("Tue, 02 Jan 2026 09:00:00 GMT")

    assert value == datetime(2026, 1, 2, 9, 0, tzinfo=UTC)


def test_slugify() -> None:
    assert slugify("How to Compare Product Specifications!") == (
        "how-to-compare-product-specifications"
    )
