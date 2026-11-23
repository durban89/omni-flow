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
    # URLs already present in content_items.json or in the lead history. Sources
    # that support discovery (topic_search) skip these and keep looking for
    # fresh leads.
    exclude_urls: frozenset[str] = frozenset()
    # Seed queries already used by generated content / earlier scrape runs.
    # topic_search skips the whole query instead of returning another article
    # about the same topic.
    exclude_queries: frozenset[str] = frozenset()
    # Titles already covered by generated content / earlier scrape runs. Used by
    # topic_search as a similarity guard across queries.
    exclude_titles: tuple[str, ...] = ()
    # Maximum number of new leads the source should return. Zero means the
    # source's own max_items setting applies.
    max_new_items: int = 0


class SourceAdapter(Protocol):
    source_id: str

    def fetch(self) -> list[RawItem]: ...
