"""
Cuantiza a FP8 el modelo fusionado (merged/) para servirlo con vLLM en una GPU de 16 GB.

Por qué hace falta: el modelo fusionado ocupa ~16,6 GB en 16 bits y no cabe en la RTX 5080. La
cuantización FP8 «al vuelo» de vLLM carga primero los pesos en 16 bits en la GPU, así que también
se queda sin memoria. Este script hace la cuantización ANTES, en la CPU (necesita ~20-24 GB de RAM),
con llm-compressor y el esquema FP8_DYNAMIC (sin datos de calibración). El codificador de imágenes
y lm_head se dejan en 16 bits, como en la receta oficial de llm-compressor para Qwen2.5-VL.

    python cuantizar_fp8.py /outputs/run1/merged /outputs/run1/merged-fp8

Con Docker (training/local/): docker compose -f training/local/compose.yaml run --rm fp8
"""

import argparse
import json
import shutil
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("source", type=Path, help="Carpeta merged/ generada por train_qwen25vl.py")
    parser.add_argument("target", type=Path, help="Carpeta de salida (p. ej. merged-fp8/)")
    args = parser.parse_args()
    if not (args.source / "config.json").is_file():
        raise SystemExit(f"{args.source} no parece un modelo fusionado (falta config.json).")
    if args.target.exists() and any(args.target.iterdir()):
        raise SystemExit(f"{args.target} ya existe y no está vacía.")

    from llmcompressor import oneshot
    from llmcompressor.modifiers.quantization import QuantizationModifier
    from transformers import AutoProcessor, Qwen2_5_VLForConditionalGeneration

    model = Qwen2_5_VLForConditionalGeneration.from_pretrained(str(args.source), dtype="auto")
    processor = AutoProcessor.from_pretrained(str(args.source))
    recipe = QuantizationModifier(
        targets="Linear",
        scheme="FP8_DYNAMIC",
        ignore=["lm_head", "re:visual.*", "re:model.visual.*"],
    )
    oneshot(model=model, recipe=recipe)
    args.target.mkdir(parents=True, exist_ok=True)
    model.save_pretrained(str(args.target), save_compressed=True)
    processor.save_pretrained(str(args.target))

    # Conserva la ficha del modelo (versión de prompt y esquema) y anota la cuantización.
    info_path = args.source / "factura_ia.json"
    info = json.loads(info_path.read_text(encoding="utf-8")) if info_path.is_file() else {}
    info["quantization"] = "fp8_dynamic (llm-compressor)"
    (args.target / "factura_ia.json").write_text(json.dumps(info, indent=2), encoding="utf-8")
    template = args.source / "chat_template.json"
    if template.is_file() and not (args.target / "chat_template.json").exists():
        shutil.copy2(template, args.target / "chat_template.json")
    print(f"Modelo FP8 guardado en {args.target}")


if __name__ == "__main__":
    main()
