"""
Admin registration for the invoice_data app. Lets us visually inspect
ingested documents, their preprocessed pages, extracted fields, and
line items - all without writing any queries.
"""

from django.contrib import admin
from django.utils.html import format_html

from invoice_data.models import (
    ExtractedInvoiceData,
    InvoiceDocument,
    InvoiceLineItem,
    InvoicePage,
)


class InvoicePageInline(admin.TabularInline):
    """Shows each preprocessed page image inline on the InvoiceDocument admin page."""

    model = InvoicePage
    extra = 0
    readonly_fields = ("page_number", "thumbnail", "width", "height")
    fields = ("page_number", "thumbnail", "width", "height")

    def thumbnail(self, obj):
        """Renders a small preview image so preprocessing quality can be checked visually."""
        if obj.processed_image:
            return format_html('<img src="{}" style="max-height: 150px;">', obj.processed_image.url)
        return "-"


class InvoiceLineItemInline(admin.TabularInline):
    """Shows each extracted line item inline on the InvoiceDocument admin page."""

    model = InvoiceLineItem
    extra = 0
    readonly_fields = ("codigo_articulo", "descripcion", "iva_porcentaje", "cantidad", "precio_unitario", "importe", "confirmado")
    fields = readonly_fields


@admin.register(InvoiceDocument)
class InvoiceDocumentAdmin(admin.ModelAdmin):
    """Admin list/detail view for every invoice that has entered the pipeline."""

    list_display = (
        "original_filename",
        "tipo_entidad",
        "departamento",
        "source_type",
        "page_count",
        "needs_ocr",
        "status",
        "imported_at",
    )
    list_filter = ("tipo_entidad", "departamento", "source_type", "status", "needs_ocr")
    search_fields = ("original_filename", "checksum", "source_path")
    readonly_fields = ("checksum", "imported_at", "raw_text_layer", "ocr_text")
    inlines = [InvoicePageInline, InvoiceLineItemInline]


@admin.register(InvoicePage)
class InvoicePageAdmin(admin.ModelAdmin):
    """Admin list view for individual preprocessed pages, useful for spot-checking."""

    list_display = ("document", "page_number", "width", "height", "created_at")


@admin.register(ExtractedInvoiceData)
class ExtractedInvoiceDataAdmin(admin.ModelAdmin):
    """Admin view for the structured header fields pulled out of each invoice."""

    list_display = (
        "document",
        "numero_factura",
        "fecha_factura",
        "total",
        "extraction_method",
        "confirmado",
        "status",
    )
    list_filter = ("status", "confirmado", "extraction_method")
    search_fields = ("numero_factura", "proveedor_cif")


@admin.register(InvoiceLineItem)
class InvoiceLineItemAdmin(admin.ModelAdmin):
    """Admin list/detail view for individual line items across all invoices."""

    list_display = ("descripcion", "document", "iva_porcentaje", "importe", "confirmado")
    list_filter = ("confirmado",)
    search_fields = ("descripcion", "codigo_articulo")