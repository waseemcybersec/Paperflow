import json
import re
import sqlite3
from pathlib import Path
from datetime import datetime, timezone
from .models import Paper
from .normalize import normalize_title


class Database:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("CREATE TABLE IF NOT EXISTS papers (id INTEGER PRIMARY KEY, doi TEXT UNIQUE, title_key TEXT NOT NULL, title TEXT NOT NULL, year INTEGER, status TEXT NOT NULL, payload TEXT NOT NULL)")
        self.conn.execute("CREATE INDEX IF NOT EXISTS papers_title_key ON papers(title_key)")
        self.conn.execute("CREATE TABLE IF NOT EXISTS daily_usage (utc_day TEXT PRIMARY KEY, openalex_content_attempts INTEGER NOT NULL DEFAULT 0)")
        self.conn.commit()

    def upsert(self, paper: Paper) -> int:
        from .normalize import normalize_doi
        paper.doi = normalize_doi(paper.doi)
        key = normalize_title(paper.title)
        existing = None
        if paper.doi:
            existing = self.conn.execute("SELECT id, payload FROM papers WHERE doi=? LIMIT 1", (paper.doi,)).fetchone()
        candidates = []
        if not existing:
            if paper.doi:
                # A DOI record may enrich a title-only record, but never merge two distinct DOIs.
                candidates = self.conn.execute("SELECT id, payload FROM papers WHERE title_key=? AND doi IS NULL", (key,)).fetchall()
            else:
                candidates = self.conn.execute("SELECT id, payload FROM papers WHERE title_key=?", (key,)).fetchall()
            incoming_authors = {re.sub(r"[^a-z0-9]", "", a.casefold()) for a in paper.authors if a}
            compatible = []
            for candidate in candidates:
                prior = json.loads(candidate["payload"])
                old_year = prior.get("year")
                if paper.year and old_year and paper.year != old_year:
                    continue
                old_authors = {re.sub(r"[^a-z0-9]", "", a.casefold()) for a in prior.get("authors", []) if a}
                if incoming_authors and old_authors and not incoming_authors.intersection(old_authors):
                    continue
                if (paper.year and old_year == paper.year) or incoming_authors.intersection(old_authors):
                    compatible.append(candidate)
            # If a DOI-less record could match multiple same-title publications, leave it separate for review.
            if len(compatible) == 1:
                existing = compatible[0]
        if existing:
            prior = json.loads(existing["payload"])
            for name, value in paper.to_dict().items():
                if name in {"raw", "crossref"} and value:
                    prior[name] = {**(prior.get(name) or {}), **value}
                elif name in {"sources", "authors", "keywords", "issn"}:
                    prior[name] = list(dict.fromkeys((prior.get(name) or []) + (value or [])))
                elif name == "download_status" and prior.get(name) in {"DOWNLOADED", "DOWNLOADED_SUBSCRIPTION"} and value not in {"DOWNLOADED", "DOWNLOADED_SUBSCRIPTION"}:
                    old_path = prior.get("local_pdf_path")
                    if old_path and Path(old_path).is_file():
                        continue
                    prior[name] = value
                elif name == "access_error" and value is None and paper.download_status in {"DOWNLOADED", "DOWNLOADED_SUBSCRIPTION"}:
                    prior[name] = None
                elif name == "local_pdf_path" and value in (None, "") and prior.get(name):
                    if Path(prior[name]).is_file():
                        continue
                    prior[name] = None
                elif value not in (None, "", [], {}):
                    prior[name] = value
            paper = Paper(**prior)
            self.conn.execute("UPDATE papers SET doi=?, title_key=?, title=?, year=?, status=?, payload=? WHERE id=?", (paper.doi, key, paper.title, paper.year, paper.classification_status, json.dumps(paper.to_dict(), ensure_ascii=False), existing["id"]))
            self.conn.commit()
            return existing["id"]
        cur = self.conn.execute("INSERT INTO papers(doi,title_key,title,year,status,payload) VALUES(?,?,?,?,?,?)", (paper.doi, key, paper.title, paper.year, paper.classification_status, json.dumps(paper.to_dict(), ensure_ascii=False)))
        self.conn.commit()
        return cur.lastrowid

    def all(self, statuses: tuple[str, ...] | None = None) -> list[Paper]:
        if statuses:
            q = ",".join("?" for _ in statuses)
            rows = self.conn.execute(f"SELECT payload FROM papers WHERE status IN ({q}) ORDER BY year,title", statuses)
        else:
            rows = self.conn.execute("SELECT payload FROM papers ORDER BY year,title")
        return [Paper(**json.loads(row[0])) for row in rows]

    def get(self, paper_id: int) -> Paper | None:
        row = self.conn.execute("SELECT payload FROM papers WHERE id=?", (paper_id,)).fetchone()
        return Paper(**json.loads(row[0])) if row else None

    def reserve_openalex_content_attempt(self, daily_limit: int) -> bool:
        today = datetime.now(timezone.utc).date().isoformat()
        with self.conn:
            self.conn.execute("INSERT OR IGNORE INTO daily_usage(utc_day,openalex_content_attempts) VALUES(?,0)", (today,))
            row = self.conn.execute("SELECT openalex_content_attempts FROM daily_usage WHERE utc_day=?", (today,)).fetchone()
            if row[0] >= daily_limit:
                return False
            self.conn.execute("UPDATE daily_usage SET openalex_content_attempts=openalex_content_attempts+1 WHERE utc_day=?", (today,))
            return True

    def close(self):
        self.conn.close()
