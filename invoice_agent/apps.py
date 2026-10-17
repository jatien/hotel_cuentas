"""Configuración de la app invoice_agent (Detección de facturas con IA)."""

from django.apps import AppConfig


class InvoiceAgentConfig(AppConfig):
    """Subida, extracción con IA, validación determinista y revisión humana de facturas de proveedores."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "invoice_agent"
    verbose_name = "Detección de facturas con IA"
