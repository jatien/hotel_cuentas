from django.apps import AppConfig


class AnalisisConfig(AppConfig):
    """Análisis de la facturación: reservas, compras a proveedores, acreedores y nóminas."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "analisis"
    verbose_name = "Análisis de facturación"
