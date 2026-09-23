import os

from django.db import models
from django.utils import timezone
from django.core.validators import FileExtensionValidator
from django.conf import settings
from django.core.exceptions import ValidationError


def invoice_upload_path(instance: "InvoiceDocument", filename: str) -> str:
    """
    Organize stored files as:
    invoices/<departamento>/<year>/<month>/<original_filename>

    Keeping the original filename (not a random hash) makes it much easier
    to eyeball the media folder and cross-check against the source scans.

    Note: we use timezone.now() here rather than instance.imported_at.
    auto_now_add fields are only populated during save(), in field-definition
    order, and FileField's upload_to runs before that assignment happens -
    reading instance.imported_at here would always see None.
    """
    departamento = instance.departamento or "sin_departamento"
    now = timezone.now()
    return os.path.join("invoices", departamento, now.strftime("%Y"), now.strftime("%m"), filename)


class SourceType(models.TextChoices):
    DIGITAL_PDF = "digital_pdf", "PDF digital (con capa de texto)"
    SCANNED_PDF = "scanned_pdf", "PDF escaneado (solo imagen)"
    IMAGE = "image", "Imagen (foto o escaneo suelto)"
    UNKNOWN = "unknown", "Desconocido"


class TipoEntidad(models.TextChoices):
    """Whether a document is from a supplier (tied to a department) or a creditor (not)."""

    PROVEEDOR = "proveedor", "Proveedor"
    ACREEDOR = "acreedor", "Acreedor"


class IngestionStatus(models.TextChoices):
    """
    Single source of truth for InvoiceDocument.status - previously defined
    twice in this file (once before InvoiceDocument with only 4 values,
    once after with 2 more added). Since Django captures choices/default
    at class-body-execution time, InvoiceDocument's status field had
    silently locked onto the incomplete first version. Consolidated here.
    """

    INGESTED = "ingested", "Ingerido"
    NEEDS_OCR = "needs_ocr", "Pendiente de OCR"
    PREPROCESSED = "preprocessed", "Preprocesado (listo para OCR)"
    OCR_DONE = "ocr_done", "OCR completado"
    ERROR = "error", "Error"
    DUPLICATE = "duplicate", "Duplicado (ya existia)"


class InvoiceDocument(models.Model):
    """
    One row per physical invoice file that has entered the pipeline.
    This is intentionally 'dumb' at this stage: it only records *what*
    came in and *what kind of thing it is*. OCR text, extracted fields,
    and validation happen in later pipeline stages / related models.
    """

    file = models.FileField(upload_to=invoice_upload_path, max_length=500)
    original_filename = models.CharField(max_length=255)
    checksum = models.CharField(
        max_length=64,
        unique=True,
        help_text="SHA-256 of the file contents, used to avoid re-importing the same file twice.",
    )

    tipo_entidad = models.CharField(
        max_length=20, choices=TipoEntidad.choices, default=TipoEntidad.PROVEEDOR
    )
    departamento = models.CharField(max_length=100, blank=True)
    source_path = models.CharField(
        max_length=1000,
        blank=True,
        help_text="Original path this file was ingested from, kept for audit purposes.",
    )

    source_type = models.CharField(
        max_length=20, choices=SourceType.choices, default=SourceType.UNKNOWN
    )
    page_count = models.PositiveIntegerField(default=1)
    needs_ocr = models.BooleanField(default=False)

    status = models.CharField(
        max_length=20, choices=IngestionStatus.choices, default=IngestionStatus.INGESTED
    )
    error_message = models.TextField(blank=True)

    # Populated at ingestion time for digital PDFs only. Cheap to grab now,
    # saves re-opening the PDF in the next pipeline stage.
    raw_text_layer = models.TextField(
        blank=True,
        help_text="Extracted text layer for digital PDFs (empty for scans/images).",
    )
    ocr_text = models.TextField(
        blank=True,
        help_text="Texto extraido por OCR para documentos escaneados/imagenes (vacio para PDFs digitales).",
    )

    imported_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-imported_at"]

    def __str__(self) -> str:
        return f"{self.original_filename} ({self.get_source_type_display()})"

    def get_extracted_text(self) -> str:
        """Unified accessor: text layer for digital PDFs, OCR output for scans/images."""
        return self.ocr_text if self.needs_ocr else self.raw_text_layer


def invoice_page_upload_path(instance: "InvoicePage", filename: str) -> str:
    departamento = instance.document.departamento or "sin_departamento"
    now = timezone.now()
    return os.path.join(
        "invoices_preprocessed", departamento, now.strftime("%Y"), now.strftime("%m"), filename
    )


class InvoicePage(models.Model):
    """
    One cleaned page image, ready for OCR. Only created for documents that
    needed preprocessing (needs_ocr=True) - digital PDFs never get one of these.
    """

    document = models.ForeignKey(
        InvoiceDocument, related_name="pages", on_delete=models.CASCADE
    )
    page_number = models.PositiveIntegerField()
    processed_image = models.ImageField(upload_to=invoice_page_upload_path)
    width = models.PositiveIntegerField(default=0)
    height = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["document", "page_number"]
        unique_together = ("document", "page_number")

    def __str__(self) -> str:
        return f"{self.document.original_filename} - pagina {self.page_number}"


class ExtractionStatus(models.TextChoices):
    """Whether field extraction found what it needed, or needs a human to check it."""

    EXTRACTED = "extracted", "Extraido correctamente"
    NEEDS_REVIEW = "needs_review", "Necesita revision manual"


class ExtractedInvoiceData(models.Model):
    """
    Structured fields pulled from one InvoiceDocument's text (either its
    digital text layer or its OCR output - see get_extracted_text()).
    One row per document, created/refreshed by
    invoice_data.services.extraction.extract_document().
    """

    document = models.OneToOneField(
        InvoiceDocument, related_name="extracted_data", on_delete=models.CASCADE
    )
    numero_factura = models.CharField(max_length=100, blank=True, null=True)
    fecha_factura = models.DateField(blank=True, null=True)
    proveedor_cif = models.CharField(max_length=20, blank=True, null=True)
    base_imponible = models.DecimalField(max_digits=12, decimal_places=2, blank=True, null=True)
    iva_porcentaje = models.DecimalField(max_digits=5, decimal_places=2, blank=True, null=True)
    total = models.DecimalField(max_digits=12, decimal_places=2, blank=True, null=True)
    status = models.CharField(
        max_length=20, choices=ExtractionStatus.choices, default=ExtractionStatus.NEEDS_REVIEW
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    extraction_method = models.CharField(
        max_length=30,
        blank=True,
        help_text="Que metodo genero estos datos: invoice2data_template, regex_fallback, etc.",
    )
    confirmado = models.BooleanField(
        default=False,
        help_text="Si el usuario ha revisado y aceptado estos datos como correctos.",
    )

    def __str__(self) -> str:
        return f"Datos extraidos de {self.document.original_filename}"


class InvoiceLineItem(models.Model):
    """
    One product/service line from an invoice's itemized table. Categorization
    into spend groups (e.g. 'Comida', 'Mantenimiento') is intentionally not
    modeled yet - a naive keyword match breaks down against cryptic real
    product codes, and needs a smarter approach to be designed separately.
    """

    document = models.ForeignKey(InvoiceDocument, related_name="line_items", on_delete=models.CASCADE)
    codigo_articulo = models.CharField(max_length=50, blank=True)
    descripcion = models.CharField(max_length=255)
    iva_porcentaje = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    cantidad = models.DecimalField(max_digits=10, decimal_places=3, null=True, blank=True)
    precio_unitario = models.DecimalField(max_digits=10, decimal_places=4, null=True, blank=True)
    importe = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    confirmado = models.BooleanField(default=False)

    class Meta:
        ordering = ["document", "id"]

    def __str__(self) -> str:
        return f"{self.descripcion} ({self.document.original_filename})"