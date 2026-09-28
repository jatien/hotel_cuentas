"""
Models for the invoice_data app (Gestión de Facturas).

Layout:
    Proveedor               one row per supplier, identified by CIF
    InvoiceDocument         one row per file that entered the pipeline
    ExtractedInvoiceData    header + totals extracted from a document (1:1)
    InvoiceTaxBreakdown     one row per IVA rate of a document (base, cuota)
    InvoiceLineItem         one row per product line of a document
    InvoicePage             cleaned page images (reserved for preprocessing)

Money is always a Decimal, never a float. The JSON produced by the engine
("factura/1") maps one-to-one onto these tables.
"""

import os

from django.db import models
from django.utils import timezone


def invoice_upload_path(instance: "InvoiceDocument", filename: str) -> str:
    """
    Storage path of an uploaded invoice:
    invoices/<departamento>/<year>/<month>/<original_filename>

    Uses timezone.now() rather than instance.imported_at because
    auto_now_add fields are only filled during save(), after upload_to runs.
    """
    departamento = instance.departamento or "sin_departamento"
    now = timezone.now()
    return os.path.join("invoices", departamento, now.strftime("%Y"), now.strftime("%m"), filename)


def invoice_page_upload_path(instance: "InvoicePage", filename: str) -> str:
    """Storage path of a cleaned page image (reserved for the preprocessing stage)."""
    departamento = instance.document.departamento or "sin_departamento"
    now = timezone.now()
    return os.path.join(
        "invoices_preprocessed", departamento, now.strftime("%Y"), now.strftime("%m"), filename
    )


class SourceType(models.TextChoices):
    """What kind of file this is, decided at ingestion time."""

    DIGITAL_PDF = "digital_pdf", "PDF digital (con capa de texto)"
    SCANNED_PDF = "scanned_pdf", "PDF escaneado (solo imagen)"
    IMAGE = "image", "Imagen (foto o escaneo suelto)"
    UNKNOWN = "unknown", "Desconocido"


class TipoEntidad(models.TextChoices):
    """Whether a document comes from a supplier (tied to a department) or a creditor (not)."""

    PROVEEDOR = "proveedor", "Proveedor"
    ACREEDOR = "acreedor", "Acreedor"


class IngestionStatus(models.TextChoices):
    """Where a document sits in the file-handling part of the pipeline."""

    INGESTED = "ingested", "Ingerido"
    NEEDS_OCR = "needs_ocr", "Pendiente de OCR"
    PREPROCESSED = "preprocessed", "Preprocesado (listo para OCR)"
    OCR_DONE = "ocr_done", "OCR completado"
    ERROR = "error", "Error"
    DUPLICATE = "duplicate", "Duplicado (ya existia)"


class EstadoExtraccion(models.TextChoices):
    """Outcome of an extraction: fully consistent, or in need of a human look."""

    OK = "ok", "Correcta"
    REVISAR = "revisar", "Necesita revisión"


class Proveedor(models.Model):
    """
    A supplier or creditor, identified by its tax id (CIF/NIF). The CIF is
    the reliable key; the name is for display and may be corrected by hand.
    """

    cif = models.CharField(max_length=20, unique=True)
    nombre = models.CharField(max_length=200, blank=True)
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["nombre", "cif"]
        verbose_name_plural = "proveedores"

    def __str__(self) -> str:
        return f"{self.nombre or 'Sin nombre'} ({self.cif})"


class InvoiceDocument(models.Model):
    """
    One row per invoice file that has entered the pipeline: what came in,
    what kind of file it is, and the text obtained by OCR when it was a scan.
    The extracted invoice data lives in ExtractedInvoiceData and its children.
    """

    file = models.FileField(upload_to=invoice_upload_path, max_length=500)
    original_filename = models.CharField(max_length=255)
    checksum = models.CharField(
        max_length=64,
        unique=True,
        help_text="SHA-256 del contenido: evita importar dos veces el mismo archivo.",
    )

    tipo_entidad = models.CharField(
        max_length=20, choices=TipoEntidad.choices, default=TipoEntidad.PROVEEDOR
    )
    departamento = models.CharField(max_length=100, blank=True)
    proveedor = models.ForeignKey(
        Proveedor, null=True, blank=True, on_delete=models.SET_NULL, related_name="documentos"
    )
    source_path = models.CharField(
        max_length=1000, blank=True, help_text="Ruta original desde la que se importó (auditoría)."
    )

    source_type = models.CharField(max_length=20, choices=SourceType.choices, default=SourceType.UNKNOWN)
    page_count = models.PositiveIntegerField(default=1)
    needs_ocr = models.BooleanField(default=False)

    status = models.CharField(
        max_length=20, choices=IngestionStatus.choices, default=IngestionStatus.INGESTED
    )
    error_message = models.TextField(blank=True)

    raw_text_layer = models.TextField(
        blank=True, help_text="Capa de texto de PDFs digitales (vacío en escaneos/imágenes)."
    )
    ocr_text = models.TextField(
        blank=True, help_text="Texto por OCR de página completa (solo escaneos/imágenes)."
    )

    imported_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-imported_at"]

    def __str__(self) -> str:
        return f"{self.original_filename} ({self.get_source_type_display()})"

    def get_extracted_text(self) -> str:
        """Unified accessor: text layer for digital PDFs, OCR output for scans/images."""
        return self.ocr_text if self.needs_ocr else self.raw_text_layer

    @property
    def nombre_proveedor(self) -> str:
        """Supplier name to display: the (possibly corrected) extracted one, else the linked supplier's, else ''."""
        datos = getattr(self, "datos", None)
        if datos is not None and datos.proveedor_nombre:
            return datos.proveedor_nombre
        return self.proveedor.nombre if self.proveedor else ""


class InvoicePage(models.Model):
    """One cleaned page image, reserved for the (optional) preprocessing stage of scans."""

    document = models.ForeignKey(InvoiceDocument, related_name="pages", on_delete=models.CASCADE)
    page_number = models.PositiveIntegerField()
    processed_image = models.ImageField(upload_to=invoice_page_upload_path)
    width = models.PositiveIntegerField(default=0)
    height = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["document", "page_number"]
        unique_together = ("document", "page_number")

    def __str__(self) -> str:
        return f"{self.document.original_filename} - página {self.page_number}"


class ExtractedInvoiceData(models.Model):
    """
    Header and totals of one document, as extracted and then (optionally)
    corrected and confirmed by a person. `verificacion` keeps the engine's
    checks (which comparisons passed, per-albarán sums, warnings) so the
    review screen and the JSON export can show why a result is trusted.
    """

    document = models.OneToOneField(InvoiceDocument, related_name="datos", on_delete=models.CASCADE)

    numero_factura = models.CharField(max_length=100, blank=True)
    fecha_factura = models.DateField(null=True, blank=True)
    fecha_texto = models.CharField(max_length=30, blank=True, help_text="La fecha tal como está impresa.")
    proveedor_nombre = models.CharField(max_length=200, blank=True)
    proveedor_cif = models.CharField(max_length=20, blank=True)

    base_imponible = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True, help_text="Total sin IVA."
    )
    iva_total = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    total = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True, help_text="Total con IVA.")

    cuadra = models.BooleanField(default=False, help_text="Las líneas cuadran con el bloque de impuestos y el total.")
    metodo = models.CharField(max_length=40, blank=True)
    estado = models.CharField(max_length=10, choices=EstadoExtraccion.choices, default=EstadoExtraccion.REVISAR)
    verificacion = models.JSONField(default=dict, blank=True)
    motor_version = models.CharField(max_length=30, blank=True)

    confirmado = models.BooleanField(default=False, help_text="Revisada y aceptada por una persona.")
    corregido = models.BooleanField(default=False, help_text="Se modificó a mano algún campo tras la extracción.")

    creado = models.DateTimeField(auto_now_add=True)
    actualizado = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "datos de factura"
        verbose_name_plural = "datos de facturas"

    def __str__(self) -> str:
        return f"Datos de {self.document.original_filename}"


class InvoiceTaxBreakdown(models.Model):
    """One IVA rate of an invoice with its taxable base and tax amount (cuota)."""

    document = models.ForeignKey(InvoiceDocument, related_name="impuestos", on_delete=models.CASCADE)
    tipo = models.DecimalField(max_digits=5, decimal_places=2, help_text="Tipo de IVA en %.")
    base = models.DecimalField(max_digits=12, decimal_places=2)
    cuota = models.DecimalField(max_digits=12, decimal_places=2)

    class Meta:
        ordering = ["document", "-tipo"]
        verbose_name = "desglose de IVA"
        verbose_name_plural = "desgloses de IVA"

    def __str__(self) -> str:
        return f"{self.tipo}% sobre {self.base}"


class InvoiceLineItem(models.Model):
    """
    One product or service line. `codigo_articulo` (the supplier's article
    code) is the stable key for counting the same product across invoices;
    descriptions can be cryptic and vary.
    """

    document = models.ForeignKey(InvoiceDocument, related_name="lineas", on_delete=models.CASCADE)
    codigo_articulo = models.CharField(max_length=50, blank=True)
    descripcion = models.CharField(max_length=500, blank=True)
    cantidad = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True)
    precio_unitario = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    iva_porcentaje = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    importe = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    albaran = models.CharField(max_length=100, blank=True)
    albaran_fecha = models.DateField(null=True, blank=True)

    class Meta:
        ordering = ["document", "id"]
        verbose_name = "línea de factura"
        verbose_name_plural = "líneas de factura"

    def __str__(self) -> str:
        return f"{self.descripcion or self.codigo_articulo} ({self.document.original_filename})"
