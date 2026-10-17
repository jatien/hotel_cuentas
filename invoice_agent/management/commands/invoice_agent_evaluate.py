"""
Evaluación de la extracción contra una verdad revisada a mano.

    # 1) Generar los PDFs del juego de demostración (facturas ficticias) en una carpeta:
    python manage.py invoice_agent_evaluate --build-demo eval_demo
    # 2) Evaluar con el proveedor configurado (OpenAI o servidor local):
    python manage.py invoice_agent_evaluate eval_demo --output informe.json
    # Comprobar la tubería sin modelo (debe dar 100 %):
    python manage.py invoice_agent_evaluate eval_demo --oracle

Para evaluar con facturas reales, crea una carpeta FUERA del repositorio con
facturas anonimizadas y su <caso>.json (formato en evaluation/runner.py).
Los PDF y los informes no se suben a git.
"""

import json
import shutil
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from reportlab.pdfgen import canvas

from invoice_agent.evaluation.runner import OracleProvider, evaluate_folder
from invoice_agent.providers import get_provider

DEMO_DIR = Path(__file__).resolve().parents[2] / "evaluation" / "dataset"


def build_demo(target: Path) -> int:
    target.mkdir(parents=True, exist_ok=True)
    count = 0
    for truth_path in sorted(DEMO_DIR.glob("*.json")):
        truth = json.loads(truth_path.read_text(encoding="utf-8"))
        pdf = canvas.Canvas(str(target / f"{truth_path.stem}.pdf"), invariant=1)
        y = 800
        for line in truth.get("render", []):
            pdf.drawString(50, y, line)
            y -= 18
        pdf.save()
        shutil.copy(truth_path, target / truth_path.name)
        count += 1
    return count


class Command(BaseCommand):
    help = "Mide la calidad de la extracción (campos, conciliación, duplicados, revisión, coste y latencia)."

    def add_arguments(self, parser):
        parser.add_argument("folder", nargs="?", help="Carpeta con <caso>.pdf/.png y <caso>.json")
        parser.add_argument("--build-demo", metavar="CARPETA", help="Genera el juego de demostración ficticio y termina.")
        parser.add_argument("--oracle", action="store_true", help="Usa la verdad como respuesta (prueba de la tubería).")
        parser.add_argument("--own-nif", default="", help="NIF de la empresa receptora para las comprobaciones.")
        parser.add_argument("--output", help="Guarda el informe JSON en este fichero.")

    def handle(self, *args, **options):
        if options["build_demo"]:
            count = build_demo(Path(options["build_demo"]))
            self.stdout.write(self.style.SUCCESS(f"{count} casos generados en {options['build_demo']}"))
            return
        if not options["folder"]:
            raise CommandError("Indica la carpeta de casos (o usa --build-demo).")
        folder = Path(options["folder"])
        if not folder.is_dir():
            raise CommandError(f"No existe la carpeta {folder}")
        provider = OracleProvider() if options["oracle"] else get_provider()
        report = evaluate_folder(folder, provider, own_nif=options["own_nif"])
        text = json.dumps(report, ensure_ascii=False, indent=2)
        if options["output"]:
            Path(options["output"]).write_text(text, encoding="utf-8")
        self.stdout.write(text)
