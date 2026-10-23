"""
Mide un modelo (el configurado en COPILOT_* del .env) sobre el test del dataset del copilot.

    python manage.py copilot_evaluar --dataset dataset_copilot/test.jsonl --salida eval_qwen35_base.json
    python manage.py copilot_evaluar --dataset dataset_copilot/test.jsonl --limite 100     # prueba rápida

No usa la base de datos: los resultados de las herramientas van guardados en el dataset. Sirve para
comparar el modelo base con el afinado (o con razonamiento activado/desactivado) con las mismas preguntas.
"""

import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from copilot_pms.entrenamiento.evaluacion import evaluar
from copilot_pms.entrenamiento.exportar import huella_herramientas
from copilot_pms.llm import get_provider
from copilot_pms.tools.base import Contexto, disponibles


class Command(BaseCommand):
    help = "Evalúa el modelo del copilot con el test del dataset (llamadas, argumentos y cifras inventadas)."

    def add_arguments(self, parser):
        parser.add_argument("--dataset", required=True, help="test.jsonl generado por copilot_dataset")
        parser.add_argument("--limite", type=int, default=None)
        parser.add_argument("--salida", default="", help="Fichero JSON con el informe completo.")

    def handle(self, *args, dataset, limite, salida, **opciones):
        ruta = Path(dataset)
        if not ruta.exists():
            raise CommandError(f"No existe {ruta}")
        ejemplos = [json.loads(linea) for linea in ruta.read_text(encoding="utf-8").splitlines() if linea.strip()]
        manifest = ruta.parent / "manifest.json"
        hoy = "2026-10-15"
        if manifest.exists():
            datos = json.loads(manifest.read_text(encoding="utf-8"))
            hoy = datos.get("hoy", hoy)
            actual = huella_herramientas([h.esquema() for h in disponibles(Contexto(hoy=None))])
            if datos.get("huella_herramientas") != actual:
                self.stdout.write(self.style.WARNING(
                    "Las herramientas han cambiado desde que se generó el dataset: regenéralo para medir lo actual."))
        proveedor = get_provider()
        total = min(limite or len(ejemplos), len(ejemplos))
        self.stdout.write(f"Evaluando {proveedor.model} con {total} conversaciones…")
        informe = evaluar(proveedor, ejemplos, hoy, limite,
                          al_avanzar=lambda n: self.stdout.write(f"  {n}/{total}") if n % 25 == 0 else None)
        informe["modelo"] = proveedor.model
        for clave, valor in informe["total"].items():
            self.stdout.write(f"{clave:34} {valor}")
        if salida:
            Path(salida).write_text(json.dumps(informe, ensure_ascii=False, indent=2), encoding="utf-8")
            self.stdout.write(f"Informe completo en {salida}")
