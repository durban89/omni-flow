from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from local_worker.config import load_settings
from local_worker.content.export_hugo import export_to_hugo
from local_worker.content.normalize import normalize_raw_items
from local_worker.content.review import ReviewService, ReviewStore
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
from local_worker.pipeline.kv_sync import sync_affiliate_offers
from local_worker.scraper import build_sources
from local_worker.scraper.base import SourceContext
from local_worker.scraper.http import HttpClient

logger = logging.getLogger("local_worker.pipeline")

PROJECT_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_SETTINGS = PROJECT_ROOT / "config/settings.yaml"


def setup_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
        force=True,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="local-worker")
    parser.add_argument(
        "stage",
        choices=[
        "sync-affilicate",
        "scrape", "content", 
        "export", "build", "validate", "sync-kv", "review", "all"],
    )
    parser.add_argument("--settings", type=Path, default=DEFAULT_SETTINGS)
    parser.add_argument("--auto-approve", action="store_true", help="Auto-approve eligible drafts")
    parser.add_argument("--dry-run", action="store_true", help="Dry run for sync actions")
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable verbose logging")

    # Arguments for review action
    parser.add_argument("--action", choices=["list", "approve", "reject"], default="list")
    parser.add_argument("--target", type=str, default="", help="Slug or ID of item to review")
    parser.add_argument("--reviewer", type=str, default="editorial", help="Reviewer identifier")
    parser.add_argument("--notes", type=str, default="", help="Review notes or rejection reason")

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



def run_map_affiliate(root: Path, settings: dict[str, Any]):
    master_db_path = root / "config/master-affiliate-db.json"
    hugo_data_path = root / "config/affiliate-map.json"

    # 🔧 修复 Bug: 原代码中误用了 self.master_db_path
    if not os.path.exists(master_db_path):
        print(f"❌ 错误: 找不到主数据库文件 [{master_db_path}]，请先创建它。")
        return

    with open(master_db_path, "r", encoding="utf-8") as f:
        master_db = json.load(f)

    hugo_front_map = {}

    for key, entity in master_db.items():
        # 过滤掉处于未激活（下架）状态的商品
        if not entity.get("meta_metrics", {}).get("is_active", True):
            print(f"⚠️ 商品 [{key}] 已标记为下架，跳过同步。")
            continue
            
        masked_path = entity.get("masked_path")
        real_url = entity.get("real_url") or entity.get("real_affiliate_url")
        product_name = entity.get("product_name")
        
        # 安全校验：确保核心路由字段不为空
        if not masked_path or not real_url or not product_name:
            print(f"⚠️ 商品 [{key}] 缺少核心配置字段，跳过。")
            continue

        # 容器 A (前端公开层): 只保留商品名和伪装路径，彻底剥离真实推广链接
        hugo_front_map[key] = {
            "product": product_name,
            "url": masked_path
        }

    # 1. 确保本地 config 目录存在并写入
    os.makedirs(os.path.dirname(hugo_data_path), exist_ok=True)
    with open(hugo_data_path, "w", encoding="utf-8") as f:
        json.dump(hugo_front_map, f, ensure_ascii=False, indent=2)
    print(f"✓ 成功刷新 Hugo 前端媒体映射表 -> [{hugo_data_path}] (敏感URL已完全剥离)")

    # Automatically export to Hugo site
    build_conf = _section(settings, "build")
    site_dir = _resolve(root, str(build_conf.get("site_dir") or "03-distribution/site"))
    
    if site_dir.exists():
        # ====== 🚀 核心修改：同步到 Hugo 站点的 data 文件夹 ======
        hugo_data_dir = site_dir / "data"
        # 如果 data 文件夹不存在则创建（parents=True 相当于 mkdir -p）
        hugo_data_dir.mkdir(parents=True, exist_ok=True)
        
        # 目标文件路径
        target_json_path = hugo_data_dir / hugo_data_path.name # 即 .../site/data/affiliate-map.json
        
        # 复制文件（shutil.copy2 会自动覆盖已存在的同名文件）
        shutil.copy2(hugo_data_path, target_json_path)
        print(f"✓ 已同步映射表至 Hugo 站点数据目录 -> [{target_json_path}]")
        # ========================================================


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
                    logger.warning("[scrape] skip %s: %s", source.source_id, exc)
                    continue
                raise
            fetched_sources.append(source.source_id)
            logger.info("[scrape] %s: %d item(s)", source.source_id, len(items))
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
    logger.info("[scrape] wrote %d raw item(s) to %s", len(items), path)
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

    # Load persistent review state if present
    review_store = ReviewStore(data_dir / "review_state.json")
    saved_reviews = review_store.load()

    master_db_path = root / "config/master-affiliate-db.json"
    items = normalize_raw_items(
        raw_items, 
        settings, 
        master_db_path,
        reviews=saved_reviews)

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

    logger.info("[content] wrote %d content item(s) to %s", len(items), content_path)

    # Automatically export to Hugo site
    build_conf = _section(settings, "build")
    site_dir = _resolve(root, str(build_conf.get("site_dir") or "03-distribution/site"))
    if site_dir.exists():
        export_to_hugo(items, site_dir)
        logger.info("[content] synchronized content to Hugo site at %s", site_dir)

    return content_path


def run_export(root: Path, settings: dict[str, Any]) -> list[Path]:
    data_dir = _data_dir(root, settings)
    content_path = data_dir / "content_items.json"
    payload = _read_json(content_path)
    entries = payload.get("items") or []

    items = [
        ContentItem.from_dict(entry)
        for entry in entries
        if isinstance(entry, dict)
    ]

    build_conf = _section(settings, "build")
    site_dir = _resolve(root, str(build_conf.get("site_dir") or "03-distribution/site"))
    written = export_to_hugo(items, site_dir)
    logger.info("[export] exported %d post(s) to %s/content/posts/", len(written), site_dir)
    return written


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

    logger.info("[validate] %d content item(s) passed", len(items))


def run_build(root: Path, settings: dict[str, Any]) -> None:
    build = _section(settings, "build")
    site_dir = _resolve(root, str(build.get("site_dir") or "03-distribution/site"))
    output_dir = _output_dir(root, settings)
    hugo = str(build.get("hugo_binary") or "hugo")

    # Ensure export to Hugo is fresh
    data_dir = _data_dir(root, settings)
    content_path = data_dir / "content_items.json"
    if content_path.exists():
        run_export(root, settings)

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

    logger.info("[build] wrote static site to %s", output_dir)


def run_kv_sync(root: Path, settings: dict[str, Any], dry_run: bool = False) -> dict[str, Any]:
    result = sync_affiliate_offers(root, settings, dry_run=dry_run)
    logger.info(
        "[sync-kv] processed %d offer(s), bulk file: %s, synced: %s (method: %s)",
        result["total_offers"],
        result["bulk_file"],
        result["synced"],
        result["method"],
    )
    return result


def run_review(
    root: Path,
    settings: dict[str, Any],
    action: str,
    target: str = "",
    reviewer: str = "editorial",
    notes: str = "",
) -> None:
    data_dir = _data_dir(root, settings)
    service = ReviewService(data_dir)

    if action == "list":
        summary = service.list_summary()
        print("\n=== Review Queue ===")
        print(f"Pending ({len(summary['pending'])}):")
        for item in summary["pending"]:
            print(f"  - [{item['slug']}] {item['title']} ({item['words']} words)")
        print(f"\nPublished ({len(summary['published'])}):")
        for item in summary["published"]:
            print(f"  - [{item['slug']}] {item['title']} (published at {item['published_at']})")
        print()
    elif action == "approve":
        if not target:
            raise ContentError("approve action requires --target <slug_or_id>")
        approved = service.approve(target, reviewer=reviewer, notes=notes)
        logger.info("[review] approved %s (status=%s, reviewed=%s)", approved.slug, approved.status, approved.reviewed)
        # Re-export to update Hugo content
        build_conf = _section(settings, "build")
        site_dir = _resolve(root, str(build_conf.get("site_dir") or "03-distribution/site"))
        if site_dir.exists():
            run_export(root, settings)
    elif action == "reject":
        if not target:
            raise ContentError("reject action requires --target <slug_or_id>")
        rejected = service.reject(target, reviewer=reviewer, reason=notes)
        logger.info("[review] rejected %s (status=%s)", rejected.slug, rejected.status)
        build_conf = _section(settings, "build")
        site_dir = _resolve(root, str(build_conf.get("site_dir") or "03-distribution/site"))
        if site_dir.exists():
            run_export(root, settings)


def run_all(root: Path, settings: dict[str, Any], dry_run: bool = False) -> None:
    run_scrape(root, settings)
    run_content(root, settings)
    run_validate(root, settings)
    run_export(root, settings)
    try:
        run_build(root, settings)
    except ContentError as exc:
        if "hugo binary not found" in str(exc):
            logger.warning("[build] skipped: %s", exc)
        else:
            raise
    run_kv_sync(root, settings, dry_run=dry_run)


def main() -> None:
    args = build_parser().parse_args()
    setup_logging(args.verbose)

    # Resolve settings path
    settings_path = args.settings
    if not settings_path.exists():
        example_settings = PROJECT_ROOT / "config/settings.example.yaml"
        if example_settings.exists():
            settings_path = example_settings

    try:
        settings = load_settings(settings_path)
        if args.auto_approve:
            if "pipeline" not in settings or not isinstance(settings["pipeline"], dict):
                settings["pipeline"] = {}
            settings["pipeline"]["auto_approve"] = True

        if args.stage == "sync-affilicate":
            run_map_affiliate(PROJECT_ROOT, settings)
        elif args.stage == "scrape":
            run_scrape(PROJECT_ROOT, settings)
        elif args.stage == "content":
            run_content(PROJECT_ROOT, settings)
        elif args.stage == "export":
            run_export(PROJECT_ROOT, settings)
        elif args.stage == "validate":
            run_validate(PROJECT_ROOT, settings)
        elif args.stage == "build":
            run_build(PROJECT_ROOT, settings)
        elif args.stage == "sync-kv":
            run_kv_sync(PROJECT_ROOT, settings, dry_run=args.dry_run)
        elif args.stage == "review":
            run_review(
                PROJECT_ROOT,
                settings,
                action=args.action,
                target=args.target,
                reviewer=args.reviewer,
                notes=args.notes,
            )
        else:
            run_all(PROJECT_ROOT, settings, dry_run=args.dry_run)
    except LocalWorkerError as exc:
        logger.error("%s", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
