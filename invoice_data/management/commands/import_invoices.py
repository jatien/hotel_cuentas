"""
Batch-imports invoices from a folder structured as:

    <source>/proveedores/<departamento>/archivo.pdf
    <source>/acreedores/archivo.pdf

Proveedores get their departamento from the immediate subfolder name under
proveedores/ (nesting deeper than one level still uses that first subfolder
name, not the file's direct parent - so proveedores/cocina/2026/factura.pdf
still resolves to departamento=cocina). Acreedores never get a departamento -
that's the whole point of the distinction, so nothing under acreedores/ is
inspected for folder structure at all, everything there is just ingested
with departamento="".

Either top-level folder can be absent (e.g. a batch that's all proveedores),
but at least one of them must exist.
"""

from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from invoice_data.models import TipoEntidad
from invoice_data.services.ingestion import (
    IMAGE_EXTENSIONS,
    PDF_EXTENSIONS,
    ingest_file,
)

ALLOWED_EXTENSIONS = IMAGE_EXTENSIONS | PDF_EXTENSIONS


class Command(BaseCommand):
    help = "Import invoices from a folder split into proveedores/<departamento>/ and acreedores/ subfolders."

    def add_arguments(self, parser):
        parser.add_argument(
            "source",
            type=str,
            help="Path to the folder containing proveedores/ and/or acreedores/ subfolders.",
        )

    def _collect_proveedores(self, proveedores_dir: Path) -> list[tuple[Path, str]]:
        """Returns (file_path, departamento) pairs for every file under proveedores/<departamento>/..."""
        results = []
        for path in sorted(proveedores_dir.rglob("*")):
            if not path.is_file() or path.suffix.lower() not in ALLOWED_EXTENSIONS:
                continue
            relative_parts = path.relative_to(proveedores_dir).parts
            # relative_parts[0] is always the departamento folder, regardless
            # of how deeply the actual file is nested underneath it.
            departamento = relative_parts[0] if len(relative_parts) > 1 else path.parent.name
            results.append((path, departamento))
        return results

    def _collect_acreedores(self, acreedores_dir: Path) -> list[Path]:
        """Returns every file under acreedores/, regardless of any subfolder structure - departamento never applies here."""
        return [
            path
            for path in sorted(acreedores_dir.rglob("*"))
            if path.is_file() and path.suffix.lower() in ALLOWED_EXTENSIONS
        ]

    def handle(self, *args, **options):
        source = Path(options["source"])
        if not source.exists() or not source.is_dir():
            raise CommandError(f"Source folder does not exist: {source}")

        proveedores_dir = source / "proveedores"
        acreedores_dir = source / "acreedores"

        if not proveedores_dir.exists() and not acreedores_dir.exists():
            raise CommandError(
                f"No se encontro ni 'proveedores' ni 'acreedores' dentro de {source}. "
                "La carpeta debe contener al menos una de las dos."
            )

        created_count = 0
        duplicate_count = 0
        error_count = 0

        if proveedores_dir.exists():
            for path, departamento in self._collect_proveedores(proveedores_dir):
                doc, created = ingest_file(path, departamento=departamento, tipo_entidad=TipoEntidad.PROVEEDOR)
                created_count, duplicate_count, error_count = self._report(
                    doc, created, path, created_count, duplicate_count, error_count
                )

        if acreedores_dir.exists():
            for path in self._collect_acreedores(acreedores_dir):
                doc, created = ingest_file(path, departamento="", tipo_entidad=TipoEntidad.ACREEDOR)
                created_count, duplicate_count, error_count = self._report(
                    doc, created, path, created_count, duplicate_count, error_count
                )

        self.stdout.write("")
        self.stdout.write(
            self.style.SUCCESS(
                f"Done. {created_count} ingested, {duplicate_count} duplicates skipped, "
                f"{error_count} errors."
            )
        )

    def _report(self, doc, created, path, created_count, duplicate_count, error_count):
        """Prints one line for this file's result and returns updated running counts."""
        if not created:
            duplicate_count += 1
            self.stdout.write(f"  skip (duplicate): {path.name}")
        elif doc.status == "error":
            error_count += 1
            self.stdout.write(self.style.ERROR(f"  error: {path.name} - {doc.error_message}"))
        else:
            created_count += 1
            label = "needs OCR" if doc.needs_ocr else "digital text layer OK"
            self.stdout.write(self.style.SUCCESS(f"  ingested: {path.name} [{doc.tipo_entidad}, {label}]"))
        return created_count, duplicate_count, error_count