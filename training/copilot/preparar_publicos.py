"""
Descarga y convierte los datasets públicos con licencia que permite uso comercial
al formato de entrenamiento del copilot ({"messages", "tools", "meta"}).

| Dataset | Licencia | Qué aporta |
|---|---|---|
| nvidia/When2Call (train_sft) | CC BY 4.0 (uso comercial permitido) | Cuándo NO llamar: pedir el dato que falta o decir que no se puede |
| Team-ACE/ToolACE | Apache 2.0 | Llamadas simples, paralelas y encadenadas sobre miles de APIs |
| Agent-Ark/Toucan-1.5M (config SFT) | Apache 2.0 | Trayectorias reales con herramientas MCP, varios pasos, multilingüe |

Uso (con internet; en el pod o en cualquier PC):

    python preparar_publicos.py --salida publicos --when2call 600 --toolace 1200 --toucan 1200

Cada fila lleva meta.origen y meta.licencia; mezclar.py escribe ATRIBUCIONES.md (CC BY 4.0 exige
atribución). Ningún dato de estos conjuntos menciona el hotel: enseñan la mecánica de las herramientas;
el contenido del hotel lo aporta el dataset propio (hotel ficticio).
"""

import argparse
import ast
import json
import random
import re
from collections import Counter
from pathlib import Path

FUENTES = {
    "when2call": ("nvidia/When2Call", "CC BY 4.0", "https://huggingface.co/datasets/nvidia/When2Call"),
    "toolace": ("Team-ACE/ToolACE", "Apache 2.0", "https://huggingface.co/datasets/Team-ACE/ToolACE"),
    "toucan": ("Agent-Ark/Toucan-1.5M", "Apache 2.0", "https://huggingface.co/datasets/Agent-Ark/Toucan-1.5M"),
}
TIPOS = {"dict": "object", "str": "string", "string": "string", "int": "integer", "integer": "integer",
         "float": "number", "number": "number", "bool": "boolean", "boolean": "boolean", "list": "array",
         "array": "array", "object": "object", "tuple": "array", "any": "string"}


def meta(fuente: str, **extra) -> dict:
    nombre, licencia, url = FUENTES[fuente]
    return {"origen": fuente, "dataset": nombre, "licencia": licencia, "url": url, **extra}


def nombre_funcion(nombre: str) -> str:
    """Nombres válidos para la plantilla y para vLLM: «SEC Filings» -> «SEC_Filings»."""
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", nombre.strip()).strip("_")[:64] or "funcion"


def esquema(tipo) -> str:
    base = str(tipo or "string").split("[")[0].split(",")[0].strip().lower()
    return TIPOS.get(base, "string")


def herramienta(spec: dict) -> dict:
    """Especificación «estilo Python» (type: dict/str/int, required fuera) -> función JSON Schema."""
    if "function" in spec:  # ya está en formato OpenAI
        spec = spec["function"]
    params = dict(spec.get("parameters") or {})
    props = {}
    for nombre, p in (params.get("properties") or {}).items():
        p = dict(p) if isinstance(p, dict) else {"description": str(p)}
        p["type"] = esquema(p.get("type"))
        if p.get("default") in ("", None):
            p.pop("default", None)
        props[nombre] = p
    requeridos = params.get("required") or spec.get("required") or []
    salida = {"type": "object", "properties": props}
    if isinstance(requeridos, list) and requeridos:
        salida["required"] = [r for r in requeridos if r in props]
    return {"type": "function", "function": {"name": nombre_funcion(spec.get("name", "")),
                                             "description": (spec.get("description") or "").strip()[:1024],
                                             "parameters": salida}}


# --------------------------------------------------------------------------- When2Call
def convertir_when2call(fila: dict) -> dict | None:
    tools = [herramienta(json.loads(t) if isinstance(t, str) else t) for t in fila["tools"]]
    messages = [{"role": m["role"], "content": m["content"]} for m in fila["messages"]]
    if not messages or messages[-1]["role"] != "assistant":
        return None
    return {"messages": messages, "tools": tools, "meta": meta("when2call")}


# --------------------------------------------------------------------------- ToolACE
_LITERALES = {"true": True, "false": False, "null": None, "none": None}


def _valor(nodo):
    if isinstance(nodo, ast.Name) and nodo.id.lower() in _LITERALES:
        return _LITERALES[nodo.id.lower()]
    return ast.literal_eval(nodo)


def parsear_llamadas(texto: str) -> list | None:
    """«[A b(x="1", y=2), C(z=[1, 2])]» -> [("A b", {"x": "1", "y": 2}), ("C", {"z": [1, 2]})]. None si no es eso."""
    t = texto.strip()
    if not (t.startswith("[") and t.endswith("]")):
        return None
    t, llamadas, i = t[1:-1], [], 0
    while i < len(t):
        while i < len(t) and t[i] in " ,\n":
            i += 1
        if i >= len(t):
            break
        abre = t.find("(", i)
        if abre < 0:
            return None
        nombre, prof, j, comilla = t[i:abre].strip(), 0, abre, None
        while j < len(t):  # paréntesis de cierre, respetando comillas y anidación
            c = t[j]
            if comilla:
                if c == "\\":
                    j += 1
                elif c == comilla:
                    comilla = None
            elif c in "\"'":
                comilla = c
            elif c in "([{":
                prof += 1
            elif c in ")]}":
                prof -= 1
                if prof == 0:
                    break
            j += 1
        if prof != 0 or not nombre:
            return None
        try:
            expr = ast.parse(f"f({t[abre + 1:j]})", mode="eval").body
            if expr.args:
                return None
            args = {k.arg: _valor(k.value) for k in expr.keywords}
        except (SyntaxError, ValueError):
            return None
        llamadas.append((nombre, args))
        i = j + 1
    return llamadas or None


def convertir_toolace(fila: dict) -> dict | None:
    sistema = fila.get("system") or ""
    inicio = sistema.find("[", sistema.find("invoke:") if "invoke:" in sistema else 0)
    try:
        specs, _ = json.JSONDecoder().raw_decode(sistema[inicio:])
    except (ValueError, json.JSONDecodeError):
        return None
    tools = [herramienta(s) for s in specs if isinstance(s, dict) and s.get("name")]
    messages, pendientes, n = [], [], 0
    for turno in fila["conversations"]:
        rol, valor = turno["from"], turno["value"]
        if rol == "user":
            messages.append({"role": "user", "content": valor})
        elif rol == "assistant":
            llamadas = parsear_llamadas(valor) if valor.strip().startswith("[") else None
            if llamadas:
                ids = []
                calls = []
                for nombre, args in llamadas:
                    n += 1
                    ids.append((f"call_{n}", nombre_funcion(nombre)))
                    calls.append({"id": ids[-1][0], "type": "function",
                                  "function": {"name": ids[-1][1], "arguments": args}})
                messages.append({"role": "assistant", "content": "", "tool_calls": calls})
                pendientes = ids
            elif valor.strip().startswith("[") and valor.strip().endswith("]") and "(" in valor:
                return None  # parecía una llamada pero no se pudo leer: fuera
            else:
                messages.append({"role": "assistant", "content": valor})
        elif rol == "tool":
            if not pendientes:
                return None
            try:
                resultados = json.loads(valor)
            except json.JSONDecodeError:
                resultados = None
            if isinstance(resultados, list) and len(resultados) == len(pendientes):
                for (cid, nombre), r in zip(pendientes, resultados):
                    messages.append({"role": "tool", "tool_call_id": cid, "name": nombre,
                                     "content": json.dumps(r.get("results", r) if isinstance(r, dict) else r,
                                                           ensure_ascii=False)})
            else:
                messages.append({"role": "tool", "tool_call_id": pendientes[0][0], "name": pendientes[0][1],
                                 "content": valor})
            pendientes = []
    if not messages or messages[-1]["role"] != "assistant" or not any(m.get("tool_calls") for m in messages):
        return None
    return {"messages": messages, "tools": tools, "meta": meta("toolace")}


# --------------------------------------------------------------------------- Toucan
def convertir_toucan(fila: dict) -> dict | None:
    try:
        tools = [herramienta(t) for t in json.loads(fila["tools"])]
        crudos = json.loads(fila["messages"])
    except (TypeError, json.JSONDecodeError):
        return None
    messages, pendientes, n = [], [], 0
    for m in crudos:
        rol, contenido = m.get("role"), m.get("content") or ""
        if rol in ("user", "system"):
            messages.append({"role": rol, "content": contenido})
        elif rol == "assistant":
            messages.append({"role": "assistant", "content": contenido})
        elif rol == "tool_call":
            try:
                llamada = ast.literal_eval(contenido) if isinstance(contenido, str) else contenido
                args = llamada.get("arguments") or "{}"
                args = json.loads(args) if isinstance(args, str) else args
            except (ValueError, SyntaxError, json.JSONDecodeError):
                return None
            if not isinstance(args, dict):
                return None
            n += 1
            call = {"id": f"call_{n}", "type": "function",
                    "function": {"name": nombre_funcion(llamada["name"]), "arguments": args}}
            ultimo = messages[-1] if messages else None
            if ultimo and ultimo["role"] == "assistant":  # llamadas seguidas = una sola respuesta
                ultimo.setdefault("tool_calls", []).append(call)
            else:
                messages.append({"role": "assistant", "content": "", "tool_calls": [call]})
            pendientes.append((call["id"], call["function"]["name"]))
        elif rol == "tool_response":
            if not pendientes:
                return None
            cid, nombre = pendientes.pop(0)
            messages.append({"role": "tool", "tool_call_id": cid, "name": nombre, "content": str(contenido)})
        else:
            return None
    if pendientes or not messages or messages[-1]["role"] != "assistant" or messages[-1].get("tool_calls"):
        return None
    return {"messages": messages, "tools": tools,
            "meta": meta("toucan", subset=fila.get("subset_name"), idioma=idioma(fila.get("question") or ""))}


_PALABRAS = {
    "es": {"el", "la", "los", "las", "de", "que", "y", "en", "un", "una", "para", "con", "por", "es", "del", "cómo", "qué", "necesito", "quiero"},
    "pt": {"o", "os", "as", "de", "que", "e", "em", "um", "uma", "para", "com", "por", "é", "do", "da", "não", "você", "preciso"},
    "en": {"the", "a", "an", "of", "and", "to", "in", "for", "with", "is", "i", "my", "how", "what", "can", "you"},
}


def idioma(texto: str) -> str:
    palabras = re.findall(r"[a-záéíóúñãõçê]+", texto.lower())
    puntos = {k: sum(p in v for p in palabras) for k, v in _PALABRAS.items()}
    return max(puntos, key=puntos.get) if any(puntos.values()) else "?"


# --------------------------------------------------------------------------- descarga y muestreo
def leer_when2call():
    from huggingface_hub import hf_hub_download

    ruta = hf_hub_download("nvidia/When2Call", "train/when2call_train_sft.jsonl", repo_type="dataset")
    with open(ruta, encoding="utf-8") as f:
        for linea in f:
            yield json.loads(linea)


def leer_toolace():
    from huggingface_hub import hf_hub_download

    ruta = hf_hub_download("Team-ACE/ToolACE", "data.json", repo_type="dataset")
    yield from json.load(open(ruta, encoding="utf-8"))


def leer_toucan(shards: int):
    import pyarrow.parquet as pq
    from huggingface_hub import hf_hub_download

    for i in range(shards):
        ruta = hf_hub_download("Agent-Ark/Toucan-1.5M", f"SFT/train-{i:05d}-of-00003.parquet", repo_type="dataset")
        tabla = pq.read_table(ruta, columns=["subset_name", "question", "tools", "messages"])
        for lote in tabla.to_batches(4096):
            yield from lote.to_pylist()


def muestrear(filas, convertir, n, rnd, *, clave=None, max_por_clave=None, preferir=None):
    """Convierte y elige n filas. `clave` limita repeticiones (When2Call repite mucho sus respuestas);
    `preferir` da prioridad a las filas que cumplen la condición (Toucan en español)."""
    convertidas, por_clave = [], Counter()
    for fila in filas:
        ejemplo = convertir(fila)
        if ejemplo is None:
            continue
        if clave:
            k = clave(ejemplo)
            if por_clave[k] >= max_por_clave:
                continue
            por_clave[k] += 1
        convertidas.append(ejemplo)
    rnd.shuffle(convertidas)
    if preferir:
        convertidas.sort(key=lambda e: not preferir(e))
    return convertidas[:n]


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--salida", type=Path, default=Path("publicos"))
    p.add_argument("--when2call", type=int, default=600)
    p.add_argument("--toolace", type=int, default=1200)
    p.add_argument("--toucan", type=int, default=1200)
    p.add_argument("--toucan-shards", type=int, default=1, help="Ficheros del config SFT a leer (de 3; ~70-240 MB cada uno).")
    p.add_argument("--semilla", type=int, default=7)
    a = p.parse_args()
    rnd = random.Random(a.semilla)
    a.salida.mkdir(parents=True, exist_ok=True)
    trabajos = [
        ("when2call", a.when2call, lambda: muestrear(leer_when2call(), convertir_when2call, a.when2call, rnd,
                                                     clave=lambda e: e["messages"][-1]["content"][:80], max_por_clave=3)),
        ("toolace", a.toolace, lambda: muestrear(leer_toolace(), convertir_toolace, a.toolace, rnd)),
        ("toucan", a.toucan, lambda: muestrear(leer_toucan(a.toucan_shards), convertir_toucan, a.toucan, rnd,
                                               preferir=lambda e: e["meta"].get("idioma") == "es")),
    ]
    for nombre, n, hacer in trabajos:
        if n <= 0:
            continue
        filas = hacer()
        with open(a.salida / f"{nombre}.jsonl", "w", encoding="utf-8") as f:
            for e in filas:
                f.write(json.dumps(e, ensure_ascii=False) + "\n")
        print(f"{nombre}: {len(filas)} ejemplos -> {a.salida / (nombre + '.jsonl')}")


if __name__ == "__main__":
    main()
