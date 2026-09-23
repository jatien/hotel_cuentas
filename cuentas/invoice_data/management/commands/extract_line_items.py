"""Batch-runs line-item extraction over every InvoiceDocument, printing each item's fields."""

from django.core.management.base import BaseCommand

from invoice_data.models import InvoiceDocument
from invoice_data.services.line_items import extract_line_items


class Command(BaseCommand):
    help = "Extract line items (product, IVA, quantity, price, amount) for every InvoiceDocument."

    def handle(self, *args, **options):
        total_matched = 0
        total_skipped = 0

        for document in InvoiceDocument.objects.all():
            items = extract_line_items(document)
            if not items:
                total_skipped += 1
                continue

            total_matched += 1
            self.stdout.write(self.style.SUCCESS(f"\n{document.original_filename}: {len(items)} linea(s)"))
            for item in items:
                iva_display = f"{item.iva_porcentaje}%" if item.iva_porcentaje is not None else "N/A"
                self.stdout.write(
                    f"  - {item.descripcion} | IVA {iva_display} | "
                    f"cant. {item.cantidad} x {item.precio_unitario} = {item.importe}"
                )

        self.stdout.write("")
        self.stdout.write(
            self.style.SUCCESS(
                f"Listo. {total_matched} documento(s) con lineas extraidas, "
                f"{total_skipped} sin ninguna linea reconocida."
            )
        )