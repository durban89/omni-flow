from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from local_worker.server.app import create_server

SETTINGS = """
app:
  site_name: "Demo"
  base_url: "https://example.com"
  mode: "test"
pipeline:
  data_dir: "build/data"
scraper:
  max_items_per_run: 1
"""

RAW_ITEMS = {
    "schema_version": "1.0",
    "generated_at": "2026-01-01T00:00:00Z",
    "source_ids": ["demo_feed"],
    "item_count": 2,
    "items": [
        {
            "source_id": "demo_feed",
            "source_url": "https://example.com/a",
            "title": "First clue",
            "summary": "Short summary",
            "body": "Body of the first clue",
            "body_format": "text",
            "retrieved_at": "2026-01-01T00:00:00Z",
            "category": "health",
            "tags": ["topic"],
        },
        {
            "source_id": "demo_feed",
            "source_url": "https://example.com/b",
            "title": "Second clue",
            "summary": "Another summary",
            "body": "Body of the second clue",
            "body_format": "text",
            "retrieved_at": "2026-01-01T00:00:00Z",
            "category": "guides",
            "tags": [],
        },
    ],
}

CONTENT_ITEMS = {
    "schema_version": "1.0",
    "generated_at": "2026-01-01T00:00:00Z",
    "item_count": 1,
    "items": [
        {
            "schema_version": "1.0",
            "id": "abc123",
            "slug": "first-guide",
            "title": "First guide",
            "summary": "Summary",
            "body_markdown": "## Heading\n\nBody text.",
            "word_count": 320,
            "locale": "en-US",
            "category": "health",
            "content_rating": "general",
            "status": "draft",
            "reviewed": False,
            "seo": {"title": "First guide", "description": "Desc", "noindex": True},
            "sources": [{"url": "https://example.com/a"}],
            "updated_at": "2026-01-01T00:00:00Z",
        }
    ],
}


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _get(url: str) -> tuple[int, object]:
    with urllib.request.urlopen(url, timeout=10) as response:
        body = response.read().decode("utf-8")
        return response.status, json.loads(body)


def _post(url: str, payload: dict[str, object]) -> tuple[int, object]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        return response.status, json.loads(response.read().decode("utf-8"))


@pytest.fixture()
def panel(tmp_path: Path):
    (tmp_path / "config").mkdir()
    (tmp_path / "config/settings.yaml").write_text(SETTINGS, encoding="utf-8")
    _write_json(tmp_path / "build/data/raw_items.json", RAW_ITEMS)
    _write_json(tmp_path / "build/data/content_items.json", CONTENT_ITEMS)

    server = create_server(
        "127.0.0.1",
        0,
        project_root=tmp_path,
        settings_path=tmp_path / "config/settings.yaml",
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_overview_reports_counts_and_settings(panel: str) -> None:
    status, data = _get(f"{panel}/api/overview")
    assert status == 200
    assert data["settings"]["mode"] == "test"
    assert data["collection"]["raw"]["count"] == 2
    assert data["collection"]["raw"]["source_ids"] == ["demo_feed"]
    assert data["collection"]["content"]["count"] == 1
    assert data["collection"]["content"]["status_counts"] == {"draft": 1}
    assert "scrape" in data["stages"]


def test_raw_items_list_and_detail(panel: str) -> None:
    _, listing = _get(f"{panel}/api/raw")
    assert [item["title"] for item in listing["items"]] == ["First clue", "Second clue"]
    assert listing["items"][0]["source_url"] == "https://example.com/a"

    _, detail = _get(f"{panel}/api/raw/item?index=1")
    assert detail["item"]["body"] == "Body of the second clue"


def test_content_items_list(panel: str) -> None:
    _, listing = _get(f"{panel}/api/content")
    assert listing["items"][0]["slug"] == "first-guide"
    assert listing["items"][0]["noindex"] is True


def test_unknown_item_index_returns_404(panel: str) -> None:
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        _get(f"{panel}/api/raw/item?index=99")
    assert excinfo.value.code == 404


def test_run_rejects_unknown_stage(panel: str) -> None:
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        _post(f"{panel}/api/run", {"stage": "rm-rf"})
    assert excinfo.value.code == 400


def test_ui_is_served(panel: str) -> None:
    with urllib.request.urlopen(f"{panel}/", timeout=10) as response:
        html = response.read().decode("utf-8")
    assert "local-worker" in html
    assert "data-stage=\"scrape\"" in html


def test_offers_crud(panel: str) -> None:
    _, initial = _get(f"{panel}/api/offers")
    assert initial["exists"] is False
    assert initial["offers"] == []
    assert initial["status_options"] == ["active", "paused"]

    offer = {
        "token": "demo-token",
        "product_name": "Demo Product",
        "masked_path": "/out/demo-offer",
        "destination": "https://example.com/aff?ref=1",
        "status": "active",
        "geo": "us, ca, US",
        "disclosure_required": True,
    }
    _, created = _post(f"{panel}/api/offers", {"offer": offer, "original_slug": None})
    assert [entry["token"] for entry in created["offers"]] == ["demo-token"]
    assert created["offers"][0]["slug"] == "demo-offer"
    assert created["offers"][0]["geo"] == ["US", "CA"]

    path = Path(created["path"])
    document = json.loads(path.read_text(encoding="utf-8"))
    assert "demo-token" in document
    assert document["demo-token"]["product_name"] == "Demo Product"
    assert document["demo-token"]["meta_metrics"]["is_active"] is True

    # duplicate token
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        _post(f"{panel}/api/offers", {"offer": offer})
    assert excinfo.value.code == 400
    assert "token already exists" in excinfo.value.read().decode("utf-8")

    # validation happens before writing
    for broken, message in (
        ({**offer, "token": "new-token", "destination": "ftp://example.com"}, "invalid destination"),
        ({**offer, "token": "new-token", "status": "live"}, "invalid status"),
        ({**offer, "token": "new-token", "geo": "USA"}, "invalid geo code"),
        ({**offer, "token": "new-token", "product_name": ""}, "product_name is required"),
        ({**offer, "token": "new-token", "masked_path": "/"}, "cannot derive offer slug"),
    ):
        with pytest.raises(urllib.error.HTTPError) as excinfo:
            _post(f"{panel}/api/offers", {"offer": broken})
        assert excinfo.value.code == 400
        assert message in excinfo.value.read().decode("utf-8")
    assert len(json.loads(path.read_text(encoding="utf-8"))) == 1

    # rename + pause keeps a single entry and backs up the previous file
    _, updated = _post(
        f"{panel}/api/offers",
        {
            "offer": {
                **offer,
                "token": "renamed-token",
                "masked_path": "/out/renamed-offer",
                "status": "paused",
                "geo": [],
            },
            "original_slug": "demo-token",
        },
    )
    assert [entry["token"] for entry in updated["offers"]] == ["renamed-token"]
    assert updated["offers"][0]["status"] == "paused"
    assert path.with_name(path.name + ".bak").exists()

    _, deleted = _post(f"{panel}/api/offers/delete", {"slug": "renamed-token"})
    assert deleted["offers"] == []

    with pytest.raises(urllib.error.HTTPError) as excinfo:
        _post(f"{panel}/api/offers/delete", {"slug": "renamed-token"})
    assert excinfo.value.code == 404


def test_offers_reports_invalid_entries(panel: str) -> None:
    _, initial = _get(f"{panel}/api/offers")
    master_file = Path(initial["path"])
    master_file.parent.mkdir(parents=True, exist_ok=True)
    master_file.write_text(
        json.dumps(
            {
                "broken": {
                    "product_name": "Bad",
                    "masked_path": "/out/broken",
                    "real_affiliate_url": "not-a-url",
                    "meta_metrics": {"is_active": True},
                }
            }
        ),
        encoding="utf-8",
    )

    _, data = _get(f"{panel}/api/offers")
    assert data["exists"] is True
    assert data["issues"][0]["token"] == "broken"
    assert any("invalid destination" in message for message in data["issues"][0]["errors"])


def test_job_captures_subprocess_log(panel: str) -> None:
    server_url = panel
    _, data = _post(f"{server_url}/api/run", {"stage": "validate"})
    assert data["job"]["status"] == "running"

    deadline = time.time() + 60
    job: dict[str, object] = {}
    while time.time() < deadline:
        _, payload = _get(f"{server_url}/api/jobs/{data['job']['id']}?cursor=0")
        job = payload["job"]
        if job["status"] != "running":
            break
        time.sleep(0.2)

    assert job["status"] == "succeeded"
    assert job["exit_code"] == 0
    text = "\n".join(line["text"] for line in job["lines"])
    assert "[config] mode=test" in text
    assert "[validate] 1 content item(s) passed" in text
