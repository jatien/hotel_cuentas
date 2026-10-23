"""
Pantallas y API del copilot.

    GET  /copilot/                       nueva conversación y conversaciones recientes
    GET  /copilot/<uuid>/                una conversación con los datos consultados
    POST /copilot/preguntar/             pregunta desde el formulario
    POST /copilot/api/preguntar          {"pregunta": "...", "conversacion": "<uuid>"?}  -> JSON
    GET  /copilot/api/herramientas       herramientas disponibles para quien pregunta
"""

import json
import uuid

from django.contrib.auth.views import redirect_to_login
from django.http import Http404, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST

from copilot_pms.access import NoAutenticado, contexto_para
from copilot_pms.agent import PreguntaNoValida, preguntar
from copilot_pms.models import Conversacion, EstadoLlamada
from copilot_pms.tools import PENDIENTES, disponibles, ejecutar

LLAMADAS_OK = {EstadoLlamada.OK, EstadoLlamada.VACIO}


def _conversaciones(ctx):
    qs = Conversacion.objects.all()
    if ctx.login_requerido:
        qs = qs.filter(usuario=ctx.user)
    return qs


def _conversacion(ctx, public_id):
    if not public_id:
        return None
    try:
        public_id = uuid.UUID(str(public_id))
    except ValueError:
        raise Http404("Conversación no encontrada.")
    conversacion = _conversaciones(ctx).filter(public_id=public_id).first()
    if conversacion is None:
        raise Http404("Conversación no encontrada.")
    return conversacion


def _enlaces(valor, salida=None):
    """Enlaces (claves «enlace*») dentro del resultado guardado de una herramienta."""
    salida = [] if salida is None else salida
    if isinstance(valor, dict):
        for clave, v in valor.items():
            if clave.startswith("enlace") and isinstance(v, str) and v.startswith("/"):
                salida.append((clave.replace("enlace_", "").replace("enlace", "informe").replace("_", " "), v))
            else:
                _enlaces(v, salida)
    elif isinstance(valor, list):
        for v in valor:
            _enlaces(v, salida)
    return salida


def _con_ctx(vista):
    def envoltura(request, *args, **kwargs):
        try:
            ctx = contexto_para(request)
        except NoAutenticado:
            return redirect_to_login(request.get_full_path())
        return vista(request, ctx, *args, **kwargs)

    envoltura.__name__ = vista.__name__
    return envoltura


@require_GET
@_con_ctx
def index(request, ctx):
    return render(request, "copilot_pms/chat.html", {
        "conversacion": None, "mensajes": [], "recientes": _conversaciones(ctx)[:15],
        "herramientas": disponibles(ctx), "pendientes": PENDIENTES,
    })


@require_GET
@_con_ctx
def conversacion(request, ctx, public_id):
    conv = _conversacion(ctx, public_id)
    mensajes = list(conv.mensajes.prefetch_related("llamadas"))
    for m in mensajes:
        m.consultas = [{"llamada": ll, "ok": ll.estado in LLAMADAS_OK, "enlaces": _enlaces(ll.resultado),
                        "fecha_datos": ll.resultado.get("fecha_datos", ""),
                        "avisos": ll.resultado.get("avisos", [])}
                       for ll in m.llamadas.all()]
        m.graficos = [(f"grafico-{c['llamada'].pk}", c["llamada"].resultado["datos"]["grafico"])
                      for c in m.consultas
                      if c["ok"] and c["llamada"].herramienta == "grafico"
                      and "grafico" in c["llamada"].resultado.get("datos", {})]
    return render(request, "copilot_pms/chat.html", {
        "conversacion": conv, "mensajes": mensajes, "recientes": _conversaciones(ctx)[:15],
        "herramientas": disponibles(ctx), "pendientes": PENDIENTES,
    })


@require_POST
@_con_ctx
def preguntar_form(request, ctx):
    conv = _conversacion(ctx, request.POST.get("conversacion") or None)
    try:
        mensaje = preguntar(request.POST.get("pregunta", ""), ctx, conv)
    except PreguntaNoValida:
        return redirect(reverse("copilot_pms:conversacion", args=[conv.public_id]) if conv
                        else reverse("copilot_pms:index"))
    return redirect(reverse("copilot_pms:conversacion", args=[mensaje.conversacion.public_id]) + "#ultimo")


@require_GET
@_con_ctx
def galeria(request, ctx):
    """Plantillas de gráficos sin IA: se elige plantilla y parámetros y se dibuja. Usa la misma herramienta
    (validación y permisos) que el copilot."""
    from copilot_pms.graficos import PLANTILLAS, TIPOS_ETIQUETAS

    plantillas = [p for p in PLANTILLAS.values() if ctx.tiene_permisos((p.permiso,))]
    args, resultado = {}, None
    if request.GET.get("plantilla"):
        args["plantilla"] = request.GET["plantilla"]
        for campo in ("anio", "mes", "desde_mes", "hasta_mes"):
            if request.GET.get(campo, "").isdigit():
                args[campo] = int(request.GET[campo])
        for campo in ("tipo", "cuenta", "titulo"):
            if request.GET.get(campo):
                args[campo] = request.GET[campo]
        if request.GET.get("series"):
            args["series"] = [x.strip() for x in request.GET["series"].split(",") if x.strip()]
        if request.GET.get("comparar_con"):
            args["comparar_con"] = [int(x) for x in request.GET["comparar_con"].split(",") if x.strip().isdigit()]
        args["con_iva"] = request.GET.get("con_iva") == "1"
        resultado = ejecutar("grafico", args, ctx)
    return render(request, "copilot_pms/galeria.html", {
        "plantillas": plantillas, "tipos": TIPOS_ETIQUETAS.items(), "args": args, "resultado": resultado,
        "grafico": resultado.datos.get("grafico") if resultado and resultado.correcto else None,
        "anio_actual": ctx.hoy.year,
    })


# --------------------------------------------------------------------------- API
def _error(status, code, message):
    return JsonResponse({"error": {"code": code, "message": message}}, status=status)


def _api_ctx(request):
    try:
        return contexto_para(request), None
    except NoAutenticado:
        return None, _error(401, "NOT_AUTHENTICATED", "Hace falta iniciar sesión.")


@require_POST
def api_preguntar(request):
    ctx, error = _api_ctx(request)
    if error:
        return error
    try:
        cuerpo = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        return _error(400, "BAD_JSON", "El cuerpo no es JSON válido.")
    if not isinstance(cuerpo, dict):
        return _error(400, "BAD_JSON", "El cuerpo debe ser un objeto JSON.")
    try:
        conv = _conversacion(ctx, cuerpo.get("conversacion"))
    except Http404:
        return _error(404, "NOT_FOUND", "Conversación no encontrada.")
    try:
        mensaje = preguntar(str(cuerpo.get("pregunta", "")), ctx, conv)
    except PreguntaNoValida as exc:
        return _error(400, "BAD_QUESTION", str(exc))
    return JsonResponse({
        "conversacion": str(mensaje.conversacion.public_id),
        "estado": mensaje.estado,
        "error_code": mensaje.error_code or None,
        "respuesta": mensaje.contenido,
        "modelo": mensaje.modelo,
        "consultas": [{"herramienta": ll.herramienta, "argumentos": ll.argumentos, "estado": ll.estado,
                       "codigo": ll.codigo or None, "fuente": ll.fuente or None,
                       "fecha_datos": ll.resultado.get("fecha_datos"),
                       "enlaces": [{"nombre": n, "url": u} for n, u in _enlaces(ll.resultado)]}
                      for ll in mensaje.llamadas.all()],
    }, json_dumps_params={"ensure_ascii": False})


@require_GET
def api_herramientas(request):
    ctx, error = _api_ctx(request)
    if error:
        return error
    return JsonResponse({
        "herramientas": [{**h.esquema()["function"], "dominio": h.dominio, "escritura": h.escritura}
                         for h in disponibles(ctx)],
        "pendientes": [{"capacidad": c, "motivo": m} for c, m in PENDIENTES],
    }, json_dumps_params={"ensure_ascii": False})
