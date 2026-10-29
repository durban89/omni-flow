from local_worker.content.normalize import normalize_raw_items
from local_worker.models import RawItem, utc_now

SETTINGS = {
    "app": {"default_locale": "en"},
    "pipeline": {"max_items_per_run": 10},
    "compliance": {"default_content_rating": "general"},
}


def make_raw(**overrides: object) -> RawItem:
    values: dict[str, object] = {
        "source_id": "test",
        "source_url": "https://example.com/post",
        "title": "Example Post",
        "summary": "A short summary.",
        "body": "A body paragraph with enough words to be useful.",
        "body_format": "text",
        "retrieved_at": utc_now(),
        "category": "guides",
        "tags": ("guide",),
        "keywords": ("example",),
    }
    values.update(overrides)
    return RawItem(**values)  # type: ignore[arg-type]


def test_normalize_creates_draft_content_item() -> None:
    items = normalize_raw_items([make_raw()], SETTINGS)

    assert len(items) == 1
    item = items[0]
    assert item.slug == "example-post"
    assert item.status == "draft"
    assert item.reviewed is False
    assert item.seo.noindex is True
    assert item.sources[0].url == "https://example.com/post"
    assert item.word_count > 0


def test_normalize_deduplicates_urls() -> None:
    items = normalize_raw_items(
        [
            make_raw(),
            make_raw(title="Duplicate Post"),
        ],
        SETTINGS,
    )

    assert len(items) == 1


def test_normalize_adds_suffix_for_duplicate_slugs() -> None:
    items = normalize_raw_items(
        [
            make_raw(source_url="https://example.com/one"),
            make_raw(source_url="https://example.com/two"),
        ],
        SETTINGS,
    )

    assert [item.slug for item in items] == ["example-post", "example-post-2"]


def test_normalize_converts_html_body_to_text() -> None:
    items = normalize_raw_items(
        [
            make_raw(
                body="<p>Hello</p><script>ignore me</script><p>World</p>",
                body_format="html",
            )
        ],
        SETTINGS,
    )

    assert items[0].body_markdown == "Hello\n\nWorld"
