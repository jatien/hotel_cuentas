"""
Comprueba un dataset exportado con `python manage.py invoice_agent_export_dataset` ANTES de
alquilar tiempo de GPU. No necesita GPU ni Django: se puede ejecutar en el PC o en el pod.

    python check_dataset.py /workspace/dataset_facturas

Revisa que cada línea de train/val sea JSON válido, que las imágenes existan, que la respuesta
esperada cumpla el esquema del manifest y estima la longitud en tokens de cada ejemplo
(Qwen2.5-VL: ~1 token por cada 28×28 píxeles de imagen; texto ~1 token por 3,5 caracteres).
"""

import argparse
import json
import sys
from pathlib import Path

PATCH = 28


def image_tokens(path: Path) -> int:
    from PIL import Image

    with Image.open(path) as image:
        width, height = image.size
    return max(1, (width // PATCH) * (height // PATCH))


def check(folder: Path, max_tokens: int) -> int:
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    schema = manifest["output_schema"]
    try:
        import jsonschema
    except ImportError:
        jsonschema = None
        print("Aviso: jsonschema no instalado; no se valida la respuesta contra el esquema.")

    problems = 0
    for split in ("train", "val"):
        path = folder / f"{split}.jsonl"
        lengths = []
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            where = f"{split}.jsonl:{number}"
            try:
                record = json.loads(line)
                system, user, assistant = record["messages"]
            except (ValueError, KeyError) as error:
                print(f"{where}: formato no válido ({error})")
                problems += 1
                continue
            tokens = 0
            for part in system["content"] + user["content"] + assistant["content"]:
                if part["type"] == "text":
                    tokens += len(part["text"]) // 3 + 1
                elif part["type"] == "image":
                    image = folder / part["image"]
                    if not image.is_file():
                        print(f"{where}: falta la imagen {part['image']}")
                        problems += 1
                    else:
                        tokens += image_tokens(image)
            answer = assistant["content"][0]["text"]
            try:
                parsed = json.loads(answer)
                if jsonschema:
                    jsonschema.validate(parsed, schema)
            except Exception as error:  # noqa: BLE001 - se informa de cualquier fallo de la respuesta
                print(f"{where}: la respuesta esperada no cumple el esquema ({type(error).__name__})")
                problems += 1
            lengths.append(tokens)
            if tokens > max_tokens:
                print(f"{where}: ~{tokens} tokens (> {max_tokens}); se descartará al entrenar (demasiadas páginas)")
        if lengths:
            lengths.sort()
            print(
                f"{split}: {len(lengths)} ejemplos · tokens mediana ~{lengths[len(lengths) // 2]} · "
                f"máximo ~{lengths[-1]}"
            )
        else:
            print(f"{split}: 0 ejemplos")
    tests = len(list((folder / "test").glob("*.json")))
    print(f"test: {tests} facturas reservadas para evaluar (no se entrenan)")
    print(f"prompt {manifest['prompt_version']} · esquema {manifest['schema_version']}")
    print("OK" if not problems else f"{problems} problema(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("folder", type=Path)
    parser.add_argument("--max-tokens", type=int, default=12288)
    args = parser.parse_args()
    sys.exit(check(args.folder, args.max_tokens))
