"""
Política de retención de originales.

    python manage.py invoice_agent_retention            # muestra qué se borraría
    python manage.py invoice_agent_retention --apply    # borra los ficheros originales caducados

Solo borra el FICHERO original de documentos aprobados o rechazados cuya
fecha borrar_despues_de ya pasó (RETENTION_DAYS). Los datos extraídos, el
borrador y la auditoría se conservan. Antes de activarlo, comprueba los
plazos legales de conservación de facturas que te apliquen.
"""

from django.core.management.base import BaseCommand
from django.utils import timezone

from invoice_agent.models import (
    AccionRevision,
    Documento,
    EstadoDocumento,
    EventoRevision,
)
from invoice_agent.services.storage import private_storage


class Command(BaseCommand):
    help = "Borra los originales caducados según la política de retención (por defecto, simulación)."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="Borrar de verdad (sin esto solo lista).")

    def handle(self, *args, **options):
        storage = private_storage()
        candidates = Documento.objects.filter(
            borrar_despues_de__lte=timezone.localdate(),
            estado__in=[EstadoDocumento.APPROVED, EstadoDocumento.REJECTED],
        ).exclude(storage_key="")
        count = 0
        for documento in candidates:
            count += 1
            self.stdout.write(f"{documento.public_id} {documento.original_filename}")
            if options["apply"]:
                storage.delete(documento.storage_key)
                documento.storage_key = ""
                documento.save(update_fields=["storage_key"])
                EventoRevision.objects.create(
                    documento=documento,
                    accion=AccionRevision.FILE_ACCESS,
                    comentario="Original borrado por la política de retención",
                    actor_label="retención",
                )
        verb = "Borrados" if options["apply"] else "Se borrarían"
        self.stdout.write(self.style.SUCCESS(f"{verb}: {count}"))
