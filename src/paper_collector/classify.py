import re
from .models import Paper

REVIEW_CUES = re.compile(r"\b(review|systematic review|literature review|survey|bibliometric analysis|meta[- ]analysis|roadmap|perspective|overview|progress report)\b", re.I)
RESEARCH_TYPES = {"article", "journal-article", "research-article", "original-research"}
EXCLUDED_TYPES = {"review", "review-article", "book-review", "book", "book-chapter", "book-part", "book-series", "edited-book", "monograph", "reference-entry", "chapter", "proceedings", "proceedings-article", "conference-paper", "editorial", "correction", "erratum", "retraction", "announcement", "news", "dataset", "dissertation", "preprint"}
TOPIC_TERMS = ("perovskite", "neuromorphic", "artificial synapse", "artificial synapses", "synaptic device", "synaptic transistor", "memristor")


def classify(paper: Paper, min_year: int = 2022) -> Paper:
    text = f"{paper.title or ''} {paper.abstract or ''}".casefold()
    ptype = (paper.publication_type or "").casefold().replace(" ", "-")
    ptype = {"journalarticle": "journal-article", "conferencepaper": "conference-paper", "originalresearch": "original-research"}.get(ptype, ptype)
    if paper.year is None or paper.year < min_year:
        paper.classification_status, paper.classification_reason = "REJECTED", f"Publication year is missing or before {min_year}."
    elif not any(term in text for term in TOPIC_TERMS):
        paper.classification_status, paper.classification_reason = "REJECTED", "No project topic found in title or abstract."
    elif ptype in EXCLUDED_TYPES or REVIEW_CUES.search(text):
        paper.classification_status, paper.classification_reason = "REJECTED", "Review or excluded publication type indicated."
    elif ptype in RESEARCH_TYPES and paper.abstract:
        paper.classification_status, paper.classification_reason = "ACCEPTED", "Research article type, year, topic, and abstract are present."
    else:
        paper.classification_status, paper.classification_reason = "NEEDS_MANUAL_REVIEW", "Original research type or sufficient evidence is not established."
    return paper
