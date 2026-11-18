import json
from pathlib import Path
from typing import Any

from local_worker.pipeline.run import (
    run_all,
    run_content,
    run_export,
    run_review,
    run_scrape,
    run_validate,
)


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


def test_run_content_is_incremental_by_id(tmp_path: Path) -> None:
    input_path = tmp_path / "input.json"
    input_path.write_text(
        json.dumps(
            {
                "items": [
                    {
                        "title": "First Guide",
                        "summary": "First summary.",
                        "url": "https://example.com/first",
                        "body_markdown": "First body content.",
                        "category": "guides",
                    },
                    {
                        "title": "Second Guide",
                        "summary": "Second summary.",
                        "url": "https://example.com/second",
                        "body_markdown": "Second body content.",
                        "category": "guides",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )

    settings: dict[str, Any] = {
        "app": {"default_locale": "en"},
        "pipeline": {"data_dir": str(tmp_path / "data"), "max_items_per_run": 10},
        "scraper": {
            "request_delay_ms": 0,
            "respect_robots_txt": False,
            "sources": [
                {"id": "curated", "type": "json_file", "enabled": True, "path": str(input_path)}
            ],
        },
        "compliance": {"default_content_rating": "general"},
    }

    raw_path = run_scrape(tmp_path, settings)
    content_path = run_content(tmp_path, settings)
    first_payload = json.loads(content_path.read_text(encoding="utf-8"))
    first_ids = sorted(item["id"] for item in first_payload["items"])
    first_modified = content_path.stat().st_mtime_ns
    assert first_payload["item_count"] == 2

    # Re-running must not regenerate existing items.
    manifest_path = tmp_path / "data" / "manifest.json"
    manifest_modified = manifest_path.stat().st_mtime_ns
    run_content(tmp_path, settings)
    second_payload = json.loads(content_path.read_text(encoding="utf-8"))
    assert second_payload["item_count"] == 2
    # Nothing new means nothing is rewritten, so hand edits stay intact.
    assert content_path.stat().st_mtime_ns == first_modified
    assert manifest_path.stat().st_mtime_ns == manifest_modified

    # Deleting a generated item makes the next run produce it again.
    second_payload["items"] = [
        item for item in second_payload["items"] if item["id"] != first_ids[0]
    ]
    content_path.write_text(json.dumps(second_payload), encoding="utf-8")
    run_content(tmp_path, settings)
    third_payload = json.loads(content_path.read_text(encoding="utf-8"))
    assert sorted(item["id"] for item in third_payload["items"]) == first_ids
    assert raw_path.exists()


def test_full_pipeline_with_export_review_and_kv_sync(tmp_path: Path) -> None:
    input_path = tmp_path / "input.json"
    long_body = " ".join(["word"] * 320)
    input_path.write_text(
        json.dumps(
            {
                "items": [
                    {
                        "title": "Comprehensive Monitor Review",
                        "summary": "Everything you need to know about monitors.",
                        "url": "https://example.com/monitor",
                        "body_markdown": long_body,
                        "category": "hardware",
                        "tags": ["display"],
                        "affiliate_slug": "best-monitor",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True)
    (config_dir / "affiliate-offers.json").write_text(
        json.dumps({
            "offers": [
                {
                    "slug": "best-monitor",
                    "destination": "https://example.com/affiliate-link",
                    "status": "active",
                    "geo": ["US"],
                    "disclosure_required": True,
                }
            ]
        }),
        encoding="utf-8",
    )

    site_dir = tmp_path / "site"
    site_dir.mkdir(parents=True)

    settings: dict[str, Any] = {
        "app": {"default_locale": "en"},
        "pipeline": {
            "data_dir": str(tmp_path / "data"),
            "max_items_per_run": 10,
        },
        "build": {
            "site_dir": str(site_dir),
            "hugo_binary": "non-existent-hugo",
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
        "cloudflare": {
            "kv_namespace_binding": "APP_KV",
        },
        "compliance": {"default_content_rating": "general"},
    }

    # Execute all stages (hugo skipped gracefully if not installed)
    run_all(tmp_path, settings, dry_run=True)

    # Verify Hugo content was exported
    post_file = site_dir / "content" / "posts" / "comprehensive-monitor-review.md"
    assert post_file.exists()
    assert "draft: true" in post_file.read_text(encoding="utf-8")

    # Verify KV bulk file was generated
    bulk_file = tmp_path / "data" / "kv_bulk.json"
    assert bulk_file.exists()

    # Now approve the article through the review system
    run_review(
        tmp_path,
        settings,
        action="approve",
        target="comprehensive-monitor-review",
        reviewer="lead_editor",
    )

    # Re-export and verify post is no longer draft
    run_export(tmp_path, settings)
    post_content = post_file.read_text(encoding="utf-8")
    assert "draft: false" in post_content
    assert "reviewed: true" in post_content
