"""App configuration for invoice_data (Gestión de Facturas)."""

from django.apps import AppConfig


class InvoiceDataConfig(AppConfig):
    """Registers the invoice_data app: invoice ingestion, extraction, review and export."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "invoice_data"
    verbose_name = "Gestión de Facturas"
