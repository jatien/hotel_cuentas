"""
Exporta las conversaciones generadas a JSONL (formato TRL: {"messages", "tools", "meta"}).

Reparto train / val / test:
- En cada escenario con 4 o más redacciones distintas se reserva una redacción
  entera para test: mide si el modelo entiende preguntas formuladas de una
  forma que nunca vio.
- Del resto, un 5 % aleatorio va a test y un 6 % a validación.

El manifest.json guarda versión del prompt, huella de los esquemas de las
herramientas, recuento por escenario y licencias: si cambian el prompt o las
herramientas, hay que regenerar el dataset y volver a entrenar.
"""

import hashlib
import json
import random
from collections import Counter, defaultdict
from pathlib import Path

from copilot_pms import prompts


def huella_herramientas(tools: list) -> str:
    return hashlib.sha256(json.dumps(tools, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]


def repartir(ejemplos: list, semilla: int = 1) -> dict:
    rnd = random.Random(semilla)
    plantillas = defaultdict(set)
    for e in ejemplos:
        plantillas[e["meta"]["escenario"]].add(e["meta"]["plantilla"])
    reservada = {esc: min(ps, key=lambda p: hashlib.md5(f"{esc}:{p}".encode()).hexdigest())
                 for esc, ps in plantillas.items() if len(ps) >= 4}
    partes = {"train": [], "val": [], "test": []}
    for e in ejemplos:
        esc, pl = e["meta"]["escenario"], e["meta"]["plantilla"]
        if reservada.get(esc) == pl:
            e["meta"]["redaccion_nueva"] = True
            partes["test"].append(e)
            continue
        x = rnd.random()
        partes["test" if x < 0.05 else "val" if x < 0.11 else "train"].append(e)
    return partes


def escribir(ejemplos: list, carpeta: Path, *, semilla: int, hoy, extra: dict | None = None) -> dict:
    carpeta.mkdir(parents=True, exist_ok=True)
    partes = repartir(ejemplos, semilla)
    for nombre, filas in partes.items():
        with open(carpeta / f"{nombre}.jsonl", "w", encoding="utf-8") as f:
            for e in filas:
                f.write(json.dumps(e, ensure_ascii=False, default=str) + "\n")
    tools = ejemplos[0]["tools"] if ejemplos else []
    manifest = {
        "origen": "hotel_ficticio",
        "licencia": "propia (datos inventados; sin datos reales)",
        "prompt_version": prompts.PROMPT_VERSION,
        "herramientas": [t["function"]["name"] for t in tools],
        "huella_herramientas": huella_herramientas(tools),
        "hoy": str(hoy),
        "semilla": semilla,
        "ejemplos": {k: len(v) for k, v in partes.items()},
        "por_escenario": dict(Counter(e["meta"]["escenario"] for e in ejemplos)),
        **(extra or {}),
    }
    (carpeta / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest
