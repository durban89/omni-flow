from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from local_worker.errors import ConfigError
from local_worker.models import SLUG_PATTERN, isoformat, slugify, utc_now

logger = logging.getLogger("local_worker.affiliate_sync")

OFFERS_FILENAME = "affiliate-offers.json"
OFFERS_EXAMPLE_FILENAME = "affiliate-offers.example.json"
HUGO_MAP_FILENAME = "affiliate-map.json"
MASTER_DB_FILENAME = "master-affiliate-db.json"
MASTER_DB_EXAMPLE_FILENAME = "master-affiliate-db.example.json"


def _section(settings: dict[str, Any], name: str) -> dict[str, Any]:
    value = settings.get(name)
    return value if isinstance(value, dict) else {}


def _resolve(root: Path, value: str) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else root / path


def resolve_master_db_path(root: Path) -> Path:
    primary = root / "config" / MASTER_DB_FILENAME
    if primary.exists():
        return primary
    example = root / "config" / MASTER_DB_EXAMPLE_FILENAME
    if example.exists():
        return example
    raise ConfigError(
        f"master affiliate db not found: {primary}; "
        f"copy config/{MASTER_DB_EXAMPLE_FILENAME} to config/{MASTER_DB_FILENAME}"
    )


def resolve_offers_path(root: Path, *, allow_example: bool = True) -> Path:
    primary = root / "config" / OFFERS_FILENAME
    if primary.exists():
        return primary
    if allow_example:
        example = root / "config" / OFFERS_EXAMPLE_FILENAME
        if example.exists():
            return example
    raise ConfigError(
        f"affiliate offers file not found: {primary}; "
        f"run sync-affiliate or copy config/{OFFERS_EXAMPLE_FILENAME}"
    )


def slug_from_masked_path(masked_path: str) -> str:
    """Derive Worker offer slug from a public path like /out/okamoto-001."""
    cleaned = masked_path.strip()
    if not cleaned:
        return ""
    path = urlparse(cleaned).path if "://" in cleaned else cleaned
    parts = [part for part in path.strip("/").split("/") if part]
    if not parts:
        return ""
    if parts[0] == "out" and len(parts) >= 2:
        candidate = parts[-1]
    else:
        candidate = parts[-1]
    slug = slugify(candidate)
    return slug if SLUG_PATTERN.fullmatch(slug or "") else ""


def _offer_status(entity: dict[str, Any]) -> str:
    metrics = entity.get("meta_metrics")
    metrics = metrics if isinstance(metrics, dict) else {}
    if bool(metrics.get("is_active", True)):
        return "active"
    return "paused"


def build_maps_from_master(
    master_db: dict[str, Any],
    *,
    disclosure_required: bool = True,
) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    """
    Build (hugo_front_map, offers_payload, warnings) from master affiliate DB.

    - hugo_front_map: token -> {product, url} for Hugo shortcodes (no real URLs)
    - offers_payload: {version, updated_at, offers[]} for Cloudflare KV sync
    """
    hugo_front_map: dict[str, Any] = {}
    offers: list[dict[str, Any]] = []
    warnings: list[str] = []
    seen_slugs: set[str] = set()

    for key, entity in master_db.items():
        if not isinstance(entity, dict):
            warnings.append(f"skip '{key}': entry must be an object")
            continue

        metrics = entity.get("meta_metrics")
        metrics = metrics if isinstance(metrics, dict) else {}
        if not bool(metrics.get("is_active", True)):
            warnings.append(f"skip '{key}': marked inactive")
            continue

        masked_path = str(entity.get("masked_path") or "").strip()
        real_url = str(
            entity.get("real_url") or entity.get("real_affiliate_url") or ""
        ).strip()
        product_name = str(entity.get("product_name") or "").strip()

        if not masked_path or not real_url or not product_name:
            warnings.append(f"skip '{key}': missing masked_path, real URL, or product_name")
            continue

        slug = slug_from_masked_path(masked_path)
        if not slug:
            warnings.append(f"skip '{key}': cannot derive offer slug from '{masked_path}'")
            continue
        if slug in seen_slugs:
            warnings.append(f"skip '{key}': duplicate offer slug '{slug}'")
            continue
        seen_slugs.add(slug)

        parsed = urlparse(real_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            warnings.append(f"skip '{key}': invalid real affiliate URL '{real_url}'")
            continue

        hugo_front_map[key] = {
            "product": product_name,
            "url": masked_path,
        }
        offers.append(
            {
                "slug": slug,
                "destination": real_url,
                "status": _offer_status(entity),
                "geo": list(entity.get("geo") or []),
                "disclosure_required": bool(
                    entity.get("disclosure_required", disclosure_required)
                ),
                "token": key,
            }
        )

    offers_payload = {
        "version": 1,
        "updated_at": isoformat(utc_now()),
        "source": MASTER_DB_FILENAME,
        "offers": offers,
    }
    return hugo_front_map, offers_payload, warnings


def sync_from_master_db(
    root: Path,
    settings: dict[str, Any],
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    """
    Generate Hugo public affiliate-map and KV affiliate-offers from master DB.

    Writes:
      - config/affiliate-offers.json
      - <site_dir>/data/affiliate-map.json
    """
    master_path = resolve_master_db_path(root)
    try:
        master_db = json.loads(master_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise ConfigError(f"invalid master affiliate db in {master_path}: {exc}") from exc
    if not isinstance(master_db, dict):
        raise ConfigError(f"master affiliate db must be a JSON object: {master_path}")

    compliance = _section(settings, "compliance")
    disclosure_required = bool(compliance.get("require_affiliate_disclosure", True))

    hugo_front_map, offers_payload, warnings = build_maps_from_master(
        master_db,
        disclosure_required=disclosure_required,
    )
    for warning in warnings:
        logger.warning("[sync-affiliate] %s", warning)

    build_conf = _section(settings, "build")
    site_dir = _resolve(root, str(build_conf.get("site_dir") or "03-distribution/site"))
    offers_path = root / "config" / OFFERS_FILENAME
    hugo_map_path = site_dir / "data" / HUGO_MAP_FILENAME

    result: dict[str, Any] = {
        "master_db": str(master_path),
        "offers_file": str(offers_path),
        "hugo_map_file": str(hugo_map_path),
        "offer_count": len(offers_payload["offers"]),
        "hugo_entry_count": len(hugo_front_map),
        "warnings": warnings,
        "written": False,
        "dry_run": dry_run,
    }

    if dry_run:
        logger.info(
            "[sync-affiliate] dry-run: %d offer(s), %d hugo entr(y/ies)",
            result["offer_count"],
            result["hugo_entry_count"],
        )
        return result

    offers_path.parent.mkdir(parents=True, exist_ok=True)
    offers_path.write_text(
        json.dumps(offers_payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    hugo_map_path.parent.mkdir(parents=True, exist_ok=True)
    hugo_map_path.write_text(
        json.dumps(hugo_front_map, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    result["written"] = True
    logger.info(
        "[sync-affiliate] wrote %d offer(s) -> %s; %d hugo entr(y/ies) -> %s",
        result["offer_count"],
        offers_path,
        result["hugo_entry_count"],
        hugo_map_path,
    )
    return result
