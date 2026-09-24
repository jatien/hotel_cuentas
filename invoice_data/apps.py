from django.apps import AppConfig


class InvoiceDataConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "invoice_data"
    verbose_name = "Invoice Data (OCR pipeline)"