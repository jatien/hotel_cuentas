"""
Mezcla el dataset propio (hotel ficticio) con los públicos y escribe la carpeta de entrenamiento.

    python mezclar.py --propio dataset_copilot --publicos publicos --salida mezcla --publicos-pct 40

- train: todo el train propio + públicos hasta que sean el --publicos-pct % del total.
- val: el val propio (es lo que importa medir: las herramientas del hotel, en español).
- test: NO se copia: se queda en dataset_copilot/test.jsonl para evaluar desde Django.
- ATRIBUCIONES.md: origen y licencia de cada dataset público usado (CC BY 4.0 exige atribución).
"""

import argparse
import json
import random
from collections import Counter
from pathlib import Path


def leer(ruta: Path) -> list:
    if not ruta.exists():
        return []
    with open(ruta, encoding="utf-8") as f:
        return [json.loads(linea) for linea in f if linea.strip()]


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--propio", type=Path, required=True)
    p.add_argument("--publicos", type=Path, required=True)
    p.add_argument("--salida", type=Path, required=True)
    p.add_argument("--publicos-pct", type=float, default=40, help="Porcentaje de ejemplos públicos en train.")
    p.add_argument("--semilla", type=int, default=7)
    a = p.parse_args()
    rnd = random.Random(a.semilla)

    propio = leer(a.propio / "train.jsonl")
    if not propio:
        raise SystemExit(f"No hay {a.propio / 'train.jsonl'}: genera antes el dataset con copilot_dataset.")
    publicos = [e for f in sorted(a.publicos.glob("*.jsonl")) for e in leer(f)]
    rnd.shuffle(publicos)
    n_publicos = min(len(publicos), int(len(propio) * a.publicos_pct / max(100 - a.publicos_pct, 1)))
    train = propio + publicos[:n_publicos]
    rnd.shuffle(train)

    a.salida.mkdir(parents=True, exist_ok=True)
    for nombre, filas in (("train", train), ("val", leer(a.propio / "val.jsonl"))):
        with open(a.salida / f"{nombre}.jsonl", "w", encoding="utf-8") as f:
            for e in filas:
                f.write(json.dumps(e, ensure_ascii=False) + "\n")

    usados = {}
    for e in publicos[:n_publicos]:
        m = e["meta"]
        usados.setdefault(m["dataset"], {"licencia": m["licencia"], "url": m["url"], "ejemplos": 0})["ejemplos"] += 1
    lineas = ["# Atribuciones de los datos de entrenamiento del PMS Copilot", "",
              "- Datos propios: hotel ficticio generado por `copilot_dataset` (datos inventados).", ""]
    lineas += [f"- **{d}** ({v['licencia']}): {v['ejemplos']} ejemplos convertidos al formato del copilot. {v['url']}"
               for d, v in usados.items()]
    lineas += ["", "Los ejemplos públicos se han convertido de formato (nombres de función, tipos de los esquemas, "
               "llamadas como objetos) sin cambiar su contenido."]
    (a.salida / "ATRIBUCIONES.md").write_text("\n".join(lineas) + "\n", encoding="utf-8")

    manifest_propio = a.propio / "manifest.json"
    manifest = {
        "train": len(train), "val": sum(1 for _ in open(a.salida / "val.jsonl", encoding="utf-8")),
        "origenes": dict(Counter(e["meta"]["origen"] for e in train)),
        "publicos_pct": round(100 * n_publicos / len(train), 1),
        "propio": json.loads(manifest_propio.read_text(encoding="utf-8")) if manifest_propio.exists() else None,
    }
    (a.salida / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: manifest[k] for k in ("train", "val", "origenes", "publicos_pct")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
