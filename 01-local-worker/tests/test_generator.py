import json
from pathlib import Path
from unittest.mock import patch

import pytest

from local_worker.content.generator import ContentGenerator
from local_worker.content.text import count_words
from local_worker.errors import LLMGenerationError
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

    with patch.dict(
        "os.environ", {"OPENAI_API_KEY": "", "GEMINI_API_KEY": "", "LLM_API_KEY": ""}
    ):
        generator = ContentGenerator({"enabled": True, "model": "gpt-4o-mini"})
        body, summary = generator.generate(raw)

    assert "# Best Ergonomic Chair 2026" in body
    assert "## Overview: Office Buying Guide" in body
    assert "## Pros and Cons" in body
    assert "Compliance Note" in body
    assert count_words(body) >= 200
    assert len(summary) > 10


def test_topic_lead_brief_is_not_copied_into_template() -> None:
    raw = RawItem(
        source_id="topic_search",
        source_url="https://news.example.com/condom-sizing",
        title="How to choose the right condom size",
        summary="Search snippet about sizing.",
        body="Topic lead: How to choose the right condom size\nSeed query: condom sizing",
        body_format="text",
        retrieved_at=utc_now(),
        category="health",
        tags=("health", "topic"),
    )

    with patch.dict(
        "os.environ", {"OPENAI_API_KEY": "", "GEMINI_API_KEY": "", "LLM_API_KEY": ""}
    ):
        body, _summary = ContentGenerator({}).generate(raw)

    assert "# How to choose the right condom size" in body
    assert "Topic lead:" not in body
    assert "Seed query:" not in body


def test_topic_lead_raises_instead_of_falling_back_when_llm_fails() -> None:
    raw = RawItem(
        source_id="topic_health_zh",
        source_url="https://www.fda.gov.tw/TC/siteListContent.aspx?id=50981",
        title="衛生福利部食品藥物管理署",
        summary="Search snippet.",
        body="Topic lead: 衛生福利部食品藥物管理署",
        body_format="text",
        retrieved_at=utc_now(),
        category="health",
        tags=("topic", "health"),
    )

    with patch.dict(
        "os.environ", {"OPENAI_API_KEY": "", "GEMINI_API_KEY": "", "LLM_API_KEY": "sk-test"}
    ):
        generator = ContentGenerator({"enable": True, "model": "free-model"})
        with (
            patch.object(generator, "_chat_completion", side_effect=ValueError("empty")),
            pytest.raises(LLMGenerationError, match="topic lead"),
        ):
            generator.generate(raw)


def test_non_topic_item_still_falls_back_when_llm_fails() -> None:
    raw = RawItem(
        source_id="rss_feed",
        source_url="https://example.com/guide",
        title="Best Ergonomic Chair 2026",
        summary="A guide on choosing ergonomic chairs.",
        body="Breathable mesh, lumbar support adjustment, and 5-year warranty.",
        body_format="markdown",
        retrieved_at=utc_now(),
        category="office",
    )

    with patch.dict(
        "os.environ", {"OPENAI_API_KEY": "", "GEMINI_API_KEY": "", "LLM_API_KEY": "sk-test"}
    ):
        generator = ContentGenerator({"enable": True, "model": "free-model"})
        with patch.object(generator, "_chat_completion", side_effect=ValueError("empty")):
            body, _summary = generator.generate(raw)

    assert "## Overview: Office Buying Guide" in body


def test_affiliate_tokens_loaded_once_per_generator(tmp_path: Path) -> None:
    db_path = tmp_path / "master-affiliate-db.json"
    db_path.write_text(
        json.dumps(
            {"tok-a": {"product_name": "Product A", "masked_path": "/out/a"}}
        ),
        encoding="utf-8",
    )
    first = RawItem(
        source_id="test",
        source_url="https://example.com/first",
        title="First Guide",
        summary="First summary.",
        body="First body content.",
        body_format="text",
        retrieved_at=utc_now(),
        category="guides",
    )
    second = RawItem(
        source_id="test",
        source_url="https://example.com/second",
        title="Second Guide",
        summary="Second summary.",
        body="Second body content.",
        body_format="text",
        retrieved_at=utc_now(),
        category="guides",
    )

    with patch.dict(
        "os.environ", {"OPENAI_API_KEY": "", "GEMINI_API_KEY": "", "LLM_API_KEY": ""}
    ):
        generator = ContentGenerator({}).set_master_db_path(db_path)
        with patch.object(
            generator,
            "load_active_affiliate_tokens",
            wraps=generator.load_active_affiliate_tokens,
        ) as loader:
            generator.generate(first)
            generator.generate(second)

    assert loader.call_count == 1
    assert set(generator.valid_tokens) == {"tok-a"}
