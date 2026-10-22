"""
Formato de entrenamiento del copilot con la plantilla de chat de Qwen3.5.

- Cada ejemplo es {"messages", "tools"} (formato TRL para llamadas a funciones),
  con los argumentos de las llamadas como objeto (lo que espera la plantilla).
- Se renderiza con tokenizer.apply_chat_template, la MISMA plantilla que usa vLLM
  en producción, y con enable_thinking=False (el copilot no razona).
- La pérdida solo cuenta en las respuestas del asistente: el sistema, las
  preguntas, los esquemas de herramientas y los resultados de las herramientas
  quedan enmascarados (-100). Tampoco cuenta el bloque vacío <think></think> que
  añade la plantilla: en producción lo pone el propio prompt de generación.
- Un mensaje de asistente con "entrenar": false (historial de turnos anteriores)
  es contexto, no objetivo.
"""

IGNORAR = -100
CABECERA = "<|im_start|>assistant\n"
FIN = "<|im_end|>"
PENSAMIENTO_VACIO = "<think>\n\n</think>\n\n"


def renderizar(tokenizer, ejemplo: dict) -> str:
    return tokenizer.apply_chat_template(ejemplo["messages"], tools=ejemplo.get("tools") or None,
                                         tokenize=False, enable_thinking=False)


def tramos_asistente(texto: str, messages: list) -> list[tuple[int, int]]:
    """(inicio, fin) en caracteres de cada respuesta del asistente que se entrena."""
    asistentes = [m for m in messages if m["role"] == "assistant"]
    tramos, pos = [], 0
    for mensaje in asistentes:
        i = texto.find(CABECERA, pos)
        if i < 0:
            raise ValueError("La plantilla no generó una cabecera de asistente por mensaje.")
        inicio = i + len(CABECERA)
        if texto.startswith(PENSAMIENTO_VACIO, inicio):
            inicio += len(PENSAMIENTO_VACIO)
        fin = texto.find(FIN, inicio)
        if fin < 0:
            raise ValueError("Respuesta del asistente sin <|im_end|>.")
        fin += len(FIN)
        pos = fin
        if mensaje.get("entrenar", True):
            tramos.append((inicio, fin))
    return tramos


def tokenizar(tokenizer, ejemplo: dict, max_tokens: int | None = None) -> dict | None:
    """input_ids + labels enmascaradas. None si el ejemplo supera max_tokens o no tiene nada que aprender."""
    texto = renderizar(tokenizer, ejemplo)
    tramos = tramos_asistente(texto, ejemplo["messages"])
    codificado = tokenizer(texto, add_special_tokens=False, return_offsets_mapping=True)
    ids = codificado["input_ids"]
    if max_tokens and len(ids) > max_tokens:
        return None
    labels = [IGNORAR] * len(ids)
    for k, (a, b) in enumerate(codificado["offset_mapping"]):
        if any(a >= ini and b <= fin and b > a for ini, fin in tramos):
            labels[k] = ids[k]
    if all(x == IGNORAR for x in labels):
        return None
    return {"input_ids": ids, "labels": labels, "attention_mask": [1] * len(ids)}


def texto_entrenado(tokenizer, tokenizado: dict) -> str:
    """Solo lo que cuenta en la pérdida (para revisar a ojo que el enmascarado es correcto)."""
    return tokenizer.decode([t for t, l in zip(tokenizado["input_ids"], tokenizado["labels"]) if l != IGNORAR])
