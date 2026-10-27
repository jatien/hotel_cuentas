"""
Afinado LoRA de Qwen2.5-VL-7B-Instruct para extraer facturas con el esquema de invoice_agent.

Se ejecuta en una GPU (pod de RunPod o el PC con la RTX 5080), con el dataset exportado por
`python manage.py invoice_agent_export_dataset`:

    # RunPod
    source /workspace/venv-train/bin/activate
    export HF_HOME=/workspace/hf-cache
    python train_qwen25vl.py --data /workspace/dataset_facturas --output /workspace/outputs/run1

    # PC local con 16 GB (Docker, ver training/local/)
    docker compose -f training/local/compose.yaml run --rm train

Qué hace:
  1. Carga el modelo base (16 bits; con --load-in-4bit, QLoRA para GPUs de 24 GB).
  2. Añade adaptadores LoRA a las capas de lenguaje (con --vision-layers también a las de visión).
  3. Entrena SOLO sobre la respuesta (el JSON): el prompt y las imágenes no cuentan en la pérdida.
  4. Evalúa la pérdida en val en cada época y se queda con la mejor.
  5. Genera la respuesta para los ejemplos de val y mide: JSON válido, cumple el esquema y
     aciertos en los campos clave. Lo guarda en <output>/val_report.json.
  6. Guarda el adaptador (<output>/lora) y el modelo fusionado en 16 bits (<output>/merged),
     que es el que se sirve con vLLM o se convierte para Ollama.

La calidad real se mide después, desde Django, con el conjunto test y `invoice_agent_evaluate`.
"""

import argparse
import inspect
import json
import random
import time
from pathlib import Path

BASE_MODEL = "Qwen/Qwen2.5-VL-7B-Instruct"
KEY_FIELDS = ["supplier_tax_id", "invoice_number", "invoice_date", "gross_total", "tax_total", "net_subtotal"]
# Marcadores del formato de chat de Qwen: la pérdida se calcula solo tras el del asistente.
QWEN_USER = "<|im_start|>user\n"
QWEN_ASSISTANT = "<|im_start|>assistant\n"


def parse_args():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--data", type=Path, required=True, help="Carpeta exportada (train.jsonl, val.jsonl, images/)")
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--base-model", default=BASE_MODEL)
    p.add_argument("--epochs", type=float, default=3)
    p.add_argument("--lr", type=float, default=1e-4)
    p.add_argument("--lora-r", type=int, default=16)
    p.add_argument("--lora-alpha", type=int, default=16)
    p.add_argument("--grad-accum", type=int, default=8, help="Ejemplos por paso de optimización (lote efectivo).")
    p.add_argument("--max-seq-len", type=int, default=12288, help="Ejemplos más largos se descartan.")
    p.add_argument("--load-in-4bit", action="store_true", help="QLoRA: para GPUs de 16-24 GB.")
    p.add_argument("--vision-layers", action="store_true", help="Afinar también el codificador de imágenes.")
    p.add_argument("--eval-generate", type=int, default=20, help="Ejemplos de val a generar al final (0 = ninguno).")
    p.add_argument("--no-merge", action="store_true", help="No guardar el modelo fusionado de 16 bits.")
    p.add_argument("--resume", action="store_true",
                   help="Continuar desde el último checkpoint de <output>/checkpoints (si se cortó).")
    p.add_argument("--seed", type=int, default=3407)
    return p.parse_args()


# --------------------------------------------------------------------------- #
# Datos
# --------------------------------------------------------------------------- #
def estimate_tokens(record: dict, folder: Path) -> int:
    from PIL import Image

    total = 0
    for message in record["messages"]:
        for part in message["content"]:
            if part["type"] == "text":
                total += len(part["text"]) // 3 + 1
            else:
                with Image.open(folder / part["image"]) as image:
                    total += (image.width // 28) * (image.height // 28)
    return total


def load_split(folder: Path, split: str, max_tokens: int) -> list[dict]:
    """Lee el .jsonl y cambia cada ruta de imagen por la imagen (PIL), como espera el colador de Unsloth."""
    from PIL import Image

    path = folder / f"{split}.jsonl"
    if not path.exists():
        return []
    records, dropped = [], 0
    for line in path.read_text(encoding="utf-8").splitlines():
        record = json.loads(line)
        if estimate_tokens(record, folder) > max_tokens:
            dropped += 1
            continue
        for message in record["messages"]:
            for part in message["content"]:
                if part["type"] == "image":
                    with Image.open(folder / part["image"]) as image:
                        part["image"] = image.convert("RGB")
        records.append(record)
    if dropped:
        print(f"[{split}] {dropped} ejemplo(s) descartados por superar {max_tokens} tokens (demasiadas páginas).")
    return records


# --------------------------------------------------------------------------- #
# Evaluación rápida en val (generación)
# --------------------------------------------------------------------------- #
def generate_report(model, processor, records: list[dict], schema: dict, limit: int) -> dict:
    import torch
    from unsloth import FastVisionModel

    try:
        import jsonschema
    except ImportError:
        jsonschema = None

    FastVisionModel.for_inference(model)
    stats = {"n": 0, "valid_json": 0, "schema_ok": 0, "fields": {k: 0 for k in KEY_FIELDS}, "seconds": 0.0}
    samples = []
    for record in records[:limit]:
        prompt_messages = record["messages"][:-1]
        expected = json.loads(record["messages"][-1]["content"][0]["text"])
        images = [p["image"] for m in prompt_messages for p in m["content"] if p["type"] == "image"]
        text = processor.apply_chat_template(prompt_messages, add_generation_prompt=True, tokenize=False)
        inputs = processor(text=[text], images=images or None, return_tensors="pt", add_special_tokens=False).to("cuda")
        started = time.monotonic()
        with torch.no_grad():
            output = model.generate(**inputs, max_new_tokens=4096, do_sample=False, use_cache=True)
        stats["seconds"] += time.monotonic() - started
        answer = processor.batch_decode(output[:, inputs["input_ids"].shape[1]:], skip_special_tokens=True)[0]
        stats["n"] += 1
        try:
            parsed = json.loads(answer)
        except ValueError:
            samples.append({"id": record["id"], "error": "JSON no válido", "answer": answer[:500]})
            continue
        stats["valid_json"] += 1
        if jsonschema:
            try:
                jsonschema.validate(parsed, schema)
                stats["schema_ok"] += 1
            except jsonschema.ValidationError as error:
                samples.append({"id": record["id"], "error": f"esquema: {error.message[:200]}"})
        for key in KEY_FIELDS:
            got = (parsed.get(key) or {}).get("value") if isinstance(parsed.get(key), dict) else None
            if got == expected[key]["value"]:
                stats["fields"][key] += 1
    n = max(stats["n"], 1)
    return {
        "examples": stats["n"],
        "valid_json_rate": round(stats["valid_json"] / n, 3),
        "schema_ok_rate": round(stats["schema_ok"] / n, 3) if jsonschema else None,
        "key_field_exact_match": {k: round(v / n, 3) for k, v in stats["fields"].items()},
        "seconds_per_invoice": round(stats["seconds"] / n, 1),
        "failures": samples[:20],
    }


# --------------------------------------------------------------------------- #
def main():
    args = parse_args()
    random.seed(args.seed)
    args.output.mkdir(parents=True, exist_ok=True)
    manifest = json.loads((args.data / "manifest.json").read_text(encoding="utf-8"))

    # Unsloth tiene que importarse ANTES que trl/transformers para aplicar sus parches.
    from unsloth import FastVisionModel, is_bf16_supported
    from unsloth.trainer import UnslothVisionDataCollator
    from trl import SFTConfig, SFTTrainer

    train = load_split(args.data, "train", args.max_seq_len)
    val = load_split(args.data, "val", args.max_seq_len)
    if not train:
        raise SystemExit("No hay ejemplos de entrenamiento.")
    print(f"Ejemplos: train {len(train)} · val {len(val)} · prompt {manifest['prompt_version']}")

    model, processor = FastVisionModel.from_pretrained(
        args.base_model,
        load_in_4bit=args.load_in_4bit,
        use_gradient_checkpointing="unsloth",  # menos memoria a cambio de algo más de tiempo
        max_seq_length=args.max_seq_len,
    )
    model = FastVisionModel.get_peft_model(
        model,
        finetune_vision_layers=args.vision_layers,
        finetune_language_layers=True,
        finetune_attention_modules=True,
        finetune_mlp_modules=True,
        r=args.lora_r,
        lora_alpha=args.lora_alpha,
        lora_dropout=0,
        bias="none",
        random_state=args.seed,
    )
    FastVisionModel.for_training(model)

    config_kwargs = dict(
        per_device_train_batch_size=1,
        per_device_eval_batch_size=1,
        gradient_accumulation_steps=args.grad_accum,
        num_train_epochs=args.epochs,
        learning_rate=args.lr,
        warmup_ratio=0.05,
        lr_scheduler_type="cosine",
        optim="adamw_8bit",
        weight_decay=0.01,
        logging_steps=5,
        seed=args.seed,
        output_dir=str(args.output / "checkpoints"),
        report_to="none",
        bf16=is_bf16_supported(),
        fp16=not is_bf16_supported(),
        remove_unused_columns=False,
        dataset_text_field="",
        dataset_kwargs={"skip_prepare_dataset": True},
        save_total_limit=2,
    )
    if val:
        config_kwargs.update(eval_strategy="epoch", save_strategy="epoch", load_best_model_at_end=True,
                             metric_for_best_model="eval_loss", greater_is_better=False)
    else:
        config_kwargs.update(save_strategy="epoch")
    # El nombre del límite de longitud cambió entre versiones de TRL.
    params = inspect.signature(SFTConfig.__init__).parameters
    config_kwargs["max_length" if "max_length" in params else "max_seq_length"] = args.max_seq_len

    trainer = SFTTrainer(
        model=model,
        tokenizer=processor,
        data_collator=UnslothVisionDataCollator(
            model,
            processor,
            train_on_responses_only=True,
            instruction_part=QWEN_USER,
            response_part=QWEN_ASSISTANT,
        ),
        train_dataset=train,
        eval_dataset=val or None,
        args=SFTConfig(**config_kwargs),
    )
    started = time.monotonic()
    checkpoints = args.output / "checkpoints"
    resume = args.resume and any(checkpoints.glob("checkpoint-*"))
    if args.resume and not resume:
        print("No hay checkpoints previos: se empieza desde el principio.")
    result = trainer.train(resume_from_checkpoint=True if resume else None)
    history = [row for row in trainer.state.log_history if "loss" in row or "eval_loss" in row]

    lora_dir = args.output / "lora"
    model.save_pretrained(str(lora_dir))
    processor.save_pretrained(str(lora_dir))

    report = {
        "base_model": args.base_model,
        "prompt_version": manifest["prompt_version"],
        "schema_version": manifest["schema_version"],
        "dataset_counts": {"train": len(train), "val": len(val)},
        "args": {k: str(v) for k, v in vars(args).items()},
        "train_loss": result.training_loss,
        "train_minutes": round((time.monotonic() - started) / 60, 1),
        "history": history,
    }
    if val and args.eval_generate:
        report["val_generation"] = generate_report(model, processor, val, manifest["output_schema"], args.eval_generate)
    (args.output / "val_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "history"}, ensure_ascii=False, indent=2))

    if not args.no_merge:
        merged_dir = args.output / "merged"
        model.save_pretrained_merged(str(merged_dir), processor, save_method="merged_16bit")
        (merged_dir / "factura_ia.json").write_text(
            json.dumps({"prompt_version": manifest["prompt_version"], "schema_version": manifest["schema_version"],
                        "vision_mode": manifest.get("vision_mode", "always"), "base_model": args.base_model,
                        "trained_4bit": args.load_in_4bit}, indent=2),
            encoding="utf-8",
        )
        print(f"Modelo fusionado en {merged_dir} (en 16 GB: cuantizar_fp8.py y luego servir)")


if __name__ == "__main__":
    main()
