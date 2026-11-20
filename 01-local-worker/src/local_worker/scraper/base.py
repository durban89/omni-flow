from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from local_worker.models import RawItem, utc_now
from local_worker.scraper.http import HttpClient


@dataclass(frozen=True)
class SourceContext:
    client: HttpClient
    retrieved_at: datetime = field(default_factory=utc_now)
    # URLs already present in content_items.json. Sources that support discovery
    # (topic_search) skip these and keep looking for fresh leads.
    exclude_urls: frozenset[str] = frozenset()
    # Maximum number of new leads the source should return. Zero means the
    # source's own max_items setting applies.
    max_new_items: int = 0


class SourceAdapter(Protocol):
    source_id: str

    def fetch(self) -> list[RawItem]: ...
