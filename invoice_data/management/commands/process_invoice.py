"""
Runs the full pipeline (OCR if needed, header extraction, line items) for
ONE InvoiceDocument, selected by --id or --filename, and prints everything
found. This is the terminal equivalent of the three buttons on the web
detail page - useful for debugging a single invoice without touching
every document in the database via the batch commands.
"""

from django.core.management.base import BaseCommand, CommandError

from invoice_data.models import InvoiceDocument
from invoice_data.services.extraction import extract_document
from invoice_data.services.line_items import extract_line_items
from invoice_data.services.ocr import run_ocr


class Command(BaseCommand):
    help = "Run OCR + header extraction + line-item extraction for a single InvoiceDocument."

    def add_arguments(self, parser):
        parser.add_argument("--id", type=int, help="InvoiceDocument primary key.")
        parser.add_argument(
            "--filename", type=str, help="Match by original_filename (partial, case-insensitive)."
        )

    def handle(self, *args, **options):
        if options["id"]:
            document = InvoiceDocument.objects.filter(pk=options["id"]).first()
        elif options["filename"]:
            document = InvoiceDocument.objects.filter(
                original_filename__icontains=options["filename"]
            ).first()
        else:
            raise CommandError("Especifica --id <numero> o --filename <texto>.")

        if document is None:
            raise CommandError("No se ha encontrado ningun documento con ese criterio.")

        self.stdout.write(self.style.SUCCESS(f"Procesando: {document.original_filename}"))

        if document.needs_ocr:
            self.stdout.write("Ejecutando OCR...")
            run_ocr(document)

        extracted = extract_document(document)
        self.stdout.write("")
        self.stdout.write("DATOS DE CABECERA:")
        self.stdout.write(f"  numero_factura : {extracted.numero_factura}")
        self.stdout.write(f"  fecha_factura  : {extracted.fecha_factura}")
        self.stdout.write(f"  proveedor_cif  : {extracted.proveedor_cif}")
        self.stdout.write(f"  base_imponible : {extracted.base_imponible}")
        self.stdout.write(f"  iva_porcentaje : {extracted.iva_porcentaje}")
        self.stdout.write(f"  total          : {extracted.total}")
        self.stdout.write(f"  metodo         : {extracted.extraction_method}")
        self.stdout.write(f"  estado         : {extracted.get_status_display()}")

        items = extract_line_items(document)
        self.stdout.write("")
        self.stdout.write(f"LINEAS DE PRODUCTO ({len(items)}):")
        for item in items:
            iva_display = f"{item.iva_porcentaje}%" if item.iva_porcentaje is not None else "N/A"
            self.stdout.write(f"  - {item.descripcion} | IVA {iva_display} | importe {item.importe}")