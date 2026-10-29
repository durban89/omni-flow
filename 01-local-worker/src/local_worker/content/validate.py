from __future__ import annotations

from collections.abc import Iterable

from local_worker.models import ContentItem


def validate_content_items(items: Iterable[ContentItem]) -> list[str]:
    errors: list[str] = []
    seen_ids: dict[str, str] = {}
    seen_slugs: dict[str, str] = {}

    for item in items:
        label = item.slug or item.id or "<unknown>"
        errors.extend(f"{label}: {error}" for error in item.validate())

        if item.id in seen_ids:
            errors.append(f"{label}: duplicate id {item.id} (also used by {seen_ids[item.id]})")
        else:
            seen_ids[item.id] = label

        if item.slug in seen_slugs:
            errors.append(
                f"{label}: duplicate slug {item.slug} (also used by {seen_slugs[item.slug]})"
            )
        else:
            seen_slugs[item.slug] = label

    return errors
