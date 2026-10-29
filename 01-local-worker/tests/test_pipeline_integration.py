import json
from pathlib import Path
from typing import Any

from local_worker.pipeline.run import run_content, run_scrape, run_validate


def test_scrape_content_validate_end_to_end(tmp_path: Path) -> None:
    input_path = tmp_path / "input.json"
    input_path.write_text(
        json.dumps(
            {
                "items": [
                    {
                        "title": "Example Buying Guide",
                        "summary": "A short summary for the example buying guide.",
                        "url": "https://example.com/guide",
                        "body_markdown": (
                            "Compare materials, dimensions, and warranty terms before buying. "
                            "Check the total cost, including delivery and replacement parts."
                        ),
                        "category": "guides",
                        "tags": ["guide"],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    settings: dict[str, Any] = {
        "app": {"default_locale": "en"},
        "pipeline": {
            "data_dir": str(tmp_path / "data"),
            "max_items_per_run": 10,
        },
        "scraper": {
            "request_delay_ms": 0,
            "respect_robots_txt": False,
            "sources": [
                {
                    "id": "curated",
                    "type": "json_file",
                    "enabled": True,
                    "path": str(input_path),
                }
            ],
        },
        "compliance": {"default_content_rating": "general"},
    }

    raw_path = run_scrape(tmp_path, settings)
    content_path = run_content(tmp_path, settings)
    run_validate(tmp_path, settings)

    raw_payload = json.loads(raw_path.read_text(encoding="utf-8"))
    content_payload = json.loads(content_path.read_text(encoding="utf-8"))
    manifest = json.loads((tmp_path / "data" / "manifest.json").read_text(encoding="utf-8"))

    assert raw_payload["item_count"] == 1
    assert content_payload["item_count"] == 1
    assert content_payload["items"][0]["slug"] == "example-buying-guide"
    assert content_payload["items"][0]["status"] == "draft"
    assert manifest["content_item_count"] == 1
    assert manifest["status_counts"] == {"draft": 1}
