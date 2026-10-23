"""Hojas de la especificación contable: empresa y ejercicios, diario (resumido y detallado),
ficha del asiento, balance de sumas y saldos, balance de comprobación y comparativa.

Todas leen los mismos asientos y apuntes; ningún informe guarda copias de los importes."""
import json
from datetime import date

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Max
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods, require_POST

from . import integridad
from .activo import ejercicio_activo
from .consultas import cargar_apuntes, comparativa as calcular_comparativa, contrapartidas, diario as filtrar_diario
from .consultas import sumas_y_saldos_periodo
from .exportar import exportar, formato_pedido
from .forms_contabilidad import AdjuntoForm, CabeceraForm, DiarioForm, EjercicioForm, EmpresaForm, SumasForm
from .models import Adjunto, Apunte, Asiento, CuentaContable, DatosEmpresa, Ejercicio, HistorialAsiento, PreferenciaTabla
from .services import duplicar_asiento, puntear, rectificar_asiento, renumerar, revertir_asiento

POR_PAGINA = 50


def _usuario(request):
    return request.user if request.user.is_authenticated else None


# ===================== Empresa y ejercicios =====================

def empresa(request):
    empresa_actual = DatosEmpresa.actual()
    form_empresa = EmpresaForm(instance=empresa_actual, prefix="emp")
    form_ejercicio = EjercicioForm(prefix="ej", initial=_siguiente_ejercicio())
    url = reverse("cuadro_cuentas:empresa")

    if request.method == "POST":
        accion = request.POST.get("accion")
        if accion == "empresa":
            form_empresa = EmpresaForm(request.POST, instance=empresa_actual, prefix="emp")
            if form_empresa.is_valid():
                with transaction.atomic():
                    guardada = form_empresa.save()
                    Ejercicio.objects.filter(empresa__isnull=True).update(empresa=guardada)
                messages.success(request, "Datos de la empresa guardados.")
                return redirect(url)
        elif accion == "ejercicio":
            form_ejercicio = EjercicioForm(request.POST, prefix="ej")
            if form_ejercicio.is_valid():
                nuevo = form_ejercicio.save(commit=False)
                nuevo.empresa = empresa_actual
                nuevo.save()
                messages.success(request, f"Ejercicio {nuevo} creado.")
                return redirect(f"{url}?ejercicio={nuevo.anio}")
        elif accion in ("cerrar", "reabrir"):
            ejercicio = get_object_or_404(Ejercicio, pk=request.POST.get("id"))
            if accion == "cerrar":
                borradores = ejercicio.asientos.filter(estado=Asiento.Estado.BORRADOR).count()
                if borradores:
                    messages.error(request, f"El ejercicio {ejercicio} tiene {borradores} borradores: "
                                            "contabilízalos o elimínalos antes de cerrarlo.")
                    return redirect(url)
            ejercicio.cerrado = accion == "cerrar"
            ejercicio.save(update_fields=["cerrado"])
            messages.success(request, f"Ejercicio {ejercicio} {'cerrado' if ejercicio.cerrado else 'reabierto'}.")
            return redirect(url)

    ejercicios, activo = ejercicio_activo(request)
    filas = ejercicios.annotate(n=Count("asientos"), ultimo_numero=Max("asientos__numero"),
                                ultimo_registro=Max("asientos__fecha"), ultimo_alta=Max("asientos__creado"))
    return render(request, "cuadro_cuentas/empresa.html", {
        "empresa": empresa_actual, "form_empresa": form_empresa, "form_ejercicio": form_ejercicio,
        "ejercicios": filas, "ejercicio": activo,
    })


def _siguiente_ejercicio():
    ultimo = Ejercicio.objects.order_by("-fecha_fin").first()
    anio = ultimo.anio + 1 if ultimo else date.today().year
    return {"anio": anio, "fecha_inicio": date(anio, 1, 1), "fecha_fin": date(anio, 12, 31)}


# ===================== Diario =====================

COLUMNAS_APUNTES = ["Asiento", "Apunte", "Fecha", "Número", "Documento", "Cuenta", "Título", "Concepto",
                    "Debe", "Haber", "Contrapartida", "Punteo"]


def diario(request):
    ejercicios, ejercicio = ejercicio_activo(request)
    form = DiarioForm(request.GET or None)
    filtros = form.cleaned_data if form.is_bound and form.is_valid() else {}
    vista = filtros.get("vista") or "detalle"
    contexto = {"ejercicios": ejercicios, "ejercicio": ejercicio, "form": form, "vista": vista,
                "cuentas": CuentaContable.objects.filter(imputable=True).only("codigo", "nombre"),
                "punteos": [c for c in Apunte.Punteo.choices if c[0] in Apunte.PUNTEO_MANUAL]}
    if not ejercicio:
        return render(request, "cuadro_cuentas/diario.html", contexto)

    asientos = filtrar_diario(ejercicio, filtros)
    if formato := formato_pedido(request):
        return _exportar_diario(formato, ejercicio, asientos, vista)

    pagina = Paginator(asientos, POR_PAGINA).get_page(request.GET.get("pagina"))
    if vista == "detalle":
        cargar_apuntes(pagina.object_list)
    # totales de TODO el filtro (no solo de la página visible): solo lo contabilizado
    totales = [a for a in asientos.values("estado", "total_debe", "total_haber")]
    oficiales = [a for a in totales if a["estado"] == Asiento.Estado.CONTABILIZADO]
    contexto.update({
        "pagina": pagina,
        "total_asientos": pagina.paginator.count,
        "borradores": sum(1 for a in totales if a["estado"] == Asiento.Estado.BORRADOR),
        "total_debe": sum((a["total_debe"] or 0 for a in oficiales), 0),
        "total_haber": sum((a["total_haber"] or 0 for a in oficiales), 0),
        "consulta": _sin_pagina(request),
    })
    return render(request, "cuadro_cuentas/diario.html", contexto)


def _sin_pagina(request):
    params = request.GET.copy()
    params.pop("pagina", None)
    params.pop("exportar", None)
    return params.urlencode()


def _exportar_diario(formato, ejercicio, asientos, vista):
    if vista == "resumen":
        cabeceras = ["Asiento", "Fecha", "Número", "Documento", "Título", "Concepto", "Tipo", "Estado",
                     "Apuntes", "Debe", "Haber", "Diferencia"]
        filas = ([a.numero, a.fecha, a.numero_externo, a.get_tipo_documento_display() if a.tipo_documento else "",
                  a.titulo, a.concepto, a.get_tipo_display(), a.get_estado_display(), a.n_apuntes,
                  a.total_debe or 0, a.total_haber or 0, (a.total_debe or 0) - (a.total_haber or 0)]
                 for a in asientos)
        return exportar(formato, f"diario_resumen_{ejercicio.anio}", cabeceras, filas)

    def filas():
        lote = list(asientos)
        cargar_apuntes(lote)
        for a in lote:
            for ap, contra in a.lineas:
                yield [a.numero, ap.orden, a.fecha, a.numero_externo,
                       a.get_tipo_documento_display() if a.tipo_documento else "", ap.cuenta.codigo,
                       ap.cuenta.nombre, ap.concepto or a.concepto, ap.debe, ap.haber,
                       contra.texto if not contra.multiple else contra.detalle, ap.get_punteo_display()]
    return exportar(formato, f"diario_{ejercicio.anio}", COLUMNAS_APUNTES, filas())


# ===================== Ficha del asiento =====================

def asiento(request, pk):
    asiento = get_object_or_404(Asiento.objects.select_related("ejercicio", "hoja", "revierte", "rectifica",
                                                               "creado_por", "contabilizado_por"), pk=pk)
    url = reverse("cuadro_cuentas:asiento", args=[asiento.pk])
    usuario = _usuario(request)
    form = CabeceraForm(instance=asiento) if asiento.editable else None
    form_adjunto = AdjuntoForm()

    if request.method == "POST":
        accion = request.POST.get("accion")
        try:
            with transaction.atomic():
                if accion == "cabecera":
                    if not asiento.editable:
                        raise ValidationError("Solo se modifica la cabecera de un borrador.")
                    form = CabeceraForm(request.POST, instance=asiento)
                    if form.is_valid():
                        form.save()
                        renumerar(asiento.ejercicio)
                        messages.success(request, "Cabecera guardada.")
                        return redirect(url)
                elif accion == "contabilizar":
                    if asiento.ejercicio.cerrado:
                        raise ValidationError(f"El ejercicio {asiento.ejercicio} está cerrado.")
                    asiento.contabilizar(usuario, permitir_duplicado=request.POST.get("confirmar_duplicado") == "1")
                    messages.success(request, f"Asiento {asiento.numero} contabilizado.")
                    return redirect(url)
                elif accion == "anular":
                    asiento.anular(request.POST.get("motivo"), usuario)
                    messages.success(request, f"Asiento {asiento.numero} anulado. Se conserva pero ya no suma.")
                    return redirect(url)
                elif accion in ("revertir", "rectificar"):
                    fecha = date.fromisoformat(request.POST.get("fecha") or asiento.fecha.isoformat())
                    motivo = request.POST.get("motivo", "")
                    if accion == "revertir":
                        reversion = revertir_asiento(asiento, fecha, usuario, motivo)
                        messages.success(request, f"Creado el asiento {reversion.numero} que revierte al {asiento.numero}.")
                        return redirect(reverse("cuadro_cuentas:asiento", args=[reversion.pk]))
                    reversion, copia = rectificar_asiento(asiento, fecha, usuario, motivo)
                    messages.success(request, f"Revertido con el asiento {reversion.numero}. Corrige el borrador "
                                              f"{copia.numero} y contabilízalo.")
                    return redirect(reverse("cuadro_cuentas:asiento", args=[copia.pk]))
                elif accion == "duplicar":
                    copia = duplicar_asiento(asiento, usuario=usuario)
                    messages.success(request, f"Copia creada en borrador: asiento {copia.numero}.")
                    return redirect(reverse("cuadro_cuentas:asiento", args=[copia.pk]))
                elif accion == "borrar":
                    if not asiento.editable:
                        raise ValidationError("Solo se eliminan borradores.")
                    from .views import borrar_borrador
                    ejercicio = asiento.ejercicio
                    borrar_borrador(asiento, usuario)
                    renumerar(ejercicio)
                    messages.success(request, "Borrador eliminado.")
                    return redirect(reverse("cuadro_cuentas:diario"))
                elif accion == "adjuntar":
                    if asiento.ejercicio.cerrado:
                        raise ValidationError(f"El ejercicio {asiento.ejercicio} está cerrado.")
                    form_adjunto = AdjuntoForm(request.POST, request.FILES)
                    if form_adjunto.is_valid():
                        adjunto = form_adjunto.save(commit=False)
                        adjunto.asiento = asiento
                        adjunto.subido_por = usuario
                        adjunto.full_clean()
                        adjunto.save()
                        HistorialAsiento.registrar(asiento, HistorialAsiento.Accion.DOCUMENTO, usuario,
                                                   f"Añadido {adjunto.get_tipo_display().lower()} {adjunto}")
                        messages.success(request, "Documento añadido.")
                        return redirect(url)
                elif accion == "quitar_adjunto":
                    adjunto = get_object_or_404(Adjunto, pk=request.POST.get("id"), asiento=asiento)
                    if asiento.estado != Asiento.Estado.BORRADOR:
                        raise ValidationError("Los documentos de un asiento contabilizado se conservan.")
                    HistorialAsiento.registrar(asiento, HistorialAsiento.Accion.DOCUMENTO, usuario,
                                               f"Quitado {adjunto}")
                    adjunto.delete()
                    return redirect(url)
        except ValidationError as e:
            if getattr(e, "code", None) == "duplicado":
                messages.warning(request, " ".join(e.messages))
                return redirect(f"{url}?duplicado=1")
            messages.error(request, " ".join(e.messages))
            return redirect(url)

    lineas = list(asiento.apuntes.select_related("cuenta", "punteado_por").order_by("orden", "id"))
    contras = contrapartidas(lineas)
    debe = sum((a.debe for a in lineas), 0)
    haber = sum((a.haber for a in lineas), 0)
    return render(request, "cuadro_cuentas/asiento.html", {
        "asiento": asiento, "form": form, "form_adjunto": form_adjunto,
        "lineas": [(a, contras[a.id]) for a in lineas],
        "debe": debe, "haber": haber, "diferencia": debe - haber,
        "errores": asiento.errores_contabilizacion() if asiento.estado == Asiento.Estado.BORRADOR else [],
        "duplicados": asiento.posibles_duplicados() if request.GET.get("duplicado") or asiento.estado == "borrador" else [],
        "pedir_confirmacion": bool(request.GET.get("duplicado")),
        "historial": asiento.historial.select_related("usuario")[:50],
        "reversiones": asiento.reversiones.all(), "rectificaciones": asiento.rectificaciones.all(),
        "puede_revertir": (asiento.tipo not in Asiento.TIPOS_UNICOS and not asiento.revierte_id
                           and not asiento.reversiones.exclude(estado=Asiento.Estado.ANULADO).exists()),
        "punteos": [c for c in Apunte.Punteo.choices if c[0] in Apunte.PUNTEO_MANUAL],
        "hoy": date.today() if asiento.ejercicio.fecha_inicio <= date.today() <= asiento.ejercicio.fecha_fin
        else asiento.ejercicio.fecha_fin,
        "libro_url": f"{reverse('cuadro_cuentas:libro_diario')}?ejercicio={asiento.ejercicio.anio}"
                     f"&mes={asiento.fecha.month}&hoja=#a{asiento.pk}",
    })


# ===================== Punteo =====================

@require_POST
def api_punteo(request):
    """Marca el punteo de uno o varios apuntes. Nunca cambia importes, fechas, cuentas ni asientos."""
    try:
        datos = json.loads(request.body or "{}")
        ids = [int(i) for i in datos.get("ids", [])]
    except (json.JSONDecodeError, TypeError, ValueError):
        return JsonResponse({"error": "Datos no válidos."}, status=400)
    if not ids:
        return JsonResponse({"error": "No hay apuntes seleccionados."}, status=400)
    apuntes = list(Apunte.objects.filter(id__in=ids).select_related("asiento__ejercicio"))
    if len(apuntes) != len(set(ids)):
        return JsonResponse({"error": "Algún apunte no existe."}, status=404)
    try:
        with transaction.atomic():
            n = puntear(apuntes, datos.get("estado", ""), _usuario(request), datos.get("nota"))
            for asiento_id in {a.asiento_id for a in apuntes}:
                a = next(x for x in apuntes if x.asiento_id == asiento_id).asiento
                HistorialAsiento.registrar(a, HistorialAsiento.Accion.PUNTEO, request.user,
                                           f"{sum(1 for x in apuntes if x.asiento_id == asiento_id)} apuntes: "
                                           f"{Apunte.Punteo(datos.get('estado', '')).label.lower()}")
    except ValidationError as e:
        return JsonResponse({"error": " ".join(e.messages)}, status=400)
    return JsonResponse({"ok": True, "n": n, "estado": datos.get("estado", ""),
                         "label": Apunte.Punteo(datos.get("estado", "")).label})


# ===================== Preferencias de columnas =====================

@require_http_methods(["GET", "POST"])
def api_preferencias(request, tabla):
    """Columnas ocultas, orden y anchos por usuario. Sin sesión iniciada se guardan en el navegador."""
    if not request.user.is_authenticated:
        return JsonResponse({"columnas": None, "servidor": False})
    if request.method == "GET":
        pref = PreferenciaTabla.objects.filter(usuario=request.user, tabla=tabla[:40]).first()
        return JsonResponse({"columnas": pref.columnas if pref else None, "servidor": True})
    try:
        columnas = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": "Datos no válidos."}, status=400)
    if not isinstance(columnas, dict) or len(request.body) > 5000:
        return JsonResponse({"error": "Preferencias no válidas."}, status=400)
    PreferenciaTabla.objects.update_or_create(usuario=request.user, tabla=tabla[:40], defaults={"columnas": columnas})
    return JsonResponse({"ok": True, "servidor": True})


# ===================== Balances =====================

def _sumas_form(request):
    datos = request.GET.copy()
    if "enviado" not in datos:          # llegada desde un enlace: valores por defecto
        datos.setdefault("nivel", "8")
        datos["apertura"] = "on"
    form = SumasForm(datos)
    return form, (form.cleaned_data if form.is_valid() else None)


def sumas_saldos(request):
    ejercicios, ejercicio = ejercicio_activo(request)
    form, filtros = _sumas_form(request)
    contexto = {"ejercicios": ejercicios, "ejercicio": ejercicio, "form": form}
    if ejercicio and filtros is not None:
        desde = filtros.get("desde") or ejercicio.fecha_inicio
        hasta = filtros.get("hasta") or ejercicio.fecha_fin
        filas, totales, comprobacion = sumas_y_saldos_periodo(
            ejercicio, desde, hasta, filtros["nivel"] or 8, filtros["cuenta_desde"], filtros["cuenta_hasta"],
            sin_movimiento=filtros["sin_movimiento"], incluir_apertura=filtros["apertura"],
            incluir_cierre=filtros["cierre"], solo_contabilizados=not filtros["borradores"])
        if formato := formato_pedido(request):
            cabeceras = ["Código", "Título", "Debe anterior", "Haber anterior", "Debe periodo", "Haber periodo",
                         "Debe acumulado", "Haber acumulado", "Saldo deudor", "Saldo acreedor"]
            datos = [[f.codigo, f.nombre, f.anterior_debe, f.anterior_haber, f.periodo_debe, f.periodo_haber,
                      f.acumulado_debe, f.acumulado_haber, f.saldo_deudor, f.saldo_acreedor] for f in filas]
            datos.append(["", "TOTALES"] + [totales[c] for c in ("anterior_debe", "anterior_haber", "periodo_debe",
                                                                 "periodo_haber", "acumulado_debe", "acumulado_haber",
                                                                 "saldo_deudor", "saldo_acreedor")])
            return exportar(formato, f"sumas_y_saldos_{ejercicio.anio}", cabeceras, datos)
        contexto.update({"filas": filas, "totales": totales, "comprobacion": comprobacion,
                         "desde": desde, "hasta": hasta, "consulta": _sin_pagina(request),
                         "solo_contabilizados": not filtros["borradores"]})
    return render(request, "cuadro_cuentas/sumas_saldos.html", contexto)


def comprobacion(request):
    ejercicios, ejercicio = ejercicio_activo(request)
    controles = integridad.comprobar(ejercicio) if ejercicio else []
    return render(request, "cuadro_cuentas/comprobacion.html", {
        "ejercicios": ejercicios, "ejercicio": ejercicio, "controles": controles,
        "errores": sum(1 for c in controles if c.estado == integridad.ERROR),
        "avisos": sum(1 for c in controles if c.estado == integridad.AVISO),
    })


def comparativa(request):
    ejercicios, ejercicio = ejercicio_activo(request)
    anterior_anio = request.GET.get("anterior")
    anterior = (ejercicios.filter(anio=anterior_anio).first() if anterior_anio and anterior_anio.isdigit()
                else ejercicios.filter(fecha_fin__lt=ejercicio.fecha_inicio).order_by("-fecha_fin").first()
                if ejercicio else None)
    try:
        nivel = int(request.GET.get("nivel", 3))
    except ValueError:
        nivel = 3
    nivel = nivel if nivel in (1, 2, 3, 4, 5, 8) else 3
    filas = calcular_comparativa(ejercicio, anterior, nivel) if ejercicio else []
    if ejercicio and (formato := formato_pedido(request)):
        cabeceras = ["Código", "Título", str(ejercicio), str(anterior or "Anterior"), "Variación", "Variación %"]
        return exportar(formato, f"comparativa_{ejercicio.anio}", cabeceras,
                        [[f["codigo"], f["nombre"], f["actual"], f["anterior"], f["variacion"], f["porcentaje"]]
                         for f in filas])
    return render(request, "cuadro_cuentas/comparativa.html", {
        "ejercicios": ejercicios, "ejercicio": ejercicio, "anterior": anterior, "nivel": nivel, "filas": filas,
        "niveles": [1, 2, 3, 4, 5, 8], "consulta": _sin_pagina(request),
    })
