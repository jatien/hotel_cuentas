"""
Worker de la cola de Facturas con IA.

    python manage.py invoice_agent_worker            # bucle continuo (Ctrl+C para parar)
    python manage.py invoice_agent_worker --once     # procesa lo pendiente y termina

Se pueden lanzar varios en paralelo (otra terminal u otro PC de la red): no
cogen el mismo trabajo y, entre todos, no pasan de MAX_CONCURRENT_JOBS.
"""

import time

from django.core.management.base import BaseCommand

from invoice_agent.services.worker import default_worker_id, run_once


class Command(BaseCommand):
    help = "Procesa los documentos en cola de Facturas con IA (preparación, extracción y validación)."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true", help="Procesa lo pendiente y termina.")
        parser.add_argument("--poll", type=float, default=3.0, help="Segundos de espera cuando no hay trabajo.")
        parser.add_argument("--max-jobs", type=int, default=0, help="Termina tras N trabajos (0 = sin límite).")

    def handle(self, *args, **options):
        worker_id = default_worker_id()
        processed = 0
        self.stdout.write(f"Worker {worker_id} en marcha.")
        try:
            while True:
                if run_once(worker_id):
                    processed += 1
                    if options["max_jobs"] and processed >= options["max_jobs"]:
                        break
                    continue
                if options["once"]:
                    break
                time.sleep(options["poll"])
        except KeyboardInterrupt:
            self.stdout.write("Parado.")
        self.stdout.write(self.style.SUCCESS(f"Trabajos procesados: {processed}"))
