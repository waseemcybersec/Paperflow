import csv
import filecmp
import json
import logging
import os
import tempfile
from pathlib import Path

from .downloads import valid_pdf
from .models import Paper
from .normalize import safe_folder_name, safe_pdf_path

CSV_FIELDS = [
    "title", "year", "publication_date", "doi", "authors", "journal", "publisher",
    "volume", "issue", "pages", "issn", "publication_type", "keywords", "abstract",
    "paper_link", "doi_url", "openalex_id", "semantic_scholar_id", "oa_status",
    "oa_license", "pdf_url", "openalex_content_url", "pdf_download_source",
    "download_status", "local_pdf_path", "sources", "classification_status",
    "classification_reason", "access_error", "citation", "collected_at",
]
OLD_METADATA_FILES = {
    "all_papers.json", "all_papers.csv", "all_papers.xlsx", "all_papers.bib",
    "accepted_papers.csv", "rejected_papers.csv", "manual_review.csv",
}


def _row(paper: Paper) -> dict:
    row = paper.to_dict()
    row["paper_link"] = paper.landing_page_url or paper.doi_url
    author_text = ", ".join(paper.authors)
    row["citation"] = f"{author_text} ({paper.year or ''}). {paper.title}. {paper.journal or ''}. https://doi.org/{paper.doi}" if paper.doi else f"{author_text} ({paper.year or ''}). {paper.title}. {paper.journal or ''}."
    for key, value in list(row.items()):
        if isinstance(value, (list, dict)):
            row[key] = json.dumps(value, ensure_ascii=False) if isinstance(value, dict) else "; ".join(str(x) for x in value)
    return {key: row.get(key, "") for key in CSV_FIELDS}


def _write_csv(path: Path, papers: list[Paper]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_name = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", newline="", encoding="utf-8-sig", dir=path.parent, suffix=".tmp", delete=False) as f:
            temp_name = f.name
            writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
            writer.writeheader()
            for paper in papers:
                writer.writerow(_row(paper))
        os.replace(temp_name, path)
    except PermissionError:
        logging.warning("Could not replace %s; it may be open in another application. Keeping its previous version.", path)
    finally:
        if temp_name and Path(temp_name).exists():
            Path(temp_name).unlink()


def _flatten_old_paper_folder(paper: Paper, output: Path) -> bool:
    if not paper.year:
        return False
    year_dir = output / str(paper.year)
    legacy_dir = year_dir / safe_folder_name(paper.title)
    legacy_pdf = legacy_dir / "paper.pdf"
    source = Path(paper.local_pdf_path) if paper.local_pdf_path else legacy_pdf
    if not source.is_file() and legacy_pdf.is_file():
        source = legacy_pdf
    changed = False
    if source.is_file() and valid_pdf(source) and paper.classification_status == "ACCEPTED":
        year_dir.mkdir(parents=True, exist_ok=True)
        title_target = year_dir / f"{safe_folder_name(paper.title, 150)}.pdf"
        if source.resolve() != title_target.resolve():
            target = title_target
            if target.exists():
                if valid_pdf(target) and filecmp.cmp(source, target, shallow=False):
                    source.unlink()
                else:
                    target = safe_pdf_path(year_dir, paper.title, paper.doi)
                    os.replace(source, target)
            else:
                os.replace(source, target)
            paper.local_pdf_path = str(target)
            paper.download_status = "DOWNLOADED"
            changed = True
        elif paper.local_pdf_path != str(title_target):
            paper.local_pdf_path = str(title_target)
            changed = True
    elif source.is_file() and source.parent == legacy_dir and paper.classification_status != "ACCEPTED":
        source.unlink()
        paper.local_pdf_path = None
        paper.download_status = "NOT_ATTEMPTED"
        changed = True
    if legacy_dir.is_dir():
        for name in ("metadata.json", "citation.bib"):
            (legacy_dir / name).unlink(missing_ok=True)
        for part in legacy_dir.glob("*.part"):
            part.unlink(missing_ok=True)
        try:
            legacy_dir.rmdir()
        except OSError:
            pass
    return changed


def organize_output(papers: list[Paper], output: str | Path) -> list[Paper]:
    """Flatten legacy per-paper directories and keep each year directory to PDFs + one CSV."""
    output = Path(output)
    for paper in papers:
        _flatten_old_paper_folder(paper, output)
        if paper.download_status in {"DOWNLOADED", "DOWNLOADED_SUBSCRIPTION"} and (not paper.local_pdf_path or not valid_pdf(paper.local_pdf_path)):
            paper.download_status = "NOT_ATTEMPTED"
            paper.local_pdf_path = None
            paper.access_error = "Saved PDF path was missing or invalid; the record has been queued for a fresh OA download attempt."
        if paper.classification_status != "ACCEPTED" and paper.download_status == "DOWNLOADED" and paper.local_pdf_path:
            old_file = Path(paper.local_pdf_path)
            try:
                old_file.resolve().relative_to(output.resolve())
            except ValueError:
                continue
            if old_file.is_file():
                old_file.unlink()
            paper.local_pdf_path = None
            paper.download_status = "NOT_ATTEMPTED"
    for year_dir in output.iterdir() if output.exists() else []:
        if not year_dir.is_dir() or not year_dir.name.isdigit():
            continue
        year_papers = [
            p for p in papers
            if p.classification_status == "ACCEPTED"
            and p.download_status in {"DOWNLOADED", "DOWNLOADED_SUBSCRIPTION"}
            and p.year == int(year_dir.name)
            and p.local_pdf_path and valid_pdf(p.local_pdf_path)
        ]
        _write_csv(year_dir / "metadata.csv", year_papers)
    metadata_dir = output / "metadata"
    if metadata_dir.is_dir():
        for child in metadata_dir.iterdir():
            if child.is_file() and child.name in OLD_METADATA_FILES:
                child.unlink()
        try:
            metadata_dir.rmdir()
        except OSError:
            pass
    return papers


def export_all(papers: list[Paper], output: str | Path):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    organize_output(papers, output)
    accepted = [p for p in papers if p.classification_status == "ACCEPTED"]
    downloaded = [p for p in accepted if p.download_status == "DOWNLOADED" and p.local_pdf_path and valid_pdf(p.local_pdf_path)]
    subscription = [
        p for p in accepted
        if p.download_status not in {"DOWNLOADED", "DOWNLOADED_SUBSCRIPTION"}
        and (p.oa_status == "closed" or p.download_status == "ACCESS_REQUIRED")
    ]
    without_pdf = [
        p for p in accepted
        if p.download_status not in {"DOWNLOADED", "DOWNLOADED_SUBSCRIPTION"}
        and p not in subscription
        and (p.download_status in {"DOWNLOAD_FAILED", "UNAVAILABLE", "ACCESS_REQUIRED"} or not (p.pdf_url or p.openalex_content_url))
    ]
    manual = [p for p in papers if p.classification_status == "NEEDS_MANUAL_REVIEW"]
    _write_csv(output / "papers_without_pdf.csv", without_pdf)
    _write_csv(output / "subscription_candidates.csv", subscription)
    _write_csv(output / "manual_review.csv", manual)
    years = sorted({p.year for p in downloaded if p.year})
    for year in years:
        year_papers = [p for p in downloaded if p.year == year]
        year_dir = output / str(year)
        year_dir.mkdir(parents=True, exist_ok=True)
        _write_csv(year_dir / "metadata.csv", year_papers)
    return papers


def summary(papers: list[Paper], free_target: int = 250) -> str:
    counts = {status: sum(p.classification_status == status for p in papers) for status in ("ACCEPTED", "REJECTED", "NEEDS_MANUAL_REVIEW")}
    free_downloaded = sum(p.classification_status == "ACCEPTED" and p.download_status == "DOWNLOADED" and p.local_pdf_path and valid_pdf(p.local_pdf_path) for p in papers)
    pending_paid = sum(
        p.classification_status == "ACCEPTED"
        and p.download_status not in {"DOWNLOADED", "DOWNLOADED_SUBSCRIPTION"}
        and (p.oa_status == "closed" or p.download_status == "ACCESS_REQUIRED")
        for p in papers
    )
    without_pdf = sum(
        p.classification_status == "ACCEPTED"
        and p.download_status not in {"DOWNLOADED", "DOWNLOADED_SUBSCRIPTION"}
        and not (p.oa_status == "closed" or p.download_status == "ACCESS_REQUIRED")
        and (p.download_status in {"DOWNLOAD_FAILED", "UNAVAILABLE", "ACCESS_REQUIRED"} or not (p.pdf_url or p.openalex_content_url))
        for p in papers
    )
    return (
        f"Records discovered: {len(papers)}\n"
        f"Accepted papers: {counts['ACCEPTED']}\n"
        f"Rejected: {counts['REJECTED']}\n"
        f"Needs manual review: {counts['NEEDS_MANUAL_REVIEW']}\n"
        f"Validated free PDFs saved: {free_downloaded}/{free_target}\n"
        f"Subscription candidates: {pending_paid}\n"
        f"Accepted papers without a downloadable PDF: {without_pdf}\n"
    )
