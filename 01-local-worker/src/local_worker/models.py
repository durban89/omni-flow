from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urldefrag, urlparse

from local_worker.errors import ModelValidationError

SCHEMA_VERSION = "1.0"
SLUG_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
CONTENT_RATINGS = {"general", "sensitive"}
STATUSES = {"draft", "review", "published", "archived"}
BODY_FORMATS = {"html", "markdown", "text"}


def utc_now() -> datetime:
    return datetime.now(UTC)


def ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def parse_datetime(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return ensure_utc(value)
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=UTC)

    text = str(value).strip()
    if not text:
        return None

    try:
        return ensure_utc(datetime.fromisoformat(text))
    except ValueError:
        pass

    try:
        return ensure_utc(parsedate_to_datetime(text))
    except (TypeError, ValueError):
        return None


def isoformat(value: datetime | None) -> str | None:
    if value is None:
        return None
    return ensure_utc(value).isoformat().replace("+00:00", "Z")


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return re.sub(r"-{2,}", "-", slug)


def canonical_url(value: str) -> str:
    return urldefrag(value.strip())[0]


def content_id(source_url: str, title: str) -> str:
    digest = hashlib.sha256(f"{source_url}\0{title}".encode()).hexdigest()
    return digest[:16]


def optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def string_list(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        values: Iterable[Any] = [value]
    elif isinstance(value, Iterable):
        values = value
    else:
        return ()

    result: list[str] = []
    seen: set[str] = set()
    for item in values:
        text = str(item).strip()
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return tuple(result)


@dataclass(frozen=True)
class SourceRef:
    url: str
    title: str | None = None
    publisher: str | None = None
    license: str | None = None
    retrieved_at: datetime = field(default_factory=utc_now)

    def validate(self) -> list[str]:
        errors: list[str] = []
        parsed = urlparse(self.url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            errors.append(f"invalid source url: {self.url}")
        if not self.retrieved_at:
            errors.append("source retrieved_at is required")
        return errors

    def to_dict(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "title": self.title,
            "publisher": self.publisher,
            "license": self.license,
            "retrieved_at": isoformat(self.retrieved_at),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SourceRef:
        return cls(
            url=str(data.get("url", "")).strip(),
            title=optional_str(data.get("title")),
            publisher=optional_str(data.get("publisher")),
            license=optional_str(data.get("license")),
            retrieved_at=parse_datetime(data.get("retrieved_at")) or utc_now(),
        )


@dataclass(frozen=True)
class SEO:
    title: str
    description: str
    canonical: str | None = None
    noindex: bool = True

    def validate(self) -> list[str]:
        errors: list[str] = []
        if not self.title.strip():
            errors.append("seo.title is required")
        if not self.description.strip():
            errors.append("seo.description is required")
        if self.canonical:
            parsed = urlparse(self.canonical)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                errors.append(f"invalid seo.canonical: {self.canonical}")
        return errors

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "description": self.description,
            "canonical": self.canonical,
            "noindex": self.noindex,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SEO:
        return cls(
            title=str(data.get("title", "")),
            description=str(data.get("description", "")),
            canonical=optional_str(data.get("canonical")),
            noindex=bool(data.get("noindex", True)),
        )


@dataclass(frozen=True)
class RawItem:
    source_id: str
    source_url: str
    title: str
    summary: str
    body: str
    body_format: str
    retrieved_at: datetime
    category: str = "general"
    tags: tuple[str, ...] = ()
    keywords: tuple[str, ...] = ()
    published_at: datetime | None = None
    publisher: str | None = None
    license: str | None = None
    affiliate_slug: str | None = None

    def validate(self) -> list[str]:
        errors: list[str] = []
        if not self.source_id.strip():
            errors.append("source_id is required")
        parsed = urlparse(self.source_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            errors.append(f"invalid source_url: {self.source_url}")
        if not self.title.strip():
            errors.append("title is required")
        if not self.body.strip():
            errors.append("body is required")
        if self.body_format not in BODY_FORMATS:
            errors.append(f"unsupported body_format: {self.body_format}")
        return errors

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "source_url": self.source_url,
            "title": self.title,
            "summary": self.summary,
            "body": self.body,
            "body_format": self.body_format,
            "retrieved_at": isoformat(self.retrieved_at),
            "category": self.category,
            "tags": list(self.tags),
            "keywords": list(self.keywords),
            "published_at": isoformat(self.published_at),
            "publisher": self.publisher,
            "license": self.license,
            "affiliate_slug": self.affiliate_slug,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RawItem:
        return cls(
            source_id=str(data.get("source_id", "")),
            source_url=str(data.get("source_url", "")),
            title=str(data.get("title", "")),
            summary=str(data.get("summary", "")),
            body=str(data.get("body", "")),
            body_format=str(data.get("body_format") or "markdown"),
            retrieved_at=parse_datetime(data.get("retrieved_at")) or utc_now(),
            category=str(data.get("category") or "general"),
            tags=string_list(data.get("tags")),
            keywords=string_list(data.get("keywords")),
            published_at=parse_datetime(data.get("published_at")),
            publisher=optional_str(data.get("publisher")),
            license=optional_str(data.get("license")),
            affiliate_slug=optional_str(data.get("affiliate_slug")),
        )


@dataclass(frozen=True)
class ContentItem:
    id: str
    slug: str
    title: str
    summary: str
    body_markdown: str
    word_count: int
    locale: str
    category: str
    content_rating: str
    status: str
    reviewed: bool
    updated_at: datetime
    seo: SEO
    tags: tuple[str, ...] = ()
    keywords: tuple[str, ...] = ()
    affiliate_slug: str | None = None
    sources: tuple[SourceRef, ...] = ()
    published_at: datetime | None = None
    schema_version: str = SCHEMA_VERSION

    def validate(self) -> list[str]:
        errors: list[str] = []
        if self.schema_version != SCHEMA_VERSION:
            errors.append(f"unsupported schema_version: {self.schema_version}")
        if not self.id.strip():
            errors.append("id is required")
        if not SLUG_PATTERN.fullmatch(self.slug):
            errors.append(f"invalid slug: {self.slug}")
        if len(self.title.strip()) < 3:
            errors.append("title must be at least 3 characters")
        if len(self.title.strip()) > 160:
            errors.append("title must be at most 160 characters")
        if not self.summary.strip():
            errors.append("summary is required")
        if len(self.summary) > 320:
            errors.append("summary must be at most 320 characters")
        if not self.body_markdown.strip():
            errors.append("body_markdown is required")
        if self.word_count < 1:
            errors.append("word_count must be positive")
        if not self.locale.strip():
            errors.append("locale is required")
        if not self.category.strip():
            errors.append("category is required")
        if self.content_rating not in CONTENT_RATINGS:
            errors.append(f"unsupported content_rating: {self.content_rating}")
        if self.status not in STATUSES:
            errors.append(f"unsupported status: {self.status}")
        if self.affiliate_slug and not SLUG_PATTERN.fullmatch(self.affiliate_slug):
            errors.append(f"invalid affiliate_slug: {self.affiliate_slug}")
        if not self.sources:
            errors.append("sources must not be empty")

        if self.status == "published":
            if not self.reviewed:
                errors.append("published content must be reviewed")
            if self.seo.noindex:
                errors.append("published content cannot be noindex")
            if self.published_at is None:
                errors.append("published content requires published_at")
            if self.word_count < 300:
                errors.append("published content requires at least 300 words")
        elif not self.seo.noindex:
            errors.append("non-published content must be noindex")

        for source in self.sources:
            errors.extend(source.validate())
        errors.extend(self.seo.validate())
        return errors

    def ensure_valid(self) -> None:
        errors = self.validate()
        if errors:
            raise ModelValidationError(f"{self.slug}: {'; '.join(errors)}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "id": self.id,
            "slug": self.slug,
            "title": self.title,
            "summary": self.summary,
            "body_markdown": self.body_markdown,
            "word_count": self.word_count,
            "locale": self.locale,
            "category": self.category,
            "tags": list(self.tags),
            "keywords": list(self.keywords),
            "content_rating": self.content_rating,
            "affiliate_slug": self.affiliate_slug,
            "sources": [source.to_dict() for source in self.sources],
            "status": self.status,
            "reviewed": self.reviewed,
            "published_at": isoformat(self.published_at),
            "updated_at": isoformat(self.updated_at),
            "seo": self.seo.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ContentItem:
        sources_data = data.get("sources") or []
        sources = tuple(
            SourceRef.from_dict(entry)
            for entry in sources_data
            if isinstance(entry, dict)
        )
        seo_data = data.get("seo") or {}
        return cls(
            schema_version=str(data.get("schema_version", SCHEMA_VERSION)),
            id=str(data.get("id", "")),
            slug=str(data.get("slug", "")),
            title=str(data.get("title", "")),
            summary=str(data.get("summary", "")),
            body_markdown=str(data.get("body_markdown", "")),
            word_count=int(data.get("word_count", 0)),
            locale=str(data.get("locale", "")),
            category=str(data.get("category", "")),
            tags=string_list(data.get("tags")),
            keywords=string_list(data.get("keywords")),
            content_rating=str(data.get("content_rating", "")),
            affiliate_slug=optional_str(data.get("affiliate_slug")),
            sources=sources,
            status=str(data.get("status", "")),
            reviewed=bool(data.get("reviewed", False)),
            published_at=parse_datetime(data.get("published_at")),
            updated_at=parse_datetime(data.get("updated_at")) or utc_now(),
            seo=SEO.from_dict(seo_data if isinstance(seo_data, dict) else {}),
        )
