"""Django admin for Gestión de Facturas: inspect and, if needed, fix any stored record."""

from django.contrib import admin
from django.utils.html import format_html

from invoice_data.models import (
    ExtractedInvoiceData,
    InvoiceDocument,
    InvoiceLineItem,
    InvoicePage,
    InvoiceTaxBreakdown,
    Proveedor,
)


class DatosInline(admin.StackedInline):
    """The extracted header of a document, shown inside the document's page."""

    model = ExtractedInvoiceData
    extra = 0
    can_delete = True
    readonly_fields = ("verificacion", "motor_version", "creado", "actualizado")


class ImpuestosInline(admin.TabularInline):
    """IVA breakdown rows of a document."""

    model = InvoiceTaxBreakdown
    extra = 0


class LineasInline(admin.TabularInline):
    """Product lines of a document."""

    model = InvoiceLineItem
    extra = 0


class PaginasInline(admin.TabularInline):
    """Cleaned page images (reserved for the preprocessing stage), with a thumbnail."""

    model = InvoicePage
    extra = 0
    readonly_fields = ("page_number", "miniatura", "width", "height")
    fields = ("page_number", "miniatura", "width", "height")

    def miniatura(self, obj):
        """Small preview of the processed page."""
        if obj.processed_image:
            return format_html('<img src="{}" style="max-height:120px">', obj.processed_image.url)
        return "-"


@admin.register(Proveedor)
class ProveedorAdmin(admin.ModelAdmin):
    """Suppliers and creditors, keyed by CIF; the name can be corrected here."""

    list_display = ("nombre", "cif", "creado")
    search_fields = ("nombre", "cif")


@admin.register(InvoiceDocument)
class InvoiceDocumentAdmin(admin.ModelAdmin):
    """Every file that entered the pipeline, with its extraction underneath."""

    list_display = ("original_filename", "tipo_entidad", "departamento", "proveedor", "source_type", "status", "imported_at")
    list_filter = ("tipo_entidad", "departamento", "source_type", "status")
    search_fields = ("original_filename", "checksum", "proveedor__nombre", "proveedor__cif")
    readonly_fields = ("checksum", "imported_at", "raw_text_layer", "ocr_text")
    inlines = [DatosInline, ImpuestosInline, LineasInline, PaginasInline]


@admin.register(ExtractedInvoiceData)
class ExtractedInvoiceDataAdmin(admin.ModelAdmin):
    """Header and totals of every extracted invoice."""

    list_display = ("document", "numero_factura", "fecha_factura", "proveedor_nombre", "total", "cuadra", "estado", "confirmado")
    list_filter = ("estado", "cuadra", "confirmado", "corregido")
    search_fields = ("numero_factura", "proveedor_nombre", "proveedor_cif", "document__original_filename")


@admin.register(InvoiceLineItem)
class InvoiceLineItemAdmin(admin.ModelAdmin):
    """Product lines across all invoices (useful to count the same article over time)."""

    list_display = ("descripcion", "codigo_articulo", "document", "cantidad", "importe")
    search_fields = ("descripcion", "codigo_articulo")
