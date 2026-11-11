from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class Paper:
    title: str
    year: int | None = None
    publication_date: str | None = None
    doi: str | None = None
    authors: list[str] = field(default_factory=list)
    abstract: str | None = None
    journal: str | None = None
    publisher: str | None = None
    volume: str | None = None
    issue: str | None = None
    pages: str | None = None
    issn: list[str] = field(default_factory=list)
    openalex_id: str | None = None
    semantic_scholar_id: str | None = None
    crossref: dict[str, Any] | None = None
    publication_type: str | None = None
    keywords: list[str] = field(default_factory=list)
    landing_page_url: str | None = None
    doi_url: str | None = None
    oa_status: str | None = None
    oa_license: str | None = None
    pdf_url: str | None = None
    openalex_content_url: str | None = None
    pdf_download_source: str | None = None
    sources: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)
    classification_status: str = "NEEDS_MANUAL_REVIEW"
    classification_reason: str | None = None
    download_status: str = "NOT_ATTEMPTED"
    local_pdf_path: str | None = None
    access_error: str | None = None
    collected_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
