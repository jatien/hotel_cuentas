"""
Evaluación del modelo del copilot sobre el test del dataset (sin tocar la base de datos).

Paso a paso con contexto correcto («teacher forcing»): para cada respuesta del
asistente que se entrena, se le da al modelo la conversación EXACTA hasta ese
punto (con los resultados reales de las herramientas guardados en el dataset) y
se compara lo que hace con lo esperado:

- llamada esperada: ¿llama?, ¿a las herramientas correctas?, ¿con los argumentos
  correctos? (normalizados con el esquema Pydantic de cada herramienta, así
  «"limite": 30» y no indicarlo cuentan igual porque 30 es el valor por defecto);
- respuesta esperada sin herramienta (aclarar, no conectado, solo lectura...):
  ¿responde sin llamar?;
- respuesta final: ¿hay importes en € que no salen de ningún resultado de
  herramienta? (cifras inventadas).
"""

import json
import re
from collections import defaultdict
from decimal import Decimal, InvalidOperation

from copilot_pms.tools.base import REGISTRO

IMPORTE = re.compile(r"(-?\d{1,3}(?:\.\d{3})*(?:,\d{1,2})?|-?\d+(?:,\d{1,2})?)\s?€")


def _para_api(m: dict) -> dict:
    """Mensaje del dataset -> formato de Chat Completions (argumentos como texto JSON, sin claves internas)."""
    if m["role"] == "tool":
        return {"role": "tool", "tool_call_id": m["tool_call_id"], "content": m["content"]}
    salida = {"role": m["role"], "content": m.get("content") or ""}
    if m.get("tool_calls"):
        salida["tool_calls"] = [{"id": tc["id"], "type": "function",
                                 "function": {"name": tc["function"]["name"],
                                              "arguments": json.dumps(tc["function"]["arguments"], ensure_ascii=False)}}
                                for tc in m["tool_calls"]]
        salida["content"] = salida["content"] or None
    return salida


def normalizar(nombre: str, args, hoy: str) -> str:
    """Argumentos canónicos: valores por defecto rellenos y «hasta» vacío = hoy."""
    if isinstance(args, str):
        try:
            args = json.loads(args or "{}")
        except json.JSONDecodeError:
            return f"<json no válido> {args}"
    h = REGISTRO.get(nombre)
    if h is None or not isinstance(args, dict):
        return json.dumps(args, sort_keys=True, ensure_ascii=False, default=str)
    try:
        datos = h.argumentos.model_validate(args).model_dump(mode="json")
    except Exception:
        return f"<no válido> {json.dumps(args, sort_keys=True, ensure_ascii=False)}"
    if "hasta" in datos and datos["hasta"] is None and nombre in ("saldo_cuenta", "extracto_cuenta"):
        datos["hasta"] = hoy
    for clave in ("texto", "proveedor", "numero"):
        if isinstance(datos.get(clave), str):
            datos[clave] = datos[clave].strip().lower()
    return json.dumps(datos, sort_keys=True, ensure_ascii=False)


def _decimal(texto: str):
    try:
        return Decimal(texto.replace(".", "").replace(",", "."))
    except InvalidOperation:
        return None


def cifras_inventadas(respuesta: str, resultados: list[str]) -> list[str]:
    """Importes en € de la respuesta que no aparecen (en ningún formato) en los resultados de las herramientas."""
    conocidos = set()
    for r in resultados:
        for n in re.findall(r"-?\d+(?:\.\d+)?", r):
            d = _decimal(n.replace(".", ",")) if n.count(".") == 1 else _decimal(n)
            if d is not None:
                conocidos.add(abs(d.quantize(Decimal("0.01"))))
    inventadas = []
    for m in IMPORTE.findall(respuesta):
        d = _decimal(m)
        if d is not None and abs(d.quantize(Decimal("0.01"))) not in conocidos and d != 0:
            inventadas.append(m)
    return inventadas


def evaluar(proveedor, ejemplos: list, hoy: str, limite: int | None = None, al_avanzar=None) -> dict:
    por_escenario = defaultdict(lambda: defaultdict(int))
    fallos = []
    for n, e in enumerate(ejemplos[:limite] if limite else ejemplos, start=1):
        esc = e.get("meta", {}).get("escenario", "?")
        msgs = e["messages"]
        for i, m in enumerate(msgs):
            if m["role"] != "assistant" or not m.get("entrenar", True):
                continue
            contexto = [_para_api(x) for x in msgs[:i]]
            try:
                r = proveedor.chat(contexto, e["tools"])
            except Exception as error:  # el modelo no responde: cuenta como fallo, sigue con el resto
                por_escenario[esc]["errores_modelo"] += 1
                fallos.append({"escenario": esc, "paso": i, "error": str(error)[:200]})
                continue
            c = por_escenario[esc]
            if m.get("tool_calls"):
                c["pasos_llamada"] += 1
                esperadas = sorted((tc["function"]["name"], normalizar(tc["function"]["name"], tc["function"]["arguments"], hoy))
                                   for tc in m["tool_calls"])
                obtenidas = sorted((tc.name, normalizar(tc.name, tc.arguments, hoy)) for tc in r.tool_calls)
                c["llama"] += bool(r.tool_calls)
                c["herramienta_ok"] += [x[0] for x in esperadas] == [x[0] for x in obtenidas]
                c["argumentos_ok"] += esperadas == obtenidas
                if esperadas != obtenidas:
                    fallos.append({"escenario": esc, "paso": i, "esperado": esperadas, "obtenido": obtenidas,
                                   "texto": r.content[:200]})
            else:
                c["pasos_texto"] += 1
                c["sin_herramienta_ok"] += not r.tool_calls
                resultados = [x["content"] for x in msgs[:i] if x["role"] == "tool"]
                inventadas = cifras_inventadas(r.content, resultados) if not r.tool_calls else []
                c["cifras_inventadas"] += bool(inventadas)
                if r.tool_calls or inventadas:
                    fallos.append({"escenario": esc, "paso": i, "esperado": "texto", "inventadas": inventadas,
                                   "obtenido": [tc.name for tc in r.tool_calls], "texto": r.content[:300]})
        if al_avanzar:
            al_avanzar(n)
    return {"por_escenario": {k: dict(v) for k, v in por_escenario.items()}, "total": _totales(por_escenario),
            "fallos": fallos[:200]}


def _totales(por_escenario) -> dict:
    t = defaultdict(int)
    for c in por_escenario.values():
        for k, v in c.items():
            t[k] += v

    def tasa(a, b):
        return round(t[a] / t[b], 3) if t[b] else None

    return {
        "pasos_llamada": t["pasos_llamada"], "pasos_texto": t["pasos_texto"],
        "llama_cuando_toca": tasa("llama", "pasos_llamada"),
        "herramienta_correcta": tasa("herramienta_ok", "pasos_llamada"),
        "argumentos_correctos": tasa("argumentos_ok", "pasos_llamada"),
        "no_llama_cuando_no_toca": tasa("sin_herramienta_ok", "pasos_texto"),
        "respuestas_con_cifras_inventadas": tasa("cifras_inventadas", "pasos_texto"),
        "errores_modelo": t["errores_modelo"],
    }
