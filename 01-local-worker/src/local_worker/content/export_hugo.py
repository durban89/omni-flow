from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import yaml

from local_worker.models import ContentItem, isoformat


def _dump_yaml_frontmatter(data: dict[str, Any]) -> str:
    cleaned = {k: v for k, v in data.items() if v is not None and v != "" and v != []}
    text = yaml.safe_dump(cleaned, allow_unicode=True, sort_keys=False).strip()
    return f"---\n{text}\n---\n\n"


def export_to_hugo(
    items: Iterable[ContentItem],
    site_dir: Path,
    *,
    clean_posts: bool = False,
) -> list[Path]:
    """Exports ContentItem objects into Hugo content and data directories."""
    posts_dir = site_dir / "content" / "posts"
    data_dir = site_dir / "data"

    posts_dir.mkdir(parents=True, exist_ok=True)
    data_dir.mkdir(parents=True, exist_ok=True)

    if clean_posts:
        for existing in posts_dir.glob("*.md"):
            existing.unlink()

    written_files: list[Path] = []
    items_list = list(items)

    for item in items_list:
        frontmatter = {
            "title": item.title,
            "date": isoformat(item.published_at or item.updated_at),
            "slug": item.slug,
            "draft": item.status != "published",
            "reviewed": item.reviewed,
            "description": item.summary,
            "category": item.category,
            "tags": list(item.tags),
            "keywords": list(item.keywords),
            "content_rating": item.content_rating,
            "affiliate_slug": item.affiliate_slug,
            "sources": [s.url for s in item.sources],
            "seo": {
                "title": item.seo.title,
                "description": item.seo.description,
                "canonical": item.seo.canonical,
                "noindex": item.seo.noindex,
            },
        }

        content = _dump_yaml_frontmatter(frontmatter) + item.body_markdown.strip() + "\n"
        dest_file = posts_dir / f"{item.slug}.md"
        dest_file.write_text(content, encoding="utf-8")
        written_files.append(dest_file)

    # Also save data file for data-driven templates
    data_file = data_dir / "content_items.json"
    data_payload = {
        "item_count": len(items_list),
        "items": [item.to_dict() for item in items_list],
    }
    data_file.write_text(
        json.dumps(data_payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    return written_files
