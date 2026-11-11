import argparse
import logging
import os
import tomllib
from pathlib import Path

from dotenv import load_dotenv

from .browser_access import institutional_download
from .classify import classify
from .database import Database
from .downloads import download_pdf, valid_pdf
from .exports import export_all, summary
from .models import Paper
from .sources import discover_openalex, discover_semantic_scholar, enrich_crossref, stamp


def load_config(path: str):
    with open(path, "rb") as f:
        return tomllib.load(f)


def free_pdf_count(papers: list[Paper]) -> int:
    return sum(
        p.classification_status == "ACCEPTED"
        and p.download_status == "DOWNLOADED"
        and p.local_pdf_path is not None
        and valid_pdf(p.local_pdf_path)
        for p in papers
    )


def save_report(db: Database, output: Path, target: int) -> str:
    items = db.all()
    export_all(items, output)
    for paper in items:
        db.upsert(paper)
    report = summary(items, target)
    reports = output / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    (reports / "collection_summary.txt").write_text(report, encoding="utf-8")
    print(report)
    return report


def checkpoint_exports(db: Database, output: Path) -> None:
    items = db.all()
    export_all(items, output)
    for paper in items:
        db.upsert(paper)


def main():
    parser = argparse.ArgumentParser(prog="paper-collector")
    parser.add_argument("mode", choices=["discover", "filter", "download", "resume", "full", "test-openalex", "test-doi", "institutional-check", "institutional-download"])
    parser.add_argument("--config", default="config.toml")
    parser.add_argument("--limit", type=int, help="Target number of validated free PDFs for this run (cannot exceed the configured target).")
    parser.add_argument("--doi")
    args = parser.parse_args()

    load_dotenv()
    cfg = load_config(args.config)
    collection, discovery = cfg["collection"], cfg["discovery"]
    target = collection["free_pdf_target"]
    if args.limit is not None:
        if args.limit < 1:
            parser.error("--limit must be at least 1")
        target = min(args.limit, target)
    output = Path(collection["output_dir"])
    output.mkdir(parents=True, exist_ok=True)
    Path("logs").mkdir(exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", handlers=[logging.FileHandler("logs/collector.log", encoding="utf-8"), logging.StreamHandler()])
    db = Database(collection["database"])
    api_key = os.getenv("OPENALEX_API_KEY")
    content_limit = collection.get("openalex_content_downloads_per_day", 80)

    def attempt_openalex_cached_pdf() -> bool:
        return db.reserve_openalex_content_attempt(content_limit)

    def process_record(paper: Paper, download: bool) -> Paper:
        paper = stamp(paper)
        classify(paper, collection["min_year"])
        if paper.classification_status != "REJECTED" and paper.doi:
            try:
                paper = enrich_crossref(paper, os.getenv("CROSSREF_MAILTO"))
            except Exception as exc:
                paper.access_error = f"Crossref metadata verification failed: {exc}"
        classify(paper, collection["min_year"])
        paper_id = db.upsert(paper)
        canonical = db.get(paper_id) or paper
        classify(canonical, collection["min_year"])
        if download and canonical.classification_status == "ACCEPTED":
            if canonical.download_status not in {"DOWNLOADED", "DOWNLOADED_SUBSCRIPTION"}:
                canonical = download_pdf(canonical, output, api_key, attempt_openalex_cached_pdf)
        db.upsert(canonical)
        return canonical

    try:
        if args.mode == "test-openalex":
            sample = next(discover_openalex(["perovskite"], api_key, collection["min_year"], 1), None)
            if not sample:
                raise RuntimeError("OpenAlex returned no test record.")
            print(f"OpenAlex request succeeded. Sample title: {sample.title}")
            return

        if args.mode in {"test-doi", "institutional-check"}:
            doi = (args.doi or "10.1016/j.jallcom.2023.170773").lower()
            paper = next((p for p in db.all() if p.doi == doi), None)
            if not paper:
                paper = Paper(title="A halide perovskite thin film diode with modulated depletion layers for artificial synapse", doi=doi, sources=["manual_test"])
            if args.mode == "test-doi":
                paper = enrich_crossref(paper, os.getenv("CROSSREF_MAILTO"))
                classify(paper, collection["min_year"])
                db.upsert(paper)
                print(f"Resolved DOI: {paper.doi}\nTitle: {paper.title}\nYear: {paper.year}\nJournal: {paper.journal}\nPublisher: {paper.publisher}\nLanding page: {paper.landing_page_url}\nClassification: {paper.classification_status}")
            else:
                paper = institutional_download(paper, output, "browser_profile")
                db.upsert(paper)
                print(f"Institutional PDF status: {paper.download_status}")
                if paper.access_error:
                    print(paper.access_error)
            save_report(db, output, target)
            return

        if args.mode == "filter":
            for paper in db.all():
                classify(paper, collection["min_year"])
                db.upsert(paper)
            save_report(db, output, target)
            return

        if args.mode in {"download", "full", "resume"}:
            checkpoint_exports(db, output)

        if args.mode == "download":
            for paper in db.all(("ACCEPTED",)):
                if free_pdf_count(db.all()) >= target:
                    break
                process_record(paper, download=True)
            save_report(db, output, target)
            return

        download_enabled = args.mode in {"full", "resume"}
        if args.mode == "resume":
            # Retry saved candidates first; rediscovery below deduplicates against SQLite and continues.
            for paper in db.all(("ACCEPTED",)):
                if free_pdf_count(db.all()) >= target:
                    break
                if paper.download_status not in {"DOWNLOADED", "DOWNLOADED_SUBSCRIPTION"}:
                    process_record(paper, download=True)

        if args.mode in {"discover", "full", "resume"}:
            sources = [
                discover_openalex(discovery["queries"], api_key, collection["min_year"], discovery["page_size"]),
                discover_semantic_scholar(discovery["queries"], os.getenv("SEMANTIC_SCHOLAR_API_KEY"), discovery["semantic_scholar_page_size"]),
            ]
            seen = 0
            reached_target = free_pdf_count(db.all()) >= target
            for source_records in sources:
                if reached_target:
                    break
                try:
                    for record in source_records:
                        seen += 1
                        process_record(record, download=download_enabled)
                        if seen % 25 == 0:
                            current = free_pdf_count(db.all())
                            logging.info("Processed %d source records; %d/%d free PDFs saved.", seen, current, target)
                            checkpoint_exports(db, output)
                        if download_enabled and free_pdf_count(db.all()) >= target:
                            reached_target = True
                            logging.info("Reached the target of %d validated free PDFs.", target)
                            break
                except Exception as exc:
                    logging.exception("Discovery source failed after %d records; continuing with the next source: %s", seen, exc)
            logging.info("Discovery ended after %d source records.", seen)

        if args.mode == "institutional-download":
            candidates = db.all(("ACCEPTED",))
            if args.doi:
                candidates = [p for p in candidates if p.doi == args.doi.strip().lower()]
            for paper in candidates:
                if paper.download_status in {"DOWNLOADED", "DOWNLOADED_SUBSCRIPTION"}:
                    continue
                print(f"Institutional access: {paper.title} ({paper.doi or 'no DOI'})")
                paper = institutional_download(paper, output, "browser_profile")
                db.upsert(paper)
                print(f"Status: {paper.download_status}")

        save_report(db, output, target)
    finally:
        db.close()


if __name__ == "__main__":
    main()
