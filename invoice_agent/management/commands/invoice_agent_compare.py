"""
Compara dos informes de invoice_agent_evaluate (modelo base y afinado, mismas facturas de prueba).

    python manage.py invoice_agent_compare base.json afinado.json --html informe.html --json comparacion.json

Imprime el resumen en la terminal y, con --html, escribe un informe que se abre en el navegador:
aciertos por campo, facturas que mejoran o empeoran y cada discrepancia con el JSON de referencia.
No escribe en la base de datos ni en Drive.
"""

import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from invoice_agent.evaluation.compare import compare, render_html, render_text


class Command(BaseCommand):
    help = "Compara el informe del modelo base con el del afinado (mismas facturas de prueba)."

    def add_arguments(self, parser):
        parser.add_argument("base", help="Informe JSON del modelo base (invoice_agent_evaluate --output)")
        parser.add_argument("trained", help="Informe JSON del modelo afinado")
        parser.add_argument("--html", help="Escribe el informe completo en este fichero HTML.")
        parser.add_argument("--json", help="Escribe la comparación en este fichero JSON.")
        parser.add_argument("--base-label", help="Nombre del modelo base en el informe.")
        parser.add_argument("--trained-label", help="Nombre del modelo afinado en el informe (p. ej. la ejecución).")

    def handle(self, *args, **options):
        reports = []
        for key in ("base", "trained"):
            path = Path(options[key])
            if not path.is_file():
                raise CommandError(f"No existe {path}")
            reports.append(json.loads(path.read_text(encoding="utf-8")))
        result = compare(*reports, base_label=options["base_label"], trained_label=options["trained_label"])
        if not result["documents"]:
            raise CommandError("Los dos informes no tienen facturas en común: ¿son del mismo conjunto de prueba?")
        self.stdout.write(render_text(result))
        if options["html"]:
            Path(options["html"]).write_text(render_html(result), encoding="utf-8")
            self.stdout.write(self.style.SUCCESS(f"Informe: {options['html']}"))
        if options["json"]:
            Path(options["json"]).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
