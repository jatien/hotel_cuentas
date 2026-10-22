"""
Afinado LoRA (bf16) de Qwen3.5-9B para el PMS Copilot.

Se ejecuta en un pod de RunPod con la carpeta de mezclar.py:

    source /workspace/venv-copilot/bin/activate
    export HF_HOME=/workspace/hf-cache
    python entrenar_qwen35.py --data /workspace/mezcla --output /workspace/outputs/copilot1

Qué hace:
  1. Carga Qwen3.5-9B en bf16. LoRA en las capas lineales del modelo de lenguaje (atención,
     Gated DeltaNet y MLP); el codificador de imágenes queda congelado y sin adaptadores.
     Qwen recomienda no usar QLoRA de 4 bits con Qwen3.5: por eso no hay opción de 4 bits.
  2. Renderiza cada conversación con la plantilla de chat del modelo (enable_thinking=False) y
     entrena SOLO las respuestas del asistente: llamadas a herramientas y respuestas finales
     (formato.py). Los ejemplos más largos que --max-seq-len se descartan y se cuentan.
  3. Evalúa la pérdida en val en cada época y se queda con la mejor.
  4. Genera la primera respuesta de --eval-generate ejemplos de val y mide si elige la
     herramienta correcta o responde sin herramienta cuando toca (<output>/val_report.json).
  5. Guarda el adaptador (<output>/lora) y el modelo fusionado (<output>/merged), listo para
     servir con copilot_pms/deploy/serve_qwen35.sh (MODEL=<output>/merged).

La evaluación de verdad se hace después, desde Django, con `python manage.py copilot_evaluar`.
"""

import argparse
import inspect
import json
import re
import shutil
import time
from collections import Counter
from pathlib import Path

from formato import IGNORAR, tokenizar

BASE = "Qwen/Qwen3.5-9B"
FUERA_DE_LORA = re.compile(r"visual|vision|lm_head|mtp")
LLAMADA = re.compile(r"<function=([^>\s]+)>")


def argumentos():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--data", type=Path, required=True, help="Carpeta con train.jsonl y val.jsonl (mezclar.py)")
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--base-model", default=BASE)
    p.add_argument("--epochs", type=float, default=2)
    p.add_argument("--lr", type=float, default=1e-4)
    p.add_argument("--lora-r", type=int, default=16)
    p.add_argument("--lora-alpha", type=int, default=32)
    p.add_argument("--lora-dropout", type=float, default=0.05)
    p.add_argument("--batch", type=int, default=1)
    p.add_argument("--grad-accum", type=int, default=16, help="Ejemplos por paso de optimización.")
    p.add_argument("--max-seq-len", type=int, default=8192)
    p.add_argument("--max-steps", type=int, default=-1, help="Para una prueba rápida (p. ej. 5).")
    p.add_argument("--eval-generate", type=int, default=40)
    p.add_argument("--no-merge", action="store_true")
    p.add_argument("--cpu", action="store_true", help="Solo para probar el script con un modelo diminuto.")
    p.add_argument("--seed", type=int, default=3407)
    return p.parse_args()


def leer(ruta: Path) -> list:
    if not ruta.exists():
        return []
    with open(ruta, encoding="utf-8") as f:
        return [json.loads(linea) for linea in f if linea.strip()]


def preparar(tokenizer, ejemplos, max_len, nombre):
    filas, descartes = [], Counter()
    for e in ejemplos:
        t = tokenizar(tokenizer, e, max_len)
        if t is None:
            descartes[e.get("meta", {}).get("origen", "?")] += 1
        else:
            filas.append(t)
    print(f"{nombre}: {len(filas)} ejemplos ({sum(descartes.values())} descartados por longitud o vacíos: "
          f"{dict(descartes)})")
    return filas


class Colador:
    """Rellena a la derecha: input_ids con pad, labels con -100, attention_mask con 0."""

    def __init__(self, pad_id):
        self.pad_id = pad_id

    def __call__(self, filas):
        import torch

        largo = max(len(f["input_ids"]) for f in filas)
        def relleno(clave, valor):
            return torch.tensor([f[clave] + [valor] * (largo - len(f[clave])) for f in filas])
        return {"input_ids": relleno("input_ids", self.pad_id), "labels": relleno("labels", IGNORAR),
                "attention_mask": relleno("attention_mask", 0)}


def cargar_modelo(nombre, dtype, cpu):
    import transformers

    kwargs = {"dtype": dtype}
    if not cpu:
        kwargs["device_map"] = {"": 0}
    for clase in ("AutoModelForImageTextToText", "AutoModelForCausalLM"):
        try:
            return getattr(transformers, clase).from_pretrained(nombre, **kwargs)
        except (ValueError, KeyError) as error:
            ultimo = error
    raise ultimo


def capas_lora(model) -> str:
    import torch

    nombres = sorted({n.rsplit(".", 1)[-1] for n, m in model.named_modules()
                      if isinstance(m, torch.nn.Linear) and not FUERA_DE_LORA.search(n)})
    print("LoRA en:", ", ".join(nombres))
    return rf"^(?!.*({FUERA_DE_LORA.pattern})).*\.({'|'.join(map(re.escape, nombres))})$"


def informe_val(model, tokenizer, ejemplos, limite, dispositivo):
    """Primera respuesta del asistente: ¿llama a la herramienta correcta / responde sin herramienta cuando toca?"""
    import torch

    aciertos, total, detalle = 0, 0, []
    model.eval()
    for e in ejemplos[:limite]:
        msgs = e["messages"]
        i = next((k for k, m in enumerate(msgs) if m["role"] == "assistant" and m.get("entrenar", True)), None)
        if i is None:
            continue
        esperado = msgs[i]["tool_calls"][0]["function"]["name"] if msgs[i].get("tool_calls") else None
        prompt = tokenizer.apply_chat_template(msgs[:i], tools=e.get("tools") or None, tokenize=False,
                                               add_generation_prompt=True, enable_thinking=False)
        ids = tokenizer(prompt, return_tensors="pt", add_special_tokens=False).to(dispositivo)
        with torch.no_grad():
            salida = model.generate(**ids, max_new_tokens=256, do_sample=False)
        texto = tokenizer.decode(salida[0][ids["input_ids"].shape[1]:], skip_special_tokens=False)
        m = LLAMADA.search(texto)
        obtenido = m.group(1) if m else None
        total += 1
        aciertos += obtenido == esperado
        detalle.append({"escenario": e.get("meta", {}).get("escenario"), "esperado": esperado, "obtenido": obtenido})
    return {"primera_respuesta_correcta": round(aciertos / total, 3) if total else None, "n": total, "detalle": detalle}


def main():
    a = argumentos()
    import torch
    from peft import LoraConfig, get_peft_model
    from transformers import AutoTokenizer, Trainer, TrainingArguments, set_seed

    set_seed(a.seed)
    a.output.mkdir(parents=True, exist_ok=True)
    tokenizer = AutoTokenizer.from_pretrained(a.base_model)
    train = preparar(tokenizer, leer(a.data / "train.jsonl"), a.max_seq_len, "train")
    val_ej = leer(a.data / "val.jsonl")
    val = preparar(tokenizer, val_ej, a.max_seq_len, "val")

    dtype = torch.float32 if a.cpu else torch.bfloat16
    model = cargar_modelo(a.base_model, dtype, a.cpu)
    model.config.use_cache = False
    model = get_peft_model(model, LoraConfig(r=a.lora_r, lora_alpha=a.lora_alpha, lora_dropout=a.lora_dropout,
                                             target_modules=capas_lora(model), task_type="CAUSAL_LM"))
    model.print_trainable_parameters()
    if not a.cpu:
        model.gradient_checkpointing_enable(gradient_checkpointing_kwargs={"use_reentrant": False})
        model.enable_input_require_grads()

    hay_val = bool(val)
    # transformers 5 quitó warmup_ratio: ahí warmup_steps < 1 se interpreta como proporción
    calentamiento = ({"warmup_ratio": 0.03} if "warmup_ratio" in inspect.signature(TrainingArguments).parameters
                     else {"warmup_steps": 0.03})
    args = TrainingArguments(
        output_dir=str(a.output / "checkpoints"), per_device_train_batch_size=a.batch,
        per_device_eval_batch_size=a.batch, gradient_accumulation_steps=a.grad_accum, num_train_epochs=a.epochs,
        max_steps=a.max_steps, learning_rate=a.lr, lr_scheduler_type="cosine", **calentamiento,
        weight_decay=0.0, logging_steps=10 if a.max_steps < 0 else 1, bf16=not a.cpu, use_cpu=a.cpu,
        eval_strategy="epoch" if hay_val and a.max_steps < 0 else "no",
        save_strategy="epoch" if a.max_steps < 0 else "no", save_total_limit=2,
        load_best_model_at_end=hay_val and a.max_steps < 0, metric_for_best_model="eval_loss",
        report_to="none", seed=a.seed, remove_unused_columns=False, dataloader_num_workers=0,
    )
    inicio = time.time()
    trainer = Trainer(model=model, args=args, train_dataset=train, eval_dataset=val or None,
                      data_collator=Colador(tokenizer.pad_token_id or 0))
    trainer.train()
    minutos = round((time.time() - inicio) / 60, 1)

    model.save_pretrained(a.output / "lora")
    tokenizer.save_pretrained(a.output / "lora")
    dispositivo = "cpu" if a.cpu else "cuda"
    informe = {"minutos": minutos, "train": len(train), "val": len(val),
               "eval_loss": trainer.evaluate()["eval_loss"] if hay_val else None,
               **(informe_val(model, tokenizer, val_ej, a.eval_generate, dispositivo) if a.eval_generate else {})}
    (a.output / "val_report.json").write_text(json.dumps(informe, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in informe.items() if k != "detalle"}, ensure_ascii=False))

    if not a.no_merge:
        destino = a.output / "merged"
        model.merge_and_unload().save_pretrained(destino, safe_serialization=True)
        tokenizer.save_pretrained(destino)
        from huggingface_hub import snapshot_download

        base = Path(snapshot_download(a.base_model, allow_patterns=["*preprocessor_config.json", "chat_template.jinja"]))
        for fichero in base.iterdir():  # vLLM necesita la configuración del procesador del modelo base
            shutil.copy(fichero, destino / fichero.name)
        manifest = a.data / "manifest.json"
        info = {"base": a.base_model, "lora_r": a.lora_r, "epochs": a.epochs,
                "datos": json.loads(manifest.read_text(encoding="utf-8")) if manifest.exists() else None}
        (destino / "copilot.json").write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Modelo fusionado en {destino}")


if __name__ == "__main__":
    main()
