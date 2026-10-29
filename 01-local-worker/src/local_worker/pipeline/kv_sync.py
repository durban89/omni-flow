from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from local_worker.errors import ConfigError
from local_worker.models import SLUG_PATTERN

logger = logging.getLogger("local_worker.kv_sync")

VALID_OFFER_STATUSES = {"active", "paused", "disabled"}


def validate_offer(entry: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    slug = str(entry.get("slug") or "").strip()
    if not slug:
        errors.append("slug is required")
    elif not SLUG_PATTERN.fullmatch(slug):
        errors.append(f"invalid slug format: {slug}")

    destination = str(entry.get("destination") or "").strip()
    if not destination:
        errors.append("destination is required")
    else:
        parsed = urlparse(destination)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            errors.append(f"invalid destination url: {destination}")

    status = str(entry.get("status") or "").strip()
    if status not in VALID_OFFER_STATUSES:
        errors.append(f"invalid status: {status}; must be one of {VALID_OFFER_STATUSES}")

    return errors


def sync_affiliate_offers(
    root: Path,
    settings: dict[str, Any],
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Validates affiliate map and generates/syncs Cloudflare KV bulk data."""
    pipeline_conf = settings.get("pipeline") if isinstance(settings.get("pipeline"), dict) else {}
    data_dir_name = str(pipeline_conf.get("data_dir") or "01-local-worker/build/data")
    data_dir = root / data_dir_name if not Path(data_dir_name).is_absolute() else Path(data_dir_name)
    data_dir.mkdir(parents=True, exist_ok=True)

    affiliate_path = root / "config/affiliate-map.json"
    if not affiliate_path.exists():
        example_path = root / "config/affiliate-map.example.json"
        if example_path.exists():
            affiliate_path = example_path
        else:
            raise ConfigError(f"affiliate map file not found: {affiliate_path}")

    try:
        raw_data = json.loads(affiliate_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise ConfigError(f"invalid JSON in {affiliate_path}: {exc}") from exc

    offers = raw_data.get("offers") if isinstance(raw_data, dict) else raw_data
    if not isinstance(offers, list):
        raise ConfigError("affiliate map must contain an offers list")

    kv_pairs: list[dict[str, str]] = []
    all_errors: list[str] = []

    for idx, offer in enumerate(offers):
        if not isinstance(offer, dict):
            all_errors.append(f"offer #{idx}: must be a JSON object")
            continue
        errors = validate_offer(offer)
        if errors:
            all_errors.extend(f"offer '{offer.get('slug', idx)}': {err}" for err in errors)
            continue

        slug = str(offer["slug"])
        value_data = {
            "destination": offer["destination"],
            "status": offer["status"],
            "geo": offer.get("geo") or [],
            "disclosure_required": bool(offer.get("disclosure_required", True)),
        }
        kv_pairs.append({
            "key": f"offer:{slug}",
            "value": json.dumps(value_data, ensure_ascii=False),
        })

    if all_errors:
        raise ConfigError("affiliate map validation failed:\n" + "\n".join(f"- {e}" for e in all_errors))

    bulk_file = data_dir / "kv_bulk.json"
    bulk_file.write_text(
        json.dumps(kv_pairs, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    logger.info("Wrote %d KV offer pair(s) to %s", len(kv_pairs), bulk_file)

    synced = False
    sync_method = "bulk_file_only"

    # Attempt Wrangler sync if available and not dry_run
    cf_conf = settings.get("cloudflare") if isinstance(settings.get("cloudflare"), dict) else {}
    kv_binding = str(cf_conf.get("kv_namespace_binding") or "APP_KV")
    wrangler_bin = shutil.which("wrangler")

    if not dry_run and wrangler_bin and os.environ.get("CLOUDFLARE_API_TOKEN"):
        try:
            logger.info("Syncing KV via Wrangler to binding %s", kv_binding)
            cmd = [
                wrangler_bin,
                "kv",
                "bulk",
                "put",
                str(bulk_file),
                "--binding",
                kv_binding,
            ]
            subprocess.run(cmd, check=True, cwd=root / "02-cloudflare/worker")
            synced = True
            sync_method = "wrangler"
        except (subprocess.CalledProcessError, OSError) as exc:
            logger.warning("Wrangler KV sync skipped/failed: %s", exc)

    return {
        "total_offers": len(kv_pairs),
        "bulk_file": str(bulk_file),
        "synced": synced,
        "method": sync_method,
    }
