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

    assert "lang: en" in content

    # Verify site/data/content_items.json
    data_file = site_dir / "data" / "content_items.json"
    assert data_file.exists()


def _item(**overrides: object) -> ContentItem:
    now = utc_now()
    values: dict[str, object] = {
        "id": "abc1234567890123",
        "slug": "sample-post",
        "title": "示例标题",
        "summary": "摘要",
        "body_markdown": "# 示例标题\n\n正文段落。",
        "word_count": 10,
        "locale": "zh-CN",
        "category": "health",
        "content_rating": "general",
        "status": "draft",
        "reviewed": False,
        "updated_at": now,
        "seo": SEO(title="示例标题", description="摘要", noindex=True),
        "tags": ("health",),
    }
    values.update(overrides)
    return ContentItem(**values)  # type: ignore[arg-type]


def test_export_strips_duplicate_h1_and_records_locale(tmp_path: Path) -> None:
    site_dir = tmp_path / "site"

    written = export_to_hugo([_item()], site_dir)

    content = written[0].read_text(encoding="utf-8")
    body = content.split("---", 2)[2]
    assert "# 示例标题" not in body
    assert "正文段落。" in body
    assert "lang: zh-CN" in content


def test_export_prunes_posts_missing_from_dataset(tmp_path: Path) -> None:
    site_dir = tmp_path / "site"
    posts_dir = site_dir / "content" / "posts"
    posts_dir.mkdir(parents=True)
    stale = posts_dir / "stale-post.md"
    stale.write_text("---\ntitle: Stale\n---\n", encoding="utf-8")
    keep = posts_dir / "_index.md"
    keep.write_text("---\ntitle: Posts\n---\n", encoding="utf-8")

    export_to_hugo([_item()], site_dir)

    assert not stale.exists()
    assert keep.exists()
    assert (posts_dir / "sample-post.md").exists()
