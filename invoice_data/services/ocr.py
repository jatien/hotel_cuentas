"""
Full-page OCR for documents that have no text layer (scanned PDFs and photos).

Digital PDFs never come here: the extraction engine reads their text layer
directly (and OCRs only images embedded in them, in extractor.py). This
module renders every page of a scan or photo to ~300 DPI and runs Tesseract
(Spanish) with a table-friendly page mode, so each printed row stays on one
line and the arithmetic-based extraction can recognise it.

Requires the Tesseract program plus its Spanish data; the path can be set
in settings.TESSERACT_CMD or the TESSERACT_CMD environment variable.
"""

import os
from pathlib import Path

import pypdfium2 as pdfium
import pytesseract
from django.conf import settings
from PIL import Image

from invoice_data.models import IngestionStatus, InvoiceDocument, SourceType

RENDER_SCALE = 300 / 72  # ~300 DPI, a good default for OCR accuracy
# --psm 6 = "a single uniform block of text": keeps table rows on one line.
OCR_CONFIG = "--psm 6"


class OCRError(Exception):
    """OCR cannot run on this document."""


def _configure_tesseract() -> None:
    """Points pytesseract at the Tesseract program if a path is configured (settings, then environment)."""
    command = getattr(settings, "TESSERACT_CMD", None) or os.environ.get("TESSERACT_CMD")
    if command:
        pytesseract.pytesseract.tesseract_cmd = command


def _language() -> str:
    """Tesseract language (settings.INVOICE_OCR_LANG, default Spanish)."""
    return getattr(settings, "INVOICE_OCR_LANG", "spa") or "spa"


def _render_pdf_pages(path: Path) -> list[Image.Image]:
    """Renders every page of a PDF to an image."""
    pdf = pdfium.PdfDocument(str(path))
    try:
        return [page.render(scale=RENDER_SCALE).to_pil() for page in pdf]
    finally:
        pdf.close()


def _load_image_page(path: Path) -> list[Image.Image]:
    """Loads a photo or loose scan as a single page."""
    with Image.open(path) as image:
        return [image.convert("RGB").copy()]


def get_pages_for_ocr(document: InvoiceDocument) -> list[Image.Image]:
    """The page images of a scan or photo document."""
    source_path = Path(document.file.path)
    if document.source_type == SourceType.SCANNED_PDF:
        return _render_pdf_pages(source_path)
    if document.source_type == SourceType.IMAGE:
        return _load_image_page(source_path)
    raise OCRError(f"OCR de página completa no aplica a source_type={document.source_type}")


def ocr_image(image: Image.Image) -> str:
    """Runs Tesseract on one page image and returns its text."""
    return pytesseract.image_to_string(image, lang=_language(), config=OCR_CONFIG)


def run_ocr(document: InvoiceDocument) -> str:
    """
    OCRs a scan or photo, stores the text on the document (ocr_text, status
    OCR_DONE) and returns it. Documents that do not need OCR return their
    text layer untouched. Safe to re-run: it overwrites ocr_text.
    """
    if not document.needs_ocr:
        return document.raw_text_layer

    _configure_tesseract()
    text = "\n\n".join(ocr_image(page) for page in get_pages_for_ocr(document))

    document.ocr_text = text
    document.status = IngestionStatus.OCR_DONE
    document.save(update_fields=["ocr_text", "status"])
    return text
