"""
Step 1 of the invoice OCR pipeline: ingestion + classification.

This module doesn't care where a file came from (a folder walk, an
uploaded file from a browser, whatever) - it just takes a path and
optional metadata, figures out what kind of document it is, and
registers it as an InvoiceDocument. Keeping this separate from the
management command means the same logic can back a web upload view
later without duplicating anything.

Classification rules (deliberately simple at this stage):
- .pdf with a real extractable text layer  -> DIGITAL_PDF (no OCR needed)
- .pdf with little/no extractable text     -> SCANNED_PDF (needs OCR)
- image file (.jpg/.jpeg/.png/.tiff/.bmp)  -> IMAGE (needs OCR)

"Little/no text" uses a low per-page character threshold rather than
zero, because some scanned PDFs carry a few stray text artifacts
(e.g. a machine-added stamp) without actually being digitally authored.
"""

import hashlib
from dataclasses import dataclass
from pathlib import Path

import pypdfium2 as pdfium
from django.core.files import File
from PIL import Image, UnidentifiedImageError
from invoice_data.models import InvoiceDocument, IngestionStatus, SourceType, TipoEntidad

from invoice_data.models import InvoiceDocument, IngestionStatus, SourceType

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".webp"}
PDF_EXTENSIONS = {".pdf"}

# Below this average character count per page, treat a PDF as image-only.
MIN_CHARS_PER_PAGE_FOR_DIGITAL = 20


class UnsupportedFileError(Exception):
    pass


@dataclass
class ClassificationResult:
    source_type: str
    page_count: int
    needs_ocr: bool
    raw_text_layer: str = ""


def compute_checksum(path: Path) -> str:
    sha256 = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            sha256.update(chunk)
    return sha256.hexdigest()


def classify_pdf(path: Path) -> ClassificationResult:
    pdf = pdfium.PdfDocument(str(path))
    try:
        page_count = len(pdf)
        text_parts = []
        total_chars = 0
        for page in pdf:
            textpage = page.get_textpage()
            text = textpage.get_text_range()
            total_chars += len(text.strip())
            text_parts.append(text)
    finally:
        pdf.close()

    avg_chars_per_page = total_chars / page_count if page_count else 0
    is_digital = avg_chars_per_page >= MIN_CHARS_PER_PAGE_FOR_DIGITAL

    return ClassificationResult(
        source_type=SourceType.DIGITAL_PDF if is_digital else SourceType.SCANNED_PDF,
        page_count=page_count,
        needs_ocr=not is_digital,
        raw_text_layer="\n".join(text_parts) if is_digital else "",
    )


def classify_image(path: Path) -> ClassificationResult:
    try:
        with Image.open(path) as img:
            img.verify()
    except UnidentifiedImageError as exc:
        raise UnsupportedFileError(f"Not a valid image file: {path}") from exc

    return ClassificationResult(
        source_type=SourceType.IMAGE,
        page_count=1,
        needs_ocr=True,
    )


def classify_document(path: Path) -> ClassificationResult:
    suffix = path.suffix.lower()
    if suffix in PDF_EXTENSIONS:
        return classify_pdf(path)
    if suffix in IMAGE_EXTENSIONS:
        return classify_image(path)
    raise UnsupportedFileError(f"Unsupported file extension: {suffix} ({path})")


def ingest_file(
    path: Path,
    departamento: str | None = None,
    original_filename: str | None = None,
    tipo_entidad: str | None = None,
) -> tuple[InvoiceDocument, bool]:
    """
    Ingest a single file into the pipeline.

    Returns (document, created). If a file with the same checksum was
    already ingested, the existing InvoiceDocument is returned with
    created=False and nothing new is written.

    `departamento`: pass this explicitly whenever there's no meaningful
    parent folder to infer it from (a web upload form field), OR when it
    should be genuinely empty (an acreedor, which has no department).
    Only "not provided at all" (None) falls back to the parent folder
    name - an explicitly empty string "" is respected as-is, which
    matters for acreedores.

    `tipo_entidad`: "proveedor" or "acreedor" (see models.TipoEntidad).
    Defaults to proveedor if not given, for backward compatibility.

    `original_filename`: pass this explicitly when `path` is a temp file
    with a meaningless name (e.g. a web upload written to a NamedTemporaryFile).
    """
    path = Path(path)
    checksum = compute_checksum(path)

    existing = InvoiceDocument.objects.filter(checksum=checksum).first()
    if existing:
        return existing, False

    tipo_entidad = tipo_entidad or TipoEntidad.PROVEEDOR
    if departamento is None:
        departamento = path.parent.name
    display_name = original_filename or path.name

    doc = InvoiceDocument(
        original_filename=display_name,
        checksum=checksum,
        departamento=departamento,
        tipo_entidad=tipo_entidad,
        source_path=str(path),
    )