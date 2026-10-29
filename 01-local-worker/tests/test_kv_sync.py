import json
from pathlib import Path

import pytest

from local_worker.errors import ConfigError
from local_worker.pipeline.kv_sync import sync_affiliate_offers, validate_offer


def test_validate_offer() -> None:
    valid = {
        "slug": "best-shoes",
        "destination": "https://example.com/shoes",
        "status": "active",
        "geo": ["US", "CA"],
        "disclosure_required": True,
    }
    assert validate_offer(valid) == []

    invalid = {
        "slug": "Bad_Slug!",
        "destination": "ftp://example.com",
        "status": "unknown_status",
    }
    errors = validate_offer(invalid)
    assert len(errors) == 3


def test_sync_affiliate_offers_generates_bulk_json(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True)
    map_file = config_dir / "affiliate-map.json"
    map_file.write_text(
        json.dumps({
            "offers": [
                {
                    "slug": "offer-a",
                    "destination": "https://example.com/a",
                    "status": "active",
                    "geo": ["US"],
                    "disclosure_required": True,
                }
            ]
        }),
        encoding="utf-8",
    )

    settings = {
        "pipeline": {"data_dir": str(tmp_path / "data")},
        "cloudflare": {"kv_namespace_binding": "APP_KV"},
    }

    result = sync_affiliate_offers(tmp_path, settings, dry_run=True)
    assert result["total_offers"] == 1
    assert result["synced"] is False

    bulk_path = Path(result["bulk_file"])
    assert bulk_path.exists()
    bulk_data = json.loads(bulk_path.read_text(encoding="utf-8"))
    assert len(bulk_data) == 1
    assert bulk_data[0]["key"] == "offer:offer-a"
    assert "https://example.com/a" in bulk_data[0]["value"]


def test_sync_affiliate_offers_missing_file_raises(tmp_path: Path) -> None:
    settings = {"pipeline": {"data_dir": str(tmp_path / "data")}}
    with pytest.raises(ConfigError, match="affiliate map file not found"):
        sync_affiliate_offers(tmp_path, settings)
