import time
from datetime import datetime, timezone
from typing import Any, Iterator
from urllib.parse import quote

import requests

from .models import Paper
from .normalize import normalize_doi

FIELDS = "title,publication_year,publication_date,doi,type,authorships,abstract_inverted_index,primary_location,locations,open_access,biblio,ids,keywords,has_content,content_urls"


def _get(url: str, params: dict[str, Any] | None = None, headers: dict[str, str] | None = None) -> dict[str, Any]:
    last = None
    for attempt in range(4):
        try:
            response = requests.get(url, params=params, headers=headers, timeout=30)
            if response.status_code == 429 or response.status_code >= 500:
                retry_after = response.headers.get("Retry-After")
                try:
                    wait_seconds = min(float(retry_after), 30) if retry_after else min(2 ** attempt, 12)
                except ValueError:
                    wait_seconds = min(2 ** attempt, 12)
                last = RuntimeError(f"API returned HTTP {response.status_code}")
                time.sleep(wait_seconds)
                continue
            response.raise_for_status()
            return response.json()
        except OSError as exc:
            message = str(exc)
            if "certificate bundle" in message.lower() or "certifi" in message.lower():
                raise RuntimeError("TLS certificate bundle is missing or invalid. In the active project environment run: python -m pip install --force-reinstall --no-cache-dir certifi") from exc
            last = exc
            if attempt < 3:
                time.sleep(min(2 ** attempt, 12))
        except (requests.RequestException, ValueError) as exc:
            last = exc
            if attempt < 3:
                time.sleep(min(2 ** attempt, 12))
    raise RuntimeError(f"API request failed: {last}") from last


def reconstruct_abstract(index: dict[str, list[int]] | None) -> str | None:
    if not index:
        return None
    positions = [p for values in index.values() for p in values]
    if not positions:
        return None
    words = [None] * (max(positions) + 1)
    for word, indexes in index.items():
        for pos in indexes:
            words[pos] = word
    return " ".join(word for word in words if word) or None


def _openalex(raw: dict[str, Any]) -> Paper:
    loc = raw.get("primary_location") or {}
    locations = raw.get("locations") or []
    source = loc.get("source") or {}
    oa = raw.get("open_access") or {}
    biblio = raw.get("biblio") or {}
    doi = normalize_doi(raw.get("doi"))
    authors = [x.get("author", {}).get("display_name", "") for x in raw.get("authorships", [])]
    pages = "-".join(str(biblio.get(k)) for k in ("first_page", "last_page") if biblio.get(k)) or None
    oa_locations = [x for x in locations if x.get("is_oa") and x.get("pdf_url")]
    direct_pdf = loc.get("pdf_url") if loc.get("pdf_url") and loc.get("is_oa") else (oa_locations[0].get("pdf_url") if oa_locations else None)
    content_url = (raw.get("content_urls") or {}).get("pdf")
    is_open = bool(oa.get("is_oa") or direct_pdf or content_url)
    return Paper(title=raw.get("title") or "Untitled", year=raw.get("publication_year"), publication_date=raw.get("publication_date"), doi=doi, authors=[x for x in authors if x], abstract=reconstruct_abstract(raw.get("abstract_inverted_index")), journal=source.get("display_name"), publisher=source.get("host_organization_name"), volume=biblio.get("volume"), issue=biblio.get("issue"), pages=pages, openalex_id=raw.get("id"), publication_type=raw.get("type"), landing_page_url=loc.get("landing_page_url"), doi_url=f"https://doi.org/{doi}" if doi else None, oa_status="open" if is_open else "closed", oa_license=oa.get("oa_status"), pdf_url=direct_pdf, pdf_download_source="openalex_location" if direct_pdf else None, openalex_content_url=content_url, sources=["openalex"], raw={"openalex": raw})


def discover_openalex(queries: list[str], api_key: str | None, from_year: int, page_size: int = 100) -> Iterator[Paper]:
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    for query in queries:
        cursor = "*"
        while cursor:
            params = {"search": query, "filter": f"from_publication_date:{from_year}-01-01", "per-page": min(page_size, 100), "cursor": cursor, "select": FIELDS}
            data = _get("https://api.openalex.org/works", params, headers)
            for item in data.get("results", []):
                yield _openalex(item)
            cursor = (data.get("meta") or {}).get("next_cursor")
            if not data.get("results"):
                break


def discover_semantic_scholar(queries: list[str], api_key: str | None, page_size: int = 100) -> Iterator[Paper]:
    fields = "paperId,title,abstract,year,publicationDate,venue,publicationTypes,externalIds,openAccessPdf,authors"
    headers = {"x-api-key": api_key} if api_key else {}
    for query in queries:
        offset = 0
        while True:
            limit = min(page_size, 100)
            data = _get("https://api.semanticscholar.org/graph/v1/paper/search", {"query": query, "limit": limit, "offset": offset, "fields": fields}, headers)
            items = data.get("data", [])
            for item in items:
                doi = normalize_doi((item.get("externalIds") or {}).get("DOI"))
                oa = item.get("openAccessPdf") or {}
                sid = item.get("paperId")
                yield Paper(title=item.get("title") or "Untitled", year=item.get("year"), publication_date=item.get("publicationDate"), doi=doi, authors=[a.get("name", "") for a in item.get("authors", [])], abstract=item.get("abstract"), journal=item.get("venue"), semantic_scholar_id=sid, publication_type=", ".join(item.get("publicationTypes") or []) or None, landing_page_url=f"https://www.semanticscholar.org/paper/{sid}" if sid else None, doi_url=f"https://doi.org/{doi}" if doi else None, pdf_url=oa.get("url"), pdf_download_source="semantic_scholar" if oa.get("url") else None, oa_status="open" if oa.get("url") else None, sources=["semantic_scholar"], raw={"semantic_scholar": item})
            offset += len(items)
            if len(items) < limit or offset >= data.get("total", 0) or offset >= 1000:
                break


def enrich_crossref(paper: Paper, mailto: str | None = None) -> Paper:
    if not paper.doi:
        return paper
    data = _get(f"https://api.crossref.org/works/{quote(paper.doi, safe='')}", {"mailto": mailto} if mailto else None)
    item = data.get("message", {})
    paper.crossref = item
    if item:
        paper.sources = sorted(set(paper.sources + ["crossref"]))
        paper.publication_type = paper.publication_type or item.get("type")
        paper.publisher = paper.publisher or item.get("publisher")
        paper.volume = paper.volume or item.get("volume")
        paper.issue = paper.issue or item.get("issue")
        paper.pages = paper.pages or item.get("page") or item.get("article-number")
        paper.issn = item.get("ISSN", [])
        paper.journal = paper.journal or (item.get("container-title") or [None])[0]
        paper.landing_page_url = paper.landing_page_url or item.get("URL")
        if not paper.publication_date:
            date = item.get("published-print") or item.get("published-online") or item.get("created") or {}
            parts = date.get("date-parts", [[]])[0]
            paper.publication_date = "-".join(str(x).zfill(2) for x in parts) or None
            if not paper.year and parts:
                paper.year = parts[0]
        if not paper.authors:
            paper.authors = [" ".join(x for x in (a.get("given"), a.get("family")) if x) for a in item.get("author", [])]
    return paper


def stamp(paper: Paper) -> Paper:
    paper.collected_at = paper.collected_at or datetime.now(timezone.utc).isoformat()
    return paper
