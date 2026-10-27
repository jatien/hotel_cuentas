"""
Ciclo completo para entrenar el modelo local de facturas con un LLM grande como «profesor».

    python manage.py invoice_agent_pipeline ~/factura-ia/drive --empresa B07794803 \\
        --labels ~/factura-ia/drive/Invoices_json --dataset ~/factura-ia/dataset_facturas

Pasos (todos se pueden repetir: lo ya hecho no se vuelve a hacer ni a pagar):
  1. Importa los PDF/imágenes de la carpeta (subcarpetas incluidas). Los que ya estaban (mismo
     SHA-256) se saltan. Los que tienen <nombre>.json en --labels se cargan con ese JSON, también
     los que estaban en cola de ejecuciones anteriores. Con --retry-failed, los que quedaron en
     «Error» (p. ej. faltaba Tesseract) vuelven a la cola antes.
  2. Los que no tienen JSON los etiqueta el profesor (INVOICE_AGENT_TEACHER_*; si no está
     definido, el modelo normal). --max-extract limita cuántos en esta ejecución (coste).
  3. Escribe en --labels el JSON de cada factura nueva etiquetada y de las corregidas por una
     persona, así la carpeta PDF + JSON (p. ej. Google Drive) queda completa.
  4. Exporta el dataset: aprobadas + pendientes de aprobación (las que pasaron todas las
     comprobaciones). Las de «Necesita revisión» esperan a que las corrijas en /facturas-ia/.

Después: entrenar en el PC de la GPU (training/local, ver training/README.md).
"""

from pathlib import Path

from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Count

from invoice_agent.models import Borrador, Documento, Empresa, EstadoDocumento
from invoice_agent.providers import get_teacher_provider
from invoice_agent.providers.label import LabelProvider
from invoice_agent.schemas import ReprocessIn
from invoice_agent.services import review, worker
from invoice_agent.services.access import system_context
from invoice_agent.services.errors import ServiceError
from invoice_agent.services.dataset import TRAINING_CONTEXT_CODES, export_dataset, trainable
from invoice_agent.services.labels import export_labels


class Command(BaseCommand):
    help = "Importa facturas + JSON, etiqueta las nuevas con el LLM grande y exporta el dataset de entrenamiento."

    def add_arguments(self, parser):
        parser.add_argument("folder", help="Carpeta con los PDF/imágenes (se recorre con subcarpetas).")
        parser.add_argument("--empresa", required=True, help="NIF de la empresa que recibe las facturas.")
        parser.add_argument("--labels", required=True,
                            help="Carpeta de los <nombre>.json (se leen y se escriben los nuevos).")
        parser.add_argument("--dataset", help="Carpeta del dataset a generar (se sustituye). Sin ella, no se exporta.")
        parser.add_argument("--max-extract", type=int, default=None,
                            help="Máximo de facturas a etiquetar con el profesor en esta ejecución.")
        parser.add_argument("--no-extract", action="store_true", help="No llamar al profesor (solo importar y exportar).")
        parser.add_argument("--retry-failed", action="store_true",
                            help="Volver a poner en cola las facturas en «Error» (tras instalar Tesseract, p. ej.).")
        parser.add_argument("--only-approved", action="store_true",
                            help="Exportar solo las aprobadas por una persona (sin etiquetas del profesor sin revisar).")
        parser.add_argument("--val", type=float, default=0.10)
        parser.add_argument("--test", type=float, default=0.15)
        parser.add_argument("--split-by", choices=["invoice", "supplier"], default="invoice")

    def handle(self, *args, **options):
        folder, labels = Path(options["folder"]).expanduser(), Path(options["labels"]).expanduser()
        if not folder.is_dir():
            raise CommandError(f"No existe la carpeta {folder}")
        labels.mkdir(parents=True, exist_ok=True)
        if not Empresa.objects.filter(nif=options["empresa"].strip().upper(), activa=True).exists():
            raise CommandError("Empresa no encontrada (créala en el admin o revisa el NIF).")

        self.stdout.write(self.style.MIGRATE_HEADING("1. Importar facturas y JSON"))
        if options["retry_failed"]:
            self.retry_failed()
        call_command("invoice_agent_import", str(folder), empresa=options["empresa"], recursive=True,
                     labels=str(labels), stdout=self.stdout, stderr=self.stderr)
        self.apply_labels(labels)

        self.stdout.write(self.style.MIGRATE_HEADING("2. Etiquetar con el profesor las que no tienen JSON"))
        if options["no_extract"]:
            self.stdout.write("Omitido (--no-extract).")
        else:
            self.extract(options["max_extract"])

        self.stdout.write(self.style.MIGRATE_HEADING("3. Guardar los JSON nuevos junto a los PDF"))
        report = export_labels(labels)
        self.stdout.write(f"{len(report.written)} nuevos · {len(report.replaced)} sustituidos por la versión "
                          f"corregida · {report.kept} sin cambios (en {labels})")

        if options["dataset"]:
            self.stdout.write(self.style.MIGRATE_HEADING("4. Exportar el dataset"))
            exported = export_dataset(
                Path(options["dataset"]).expanduser(),
                val=options["val"], test=options["test"], split_by=options["split_by"],
                overwrite=True, include_pending=not options["only_approved"],
            )
            counts, sources = exported.counts, exported.label_sources
            self.stdout.write(self.style.SUCCESS(
                f"train {counts['train']} · val {counts['val']} · test {counts['test']} "
                f"({sources['human']} aprobadas · {sources['teacher']} del profesor sin revisar)"
            ))
            for item in exported.skipped:
                self.stdout.write(self.style.WARNING(f"Omitida {item['id']}: {item['motivo']}"))
        self.summary()

    def retry_failed(self) -> None:
        ctx = system_context()
        retried = 0
        for documento in Documento.objects.filter(estado=EstadoDocumento.FAILED):
            try:
                review.request_reprocess(ctx, documento, ReprocessIn(comentario="invoice_agent_pipeline --retry-failed"))
                retried += 1
            except ServiceError as error:
                self.stdout.write(self.style.WARNING(f"{documento.original_filename}: {error.message}"))
        self.stdout.write(f"{retried} facturas con error vuelven a la cola.")

    def apply_labels(self, labels: Path) -> None:
        """Facturas en cola que ya tienen su JSON (de una ejecución anterior o reintentadas): se cargan con él."""
        applied = 0
        for documento in Documento.objects.filter(estado=EstadoDocumento.QUEUED).order_by("pk"):
            label = labels / f"{Path(documento.original_filename).stem}.json"
            if not label.is_file():
                continue
            claimed = worker.claim_next("pipeline-etiquetas", documento_id=documento.pk)
            if claimed is None:  # p. ej. reintento programado más tarde
                continue
            worker.run_claimed(claimed, provider=LabelProvider(label.read_text(encoding="utf-8")))
            documento.refresh_from_db()
            applied += 1
            if documento.estado == EstadoDocumento.FAILED or documento.estado == EstadoDocumento.QUEUED:
                self.stdout.write(self.style.WARNING(
                    f"{documento.original_filename}: no se pudo cargar con su JSON ({documento.error_code}: "
                    f"{documento.error_message})"
                ))
        if applied:
            self.stdout.write(f"{applied} facturas en cola cargadas con su JSON.")

    def extract(self, limit: int | None) -> None:
        teacher = get_teacher_provider()
        pending = Documento.objects.filter(estado=EstadoDocumento.QUEUED).count()
        self.stdout.write(f"Profesor: {teacher.model} · {pending} en cola"
                          + (f" · máximo {limit} en esta ejecución" if limit is not None else ""))
        done = 0
        while limit is None or done < limit:
            claimed = worker.claim_next("pipeline-profesor")
            if claimed is None:
                break
            worker.run_claimed(claimed, provider=teacher)
            done += 1
            if done % 10 == 0:
                self.stdout.write(f"  {done} etiquetadas…")
        self.stdout.write(f"{done} facturas enviadas al profesor.")

    def summary(self) -> None:
        by_state = dict(Documento.objects.values_list("estado").annotate(n=Count("pk")))
        names = dict(EstadoDocumento.choices)
        self.stdout.write(self.style.MIGRATE_HEADING("Estado de todas las facturas"))
        for state, label in names.items():
            if by_state.get(state):
                self.stdout.write(f"  {label}: {by_state[state]}")
        if by_state.get(EstadoDocumento.NEEDS_REVIEW):
            review = Borrador.objects.filter(documento__estado=EstadoDocumento.NEEDS_REVIEW).select_related("documento")
            context_only = sum(1 for b in review if trainable(b))
            blocked = by_state[EstadoDocumento.NEEDS_REVIEW] - context_only
            if context_only:
                self.stdout.write(
                    f"De las de «Necesita revisión», {context_only} solo tienen avisos de contexto "
                    f"({', '.join(sorted(TRAINING_CONTEXT_CODES))}): sí se entrenan."
                )
            if blocked:
                self.stdout.write(self.style.WARNING(
                    f"{blocked} no se entrenan: alguna comprobación indica un posible error de lectura. "
                    "Corrígelas en /facturas-ia/ y vuelve a ejecutar este comando."
                ))
        if by_state.get(EstadoDocumento.FAILED):
            self.stdout.write(self.style.WARNING(
                "Las de «Error» se reintentan con --retry-failed (pipeline.sh: RETRY_FAILED=1) o desde /facturas-ia/."))
