from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from local_worker.config import load_settings
from local_worker.content.normalize import normalize_raw_items
from local_worker.content.validate import validate_content_items
from local_worker.errors import ContentError, LocalWorkerError, ScrapeError
from local_worker.models import (
    SCHEMA_VERSION,
    ContentItem,
    RawItem,
    canonical_url,
    isoformat,
    utc_now,
)
from local_worker.scraper import build_sources
from local_worker.scraper.base import SourceContext
from local_worker.scraper.http import HttpClient

PROJECT_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_SETTINGS = PROJECT_ROOT / "config/settings.yaml"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="local-worker")
    parser.add_argument(
        "stage",
        choices=["scrape", "content", "build", "validate", "all"],
    )
    parser.add_argument("--settings", type=Path, default=DEFAULT_SETTINGS)
    return parser


def _section(settings: dict[str, Any], name: str) -> dict[str, Any]:
    value = settings.get(name)
    return value if isinstance(value, dict) else {}


def _resolve(root: Path, value: str) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else root / path


def _data_dir(root: Path, settings: dict[str, Any]) -> Path:
    pipeline = _section(settings, "pipeline")
    value = str(pipeline.get("data_dir") or "01-local-worker/build/data")
    return _resolve(root, value)


def _output_dir(root: Path, settings: dict[str, Any]) -> Path:
    pipeline = _section(settings, "pipeline")
    value = str(pipeline.get("output_dir") or "01-local-worker/build/public")
    return _resolve(root, value)


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ContentError(f"file not found: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ContentError(f"invalid JSON in {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ContentError(f"expected a JSON object in {path}")
    return data


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def run_scrape(root: Path, settings: dict[str, Any]) -> Path:
    scraper = _section(settings, "scraper")
    pipeline = _section(settings, "pipeline")

    http = HttpClient(
        user_agent=str(scraper.get("user_agent") or "LocalWorker/0.1"),
        timeout_seconds=float(scraper.get("request_timeout_seconds") or 20),
        delay_ms=int(scraper.get("request_delay_ms") or 0),
        respect_robots=bool(scraper.get("respect_robots_txt", True)),
    )
    context = SourceContext(client=http, retrieved_at=utc_now())
    sources = build_sources(settings, context)

    raw_items: list[RawItem] = []
    fetched_sources: list[str] = []
    with http:
        for source in sources:
            try:
                items = source.fetch()
            except ScrapeError as exc:
                if bool(scraper.get("continue_on_error", False)):
                    print(f"[scrape] skip {source.source_id}: {exc}", file=sys.stderr)
                    continue
                raise
            fetched_sources.append(source.source_id)
            print(f"[scrape] {source.source_id}: {len(items)} item(s)")
            raw_items.extend(items)

    max_items = int(
        scraper.get("max_items_per_run")
        or pipeline.get("max_items_per_run")
        or 200
    )
    seen_urls: set[str] = set()
    items: list[RawItem] = []
    for item in raw_items:
        key = canonical_url(item.source_url)
        if key in seen_urls:
            continue
        seen_urls.add(key)
        items.append(item)
        if len(items) >= max_items:
            break

    path = _data_dir(root, settings) / "raw_items.json"
    _write_json(
        path,
        {
            "schema_version": SCHEMA_VERSION,
            "generated_at": isoformat(utc_now()),
            "source_ids": fetched_sources,
            "item_count": len(items),
            "items": [item.to_dict() for item in items],
        },
    )
    print(f"[scrape] wrote {len(items)} raw item(s) to {path}")
    return path


def run_content(root: Path, settings: dict[str, Any]) -> Path:
    data_dir = _data_dir(root, settings)
    raw_payload = _read_json(data_dir / "raw_items.json")
    entries = raw_payload.get("items")
    if not isinstance(entries, list):
        raise ContentError("raw_items.json requires an items array")

    raw_items = [
        RawItem.from_dict(entry)
        for entry in entries
        if isinstance(entry, dict)
    ]
    items = normalize_raw_items(raw_items, settings)

    errors = validate_content_items(items)
    if errors:
        message = "\n".join(f"- {error}" for error in errors)
        raise ContentError(f"content validation failed:\n{message}")

    generated_at = isoformat(utc_now())
    content_path = data_dir / "content_items.json"
    _write_json(
        content_path,
        {
            "schema_version": SCHEMA_VERSION,
            "generated_at": generated_at,
            "item_count": len(items),
            "items": [item.to_dict() for item in items],
        },
    )

    status_counts = Counter(item.status for item in items)
    category_counts = Counter(item.category for item in items)
    _write_json(
        data_dir / "manifest.json",
        {
            "schema_version": SCHEMA_VERSION,
            "generated_at": generated_at,
            "raw_item_count": len(raw_items),
            "content_item_count": len(items),
            "status_counts": dict(sorted(status_counts.items())),
            "category_counts": dict(sorted(category_counts.items())),
            "affiliate_linked_count": sum(1 for item in items if item.affiliate_slug),
            "outputs": {
                "raw_items": "raw_items.json",
                "content_items": "content_items.json",
            },
        },
    )

    print(f"[content] wrote {len(items)} content item(s) to {content_path}")
    return content_path


def run_validate(root: Path, settings: dict[str, Any]) -> None:
    data_dir = _data_dir(root, settings)
    content_path = data_dir / "content_items.json"
    payload = _read_json(content_path)

    if payload.get("schema_version") != SCHEMA_VERSION:
        raise ContentError(
            f"unsupported content schema_version: {payload.get('schema_version')}"
        )

    entries = payload.get("items")
    if not isinstance(entries, list):
        raise ContentError("content_items.json requires an items array")

    items = [
        ContentItem.from_dict(entry)
        for entry in entries
        if isinstance(entry, dict)
    ]
    errors = validate_content_items(items)
    if int(payload.get("item_count", len(items))) != len(items):
        errors.append("item_count does not match the items array length")
    if errors:
        message = "\n".join(f"- {error}" for error in errors)
        raise ContentError(f"content validation failed:\n{message}")

    print(f"[validate] {len(items)} content item(s) passed")


def run_build(root: Path, settings: dict[str, Any]) -> None:
    build = _section(settings, "build")
    site_dir = _resolve(root, str(build.get("site_dir") or "03-distribution/site"))
    output_dir = _output_dir(root, settings)
    hugo = str(build.get("hugo_binary") or "hugo")

    output_dir.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(
            [
                hugo,
                "--source",
                str(site_dir),
                "--minify",
                "--destination",
                str(output_dir),
            ],
            check=True,
            cwd=root,
        )
    except FileNotFoundError as exc:
        raise ContentError(
            "hugo binary not found; install Hugo Extended or set build.hugo_binary"
        ) from exc
    except subprocess.CalledProcessError as exc:
        raise ContentError(f"hugo build failed with exit code {exc.returncode}") from exc

    print(f"[build] wrote static site to {output_dir}")


def run_all(root: Path, settings: dict[str, Any]) -> None:
    run_scrape(root, settings)
    run_content(root, settings)
    run_validate(root, settings)
    run_build(root, settings)


def main() -> None:
    args = build_parser().parse_args()
    try:
        settings = load_settings(args.settings)
        if args.stage == "scrape":
            run_scrape(PROJECT_ROOT, settings)
        elif args.stage == "content":
            run_content(PROJECT_ROOT, settings)
        elif args.stage == "validate":
            run_validate(PROJECT_ROOT, settings)
        elif args.stage == "build":
            run_build(PROJECT_ROOT, settings)
        else:
            run_all(PROJECT_ROOT, settings)
    except LocalWorkerError as exc:
        raise SystemExit(f"error: {exc}") from exc


if __name__ == "__main__":
    main()
