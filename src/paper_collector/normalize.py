import re
import unicodedata
import hashlib
from pathlib import Path


def normalize_doi(value: str | None) -> str | None:
    if not value:
        return None
    doi = value.strip().lower()
    doi = re.sub(r"^doi:\s*", "", doi)
    doi = re.sub(r"^https?://(dx\.)?doi\.org/", "", doi)
    return doi.strip().rstrip(".,; ") or None


def normalize_title(value: str) -> str:
    value = unicodedata.normalize("NFKD", value).casefold()
    return " ".join(re.findall(r"[a-z0-9]+", value))


def safe_folder_name(title: str, max_length: int = 100) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", title)
    name = re.sub(r"\s+", " ", name).strip(" .")
    name = re.sub(r"[._ ]+$", "", name) or "Untitled paper"
    reserved = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
    if name.upper() in reserved:
        name = f"_{name}"
    return name[:max_length].rstrip(" .") or "Untitled paper"


def safe_pdf_path(year_dir: str | Path, title: str, doi: str | None = None, max_length: int = 150) -> Path:
    """Use a sanitized title as the filename; add an identity suffix only on a real collision."""
    year_dir = Path(year_dir)
    base = safe_folder_name(title, max_length=max_length)
    candidate = year_dir / f"{base}.pdf"
    if not candidate.exists():
        return candidate
    identity = doi or title
    suffix = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:8]
    candidate = year_dir / f"{base}_{suffix}.pdf"
    number = 2
    while candidate.exists():
        candidate = year_dir / f"{base}_{suffix}_{number}.pdf"
        number += 1
    return candidate
