import json
from pathlib import Path

import pytest

from local_worker.content.review import ReviewService
from local_worker.errors import ContentError


def _setup_content_data(data_dir: Path, word_count: int = 350) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    body = " ".join(["word"] * word_count)
    item = {
        "schema_version": "1.0",
        "id": "1234567890abcdef",
        "slug": "sample-guide",
        "title": "Sample Guide",
        "summary": "Sample summary",
        "body_markdown": body,
        "word_count": word_count,
        "locale": "en",
        "category": "guides",
        "tags": ["test"],
        "keywords": [],
        "content_rating": "general",
        "affiliate_slug": "test-offer",
        "sources": [
            {
                "url": "https://example.com/test",
                "title": "Test Source",
                "retrieved_at": "2026-01-01T00:00:00Z",
            }
        ],
        "status": "draft",
        "reviewed": False,
        "published_at": None,
        "updated_at": "2026-01-01T00:00:00Z",
        "seo": {
            "title": "Sample Guide",
            "description": "Sample summary",
            "canonical": None,
            "noindex": True,
        },
    }
    payload = {
        "schema_version": "1.0",
        "generated_at": "2026-01-01T00:00:00Z",
        "item_count": 1,
        "items": [item],
    }
    (data_dir / "content_items.json").write_text(json.dumps(payload), encoding="utf-8")


def test_review_service_approve_and_list(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _setup_content_data(data_dir, word_count=350)

    service = ReviewService(data_dir)
    summary = service.list_summary()
    assert len(summary["pending"]) == 1
    assert summary["pending"][0]["slug"] == "sample-guide"

    approved = service.approve("sample-guide", reviewer="tester", notes="Looks great")
    assert approved.status == "published"
    assert approved.reviewed is True
    assert approved.published_at is not None
    assert approved.seo.noindex is False

    summary_after = service.list_summary()
    assert len(summary_after["pending"]) == 0
    assert len(summary_after["published"]) == 1


def test_review_service_reject_short_words(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _setup_content_data(data_dir, word_count=100)

    service = ReviewService(data_dir)
    with pytest.raises(ContentError, match="at least 300 words"):
        service.approve("sample-guide")
