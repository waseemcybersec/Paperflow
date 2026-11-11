import os
import tempfile
from pathlib import Path
from typing import Callable

import pymupdf
import requests

from .models import Paper
from .normalize import safe_folder_name, safe_pdf_path

USER_AGENT = "ResearchPaperCollector/0.2 (open-access research archiver)"
MAX_PDF_BYTES = 150 * 1024 * 1024


def valid_pdf(path: str | Path) -> bool:
    path = Path(path)
    try:
        if path.stat().st_size < 1000 or path.read_bytes()[:5] != b"%PDF-":
            return False
        with pymupdf.open(path) as doc:
            return doc.page_count > 0
    except (OSError, RuntimeError, ValueError):
        return False


def _download_one(url: str, target: Path, source: str, api_key: str | None) -> None:
    headers = {"User-Agent": USER_AGENT}
    if source == "openalex_content":
        if not api_key:
            raise ValueError("OpenAlex cached PDF requires OPENALEX_API_KEY.")
        headers["Authorization"] = f"Bearer {api_key}"
    temp_name = None
    try:
        with requests.get(url, timeout=(15, 45), stream=True, headers=headers) as response:
            response.raise_for_status()
            content_type = response.headers.get("Content-Type", "").lower()
            if "html" in content_type:
                raise ValueError("PDF URL returned HTML rather than a PDF.")
            total = 0
            with tempfile.NamedTemporaryFile(dir=target.parent, suffix=".part", delete=False) as f:
                temp_name = f.name
                for chunk in response.iter_content(1024 * 128):
                    if not chunk:
                        continue
                    total += len(chunk)
                    if total > MAX_PDF_BYTES:
                        raise ValueError("PDF exceeds the 150 MB safety limit.")
                    f.write(chunk)
        if not valid_pdf(temp_name):
            raise ValueError("Downloaded file failed PDF signature/parser validation.")
        os.replace(temp_name, target)
    finally:
        if temp_name and Path(temp_name).exists():
            Path(temp_name).unlink()


def download_pdf(
    paper: Paper,
    output_dir: str | Path,
    api_key: str | None = None,
    allow_openalex_content: Callable[[], bool] | None = None,
) -> Paper:
    """Download only OA-marked copies; use OpenAlex's cached OA PDF as a fallback."""
    if paper.classification_status != "ACCEPTED":
        paper.download_status = "NOT_ATTEMPTED"
        return paper
    if paper.oa_status != "open":
        paper.download_status = "ACCESS_REQUIRED" if paper.oa_status == "closed" else "UNAVAILABLE"
        return paper
    if paper.year is None:
        paper.download_status, paper.access_error = "DOWNLOAD_FAILED", "Publication year is required to organize the PDF."
        return paper

    year_dir = Path(output_dir) / str(paper.year)
    year_dir.mkdir(parents=True, exist_ok=True)
    if paper.local_pdf_path and valid_pdf(paper.local_pdf_path):
        existing = Path(paper.local_pdf_path)
        target = year_dir / f"{safe_folder_name(paper.title, 150)}.pdf"
        if existing.resolve() != target.resolve():
            if target.exists():
                target = safe_pdf_path(year_dir, paper.title, paper.doi)
            os.replace(existing, target)
        paper.download_status, paper.local_pdf_path = "DOWNLOADED", str(target)
        return paper

    target = safe_pdf_path(year_dir, paper.title, paper.doi)
    candidates = []
    if paper.pdf_url:
        candidates.append((paper.pdf_url, paper.pdf_download_source or "oa_location"))
    if paper.openalex_content_url:
        candidates.append((paper.openalex_content_url, "openalex_content"))
    if not candidates:
        paper.download_status = "UNAVAILABLE"
        paper.access_error = "No open-access PDF URL is known."
        return paper

    errors = []
    for url, source in candidates:
        if source == "openalex_content":
            if allow_openalex_content is None or not allow_openalex_content():
                errors.append("OpenAlex cached-PDF daily safety allowance reached; retry after its UTC reset.")
                continue
        try:
            _download_one(url, target, source, api_key)
            paper.download_status = "DOWNLOADED"
            paper.local_pdf_path = str(target)
            paper.pdf_download_source = source
            paper.access_error = None
            return paper
        except Exception as exc:
            errors.append(f"{source}: {exc}")
    paper.download_status = "DOWNLOAD_FAILED"
    paper.access_error = " | ".join(errors)
    return paper
