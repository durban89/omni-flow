from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from local_worker.errors import ContentError
from local_worker.models import (
    SCHEMA_VERSION,
    SEO,
    ContentItem,
    isoformat,
    utc_now,
)


class ReviewStore:
    def __init__(self, state_file: Path) -> None:
        self.state_file = state_file

    def load(self) -> dict[str, dict[str, Any]]:
        if not self.state_file.exists():
            return {}
        try:
            data = json.loads(self.state_file.read_text(encoding="utf-8"))
            return data.get("reviews", {}) if isinstance(data, dict) else {}
        except (json.JSONDecodeError, OSError):
            return {}

    def save(self, reviews: dict[str, dict[str, Any]]) -> None:
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": SCHEMA_VERSION,
            "updated_at": isoformat(utc_now()),
            "reviews": reviews,
        }
        self.state_file.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def record_decision(
        self,
        item_id: str,
        status: str,
        reviewed: bool,
        reviewer: str = "editorial",
        notes: str = "",
        published_at: str | None = None,
    ) -> None:
        reviews = self.load()
        reviews[item_id] = {
            "status": status,
            "reviewed": reviewed,
            "reviewer": reviewer,
            "notes": notes,
            "published_at": published_at,
            "reviewed_at": isoformat(utc_now()),
        }
        self.save(reviews)


class ReviewService:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.content_file = data_dir / "content_items.json"
        self.store = ReviewStore(data_dir / "review_state.json")

    def _load_items(self) -> list[ContentItem]:
        if not self.content_file.exists():
            raise ContentError(f"content file not found: {self.content_file}")
        try:
            payload = json.loads(self.content_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ContentError(f"invalid JSON in {self.content_file}: {exc}") from exc

        entries = payload.get("items")
        if not isinstance(entries, list):
            raise ContentError("content_items.json requires an items array")
        return [
            ContentItem.from_dict(entry)
            for entry in entries
            if isinstance(entry, dict)
        ]

    def _save_items(self, items: list[ContentItem]) -> None:
        now = isoformat(utc_now())
        self.content_file.write_text(
            json.dumps(
                {
                    "schema_version": SCHEMA_VERSION,
                    "generated_at": now,
                    "item_count": len(items),
                    "items": [item.to_dict() for item in items],
                },
                ensure_ascii=False,
                indent=2,
            ) + "\n",
            encoding="utf-8",
        )

        manifest_file = self.data_dir / "manifest.json"
        if manifest_file.exists():
            try:
                manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                manifest = {}
            status_counts = Counter(item.status for item in items)
            manifest["status_counts"] = dict(sorted(status_counts.items()))
            manifest["updated_at"] = now
            manifest_file.write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )

    def list_summary(self) -> dict[str, list[dict[str, Any]]]:
        items = self._load_items()
        pending = [
            {"id": item.id, "slug": item.slug, "title": item.title, "words": item.word_count}
            for item in items
            if item.status == "draft" or not item.reviewed
        ]
        published = [
            {"id": item.id, "slug": item.slug, "title": item.title, "published_at": isoformat(item.published_at)}
            for item in items
            if item.status == "published" and item.reviewed
        ]
        archived = [
            {"id": item.id, "slug": item.slug, "title": item.title}
            for item in items
            if item.status == "archived"
        ]
        return {"pending": pending, "published": published, "archived": archived}

    def approve(
        self,
        target: str,
        reviewer: str = "editorial",
        notes: str = "",
    ) -> ContentItem:
        items = self._load_items()
        target_item: ContentItem | None = None
        target_idx = -1

        for idx, item in enumerate(items):
            if item.slug == target or item.id == target:
                target_item = item
                target_idx = idx
                break

        if target_item is None:
            raise ContentError(f"item not found: '{target}'")

        if target_item.word_count < 300:
            raise ContentError(
                f"cannot publish '{target_item.slug}': word count is {target_item.word_count}, "
                "published content requires at least 300 words"
            )

        now = utc_now()
        approved_item = ContentItem(
            schema_version=target_item.schema_version,
            id=target_item.id,
            slug=target_item.slug,
            title=target_item.title,
            summary=target_item.summary,
            body_markdown=target_item.body_markdown,
            word_count=target_item.word_count,
            locale=target_item.locale,
            category=target_item.category,
            tags=target_item.tags,
            keywords=target_item.keywords,
            content_rating=target_item.content_rating,
            affiliate_slug=target_item.affiliate_slug,
            sources=target_item.sources,
            status="published",
            reviewed=True,
            published_at=target_item.published_at or now,
            updated_at=now,
            seo=SEO(
                title=target_item.seo.title,
                description=target_item.seo.description,
                canonical=target_item.seo.canonical,
                noindex=False,
            ),
        )
        approved_item.ensure_valid()

        items[target_idx] = approved_item
        self._save_items(items)
        self.store.record_decision(
            item_id=approved_item.id,
            status="published",
            reviewed=True,
            reviewer=reviewer,
            notes=notes,
            published_at=isoformat(approved_item.published_at),
        )
        return approved_item

    def reject(
        self,
        target: str,
        reviewer: str = "editorial",
        reason: str = "",
    ) -> ContentItem:
        items = self._load_items()
        target_item: ContentItem | None = None
        target_idx = -1

        for idx, item in enumerate(items):
            if item.slug == target or item.id == target:
                target_item = item
                target_idx = idx
                break

        if target_item is None:
            raise ContentError(f"item not found: '{target}'")

        now = utc_now()
        rejected_item = ContentItem(
            schema_version=target_item.schema_version,
            id=target_item.id,
            slug=target_item.slug,
            title=target_item.title,
            summary=target_item.summary,
            body_markdown=target_item.body_markdown,
            word_count=target_item.word_count,
            locale=target_item.locale,
            category=target_item.category,
            tags=target_item.tags,
            keywords=target_item.keywords,
            content_rating=target_item.content_rating,
            affiliate_slug=target_item.affiliate_slug,
            sources=target_item.sources,
            status="archived",
            reviewed=False,
            published_at=None,
            updated_at=now,
            seo=SEO(
                title=target_item.seo.title,
                description=target_item.seo.description,
                canonical=target_item.seo.canonical,
                noindex=True,
            ),
        )

        items[target_idx] = rejected_item
        self._save_items(items)
        self.store.record_decision(
            item_id=rejected_item.id,
            status="archived",
            reviewed=False,
            reviewer=reviewer,
            notes=reason,
        )
        return rejected_item
