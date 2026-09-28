"""
Runs the extraction for documents already imported and stores the result.

    python manage.py process_invoices                 every document not yet extracted
    python manage.py process_invoices --id 12         one document
    python manage.py process_invoices --filename H-111  documents whose name contains the text
    python manage.py process_invoices --reprocesar    also documents that already have data
    python manage.py process_invoices --forzar        also overwrite CONFIRMED data (careful)
    python manage.py process_invoices --id 12 --json  print the resulting JSON

Confirmed invoices are never overwritten unless --forzar is given.
"""

import json

from django.core.management.base import BaseCommand

from invoice_data.models import IngestionStatus, InvoiceDocument
from invoice_data.services.pipeline import DatosConfirmados, document_to_dict, process_document


class Command(BaseCommand):
    """Extracts invoices into the database, one line of output per document."""

    help = "Extrae los datos de las facturas importadas y los guarda en la base de datos."

    def add_arguments(self, parser):
        """Selection and behaviour options."""
        parser.add_argument("--id", type=int, help="Procesa solo el documento con este id.")
        parser.add_argument("--filename", type=str, help="Procesa los documentos cuyo nombre contenga este texto.")
        parser.add_argument("--reprocesar", action="store_true", help="Incluye documentos que ya tienen datos.")
        parser.add_argument("--forzar", action="store_true", help="Sobrescribe también los datos confirmados.")
        parser.add_argument("--json", action="store_true", help="Imprime el JSON resultante de cada documento.")

    def handle(self, *args, **options):
        """Selects documents, processes each one, isolates failures, prints a summary."""
        documents = InvoiceDocument.objects.exclude(status=IngestionStatus.ERROR).order_by("id")
        if options["id"]:
            documents = documents.filter(pk=options["id"])
        if options["filename"]:
            documents = documents.filter(original_filename__icontains=options["filename"])
        if not (options["id"] or options["filename"] or options["reprocesar"]):
            documents = documents.filter(datos__isnull=True)

        counts = {"ok": 0, "revisar": 0, "confirmadas_omitidas": 0, "errores": 0}
        total = documents.count()
        if not total:
            self.stdout.write(self.style.WARNING("No hay documentos que procesar."))
            return

        for document in documents:
            try:
                datos = process_document(document, force=options["forzar"])
            except DatosConfirmados:
                counts["confirmadas_omitidas"] += 1
                self.stdout.write(f"  omitida (confirmada): {document.original_filename}")
                continue
            except Exception as error:
                counts["errores"] += 1
                self.stdout.write(self.style.ERROR(f"  ERROR {document.original_filename}: {error}"))
                continue

            counts[datos.estado] += 1
            style = self.style.SUCCESS if datos.estado == "ok" else self.style.WARNING
            self.stdout.write(style(
                f"  {datos.estado.upper():<8} {document.original_filename[:40]:<40} "
                f"nº {datos.numero_factura or '-':<14} total {datos.total if datos.total is not None else '-'}"
            ))
            if options["json"]:
                self.stdout.write(json.dumps(document_to_dict(document), ensure_ascii=False, indent=2))

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(
            f"Listo. {counts['ok']} correctas, {counts['revisar']} a revisar, "
            f"{counts['confirmadas_omitidas']} confirmadas omitidas, {counts['errores']} con error."
        ))
