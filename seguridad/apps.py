from django.apps import AppConfig


class SeguridadConfig(AppConfig):
    """Piezas transversales de seguridad: informes CSP, health check, comprobaciones de despliegue."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "seguridad"
    verbose_name = "Seguridad"
