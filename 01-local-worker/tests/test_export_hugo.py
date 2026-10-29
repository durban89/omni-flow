from pathlib import Path

from local_worker.content.export_hugo import export_to_hugo
from local_worker.models import SEO, ContentItem, SourceRef, utc_now


def test_export_to_hugo(tmp_path: Path) -> None:
    site_dir = tmp_path / "site"
    now = utc_now()
    item = ContentItem(
        id="abc1234567890123",
        slug="test-product-review",
        title="Test Product Review",
        summary="A summary of test product.",
        body_markdown="## Features\n\nGreat features.",
        word_count=5,
        locale="en",
        category="guides",
        content_rating="general",
        status="published",
        reviewed=True,
        updated_at=now,
        published_at=now,
        seo=SEO(
            title="Test Product Review",
            description="A summary of test product.",
            noindex=False,
        ),
        tags=("tech", "gadget"),
        sources=(
            SourceRef(
                url="https://example.com/source",
                title="Source Title",
            ),
        ),
    )

    written = export_to_hugo([item], site_dir)
    assert len(written) == 1
    post_file = written[0]
    assert post_file.name == "test-product-review.md"
    assert post_file.exists()

    content = post_file.read_text(encoding="utf-8")
    assert "title: Test Product Review" in content
    assert "draft: false" in content
    assert "reviewed: true" in content
    assert "## Features" in content

    # Verify site/data/content_items.json
    data_file = site_dir / "data" / "content_items.json"
    assert data_file.exists()
