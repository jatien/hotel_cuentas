from django.apps import AppConfig


class CajasConfig(AppConfig):
    """Cajas diarias del hotel: ventas por punto de venta (bar, comedor, chill out) y otros cobros."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "cajas"
    verbose_name = "Cajas"
