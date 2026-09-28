"""
Batch-imports invoices from a folder structured as:

    <source>/proveedores/<departamento>/archivo.pdf
    <source>/acreedores/archivo.pdf

Proveedores take their departamento from the first subfolder under
proveedores/ (however deeply the file is nested). Acreedores never get a
departamento. Either top-level folder may be absent, but one must exist.
Importing does not extract; run `process_invoices` afterwards.
"""

from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from invoice_data.models import TipoEntidad
from invoice_data.services.ingestion import IMAGE_EXTENSIONS, PDF_EXTENSIONS, ingest_file

ALLOWED_EXTENSIONS = IMAGE_EXTENSIONS | PDF_EXTENSIONS


class Command(BaseCommand):
    """Registers every invoice file found under proveedores/ and acreedores/."""

    help = "Importa facturas desde una carpeta con subcarpetas proveedores/<departamento>/ y acreedores/."

    def add_arguments(self, parser):
        """Only argument: the folder that contains proveedores/ and/or acreedores/."""
        parser.add_argument("source", type=str, help="Carpeta que contiene proveedores/ y/o acreedores/.")

    def _collect_proveedores(self, base: Path) -> list[tuple[Path, str]]:
        """(file, departamento) for every file under proveedores/<departamento>/..."""
        found = []
        for path in sorted(base.rglob("*")):
            if not path.is_file() or path.suffix.lower() not in ALLOWED_EXTENSIONS:
                continue
            parts = path.relative_to(base).parts
            found.append((path, parts[0] if len(parts) > 1 else path.parent.name))
        return found

    def _collect_acreedores(self, base: Path) -> list[Path]:
        """Every file under acreedores/, whatever its subfolders (a creditor has no department)."""
        return [p for p in sorted(base.rglob("*")) if p.is_file() and p.suffix.lower() in ALLOWED_EXTENSIONS]

    def _report(self, document, created, path, counts):
        """Prints one line for a file and updates the running counters."""
        if not created:
            counts["duplicadas"] += 1
            self.stdout.write(f"  omitida (duplicada): {path.name}")
        elif document.status == "error":
            counts["errores"] += 1
            self.stdout.write(self.style.ERROR(f"  error: {path.name} - {document.error_message}"))
        else:
            counts["importadas"] += 1
            self.stdout.write(self.style.SUCCESS(f"  importada: {path.name} [{document.tipo_entidad}]"))

    def handle(self, *args, **options):
        """Imports both branches and prints a summary."""
        source = Path(options["source"])
        if not source.is_dir():
            raise CommandError(f"No existe la carpeta: {source}")
        proveedores, acreedores = source / "proveedores", source / "acreedores"
        if not proveedores.exists() and not acreedores.exists():
            raise CommandError(f"No se encontró 'proveedores' ni 'acreedores' dentro de {source}.")

        counts = {"importadas": 0, "duplicadas": 0, "errores": 0}
        if proveedores.exists():
            for path, departamento in self._collect_proveedores(proveedores):
                document, created = ingest_file(path, departamento=departamento, tipo_entidad=TipoEntidad.PROVEEDOR)
                self._report(document, created, path, counts)
        if acreedores.exists():
            for path in self._collect_acreedores(acreedores):
                document, created = ingest_file(path, departamento="", tipo_entidad=TipoEntidad.ACREEDOR)
                self._report(document, created, path, counts)

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(
            f"Listo. {counts['importadas']} importadas, {counts['duplicadas']} duplicadas, {counts['errores']} con error."
        ))
