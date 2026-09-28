"""
Step 1 of the invoice pipeline: ingestion + classification.

Takes a file from anywhere (a folder walk, a browser upload), works out
what kind of document it is and registers it as an InvoiceDocument.
Both the `import_invoices` command and the upload view call ingest_file().

Classification (deliberately simple):
- .pdf with a real text layer            -> DIGITAL_PDF (no full-page OCR)
- .pdf with little or no text            -> SCANNED_PDF (needs OCR)
- image (.jpg/.jpeg/.png/.tif/.bmp/.webp) -> IMAGE (needs OCR)

A digital PDF may still carry images with text (header blocks, product
descriptions); the extraction engine handles that later, so it is still
classified DIGITAL_PDF here.
"""

import hashlib
from dataclasses import dataclass
from pathlib import Path

import pypdfium2 as pdfium
from django.core.files import File
from PIL import Image, UnidentifiedImageError

from invoice_data.models import IngestionStatus, InvoiceDocument, SourceType, TipoEntidad

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".webp"}
PDF_EXTENSIONS = {".pdf"}

# Below this average number of characters per page, a PDF is treated as image-only.
MIN_CHARS_PER_PAGE_FOR_DIGITAL = 20


class UnsupportedFileError(Exception):
    """The file is not a PDF or image we can classify."""


@dataclass
class ClassificationResult:
    """What classification learned about a file."""

    source_type: str
    page_count: int
    needs_ocr: bool
    raw_text_layer: str = ""


def compute_checksum(path: Path) -> str:
    """SHA-256 of a file, read in 1 MB chunks (used to avoid importing the same file twice)."""
    sha256 = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            sha256.update(chunk)
    return sha256.hexdigest()


def classify_pdf(path: Path) -> ClassificationResult:
    """Digital or scanned? Decided by how much text pdfium finds in the PDF's text layer."""
    pdf = pdfium.PdfDocument(str(path))
    try:
        page_count = len(pdf)
        text_parts = []
        total_chars = 0
        for page in pdf:
            text = page.get_textpage().get_text_range()
            total_chars += len(text.strip())
            text_parts.append(text)
    finally:
        pdf.close()

    average = total_chars / page_count if page_count else 0
    is_digital = average >= MIN_CHARS_PER_PAGE_FOR_DIGITAL
    return ClassificationResult(
        source_type=SourceType.DIGITAL_PDF if is_digital else SourceType.SCANNED_PDF,
        page_count=page_count,
        needs_ocr=not is_digital,
        raw_text_layer="\n".join(text_parts) if is_digital else "",
    )


def classify_image(path: Path) -> ClassificationResult:
    """Validates that the file is a readable image; images always need OCR."""
    try:
        with Image.open(path) as image:
            image.verify()
    except UnidentifiedImageError as error:
        raise UnsupportedFileError(f"No es una imagen válida: {path}") from error
    return ClassificationResult(source_type=SourceType.IMAGE, page_count=1, needs_ocr=True)


def classify_document(path: Path) -> ClassificationResult:
    """Routes a file to the right classifier by extension."""
    suffix = path.suffix.lower()
    if suffix in PDF_EXTENSIONS:
        return classify_pdf(path)
    if suffix in IMAGE_EXTENSIONS:
        return classify_image(path)
    raise UnsupportedFileError(f"Extensión no soportada: {suffix} ({path})")


def ingest_file(
    path: Path,
    departamento: str | None = None,
    original_filename: str | None = None,
    tipo_entidad: str | None = None,
) -> tuple[InvoiceDocument, bool]:
    """
    Registers one file. Returns (document, created); a file whose checksum
    already exists returns the existing document with created=False.

    departamento: pass it explicitly when there is no meaningful parent
        folder (web upload) or when it must be empty (a creditor, ""). Only
        None falls back to the parent folder name (folder import).
    original_filename: the real name when `path` is a temp file.
    tipo_entidad: "proveedor" (default) or "acreedor".

    A file that cannot be classified (unsupported type, corrupt PDF) is
    still stored, with status ERROR and the reason in error_message.
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

    document = InvoiceDocument(
        original_filename=display_name,
        checksum=checksum,
        departamento=departamento,
        tipo_entidad=tipo_entidad,
        source_path=str(path),
    )

    try:
        result = classify_document(path)
        document.source_type = result.source_type
        document.page_count = result.page_count
        document.needs_ocr = result.needs_ocr
        document.raw_text_layer = result.raw_text_layer
        document.status = IngestionStatus.NEEDS_OCR if result.needs_ocr else IngestionStatus.INGESTED
    except Exception as error:  # unsupported type or a corrupt file: keep it, flag it
        document.status = IngestionStatus.ERROR
        document.error_message = str(error)

    with open(path, "rb") as handle:
        document.file.save(display_name, File(handle), save=False)

    document.save()
    return document, True
