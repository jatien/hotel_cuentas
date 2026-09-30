"""
Full-page OCR for documents that have no text layer (scanned PDFs and photos).

Digital PDFs never come here: the extraction engine reads their text layer
directly (and OCRs only images embedded in them, in extractor.py). This
module renders every page of a scan or photo to ~300 DPI and runs Tesseract
(Spanish) with a table-friendly page mode, so each printed row stays on one
line and the arithmetic-based extraction can recognise it.

Position + confidence: Tesseract computes a bounding box and a confidence
score for every word it reads, regardless of which output format is asked
for - image_to_string() just throws that away and returns plain text. This
module uses image_to_data() instead, so both are kept:

    run_ocr(document)   unchanged signature/behaviour - stores flat text on
                         document.ocr_text, exactly as before, now
                         reconstructed in real reading order from the
                         positioned words rather than Tesseract's own
                         single-block guess.
    ocr_words(document) NEW - the positioned, confidence-scored words
                         themselves (per page), for anything that needs to
                         know WHERE something is or HOW SURE the OCR was -
                         e.g. telling a genuine heading apart from body
                         text by its size, or flagging a specific low-
                         confidence read for a person to check.

Column/table reconstruction (pairing a description with an amount that
sits in a separate column, not on the same text line) is NOT done here -
that needs per-document layout assumptions this module deliberately
doesn't make. ocr_words() is the building block for that; the column
logic itself is a separate, not-yet-built step.

Requires the Tesseract program plus its Spanish data; the path can be set
in settings.TESSERACT_CMD or the TESSERACT_CMD environment variable.
"""

import os
from dataclasses import dataclass
from pathlib import Path

import pypdfium2 as pdfium
import pytesseract
from django.conf import settings
from PIL import Image

from invoice_data.models import IngestionStatus, InvoiceDocument, SourceType

RENDER_SCALE = 300 / 72  # ~300 DPI, a good default for OCR accuracy
# --psm 6 = "a single uniform block of text": keeps table rows on one line.
OCR_CONFIG = "--psm 6"
# Words closer together (vertically) than this fraction of the taller one's
# height are treated as being on the same printed line when reconstructing
# reading-order text. Relative, not a fixed pixel count, so it holds at any
# render scale or font size.
LINE_GROUPING_RATIO = 0.6
LOW_CONFIDENCE_THRESHOLD = 50  # Tesseract's own 0-100 score; below this, flag for a human to check


class OCRError(Exception):
    """OCR cannot run on this document."""


@dataclass
class OcrWord:
    """
    One word Tesseract read, with where it sits on the page and how sure
    Tesseract was. Coordinates are pixels on the rendered page image
    (top-left origin, y grows downwards - the opposite of pdfium's
    PDF-coordinate convention used elsewhere in this app).
    """

    page: int
    text: str
    x: int
    y: int
    width: int
    height: int
    confidence: float  # 0-100


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


def get_raw_pages(document: InvoiceDocument) -> list[Image.Image]:
    """The page images of a scan or photo document, always freshly rendered/loaded from the original file."""
    source_path = Path(document.file.path)
    if document.source_type == SourceType.SCANNED_PDF:
        return _render_pdf_pages(source_path)
    if document.source_type == SourceType.IMAGE:
        return _load_image_page(source_path)
    raise OCRError(f"OCR de página completa no aplica a source_type={document.source_type}")


def get_pages_for_ocr(document: InvoiceDocument) -> list[Image.Image]:
    """
    The page images of a scan or photo document - the cleaned
    (preprocessed) version if one has been saved (see
    services/preprocessing.py), otherwise the raw render/load. Checked
    via the InvoicePage relation, not an import of preprocessing.py, so
    the two modules stay decoupled - they only communicate through the
    database.
    """
    if document.pages.exists():
        return [
            Image.open(page.processed_image.path).convert("RGB")
            for page in document.pages.order_by("page_number")
        ]
    return get_raw_pages(document)


def ocr_page_words(image: Image.Image, page: int) -> list[OcrWord]:
    """
    Runs Tesseract on one page image and returns every word it found, with
    its position and confidence. Empty tokens (Tesseract emits structural
    placeholder rows for blocks/paragraphs/lines with no text of their
    own) and a raw confidence of -1 (Tesseract's own "not applicable"
    marker, distinct from a genuine low score) are dropped.
    """
    data = pytesseract.image_to_data(
        image, lang=_language(), config=OCR_CONFIG, output_type=pytesseract.Output.DICT
    )
    words = []
    for i in range(len(data["text"])):
        text = data["text"][i].strip()
        confidence = float(data["conf"][i])
        if text and confidence >= 0:
            words.append(
                OcrWord(
                    page=page,
                    text=text,
                    x=data["left"][i],
                    y=data["top"][i],
                    width=data["width"][i],
                    height=data["height"][i],
                    confidence=confidence,
                )
            )
    return words


def group_words_into_rows(words: list[OcrWord]) -> list[list[OcrWord]]:
    """
    Groups words (already all on the same page) into printed rows by
    vertical proximity, relative to word height so it holds regardless of
    render scale or font size. Returns rows in top-to-bottom order; each
    row is unordered internally (callers that need left-to-right order
    should sort by .x themselves). Used both for reading-order text
    reconstruction (words_to_text) and, elsewhere, for reading a table
    column's rows independently of the rest of the page.
    """
    ordered = sorted(words, key=lambda w: w.y)
    rows: list[list[OcrWord]] = []
    current: list[OcrWord] = []
    baseline_y = None
    for word in ordered:
        if current and baseline_y is not None:
            threshold = LINE_GROUPING_RATIO * max(word.height, current[-1].height, 1)
            if word.y - baseline_y > threshold:
                rows.append(current)
                current = []
                baseline_y = None  # starting a new row: forget the previous row's baseline
        current.append(word)
        baseline_y = word.y if baseline_y is None else min(baseline_y, word.y)
    if current:
        rows.append(current)
    return rows


def words_to_text(words: list[OcrWord]) -> str:
    """
    Reconstructs plain, reading-order text from positioned words: groups
    words into printed lines (group_words_into_rows), sorts each line
    left-to-right, and joins lines top-to-bottom. This is a reading-order
    pass only - it does NOT attempt to detect columns, so a multi-column
    table's cells still end up read left-to-right across the whole page
    width, same as plain OCR text always has.
    """
    if not words:
        return ""

    pages: dict[int, list[OcrWord]] = {}
    for word in words:
        pages.setdefault(word.page, []).append(word)

    page_texts = []
    for page in sorted(pages):
        rows = group_words_into_rows(pages[page])
        page_texts.append(
            "\n".join(" ".join(w.text for w in sorted(row, key=lambda w: w.x)) for row in rows)
        )
    return "\n\n".join(page_texts)


def low_confidence_words(words: list[OcrWord], threshold: float = LOW_CONFIDENCE_THRESHOLD) -> list[OcrWord]:
    """Words Tesseract itself was unsure about - candidates for a person to double-check rather than trust blindly."""
    return [w for w in words if w.confidence < threshold]


def ocr_pages(pages: list[Image.Image]) -> tuple[str, list[OcrWord]]:
    """
    OCRs a specific, already-obtained list of page images (raw or
    cleaned - this function has no opinion) and returns both the
    reconstructed text and the positioned words. The low-level building
    block both run_ocr()/ocr_words() (which decide WHICH pages to use)
    and any raw-vs-cleaned comparison are built on.
    """
    _configure_tesseract()
    words = []
    for page_number, image in enumerate(pages):
        words.extend(ocr_page_words(image, page_number))
    return words_to_text(words), words


def ocr_words(document: InvoiceDocument) -> list[OcrWord]:
    """
    The positioned, confidence-scored words of a scan or photo document -
    the building block for anything that needs position (telling a
    heading from body text by size, eventually reconstructing a
    multi-column table) or confidence (flagging an unreliable read).
    Returns [] for a document that doesn't need OCR - there is nothing to
    read positions from, its text layer already has real characters.
    """
    if not document.needs_ocr:
        return []
    _, words = ocr_pages(get_pages_for_ocr(document))
    return words


def run_ocr(document: InvoiceDocument) -> str:
    """
    OCRs a scan or photo, stores the text on the document (ocr_text, status
    OCR_DONE) and returns it. Documents that do not need OCR return their
    text layer untouched. Safe to re-run: it overwrites ocr_text.

    The stored text is reconstructed from positioned words (words_to_text),
    not Tesseract's own single-block text output - same reading-order
    result for a simple page, more reliable on a page with an unusual
    layout, since it groups by actual word position rather than
    Tesseract's internal guess at paragraph structure.
    """
    if not document.needs_ocr:
        return document.raw_text_layer

    text, _ = ocr_pages(get_pages_for_ocr(document))

    document.ocr_text = text
    document.status = IngestionStatus.OCR_DONE
    document.save(update_fields=["ocr_text", "status"])
    return text