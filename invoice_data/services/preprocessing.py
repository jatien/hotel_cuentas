"""
Step 2 of the invoice pipeline (optional): image cleanup before OCR.

Only applies to documents that need OCR (scanned PDFs, photos) - digital
PDFs already have a real text layer and never reach this. Each raw page
(from ocr.get_pages_for_ocr) goes through:

    1. Grayscale
    2. Denoise (cv2.fastNlMeansDenoising) - removes scan/photo speckle
    3. Deskew (auto-rotate to correct scanning/photographing tilt)
    4. Contrast enhancement (CLAHE) - boosts faded ink / uneven lighting

Deliberately NOT binarized (no black/white threshold): that can help a
template-matching OCR engine but hurts a layout-aware one, and grayscale
+ CLAHE tested better on real scans (see test_preprocessing.py).

Cleaned pages are saved as InvoicePage rows, so they can be eyeballed in
the admin to check whether cleanup actually helped before trusting OCR on
them. ocr.get_pages_for_ocr() automatically prefers these saved pages
over the raw ones once they exist - the two modules only communicate
through the database (InvoicePage), never by importing each other, so
there is no import-order dependency either way.
"""

import io

import cv2
import numpy as np
from django.core.files.base import ContentFile
from PIL import Image

from invoice_data.models import IngestionStatus, InvoiceDocument, InvoicePage
from invoice_data.services.ocr import get_pages_for_ocr

DENOISE_STRENGTH = 10  # cv2.fastNlMeansDenoising's h parameter: higher removes more noise, but softens detail
MIN_DESKEW_ANGLE = 0.1  # degrees: tilts smaller than this aren't worth the interpolation cost
CLAHE_CLIP_LIMIT = 2.0
CLAHE_TILE_SIZE = (8, 8)


def _pil_to_gray(image: Image.Image) -> np.ndarray:
    """A PIL image (any mode) as a single-channel grayscale numpy array."""
    return cv2.cvtColor(np.array(image.convert("RGB")), cv2.COLOR_RGB2GRAY)


def _denoise(gray: np.ndarray) -> np.ndarray:
    """Removes scan/photo speckle while keeping edges reasonably sharp."""
    return cv2.fastNlMeansDenoising(gray, h=DENOISE_STRENGTH)


def _deskew(gray: np.ndarray) -> np.ndarray:
    """
    Straightens a tilted scan/photo. Finds the minimum-area rectangle
    around all non-background pixels and rotates by its angle. A blank
    page, or a tilt too small to matter, is left untouched.
    """
    inverted = cv2.bitwise_not(gray)
    threshold = cv2.threshold(inverted, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)[1]
    coords = np.column_stack(np.where(threshold > 0))
    if coords.size == 0:
        return gray  # blank page: nothing to align to

    angle = cv2.minAreaRect(coords)[-1]
    angle = -(90 + angle) if angle < -45 else -angle
    if abs(angle) < MIN_DESKEW_ANGLE:
        return gray

    height, width = gray.shape
    matrix = cv2.getRotationMatrix2D((width // 2, height // 2), angle, 1.0)
    return cv2.warpAffine(
        gray, matrix, (width, height), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE
    )


def _enhance_contrast(gray: np.ndarray) -> np.ndarray:
    """CLAHE: boosts local contrast so faded ink or uneven scan lighting becomes more legible."""
    clahe = cv2.createCLAHE(clipLimit=CLAHE_CLIP_LIMIT, tileGridSize=CLAHE_TILE_SIZE)
    return clahe.apply(gray)


def clean_page_image(image: Image.Image) -> Image.Image:
    """Runs the full cleanup pipeline (grayscale, denoise, deskew, contrast) on one page image."""
    gray = _pil_to_gray(image)
    gray = _denoise(gray)
    gray = _deskew(gray)
    gray = _enhance_contrast(gray)
    return Image.fromarray(gray)


def preprocess_document(document: InvoiceDocument, force: bool = False) -> list[InvoicePage]:
    """
    Cleans every page of a scan/photo document and saves the results as
    InvoicePage rows, so ocr.get_pages_for_ocr() picks them up
    automatically from then on. A no-op that returns the existing pages
    if the document was already preprocessed, unless force=True (which
    replaces them - the old image files are deleted too, not just the
    database rows). Returns [] for a document that doesn't need OCR -
    there's nothing to clean.
    """
    if not document.needs_ocr:
        return []
    if not force and document.pages.exists():
        return list(document.pages.all())

    raw_pages = get_pages_for_ocr(document)

    for old_page in document.pages.all():
        old_page.processed_image.delete(save=False)  # the file on disk, not only the row
        old_page.delete()

    created = []
    for index, raw_image in enumerate(raw_pages, start=1):
        cleaned = clean_page_image(raw_image)
        buffer = io.BytesIO()
        cleaned.save(buffer, format="PNG")

        page = InvoicePage(document=document, page_number=index)
        page.processed_image.save(f"pagina_{index}.png", ContentFile(buffer.getvalue()), save=False)
        page.width, page.height = cleaned.size
        page.save()
        created.append(page)

    document.status = IngestionStatus.PREPROCESSED
    document.save(update_fields=["status"])
    return created