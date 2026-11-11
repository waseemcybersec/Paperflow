import os
import tempfile
from pathlib import Path

from .downloads import valid_pdf
from .models import Paper
from .normalize import safe_pdf_path


def institutional_download(paper: Paper, output_dir: str | Path, profile_dir: str | Path, timeout_ms: int = 900_000) -> Paper:
    """Reuse a browser profile; user signs in and initiates the publisher's normal PDF download."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError("Install the optional browser dependency: pip install -e .[browser], then playwright install chromium") from exc
    landing_url = paper.landing_page_url or paper.doi_url
    if not landing_url:
        paper.download_status = "ACCESS_REQUIRED"
        paper.access_error = "No publisher or DOI landing page is available."
        return paper
    profile = Path(profile_dir)
    profile.mkdir(parents=True, exist_ok=True)
    folder = Path(output_dir) / "paid_downloads" / str(paper.year or "Unknown")
    folder.mkdir(parents=True, exist_ok=True)
    target = Path(paper.local_pdf_path) if paper.local_pdf_path else safe_pdf_path(folder, paper.title, paper.doi)
    if valid_pdf(target):
        paper.download_status, paper.local_pdf_path, paper.access_error = "DOWNLOADED_SUBSCRIPTION", str(target), None
        return paper
    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(str(profile), headless=False, accept_downloads=True)
        try:
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(landing_url, wait_until="domcontentloaded", timeout=60000)
            print("Sign in through your institution manually if prompted. Do not enter credentials in this terminal.")
            input("When the article page is ready, press Enter. Then click its official PDF/download button in the browser... ")
            try:
                download = page.wait_for_event("download", timeout=timeout_ms)
                with tempfile.NamedTemporaryFile(dir=folder, suffix=".part", delete=False) as tmp:
                    staged = Path(tmp.name)
                staged.unlink(missing_ok=True)
                try:
                    download.save_as(str(staged))
                    if not valid_pdf(staged):
                        raise ValueError("Publisher download did not produce a valid PDF.")
                    os.replace(staged, target)
                    paper.download_status, paper.local_pdf_path, paper.access_error = "DOWNLOADED_SUBSCRIPTION", str(target), None
                finally:
                    staged.unlink(missing_ok=True)
            except Exception as exc:
                timed_out = "Timeout" in type(exc).__name__
                paper.download_status = "ACCESS_REQUIRED" if timed_out else "DOWNLOAD_FAILED"
                paper.access_error = "No browser download was captured before timeout; confirm access and click the publisher's PDF button." if timed_out else str(exc)
        finally:
            context.close()
    return paper
