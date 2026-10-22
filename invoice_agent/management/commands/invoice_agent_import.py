"""
Importa una carpeta de facturas (PDF o imágenes) a Facturas con IA, como si se subieran a mano.

    python manage.py invoice_agent_import "C:\\facturas\\2026" --empresa B07794803
    python manage.py invoice_agent_import carpeta --empresa B07794803 --recursive

Usa la misma validación que la subida web (tipo real del fichero, tamaño, páginas, duplicados
por SHA-256). Los ficheros quedan en cola: arranca el worker para extraerlos y después
revísalos y apruébalos en /facturas-ia/. Las facturas aprobadas son las que se exportan
para entrenar (invoice_agent_export_dataset).
"""

from pathlib import Path

from django.core.files import File
from django.core.management.base import BaseCommand, CommandError

from invoice_agent.models import Empresa
from invoice_agent.services.access import system_context
from invoice_agent.services.errors import ServiceError
from invoice_agent.services.ingestion import EXTENSIONS, ingest_upload


class Command(BaseCommand):
    help = "Importa todas las facturas de una carpeta a Facturas con IA y las pone en cola."

    def add_arguments(self, parser):
        parser.add_argument("folder")
        parser.add_argument("--empresa", required=True, help="NIF de la empresa que recibe las facturas.")
        parser.add_argument("--recursive", action="store_true", help="Incluir subcarpetas.")

    def handle(self, *args, **options):
        folder = Path(options["folder"])
        if not folder.is_dir():
            raise CommandError(f"No existe la carpeta {folder}")
        empresa = Empresa.objects.filter(nif=options["empresa"].strip().upper(), activa=True).first()
        if empresa is None:
            raise CommandError("Empresa no encontrada (créala en el admin o revisa el NIF).")
        ctx = system_context()
        pattern = "**/*" if options["recursive"] else "*"
        files = sorted(p for p in folder.glob(pattern) if p.is_file() and p.suffix.lower() in EXTENSIONS)
        new = repeated = failed = 0
        for path in files:
            with open(path, "rb") as handle:
                try:
                    _, created = ingest_upload(File(handle, name=path.name), empresa.pk, ctx)
                except ServiceError as error:
                    failed += 1
                    self.stdout.write(self.style.WARNING(f"{path.name}: {error.message}"))
                    continue
            new += created
            repeated += not created
        self.stdout.write(
            self.style.SUCCESS(f"{new} en cola · {repeated} ya estaban · {failed} rechazadas (de {len(files)} ficheros)")
        )
