"""Batch-runs field extraction over every InvoiceDocument that has extractable text but no ExtractedInvoiceData yet."""

from django.core.management.base import BaseCommand

from invoice_data.models import InvoiceDocument
from invoice_data.services.extraction import extract_document


class Command(BaseCommand):
    help = "Run field extraction on every InvoiceDocument with extractable text that hasn't been extracted yet."

    def add_arguments(self, parser):
        parser.add_argument(
            "--reprocess",
            action="store_true",
            help="Re-run extraction even for documents that already have ExtractedInvoiceData.",
        )

    def handle(self, *args, **options):
        queryset = InvoiceDocument.objects.all()
        if not options["reprocess"]:
            queryset = queryset.filter(extracted_data__isnull=True)

        if not queryset.exists():
            self.stdout.write(self.style.WARNING("No hay documentos pendientes de extraccion."))
            return

        for document in queryset:
            result = extract_document(document)
            self.stdout.write(self.style.SUCCESS(f"\n{document.original_filename}:"))
            self.stdout.write(f"  numero_factura : {result.numero_factura}")
            self.stdout.write(f"  fecha_factura  : {result.fecha_factura}")
            self.stdout.write(f"  proveedor_cif  : {result.proveedor_cif}")
            self.stdout.write(f"  base_imponible : {result.base_imponible}")
            self.stdout.write(f"  iva_porcentaje : {result.iva_porcentaje}")
            self.stdout.write(f"  total          : {result.total}")
            self.stdout.write(f"  estado         : {result.get_status_display()}")
            self.stdout.write(f"  metodo         : {result.extraction_method}")