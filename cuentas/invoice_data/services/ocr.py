"""
Step 3 of the invoice OCR pipeline: OCR.

Runs on documents flagged needs_ocr=True (SCANNED_PDF or IMAGE). Digital
PDFs already have a usable text layer from ingestion and skip this entirely.

Currently renders pages fresh from the original file rather than depending
on the (designed but not yet wired in) InvoicePage preprocessing step - so
this works standalone right now. Accuracy on genuinely messy scans/photos
will improve once preprocessing is added back in front of this.

Engine: Tesseract via pytesseract (Apache 2.0 wrapper around the Apache
2.0-licensed Tesseract engine). Requires the Tesseract binary installed
separately, plus the Spanish language pack (spa) - see README_OCR_SETUP.md.
"""

from pathlib import Path

import pypdfium2 as pdfium
import pytesseract
from django.conf import settings
from PIL import Image

from invoice_data.models import IngestionStatus, InvoiceDocument, SourceType

RENDER_SCALE = 300 / 72  # ~300 DPI, a reasonable default for OCR accuracy
TESSERACT_LANG = "spa"

# Optional: set TESSERACT_CMD in settings.py if tesseract.exe isn't on PATH
# (common on Windows unless you added the install folder to PATH manually).
_tesseract_cmd = getattr(settings, "TESSERACT_CMD", None)
if _tesseract_cmd:
    pytesseract.pytesseract.tesseract_cmd = _tesseract_cmd


class OCRError(Exception):
    pass


def _render_pdf_pages(path: Path) -> list[Image.Image]:
    pdf = pdfium.PdfDocument(str(path))
    try:
        return [page.render(scale=RENDER_SCALE).to_pil() for page in pdf]
    finally:
        pdf.close()


def _load_image_page(path: Path) -> list[Image.Image]:
    with Image.open(path) as img:
        return [img.convert("RGB").copy()]


def get_pages_for_ocr(document: InvoiceDocument) -> list[Image.Image]:
    source_path = Path(document.file.path)
    if document.source_type == SourceType.SCANNED_PDF:
        return _render_pdf_pages(source_path)
    if document.source_type == SourceType.IMAGE:
        return _load_image_page(source_path)
    raise OCRError(f"OCR no aplica para source_type={document.source_type}")


def ocr_image(image: Image.Image) -> str:
    return pytesseract.image_to_string(image, lang=TESSERACT_LANG)


def run_ocr(document: InvoiceDocument) -> str:
    """
    Runs OCR for one document and stores the result on it. Safe to re-run -
    just overwrites ocr_text. Returns the extracted text either way.
    """
    if not document.needs_ocr:
        return document.raw_text_layer

    pages = get_pages_for_ocr(document)
    text = "\n\n".join(ocr_image(page) for page in pages)

    document.ocr_text = text
    document.status = IngestionStatus.OCR_DONE
    document.save(update_fields=["ocr_text", "status"])
    return text