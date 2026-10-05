import json
from datetime import date
from decimal import Decimal, InvalidOperation
from functools import wraps

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q, Sum
from django.db.models.functions import Length
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from .forms import AltaSubcuentaForm
from .modelos_asiento import MODELOS, crear_desde_modelo, previsualizar
from .cuentas_anuales import calcular_cuentas_anuales, evaluar_abreviado
from .forms import AltaSubcuentaForm, DatosEjercicioForm
from .models import LONGITUD_SUBCUENTA, Apunte, Asiento, CuentaContable, DatosEmpresa, Ejercicio
from .services import (LONGITUDES_BASE, asignar_contrapartidas, crear_subcuenta, renumerar,
                       siguiente_codigo)
from .tipos_alta import TIPOS_ALTA


# ===================== Libro diario =====================

MESES = [(1, "Enero"), (2, "Febrero"), (3, "Marzo"), (4, "Abril"), (5, "Mayo"), (6, "Junio"),
         (7, "Julio"), (8, "Agosto"), (9, "Septiembre"), (10, "Octubre"), (11, "Noviembre"),
         (12, "Diciembre")]


def serializar_asiento(asiento):
    # primero las cuentas del Debe y después las del Haber, como en el libro diario
    apuntes = sorted(asiento.apuntes.all(), key=lambda a: (a.haber > 0, a.orden, a.id))
    debe = sum((a.debe for a in apuntes), Decimal("0"))
    haber = sum((a.haber for a in apuntes), Decimal("0"))
    return {
        "id": asiento.id, "numero": asiento.numero, "fecha": asiento.fecha.isoformat(),
        "concepto": asiento.concepto, "estado": asiento.estado,
        "tipo": asiento.tipo, "tipo_label": asiento.get_tipo_display(), "modelo": asiento.modelo,
        "clase": "Simple" if len(apuntes) == 2 else "Compuesto" if len(apuntes) > 2 else "Incompleto",
        "estado_label": asiento.get_estado_display(), "origen_label": asiento.get_origen_display(),
        "referencia_origen": asiento.referencia_origen, "ejercicio": asiento.ejercicio.anio,
        "cerrado": asiento.ejercicio.cerrado, "debe": f"{debe:.2f}", "haber": f"{haber:.2f}",
        "apuntes": [{
            "id": a.id, "cuenta": a.cuenta.codigo, "cuenta_nombre": a.cuenta.nombre,
            "contrapartida": a.contrapartida.codigo if a.contrapartida_id else "",
            "concepto": a.concepto, "debe": f"{a.debe:.2f}", "haber": f"{a.haber:.2f}",
            
        } for a in apuntes],
    }


def api(view):
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        try:
            with transaction.atomic():
                return view(request, *args, **kwargs)
        except ValidationError as e:
            return JsonResponse({"error": " ".join(e.messages)}, status=400)
    return wrapper


def _json(request):
    try:
        return json.loads(request.body or "{}")
    except json.JSONDecodeError:
        raise ValidationError("Datos no válidos.")


def _fecha(valor):
    try:
        return date.fromisoformat(str(valor))
    except ValueError:
        raise ValidationError(f"Fecha no válida: {valor}")


def _importe(valor):
    texto = str(valor or "0").strip().replace(" ", "")
    if "," in texto:
        texto = texto.replace(".", "").replace(",", ".")
    try:
        importe = Decimal(texto or "0").quantize(Decimal("0.01"))
    except InvalidOperation:
        raise ValidationError(f"Importe no válido: {valor}")
    if importe < 0:
        raise ValidationError("Los importes no pueden ser negativos.")
    return importe


def _editable(asiento):
    if asiento.ejercicio.cerrado:
        raise ValidationError(f"El ejercicio {asiento.ejercicio} está cerrado.")


def _aplicar_apunte(apunte, datos):
    if "cuenta" in datos:
        codigo = str(datos["cuenta"]).strip()
        cuenta = CuentaContable.objects.filter(codigo=codigo, activa=True).first()
        if not cuenta:
            raise ValidationError(f"No existe la cuenta {codigo}.")
        apunte.cuenta = cuenta
    if "concepto" in datos:
        apunte.concepto = str(datos["concepto"]).strip()[:255]
    
    if "debe" in datos:
        apunte.debe = _importe(datos["debe"])
        if apunte.debe:
            apunte.haber = Decimal("0")
    if "haber" in datos:
        apunte.haber = _importe(datos["haber"])
        if apunte.haber:
            apunte.debe = Decimal("0")
    apunte.full_clean(validate_constraints=False)


def _tras_cambio(asiento):
    asignar_contrapartidas(asiento, recalcular=True)
    if asiento.estado == Asiento.Estado.CONTABILIZADO and not asiento.cuadrado:
        asiento.estado = Asiento.Estado.BORRADOR
        asiento.save(update_fields=["estado"])
    return JsonResponse(serializar_asiento(asiento))


def libro_diario(request):
    ejercicios = Ejercicio.objects.all()
    anio = request.GET.get("ejercicio")
    ejercicio = ejercicios.filter(anio=anio).first() if anio else ejercicios.first()
    asientos = Asiento.objects.none()
    mes = request.GET.get("mes")
    if ejercicio:
        asientos = (Asiento.objects.filter(ejercicio=ejercicio).select_related("ejercicio")
                    .prefetch_related("apuntes__cuenta", "apuntes__contrapartida").order_by("fecha", "numero"))
        if mes is None:
            ultimo = asientos.order_by("-fecha").first()
            mes = str(ultimo.fecha.month) if ultimo else ""
        if mes:
            asientos = asientos.filter(fecha__month=int(mes))
    anio = ejercicio.anio if ejercicio else date.today().year
    return render(request, "cuadro_cuentas/libro_diario.html", {
        "ejercicios": ejercicios, "ejercicio": ejercicio, "mes": mes or "", "meses": MESES,
        "modelos": _modelos_para_pantalla(),
        "ejercicio_info": {
            "anio": anio,
            "inicio": (ejercicio.fecha_inicio if ejercicio else date(anio, 1, 1)).isoformat(),
            "fin": (ejercicio.fecha_fin if ejercicio else date(anio, 12, 31)).isoformat(),
        },
        "asientos": [serializar_asiento(a) for a in asientos],
        "cuentas": CuentaContable.objects.filter(imputable=True, activa=True),
        
    })


@require_POST
@api
def api_crear_asiento(request):
    datos = _json(request)
    asiento = Asiento(fecha=_fecha(datos.get("fecha")),
                      concepto=(datos.get("concepto") or "Nuevo asiento").strip()[:255],
                      tipo=datos.get("tipo") or Asiento.Tipo.OPERACION)
    asiento.full_clean(exclude=["ejercicio", "numero"], validate_constraints=False)
    asiento.save()
    renumerado = renumerar(asiento.ejercicio)
    asiento.refresh_from_db()
    return JsonResponse({**serializar_asiento(asiento), "renumerado": renumerado}, status=201)


@require_http_methods(["PATCH", "DELETE"])
@api
def api_asiento(request, pk):
    asiento = get_object_or_404(Asiento.objects.select_related("ejercicio"), pk=pk)
    _editable(asiento)
    if request.method == "DELETE":
        if asiento.estado != Asiento.Estado.BORRADOR:
            raise ValidationError("Pasa el asiento a borrador antes de eliminarlo.")
        ejercicio = asiento.ejercicio
        asiento.delete()
        return JsonResponse({"ok": True, "renumerado": renumerar(ejercicio)})
    datos = _json(request)
    if "concepto" in datos:
        concepto = str(datos["concepto"]).strip()
        if not concepto:
            raise ValidationError("El concepto no puede quedar vacío.")
        asiento.concepto = concepto[:255]
    if "fecha" in datos:
        fecha = _fecha(datos["fecha"])
        if Ejercicio.buscar(fecha) != asiento.ejercicio:
            raise ValidationError(f"La fecha debe estar dentro del ejercicio {asiento.ejercicio}.")
        asiento.fecha = fecha
    asiento.full_clean(validate_constraints=False)
    asiento.save()
    renumerado = renumerar(asiento.ejercicio)
    asiento.refresh_from_db()
    return JsonResponse({**serializar_asiento(asiento), "renumerado": renumerado})


@require_POST
@api
def api_estado(request, pk):
    asiento = get_object_or_404(Asiento.objects.select_related("ejercicio"), pk=pk)
    _editable(asiento)
    estado = _json(request).get("estado")
    if estado == Asiento.Estado.CONTABILIZADO:
        asiento.contabilizar()
    elif estado == Asiento.Estado.BORRADOR:
        asiento.estado = Asiento.Estado.BORRADOR
        asiento.save(update_fields=["estado"])
    else:
        raise ValidationError("Estado no válido.")
    return JsonResponse(serializar_asiento(asiento))


@require_POST
@api
def api_crear_apunte(request, pk):
    asiento = get_object_or_404(Asiento.objects.select_related("ejercicio"), pk=pk)
    _editable(asiento)
    datos = _json(request)
    ultimo = asiento.apuntes.order_by("-orden").values_list("orden", flat=True).first()
    apunte = Apunte(asiento=asiento, orden=(ultimo or 0) + 1)
    _aplicar_apunte(apunte, datos)
    if not apunte.concepto:
        apunte.concepto = asiento.concepto
    apunte.save()
    return _tras_cambio(asiento)


@require_http_methods(["PATCH", "DELETE"])
@api
def api_apunte(request, pk):
    apunte = get_object_or_404(Apunte.objects.select_related("asiento__ejercicio"), pk=pk)
    asiento = apunte.asiento
    _editable(asiento)
    if request.method == "DELETE":
        apunte.delete()
    else:
        _aplicar_apunte(apunte, _json(request))
        apunte.save()
    return _tras_cambio(asiento)


@require_POST
@api
def api_ordenar(request, pk):
    asiento = get_object_or_404(Asiento.objects.select_related("ejercicio"), pk=pk)
    _editable(asiento)
    try:
        ids = [int(i) for i in _json(request).get("ids", [])]
    except (TypeError, ValueError):
        raise ValidationError("Orden no válido.")
    apuntes = {a.id: a for a in asiento.apuntes.all()}
    if sorted(ids) != sorted(apuntes):
        raise ValidationError("La lista de líneas no coincide con el asiento.")
    for posicion, apunte_id in enumerate(ids):
        apuntes[apunte_id].orden = posicion
    Apunte.objects.bulk_update(apuntes.values(), ["orden"])
    return JsonResponse(serializar_asiento(asiento))


@require_GET
def api_resumen(request, pk):
    asiento = get_object_or_404(Asiento.objects.select_related("ejercicio"), pk=pk)
    ids = set(asiento.apuntes.values_list("cuenta_id", flat=True))
    base = Apunte.objects.filter(asiento__ejercicio=asiento.ejercicio, cuenta_id__in=ids).order_by()
    hasta = (Q(asiento__fecha__lt=asiento.fecha)
             | Q(asiento__fecha=asiento.fecha, asiento__numero__lte=asiento.numero))

    def saldos(qs):
        return {r["cuenta_id"]: r["d"] - r["h"]
                for r in qs.values("cuenta_id").annotate(d=Sum("debe"), h=Sum("haber"))}

    tras = saldos(base.filter(hasta))
    ejercicio = saldos(base)
    mov = {r["cuenta_id"]: r for r in asiento.apuntes.order_by()
           .values("cuenta_id").annotate(d=Sum("debe"), h=Sum("haber"))}
    cuentas = sorted(CuentaContable.objects.in_bulk(ids).values(), key=lambda c: c.codigo)
    return JsonResponse({
        "asiento": serializar_asiento(asiento),
        "cuentas": [{
            "codigo": c.codigo, "nombre": c.nombre,
            "mov_debe": f"{mov[c.id]['d']:.2f}", "mov_haber": f"{mov[c.id]['h']:.2f}",
            "saldo_tras": f"{tras.get(c.id, 0):.2f}", "saldo_ejercicio": f"{ejercicio.get(c.id, 0):.2f}",
        } for c in cuentas],
    })


def _modelos_para_pantalla():
    """Catálogo de asientos modelo para el diálogo, con las subcuentas que admite cada campo."""
    subcuentas = list(
        CuentaContable.objects.filter(imputable=True, activa=True)
        .annotate(largo=Length("codigo")).filter(largo=LONGITUD_SUBCUENTA)
        .order_by("codigo").values("codigo", "nombre")
    )
    salida = []
    for m in MODELOS:
        campos = []
        for c in m.campos:
            dato = {"nombre": c.nombre, "etiqueta": c.etiqueta, "tipo": c.tipo,
                    "obligatorio": c.obligatorio, "inicial": c.inicial, "ayuda": c.ayuda,
                    "opciones": [list(o) for o in c.opciones]}
            if c.tipo == "cuenta":
                dato["prefijos"] = list(c.prefijos)
                dato["cuentas"] = [x for x in subcuentas if x["codigo"].startswith(c.prefijos)]
            campos.append(dato)
        salida.append({"clave": m.clave, "nombre": m.nombre, "descripcion": m.descripcion,
                       "tipo": m.tipo, "tipo_label": Asiento.Tipo(m.tipo).label, "grupo": m.grupo,
                       "fecha": m.fecha, "con_documento": m.con_documento, "campos": campos})
    return salida


def _datos_modelo(request):
    datos = _json(request)
    return (_fecha(datos.get("fecha")), datos.get("datos") or {},
            str(datos.get("concepto") or ""), str(datos.get("documento") or ""))


@require_POST
def api_modelo_previsualizar(request, clave):
    try:
        fecha, entrada, concepto, documento = _datos_modelo(request)
        return JsonResponse(previsualizar(clave, fecha, entrada, concepto, documento))
    except ValidationError as e:
        return JsonResponse({"error": " ".join(e.messages)}, status=400)


@require_POST
@api
def api_modelo_crear(request, clave):
    fecha, entrada, concepto, documento = _datos_modelo(request)
    asiento = crear_desde_modelo(clave, fecha, entrada, concepto, documento)
    return JsonResponse({"id": asiento.id, "numero": asiento.numero,
                         "ejercicio": asiento.ejercicio.anio, "mes": asiento.fecha.month}, status=201)


# ===================== Cuadro de cuentas =====================

def _tipos_con_cuentas(por_codigo):
    """Catálogo de tipos de alta con el nombre de cada cuenta base (solo las que existen)."""
    todas = [c for c in por_codigo if len(c) in LONGITUDES_BASE]
    return [
        {**t, "bases": [{"codigo": c, "nombre": por_codigo[c].nombre}
                        for c in (t["bases"] if t["bases"] is not None else todas)
                        if c in por_codigo]}
        for t in TIPOS_ALTA
    ]


def plan_cuentas(request):
    form = AltaSubcuentaForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        try:
            subcuenta = crear_subcuenta(d["base"], d["nombre"], d["nif"], d["codigo"])
        except ValidationError as e:
            form.add_error(None, " ".join(e.messages))
        else:
            messages.success(request, f"Alta hecha: {subcuenta.codigo} {subcuenta.nombre}")
            # se mantiene el tipo elegido para dar de alta varios seguidos
            return redirect(f"{reverse('cuadro_cuentas:plan_cuentas')}?tipo={d['tipo']}#c{subcuenta.codigo}")

    cuentas = list(CuentaContable.objects.order_by("codigo"))
    nodos = {c.id: {"cuenta": c, "hijos": []} for c in cuentas}
    arbol = []
    for c in cuentas:
        destino = nodos[c.padre_id]["hijos"] if c.padre_id in nodos else arbol
        destino.append(nodos[c.id])
    por_codigo = {c.codigo: c for c in cuentas}

    return render(request, "cuadro_cuentas/plan_cuentas.html", {
        "form": form,
        "arbol": arbol,
        "tipos": _tipos_con_cuentas(por_codigo),
        "tipo_inicial": form.data.get("tipo") if form.is_bound else request.GET.get("tipo", "proveedor"),
        "base_inicial": form.data.get("base", "") if form.is_bound else "",
        "total_plan": sum(1 for c in cuentas if not c.es_subcuenta),
        "total_subcuentas": sum(1 for c in cuentas if c.es_subcuenta),
        "longitud": LONGITUD_SUBCUENTA,
    })


@require_GET
def api_siguiente_codigo(request):
    base = CuentaContable.objects.filter(codigo=request.GET.get("base", "").strip()).first()
    if not base or len(base.codigo) not in LONGITUDES_BASE:
        return JsonResponse({"error": "Elige una cuenta del plan de 3, 4 o 5 dígitos."}, status=400)
    try:
        codigo = siguiente_codigo(base)
    except ValidationError as e:
        return JsonResponse({"error": " ".join(e.messages)}, status=400)
    _, definicion = base.heredado("definicion")
    _, ubicacion = base.heredado("ubicacion")
    return JsonResponse({
        "codigo": codigo,
        "nombre": base.nombre,
        "resumen": definicion.split("\n", 1)[0],
        "ubicacion": ubicacion,
    })


@require_GET
def api_ficha_cuenta(request, codigo):
    cuenta = get_object_or_404(CuentaContable.objects.select_related("padre"), codigo=codigo)
    origen, definicion = cuenta.heredado("definicion")
    _, ubicacion = cuenta.heredado("ubicacion")

    ruta, antecesor = [], cuenta.padre
    while antecesor:
        ruta.insert(0, {"codigo": antecesor.codigo, "nombre": antecesor.nombre})
        antecesor = antecesor.padre

    # movimientos de la cuenta y de todas las que cuelgan de ella
    t = Apunte.objects.filter(cuenta__codigo__startswith=cuenta.codigo).aggregate(
        debe=Sum("debe"), haber=Sum("haber"))
    debe, haber = t["debe"] or 0, t["haber"] or 0

    return JsonResponse({
        "codigo": cuenta.codigo,
        "nombre": cuenta.nombre,
        "nivel": cuenta.nivel,
        "nif": cuenta.nif,
        "imputable": cuenta.imputable,
        "admite_subcuentas": len(cuenta.codigo) in LONGITUDES_BASE,
        "ruta": ruta,
        "definicion": definicion,
        "definicion_de": origen.codigo if origen and origen != cuenta else "",
        "ubicacion": ubicacion,
        "debe": f"{debe:.2f}",
        "haber": f"{haber:.2f}",
        "saldo": f"{debe - haber:.2f}",
        "hijas": list(cuenta.hijas.order_by("codigo").values("codigo", "nombre", "nif")),
    })

# ===================== Cuentas anuales =====================

def cuentas_anuales(request):
    ejercicios = Ejercicio.objects.all()
    anio = request.GET.get("ejercicio")
    ejercicio = ejercicios.filter(anio=anio).first() if anio else ejercicios.first()

    form = None
    if ejercicio:
        form = DatosEjercicioForm(request.POST or None, instance=ejercicio)
        if request.method == "POST" and form.is_valid():
            form.save()
            messages.success(request, "Datos del ejercicio guardados.")
            return redirect(f"{reverse('cuadro_cuentas:cuentas_anuales')}?ejercicio={ejercicio.anio}")

    contexto = {"ejercicios": ejercicios, "ejercicio": ejercicio, "form": form,
                "empresa": DatosEmpresa.actual()}
    if ejercicio:
        evaluacion = evaluar_abreviado(ejercicio)
        propuesto = "abreviado" if evaluacion["balance"]["puede"] and evaluacion["pyg"]["puede"] else "normal"
        modelo = request.GET.get("modelo") if request.GET.get("modelo") in ("normal", "abreviado") else propuesto
        ca = calcular_cuentas_anuales(ejercicio, modelo)
        balance = ca["balance"]
        contexto.update({
            "modelo": modelo,
            "propuesto": propuesto,
            "evaluacion": evaluacion,
            "ca": ca,
            "estados": [balance, ca["pyg"], ca["ecpn"]],
            "descuadre": balance.secciones[0][2] - balance.secciones[1][2],
            "anio_anterior": ejercicio.anio - 1,
        })
    return render(request, "cuadro_cuentas/cuentas_anuales.html", contexto)