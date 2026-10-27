"""
Exporta las facturas APROBADAS como dataset de entrenamiento (afinado de Qwen2.5-VL).

    python manage.py invoice_agent_export_dataset dataset_facturas
    python manage.py invoice_agent_export_dataset dataset_facturas --include-pending
    python manage.py invoice_agent_export_dataset dataset_facturas --val 0.1 --test 0.15 --split-by supplier

    --split-by invoice   (por defecto) reparte factura a factura (las duplicadas van juntas)
    --split-by supplier  todas las facturas de un proveedor van al mismo conjunto: mide si el
                         modelo generaliza a proveedores que no ha visto
    --include-pending    añade las «Pendiente de aprobación»: etiquetas del LLM grande que pasaron
                         todas las comprobaciones, aún sin aprobar (label_source="teacher")

Formato y contenido en invoice_agent/services/dataset.py y training/README.md.
La carpeta contiene datos reales: está en .gitignore (dataset_*/) y no debe subirse a git.
"""

from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from invoice_agent.models import Empresa
from invoice_agent.services.dataset import export_dataset


class Command(BaseCommand):
    help = "Exporta las facturas aprobadas como dataset de entrenamiento (train/val) y de prueba (test)."

    def add_arguments(self, parser):
        parser.add_argument("folder", help="Carpeta de salida (p. ej. dataset_facturas)")
        parser.add_argument("--val", type=float, default=0.10, help="Fracción de validación (0-1).")
        parser.add_argument("--test", type=float, default=0.15, help="Fracción de prueba (0-1), nunca se entrena.")
        parser.add_argument("--split-by", choices=["invoice", "supplier"], default="invoice")
        parser.add_argument("--empresa", action="append", help="NIF de la empresa (repetible). Por defecto, todas.")
        parser.add_argument("--overwrite", action="store_true", help="Vaciar la carpeta si ya existe.")
        parser.add_argument("--include-pending", action="store_true",
                            help="Incluir las pendientes de aprobación (etiquetas del LLM grande sin revisar).")

    def handle(self, *args, **options):
        if not (0 <= options["val"] < 1 and 0 <= options["test"] < 1 and options["val"] + options["test"] < 1):
            raise CommandError("val y test deben ser fracciones entre 0 y 1 que sumen menos de 1.")
        empresa_ids = None
        if options["empresa"]:
            empresas = Empresa.objects.filter(nif__in=[n.strip().upper() for n in options["empresa"]])
            if empresas.count() != len(set(options["empresa"])):
                raise CommandError("Alguna empresa no existe.")
            empresa_ids = list(empresas.values_list("pk", flat=True))
        try:
            report = export_dataset(
                Path(options["folder"]),
                empresa_ids=empresa_ids,
                val=options["val"],
                test=options["test"],
                split_by=options["split_by"],
                overwrite=options["overwrite"],
                include_pending=options["include_pending"],
            )
        except FileExistsError as error:
            raise CommandError(str(error)) from error
        counts = report.counts
        self.stdout.write(
            self.style.SUCCESS(
                f"Dataset en {report.folder}: train {counts['train']} · val {counts['val']} · test {counts['test']}"
            )
        )
        sources = report.label_sources
        self.stdout.write(f"Etiquetas: {sources['human']} aprobadas por una persona · {sources['teacher']} del LLM grande")
        for item in report.skipped:
            self.stdout.write(self.style.WARNING(f"Omitida {item['id']}: {item['motivo']}"))
        if counts["train"] < 50:
            self.stdout.write(
                self.style.WARNING("Menos de 50 ejemplos de entrenamiento: el afinado apenas aprenderá; aprueba más facturas.")
            )
