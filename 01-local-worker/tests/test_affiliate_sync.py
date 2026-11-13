import json
from pathlib import Path

import pytest

from local_worker.errors import ConfigError
from local_worker.pipeline.affiliate_sync import (
    build_maps_from_master,
    slug_from_masked_path,
    sync_from_master_db,
)


def test_slug_from_masked_path() -> None:
    assert slug_from_masked_path("/out/okamoto-001") == "okamoto-001"
    assert slug_from_masked_path("out/skyn-original") == "skyn-original"
    assert slug_from_masked_path("") == ""


def test_build_maps_from_master_splits_public_and_secret() -> None:
    master = {
        "okamoto-001-poly": {
            "product_name": "Okamoto 001",
            "masked_path": "/out/okamoto-001",
            "real_affiliate_url": "https://taobao.com/a",
            "meta_metrics": {"is_active": True},
        },
        "inactive-item": {
            "product_name": "Gone",
            "masked_path": "/out/gone",
            "real_affiliate_url": "https://example.com/gone",
            "meta_metrics": {"is_active": False},
        },
    }

    hugo_map, offers_payload, warnings = build_maps_from_master(master)

    assert "okamoto-001-poly" in hugo_map
    assert hugo_map["okamoto-001-poly"] == {
        "product": "Okamoto 001",
        "url": "/out/okamoto-001",
    }
    assert "real_affiliate_url" not in json.dumps(hugo_map)
    assert "https://taobao.com" not in json.dumps(hugo_map)

    assert len(offers_payload["offers"]) == 1
    offer = offers_payload["offers"][0]
    assert offer["slug"] == "okamoto-001"
    assert offer["destination"] == "https://taobao.com/a"
    assert offer["status"] == "active"
    assert offer["token"] == "okamoto-001-poly"
    assert any("inactive" in warning for warning in warnings)


def test_sync_from_master_db_writes_both_files(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "master-affiliate-db.json").write_text(
        json.dumps(
            {
                "token-a": {
                    "product_name": "Product A",
                    "masked_path": "/out/product-a",
                    "real_affiliate_url": "https://partner.example/a",
                    "meta_metrics": {"is_active": True},
                }
            }
        ),
        encoding="utf-8",
    )
    site_dir = tmp_path / "site"
    settings = {
        "build": {"site_dir": str(site_dir)},
        "compliance": {"require_affiliate_disclosure": True},
    }

    result = sync_from_master_db(tmp_path, settings)
    assert result["written"] is True
    assert result["offer_count"] == 1

    offers = json.loads((tmp_path / "config" / "affiliate-offers.json").read_text())
    assert offers["offers"][0]["destination"] == "https://partner.example/a"

    hugo_map = json.loads((site_dir / "data" / "affiliate-map.json").read_text())
    assert hugo_map["token-a"]["url"] == "/out/product-a"
    assert "https://partner.example" not in json.dumps(hugo_map)


def test_sync_from_master_db_missing_raises(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="master affiliate db not found"):
        sync_from_master_db(tmp_path, {})
