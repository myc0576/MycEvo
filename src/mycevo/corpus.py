"""License-first corpus provider contracts for PaperFrames."""
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
import json
from urllib.parse import quote
from urllib.request import Request, urlopen

@dataclass(frozen=True)
class CorpusQuery:
    text: str

@dataclass(frozen=True)
class WorkRecord:
    work_id: str
    title: str
    license_url: str | None = None

@dataclass(frozen=True)
class ResolvedWork:
    record: WorkRecord
    source_url: str | None = None

@dataclass(frozen=True)
class FetchResult:
    path: Path
    status: str
    provenance: dict[str, object]

class CorpusProvider(Protocol):
    def discover(self, query: CorpusQuery) -> list[WorkRecord]: ...
    def resolve(self, work: WorkRecord) -> ResolvedWork: ...
    def fetch(self, work: ResolvedWork, destination: Path) -> FetchResult: ...

class LocalPrivateProvider:
    """Local-only provider; never uploads or copies into public fixtures."""
    def discover(self, query: CorpusQuery) -> list[WorkRecord]:
        return []
    def resolve(self, work: WorkRecord) -> ResolvedWork:
        return ResolvedWork(work)
    def fetch(self, work: ResolvedWork, destination: Path) -> FetchResult:
        raise RuntimeError("local provider requires an explicit local source; no implicit copy is allowed")

class CrossrefProvider:
    """Metadata-only Crossref provider; full text is never fetched implicitly."""
    def __init__(self, *, user_agent: str = "mycevo-paperframes/0.1"):
        self.user_agent = user_agent

    def discover(self, query: CorpusQuery) -> list[WorkRecord]:
        request = Request("https://api.crossref.org/works?query=" + quote(query.text) + "&rows=10", headers={"User-Agent": self.user_agent})
        with urlopen(request, timeout=15) as response:  # noqa: S310 - fixed HTTPS endpoint; caller opts into network provider
            payload = json.load(response)
        return [WorkRecord(str(item.get("DOI", "")), (item.get("title") or ["untitled"])[0], item.get("URL")) for item in payload.get("message", {}).get("items", []) if item.get("DOI")]

    def resolve(self, work: WorkRecord) -> ResolvedWork:
        return ResolvedWork(work, work.license_url)

    def fetch(self, work: ResolvedWork, destination: Path) -> FetchResult:
        raise RuntimeError("CrossrefProvider is metadata-only in V1; license and TDM authorization are required before content fetch")
