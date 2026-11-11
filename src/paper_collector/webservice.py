"""Per-user collection settings and background job runner for the local UI."""

from __future__ import annotations

import json
import logging
import os
import re
import threading
from datetime import date
from pathlib import Path

from .database import Database
from .downloads import download_pdf, valid_pdf
from .exports import export_all, summary
from .models import Paper
from .normalize import normalize_doi, normalize_title
from .sources import discover_openalex, discover_semantic_scholar, enrich_crossref, stamp

PAPER_TYPES = {"research", "review", "preprint", "conference", "book", "other"}
APP_ROOT = Path(__file__).resolve().parents[2]
TYPE_ALIASES = {
    "article": "research", "journal-article": "research", "journalarticle": "research",
    "research-article": "research", "original-research": "research", "study": "research",
    "review": "review", "review-article": "review", "meta-analysis": "review",
    "preprint": "preprint", "posted-content": "preprint", "posted-content-journal": "preprint",
    "conference": "conference", "conference-paper": "conference", "conferencepaper": "conference",
    "proceedings-article": "conference", "proceedings-paper": "conference",
    "book": "book", "book-chapter": "book", "booksection": "book", "book-section": "book", "chapter": "book",
}
REVIEW_CUES = re.compile(r"\b(review|systematic review|literature review|survey|bibliometric analysis|meta[- ]analysis|roadmap|perspective|overview|progress report)\b", re.I)


def _parse_options(options: dict) -> dict:
    queries = [part.strip() for part in str(options.get("queries", "")).split(",") if part.strip()]
    if not queries:
        raise ValueError("Enter at least one keyword or phrase, separated by commas.")
    if len(queries) > 30:
        raise ValueError("Please use 30 or fewer comma-separated search phrases.")
    years = sorted({int(y) for y in options.get("years", [])})
    current_year = date.today().year
    if not years or any(y < 1900 or y > current_year for y in years):
        raise ValueError(f"Choose one or more valid publication years (through {current_year}).")
    types = sorted(set(options.get("paper_types", [])) & PAPER_TYPES)
    if not types:
        raise ValueError("Select at least one paper type.")
    sources = set(options.get("sources", ["openalex", "semantic_scholar"]))
    if not sources or not sources.issubset({"openalex", "semantic_scholar"}):
        raise ValueError("Select OpenAlex, Semantic Scholar, or both.")
    key = str(options.get("openalex_key", "")).strip()
    if "openalex" in sources and not key:
        raise ValueError("Enter your own OpenAlex API key, or turn off OpenAlex.")
    workspace_text = str(options.get("workspace", "")).strip()
    if not workspace_text:
        raise ValueError("Choose where this collection should be saved.")
    base = APP_ROOT if workspace_text.casefold() in {"paperflow", ".", "./"} and APP_ROOT.name.casefold() == "paperflow" else APP_ROOT / "Paperflow" if workspace_text.casefold() in {"paperflow", ".", "./"} else Path(workspace_text).expanduser()
    if not base.is_absolute():
        base = APP_ROOT / base
    base = base.resolve()
    workspace_display = "Paperflow" if workspace_text.casefold() in {"paperflow", ".", "./"} else workspace_text
    target = int(options.get("target", 400))
    if not 1 <= target <= 5000:
        raise ValueError("PDF target must be between 1 and 5,000.")
    return {
        "queries": queries,
        "years": years,
        "paper_types": types,
        "sources": sources,
        "openalex_key": key,
        "workspace": base,
        "workspace_display": workspace_display,
        "target": target,
    }


def _category(paper: Paper) -> str | None:
    value = (paper.publication_type or "").casefold().replace(" ", "-").replace("_", "-")
    value = value.replace("journalarticle", "journal-article").replace("conferencepaper", "conference-paper")
    # Semantic Scholar can report multiple types; use the first recognized type.
    for item in value.split(","):
        item = item.strip()
        if item in TYPE_ALIASES:
            return TYPE_ALIASES[item]
    return "other" if value else None


def classify_for_preferences(paper: Paper, years: set[int], types: set[str]) -> Paper:
    if paper.year not in years:
        paper.classification_status = "REJECTED"
        paper.classification_reason = "Publication year is outside the selected years."
        return paper
    category = _category(paper)
    if category is None:
        paper.classification_status = "NEEDS_MANUAL_REVIEW"
        paper.classification_reason = "The source did not provide a recognizable publication type."
    elif category not in types:
        paper.classification_status = "REJECTED"
        paper.classification_reason = f"Publication type '{category}' was not selected."
    elif category == "research" and REVIEW_CUES.search(f"{paper.title or ''} {paper.abstract or ''}"):
        paper.classification_status = "REJECTED"
        paper.classification_reason = "The selected research-article type contains a review publication cue."
    elif category == "research" and not paper.abstract:
        paper.classification_status = "NEEDS_MANUAL_REVIEW"
        paper.classification_reason = "Abstract is missing, so research content cannot be checked."
    else:
        paper.classification_status = "ACCEPTED"
        paper.classification_reason = f"Publication year and selected '{category}' type match."
    return paper


def existing_version(db: Database, paper: Paper) -> Paper | None:
    """Find a prior duplicate before upsert can alter its classification or saved path."""
    doi = normalize_doi(paper.doi)
    if doi:
        row = db.conn.execute("SELECT payload FROM papers WHERE doi=? LIMIT 1", (doi,)).fetchone()
        if row:
            return Paper(**json.loads(row[0]))
        rows = db.conn.execute("SELECT payload FROM papers WHERE title_key=? AND doi IS NULL", (normalize_title(paper.title),)).fetchall()
    else:
        rows = db.conn.execute("SELECT payload FROM papers WHERE title_key=?", (normalize_title(paper.title),)).fetchall()
    incoming_authors = {"".join(c for c in a.casefold() if c.isalnum()) for a in paper.authors if a}
    matches = []
    for row in rows:
        prior = Paper(**json.loads(row[0]))
        if paper.year and prior.year and paper.year != prior.year:
            continue
        old_authors = {"".join(c for c in a.casefold() if c.isalnum()) for a in prior.authors if a}
        shared = bool(incoming_authors.intersection(old_authors))
        if incoming_authors and old_authors and not shared:
            continue
        if (paper.year and paper.year == prior.year) or shared:
            matches.append(prior)
    return matches[0] if len(matches) == 1 else None


class CollectionJob:
    def __init__(self, raw_options: dict):
        self.options = _parse_options(raw_options)
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self.running = False
        self.phase = "queued"
        self.message = "Preparing your local collection."
        self.error = None
        self.processed = 0
        self.saved = 0
        self.target = self.options["target"]
        self.workspace = self.options["workspace_display"]
        self.output = self.options["workspace"] / "Research_Papers"
        self.output_display = f"{self.workspace}\\Research_Papers"
        self.database_path = self.options["workspace"] / "data" / "collection.sqlite3"
        self.report = ""

    def stop(self):
        self._stop.set()
        with self._lock:
            self.message = "Stop requested. Saving the current checkpoint…"

    def snapshot(self):
        with self._lock:
            return {
                "running": self.running,
                "phase": self.phase,
                "message": self.message,
                "error": self.error,
                "counts": {"processed": self.processed, "saved": self.saved, "target": self.target},
                "workspace": self.workspace,
                "output": self.output_display,
                "output_path": str(self.output),
                "report": self.report,
            }

    def _status(self, phase: str, message: str):
        with self._lock:
            self.phase, self.message = phase, message

    def run(self):
        self.running = True
        db = None
        file_handler = None
        api_key = self.options["openalex_key"]
        try:
            self.output.mkdir(parents=True, exist_ok=True)
            self.database_path.parent.mkdir(parents=True, exist_ok=True)
            log_dir = self.options["workspace"] / "logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            file_handler = logging.FileHandler(log_dir / "collector.log", encoding="utf-8")
            file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
            logging.getLogger().addHandler(file_handler)
            logging.getLogger().setLevel(logging.INFO)
            db = Database(self.database_path)
            years = set(self.options["years"])
            types = set(self.options["paper_types"])
            for year in years:
                (self.output / str(year)).mkdir(parents=True, exist_ok=True)
            saved = sum(
                p.classification_status == "ACCEPTED"
                and p.download_status == "DOWNLOADED"
                and p.local_pdf_path
                and valid_pdf(p.local_pdf_path)
                for p in db.all()
            )
            self.saved = saved
            export_all(db.all(), self.output)

            def openalex_content_allowed():
                # Uses the same conservative daily cap as the CLI workflow.
                return db.reserve_openalex_content_attempt(80)

            def process(paper: Paper):
                nonlocal saved
                stamp(paper)
                if paper.year not in years:
                    return
                if paper.doi:
                    try:
                        enrich_crossref(paper)
                    except Exception as exc:
                        paper.access_error = f"Crossref metadata lookup failed: {exc}"
                prior = existing_version(db, paper)
                classify_for_preferences(paper, years, types)
                row_id = db.upsert(paper)
                canonical = db.get(row_id) or paper
                prior_pdf_exists = bool(
                    prior
                    and prior.download_status in {"DOWNLOADED", "DOWNLOADED_SUBSCRIPTION"}
                    and prior.local_pdf_path
                    and valid_pdf(prior.local_pdf_path)
                )
                if prior_pdf_exists:
                    canonical.classification_status = prior.classification_status
                    canonical.classification_reason = prior.classification_reason
                    canonical.download_status = prior.download_status
                    canonical.local_pdf_path = prior.local_pdf_path
                else:
                    classify_for_preferences(canonical, years, types)
                if canonical.classification_status == "ACCEPTED":
                    was_downloaded = canonical.download_status == "DOWNLOADED" and canonical.local_pdf_path and valid_pdf(canonical.local_pdf_path)
                    if not was_downloaded and saved < self.target:
                        canonical = download_pdf(canonical, self.output, api_key, openalex_content_allowed)
                        if canonical.download_status == "DOWNLOADED" and canonical.local_pdf_path and valid_pdf(canonical.local_pdf_path):
                            saved += 1
                db.upsert(canonical)
                self.saved = saved

            source_iters = []
            if "openalex" in self.options["sources"]:
                source_iters.append(("OpenAlex", discover_openalex(self.options["queries"], api_key, min(years), 100)))
            if "semantic_scholar" in self.options["sources"]:
                source_iters.append(("Semantic Scholar", discover_semantic_scholar(self.options["queries"], None, 100)))

            for source_name, source_records in source_iters:
                if self._stop.is_set() or saved >= self.target:
                    break
                self._status("discovering", f"Searching {source_name} and checking for duplicates…")
                try:
                    for record in source_records:
                        if self._stop.is_set() or saved >= self.target:
                            break
                        self.processed += 1
                        if record.year in years:
                            process(record)
                        if self.processed % 10 == 0:
                            export_all(db.all(), self.output)
                            self._status("downloading", f"Checked {self.processed:,} records · {saved:,} of {self.target:,} PDFs saved")
                except Exception as exc:
                    logging.exception("%s search failed: %s", source_name, exc)
                    self.message = f"{source_name} had a connection issue; continuing with the next source."

            self._status("exporting", "Updating your year folders, metadata, and checkpoint…")
            papers = db.all()
            export_all(papers, self.output)
            self.report = summary(papers, self.target)
            (self.output / "reports").mkdir(parents=True, exist_ok=True)
            (self.output / "reports" / "collection_summary.txt").write_text(self.report, encoding="utf-8")
            if self._stop.is_set():
                self._status("stopped", f"Stopped safely · {saved:,} of {self.target:,} PDFs saved")
            elif saved >= self.target:
                self._status("complete", f"Target reached · {saved:,} validated PDFs saved")
            else:
                self._status("complete", f"Search finished · {saved:,} of {self.target:,} PDFs saved; available sources may be exhausted")
        except Exception as exc:
            logging.exception("Collection failed: %s", exc)
            self.error = str(exc)
            self._status("error", "The run stopped with an error. Your database checkpoint is preserved.")
        finally:
            self.options["openalex_key"] = ""
            api_key = ""
            if db:
                try:
                    db.close()
                except Exception:
                    pass
            if file_handler:
                logging.getLogger().removeHandler(file_handler)
                file_handler.close()
            self.running = False

