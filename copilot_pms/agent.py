"""
Bucle del copilot: pregunta -> modelo -> herramientas -> modelo -> respuesta.

- El modelo solo ve las herramientas que el contexto puede usar; aun así el
  ejecutor vuelve a comprobar permisos y valida los argumentos.
- Límites explícitos de pasos y de llamadas a herramientas por pregunta.
- La llamada al modelo se reintenta solo ante errores transitorios (timeout,
  429, 5xx). Las herramientas no se reintentan: son de solo lectura y
  deterministas; si fallan, el modelo recibe el error y debe explicarlo.
- Todo queda guardado: pregunta, respuesta, cada herramienta con sus
  argumentos, estado y resultado (LlamadaHerramienta).
"""

import json
import logging
import time

from django.db import transaction

from copilot_pms import conf, prompts
from copilot_pms.llm import get_provider
from copilot_pms.llm.base import ProviderError
from copilot_pms.models import Conversacion, EstadoLlamada, LlamadaHerramienta, Mensaje
from copilot_pms.tools import PENDIENTES
from copilot_pms.tools.base import Contexto, Resultado, disponibles, ejecutar

logger = logging.getLogger("copilot_pms")

MAX_PREGUNTA = 4000

MENSAJE_SIN_MODELO = ("No puedo responder ahora: el modelo de IA no está disponible ({codigo}). "
                      "No se ha dado ningún dato. Vuelve a intentarlo en unos minutos o revisa la "
                      "configuración del copilot.")
MENSAJE_LIMITE = ("No he podido completar la consulta dentro del límite de pasos. Prueba con una "
                  "pregunta más concreta (una cuenta, un periodo, una factura).")
MENSAJE_VACIO = "El modelo no ha devuelto ninguna respuesta. Vuelve a intentarlo."


class PreguntaNoValida(ValueError):
    pass


def _entidad() -> str:
    from cuadro_cuentas.models import DatosEmpresa

    datos = DatosEmpresa.actual()
    if datos is None:
        return "entidad sin configurar en Contabilidad (DatosEmpresa)"
    return f"{datos.denominacion}" + (f" (NIF {datos.nif})" if datos.nif else "")


def _historial(conversacion: Conversacion) -> list[dict]:
    """Últimos turnos en texto. Los resultados de herramientas no se reenvían: se vuelven a consultar."""
    n = int(conf.get("TURNOS_HISTORIAL")) * 2
    mensajes = list(conversacion.mensajes.filter(estado=Mensaje.Estado.OK).order_by("-creado", "-id")[:n])
    return [{"role": m.rol, "content": m.contenido} for m in reversed(mensajes)]


def _contenido_herramienta(resultado: Resultado) -> str:
    salida = resultado.para_modelo()
    texto = json.dumps(salida, ensure_ascii=False)
    maximo = int(conf.get("MAX_CARACTERES_RESULTADO"))
    if len(texto) > maximo:
        salida.pop("datos", None)
        salida.setdefault("avisos", []).append(
            "Resultado demasiado grande para mostrarlo: pide menos filas o un periodo más corto.")
        texto = json.dumps(salida, ensure_ascii=False)
    return texto


def _llamar_modelo(proveedor, mensajes, herramientas):
    reintentos = int(conf.get("PROVIDER_MAX_RETRIES"))
    for intento in range(reintentos + 1):
        try:
            return proveedor.chat(mensajes, herramientas)
        except ProviderError as error:
            if not error.retryable or intento == reintentos:
                raise
            time.sleep(min(error.retry_after or float(conf.get("BACKOFF_SECONDS")) * (2 ** intento), 30))


def preguntar(pregunta: str, ctx: Contexto, conversacion: Conversacion | None = None, proveedor=None) -> Mensaje:
    """Responde una pregunta y devuelve el Mensaje del copilot (con sus llamadas guardadas)."""
    pregunta = (pregunta or "").strip()
    if not pregunta:
        raise PreguntaNoValida("Escribe una pregunta.")
    if len(pregunta) > MAX_PREGUNTA:
        raise PreguntaNoValida(f"La pregunta es demasiado larga (máximo {MAX_PREGUNTA} caracteres).")

    if conversacion is None:
        conversacion = Conversacion.objects.create(
            titulo=pregunta[:120], usuario=ctx.user if getattr(ctx.user, "pk", None) else None,
            actor_label=ctx.actor_label[:150])
    historial = _historial(conversacion)
    Mensaje.objects.create(conversacion=conversacion, rol=Mensaje.Rol.USUARIO, contenido=pregunta)

    proveedor = proveedor or get_provider()
    herramientas = [h.esquema() for h in disponibles(ctx)]
    mensajes = [{"role": "system", "content": prompts.sistema(hoy=ctx.hoy, entidad=_entidad(), pendientes=PENDIENTES)},
                *historial, {"role": "user", "content": pregunta}]

    inicio = time.monotonic()
    registros: list[tuple[str, dict | str, Resultado]] = []
    uso = {"llamadas_modelo": 0, "tokens_entrada": 0, "tokens_salida": 0}
    estado, error_code, modelo, texto = Mensaje.Estado.OK, "", getattr(proveedor, "model", ""), ""
    max_llamadas = int(conf.get("MAX_LLAMADAS_HERRAMIENTA"))

    try:
        for _ in range(int(conf.get("MAX_PASOS"))):
            respuesta = _llamar_modelo(proveedor, mensajes, herramientas)
            uso["llamadas_modelo"] += 1
            uso["tokens_entrada"] += respuesta.input_tokens
            uso["tokens_salida"] += respuesta.output_tokens
            modelo = respuesta.model or modelo

            if not respuesta.tool_calls:
                texto = respuesta.content.strip()
                if not texto:
                    estado, error_code, texto = Mensaje.Estado.ERROR, "EMPTY_ANSWER", MENSAJE_VACIO
                elif respuesta.truncated:
                    texto += "\n\n(Respuesta cortada por el límite de longitud del modelo.)"
                break

            mensajes.append({
                "role": "assistant", "content": respuesta.content or None,
                "tool_calls": [{"id": c.id, "type": "function",
                                "function": {"name": c.name, "arguments": c.arguments}}
                               for c in respuesta.tool_calls],
            })
            for llamada in respuesta.tool_calls:
                if len(registros) >= max_llamadas:
                    resultado = Resultado(EstadoLlamada.DENEGADO, codigo="TOOL_LIMIT",
                                          mensaje="Límite de consultas por pregunta alcanzado.")
                else:
                    resultado = ejecutar(llamada.name, llamada.arguments, ctx)
                registros.append((llamada.name, llamada.arguments, resultado))
                mensajes.append({"role": "tool", "tool_call_id": llamada.id,
                                 "content": _contenido_herramienta(resultado)})
        else:
            estado, error_code, texto = Mensaje.Estado.LIMITE, "STEP_LIMIT", MENSAJE_LIMITE
    except ProviderError as error:
        logger.warning("copilot: error del proveedor %s: %s", error.code, error)
        estado, error_code, texto = Mensaje.Estado.ERROR, error.code, MENSAJE_SIN_MODELO.format(codigo=error.code)

    with transaction.atomic():
        mensaje = Mensaje.objects.create(
            conversacion=conversacion, rol=Mensaje.Rol.COPILOT, contenido=texto, estado=estado,
            error_code=error_code, modelo=modelo[:100], prompt_version=prompts.PROMPT_VERSION,
            duracion_ms=int((time.monotonic() - inicio) * 1000), **uso)
        LlamadaHerramienta.objects.bulk_create([
            LlamadaHerramienta(
                mensaje=mensaje, orden=i, herramienta=nombre[:80], argumentos=_argumentos_guardables(args),
                estado=r.estado, codigo=r.codigo, resultado=r.para_modelo(), fuente=r.fuente[:200],
                duracion_ms=r.duracion_ms)
            for i, (nombre, args, r) in enumerate(registros, start=1)
        ])
        conversacion.save(update_fields=["actualizado"])
    return mensaje


def _argumentos_guardables(argumentos) -> dict:
    if isinstance(argumentos, dict):
        return argumentos
    try:
        valor = json.loads(argumentos or "{}")
        return valor if isinstance(valor, dict) else {"_crudo": str(argumentos)[:2000]}
    except (TypeError, json.JSONDecodeError):
        return {"_crudo": str(argumentos)[:2000]}
