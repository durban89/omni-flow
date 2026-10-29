from local_worker.content.generator import ContentGenerator
from local_worker.content.text import count_words
from local_worker.models import RawItem, utc_now


def test_content_generator_template_fallback() -> None:
    raw = RawItem(
        source_id="test",
        source_url="https://example.com/guide",
        title="Best Ergonomic Chair 2026",
        summary="A guide on choosing ergonomic chairs.",
        body="Breathable mesh, lumbar support adjustment, and 5-year warranty.",
        body_format="markdown",
        retrieved_at=utc_now(),
        category="office",
    )

    generator = ContentGenerator({"enabled": True, "model": "gpt-4o-mini"})
    body, summary = generator.generate(raw)

    assert "# Best Ergonomic Chair 2026" in body
    assert "## Overview: Office Buying Guide" in body
    assert "## Pros and Cons" in body
    assert "Compliance Note" in body
    assert count_words(body) >= 200
    assert len(summary) > 10
