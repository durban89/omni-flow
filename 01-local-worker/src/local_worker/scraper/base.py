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


class SourceAdapter(Protocol):
    source_id: str

    def fetch(self) -> list[RawItem]: ...
