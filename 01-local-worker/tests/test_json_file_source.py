import json
from pathlib import Path

import pytest

from local_worker.errors import ScrapeError
from local_worker.models import utc_now
from local_worker.scraper.base import SourceContext
from local_worker.scraper.http import HttpClient
from local_worker.scraper.json_file import JsonFileSource


def test_json_file_source_normalizes_entries(tmp_path: Path) -> None:
    path = tmp_path / "items.json"
    path.write_text(
        json.dumps(
            {
                "items": [
                    {
                        "title": "A Curated Item",
                        "summary": "Summary text.",
                        "url": "https://example.com/item",
                        "body_html": "<p>Hello</p>",
                        "tags": ["extra"],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    with HttpClient("test", respect_robots=False, delay_ms=0) as client:
        context = SourceContext(client=client, retrieved_at=utc_now())
        source = JsonFileSource(
            "curated",
            {
                "path": str(path),
                "default_category": "guides",
                "default_tags": ["curated"],
            },
            context,
        )
        items = source.fetch()

    assert len(items) == 1
    assert items[0].source_id == "curated"
    assert items[0].category == "guides"
    assert items[0].tags == ("curated", "extra")
    assert items[0].body_format == "html"
    assert items[0].body == "<p>Hello</p>"


def test_json_file_source_requires_existing_path(tmp_path: Path) -> None:
    with HttpClient("test", respect_robots=False, delay_ms=0) as client:
        context = SourceContext(client=client, retrieved_at=utc_now())
        source = JsonFileSource("missing", {"path": str(tmp_path / "missing.json")}, context)

        with pytest.raises(ScrapeError):
            source.fetch()
