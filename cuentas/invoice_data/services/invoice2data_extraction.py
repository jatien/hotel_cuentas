"""
Primary field-extraction path (step 4, tier 1): invoice2data template matching.

Loads our custom per-supplier YAML templates (invoice_data/invoice2data_templates/)
and runs invoice2data's own extraction directly against the original file -
it does its own text extraction internally (pdfium by default, same engine
we already use elsewhere), so this is independent of our raw_text_layer/ocr_text.

Built-in templates that ship with the library are intentionally excluded:
they're aimed at international SaaS invoices (AWS, hosting providers, etc.)
that are irrelevant to Spanish hotel suppliers, and including them just
adds keyword-collision risk.
"""

from pathlib import Path

from invoice2data import extract_data
from invoice2data.extract.loader import read_templates

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "invoice2data_templates"

_cached_templates = None


def get_templates() -> list:
    """Loads our custom templates once and caches them for the life of the process."""
    global _cached_templates
    if _cached_templates is None:
        _cached_templates = read_templates(str(TEMPLATES_DIR))
    return _cached_templates


def extract_with_invoice2data(document) -> dict | None:
    """
    Attempts extraction using our per-supplier templates. Returns the raw
    invoice2data result dict on a match, or None if no template's keywords
    matched this document's text - meaning it's a supplier we haven't
    written a template for yet.
    """
    templates = get_templates()
    result = extract_data(str(Path(document.file.path)), templates=templates)
    return result or None