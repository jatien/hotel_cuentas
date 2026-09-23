from django.core.management.base import BaseCommand

from invoice_data.models import IngestionStatus, InvoiceDocument
from invoice_data.services.ocr import run_ocr


class Command(BaseCommand):
    help = "Run OCR on every InvoiceDocument that needs it and hasn't been OCR'd yet."

    def add_arguments(self, parser):
        parser.add_argument(
            "--reprocess",
            action="store_true",
            help="Re-run OCR even for documents already marked as ocr_done.",
        )

    def handle(self, *args, **options):
        queryset = InvoiceDocument.objects.filter(needs_ocr=True)
        if not options["reprocess"]:
            queryset = queryset.exclude(status=IngestionStatus.OCR_DONE)

        if not queryset.exists():
            self.stdout.write(self.style.WARNING("No hay documentos pendientes de OCR."))
            return

        done_count = 0
        error_count = 0

        for document in queryset:
            try:
                text = run_ocr(document)
                done_count += 1
                preview = text.strip().replace("\n", " ")[:80]
                self.stdout.write(self.style.SUCCESS(f"  OCR ok: {document.original_filename} -> \"{preview}...\""))
            except Exception as exc:
                error_count += 1
                document.status = IngestionStatus.ERROR
                document.error_message = f"Error en OCR: {exc}"
                document.save(update_fields=["status", "error_message"])
                self.stdout.write(self.style.ERROR(f"  error: {document.original_filename} - {exc}"))

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(f"Listo. {done_count} completados, {error_count} errores."))