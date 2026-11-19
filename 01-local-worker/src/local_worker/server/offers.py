"""Helpers for managing ``config/master-affiliate-db.json`` from the console.

The master DB is the single source of truth: ``sync-affiliate`` derives
``config/affiliate-offers.json`` and the Hugo affiliate map from it.  Editing
here therefore propagates to every downstream file after the next
``sync-affiliate`` run.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from local_worker.errors import ConfigError
from local_worker.pipeline.affiliate_sync import slug_from_masked_path

# Re-exported for callers / tests.
GEO_PATTERN = re.compile(r"[A-Z]{2}")
STATUS_OPTIONS = ["active", "paused"]


def _master_path(root: Path) -> Path:
    return root / "config" / "master-affiliate-db.json"


def master_db_path(root: Path) -> Path:
    """Return the master DB path (may not exist yet)."""
    return _master_path(root)


def _read_master(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise ConfigError(f"invalid master affiliate db in {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError(f"master affiliate db must be a JSON object: {path}")
    return raw


def _normalize_geo(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        items = [part.strip() for part in value.split(",")]
    elif isinstance(value, (list, tuple)):
        items = [str(item).strip() for item in value]
    else:
        items = [str(value).strip()]
    result: list[str] = []
    seen: set[str] = set()
    for item in items:
        if not item:
            continue
        code = item.upper()
        if code not in seen:
            seen.add(code)
            result.append(code)
    return result


def _is_active(entity: dict[str, Any]) -> bool:
    metrics = entity.get("meta_metrics")
    metrics = metrics if isinstance(metrics, dict) else {}
    return bool(metrics.get("is_active", True))


def _destination(entity: dict[str, Any]) -> str:
    for key in ("real_affiliate_url", "real_url", "destination", "url"):
        value = entity.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def normalize_entry(token: str, entity: Any) -> dict[str, Any]:
    entity = entity if isinstance(entity, dict) else {}
    metrics = entity.get("meta_metrics")
    metrics = metrics if isinstance(metrics, dict) else {}
    masked_path = str(entity.get("masked_path") or "").strip()
    geo = _normalize_geo(entity.get("geo"))
    is_active = bool(metrics.get("is_active", True))
    return {
        "token": token,
        "product_name": str(entity.get("product_name") or "").strip(),
        "brand": str(entity.get("brand") or "").strip(),
        "niche": str(entity.get("niche") or "").strip(),
        "masked_path": masked_path,
        "target_platform": str(entity.get("target_platform") or "").strip(),
        "destination": _destination(entity),
        "status": "active" if is_active else "paused",
        "is_active": is_active,
        "geo": geo,
        "disclosure_required": bool(entity.get("disclosure_required", True)),
        "slug": slug_from_masked_path(masked_path),
    }


def validate_entry(token: str, entity: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if not token.strip():
        errors.append("token is required")

    product_name = str(entity.get("product_name") or "").strip()
    if not product_name:
        errors.append("product_name is required")

    masked_path = str(entity.get("masked_path") or "").strip()
    if not masked_path:
        errors.append("masked_path is required")
    else:
        slug = slug_from_masked_path(masked_path)
        if not slug:
            errors.append(f"cannot derive offer slug from masked_path: {masked_path}")

    destination = _destination(entity)
    if not destination:
        errors.append("destination is required")
    else:
        parsed = urlparse(destination)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            errors.append(f"invalid destination url: {destination}")

    metrics = entity.get("meta_metrics")
    metrics = metrics if isinstance(metrics, dict) else {}
    status = "active" if bool(metrics.get("is_active", True)) else "paused"
    if status not in STATUS_OPTIONS:
        errors.append(f"invalid status: {status}; must be one of {set(STATUS_OPTIONS)}")

    geo = _normalize_geo(entity.get("geo"))
    for code in geo:
        if not GEO_PATTERN.fullmatch(code):
            errors.append(f"invalid geo code: {code}; use ISO 3166-1 alpha-2 codes like US, CA, GB")

    return errors


def describe(root: Path) -> dict[str, Any]:
    path = _master_path(root)
    exists = path.exists()
    data = _read_master(path) if exists else {}
    entries: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    for token, entity in data.items():
        norm = normalize_entry(str(token), entity)
        entries.append(norm)
        errors = validate_entry(str(token), entity if isinstance(entity, dict) else {})
        if errors:
            issues.append({"token": str(token), "errors": errors})

    return {
        "path": str(path),
        "exists": exists,
        "entries": entries,
        # Keep the legacy key name so the existing table/filter code keeps working.
        "offers": entries,
        "issues": issues,
        "status_options": list(STATUS_OPTIONS),
        "geo_pattern": GEO_PATTERN.pattern,
    }


def _write(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        backup = path.with_name(path.name + ".bak")
        backup.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    tmp.replace(path)


def save_entry(
    root: Path,
    payload: Any,
    *,
    original_token: str | None = None,
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ConfigError("offer payload must be a JSON object")

    token = str(
        payload.get("token")
        or payload.get("slug")
        or payload.get("key")
        or ""
    ).strip()
    if not token:
        raise ConfigError("token is required")

    path = _master_path(root)
    data = _read_master(path)

    original = (original_token or "").strip()
    if original and original not in data:
        raise LookupError(f"master db entry not found: {original}")

    if token in data and token != original:
        raise ConfigError(f"token already exists: {token}")

    base: dict[str, Any] = {}
    if original:
        existing = data.get(original)
        if isinstance(existing, dict):
            base = dict(existing)

    entity = dict(base)

    if "product_name" in payload and payload.get("product_name") is not None:
        entity["product_name"] = str(payload.get("product_name")).strip()
    elif "product_name" not in entity:
        entity["product_name"] = base.get("product_name", "")

    if "brand" in payload and payload.get("brand") is not None:
        entity["brand"] = str(payload.get("brand")).strip()
    if "niche" in payload and payload.get("niche") is not None:
        entity["niche"] = str(payload.get("niche")).strip()
    if "target_platform" in payload and payload.get("target_platform") is not None:
        entity["target_platform"] = str(payload.get("target_platform")).strip()

    if "masked_path" in payload and payload.get("masked_path") is not None:
        entity["masked_path"] = str(payload.get("masked_path")).strip()
    elif "masked_path" not in entity:
        entity["masked_path"] = base.get("masked_path", "")

    destination = payload.get("destination")
    if destination is None:
        destination = payload.get("real_url") or payload.get("real_affiliate_url")
    if destination is not None:
        entity["real_affiliate_url"] = str(destination).strip()

    if "status" in payload and payload.get("status") is not None:
        status_value = str(payload.get("status")).strip().lower()
        if status_value not in STATUS_OPTIONS:
            raise ConfigError(
                f"invalid status: {payload.get('status')}; must be one of {set(STATUS_OPTIONS)}"
            )

    # status -> is_active
    metrics = entity.get("meta_metrics")
    metrics = metrics if isinstance(metrics, dict) else {}
    if "status" in payload and payload.get("status") is not None:
        metrics["is_active"] = str(payload.get("status")).strip().lower() == "active"
    elif "is_active" in payload and payload.get("is_active") is not None:
        metrics["is_active"] = bool(payload.get("is_active"))
    elif "is_active" not in metrics:
        metrics["is_active"] = True
    entity["meta_metrics"] = metrics

    if "geo" in payload:
        entity["geo"] = _normalize_geo(payload.get("geo"))
    elif "geo" not in entity:
        entity["geo"] = []

    if "disclosure_required" in payload and payload.get("disclosure_required") is not None:
        entity["disclosure_required"] = bool(payload.get("disclosure_required"))

    errors = validate_entry(token, entity)
    if errors:
        raise ConfigError("; ".join(errors))

    if original and original != token:
        del data[original]
    data[token] = entity
    _write(path, data)
    return describe(root)


# Backwards-friendly alias used by the HTTP layer.
def save_offer(root: Path, payload: Any, *, original_slug: str | None = None) -> dict[str, Any]:
    return save_entry(root, payload, original_token=original_slug)


def delete_entry(root: Path, token: str) -> dict[str, Any]:
    token = (token or "").strip()
    if not token:
        raise ConfigError("token is required")
    path = _master_path(root)
    data = _read_master(path)
    if token not in data:
        raise LookupError(f"master db entry not found: {token}")
    del data[token]
    _write(path, data)
    return describe(root)


def delete_offer(root: Path, token: str) -> dict[str, Any]:
    return delete_entry(root, token)


# Compatibility shims for any external callers.
offers_path = master_db_path
normalize_offer = normalize_entry