import json
from datetime import date
from decimal import Decimal, InvalidOperation
from functools import wraps

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q, Sum
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from .models import DEPARTAMENTOS, Apunte, Asiento, CuentaContable, Ejercicio
from .services import asignar_contrapartidas

MESES = [(1, "Enero"), (2, "Febrero"), (3, "Marzo"), (4, "Abril"), (5, "Mayo"), (6, "Junio"),
         (7, "Julio"), (8, "Agosto"), (9, "Septiembre"), (10, "Octubre"), (11, "Noviembre"),
         (12, "Diciembre")]


# ---------- utilidades ----------

def serializar_asiento(asiento):
    apuntes = list(asiento.apuntes.all())
    debe = sum((a.debe for a in apuntes), Decimal("0"))
    haber = sum((a.haber for a in apuntes), Decimal("0"))
    return {
        "id": asiento.id,
        "numero": asiento.numero,
        "fecha": asiento.fecha.isoformat(),
        "concepto": asiento.concepto,
        "estado": asiento.estado,
        "estado_label": asiento.get_estado_display(),
        "origen_label": asiento.get_origen_display(),
        "referencia_origen": asiento.referencia_origen,
        "ejercicio": asiento.ejercicio.anio,
        "cerrado": asiento.ejercicio.cerrado,
        "debe": f"{debe:.2f}",
        "haber": f"{haber:.2f}",
        "apuntes": [{
            "id": a.id,
            "cuenta": a.cuenta.codigo,
            "cuenta_nombre": a.cuenta.nombre,
            "contrapartida": a.contrapartida.codigo if a.contrapartida_id else "",
            "concepto": a.concepto,
            "debe": f"{a.debe:.2f}",
            "haber": f"{a.haber:.2f}",
            "departamento": a.departamento,
        } for a in apuntes],
    }


def api(view):
    """Ejecuta la vista en una transacción y convierte ValidationError en JSON 400."""
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
    """Acepta '1.234,56', '1234,56' o '1234.56'."""
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
    if "departamento" in datos:
        dep = datos["departamento"] or ""
        if dep and dep not in dict(DEPARTAMENTOS):
            raise ValidationError(f"Departamento no válido: {dep}")
        apunte.departamento = dep
    if "debe" in datos:
        apunte.debe = _importe(datos["debe"])
        if apunte.debe:
            apunte.haber = Decimal("0")
    if "haber" in datos:
        apunte.haber = _importe(datos["haber"])
        if apunte.haber:
            apunte.debe = Decimal("0")
    apunte.full_clean(validate_constraints=False)  # la BD sigue aplicando la restricción


def _tras_cambio(asiento):
    asignar_contrapartidas(asiento, recalcular=True)
    if asiento.estado == Asiento.Estado.CONTABILIZADO and not asiento.cuadrado:
        asiento.estado = Asiento.Estado.BORRADOR
        asiento.save(update_fields=["estado"])
    return JsonResponse(serializar_asiento(asiento))


# ---------- página ----------

def libro_diario(request):
    ejercicios = Ejercicio.objects.all()
    anio = request.GET.get("ejercicio")
    ejercicio = ejercicios.filter(anio=anio).first() if anio else ejercicios.first()

    asientos = Asiento.objects.none()
    mes = request.GET.get("mes")
    if ejercicio:
        asientos = (Asiento.objects.filter(ejercicio=ejercicio)
                    .select_related("ejercicio")
                    .prefetch_related("apuntes__cuenta", "apuntes__contrapartida")
                    .order_by("fecha", "numero"))
        if mes is None:  # por defecto, el mes del último asiento
            ultimo = asientos.order_by("-fecha").first()
            mes = str(ultimo.fecha.month) if ultimo else ""
        if mes:
            asientos = asientos.filter(fecha__month=int(mes))

    return render(request, "cuadro_cuentas/libro_diario.html", {
        "ejercicios": ejercicios,
        "ejercicio": ejercicio,
        "mes": mes or "",
        "meses": MESES,
        "asientos": [serializar_asiento(a) for a in asientos],
        "cuentas": CuentaContable.objects.filter(imputable=True, activa=True),
        "departamentos": DEPARTAMENTOS,
    })


# ---------- API ----------

@require_POST
@api
def api_crear_asiento(request):
    datos = _json(request)
    asiento = Asiento(fecha=_fecha(datos.get("fecha")),
                      concepto=(datos.get("concepto") or "Nuevo asiento").strip()[:255])
    asiento.full_clean(exclude=["ejercicio", "numero"], validate_constraints=False)
    asiento.save()
    return JsonResponse(serializar_asiento(asiento), status=201)


@require_http_methods(["PATCH", "DELETE"])
@api
def api_asiento(request, pk):
    asiento = get_object_or_404(Asiento.objects.select_related("ejercicio"), pk=pk)
    _editable(asiento)

    if request.method == "DELETE":
        if asiento.estado != Asiento.Estado.BORRADOR:
            raise ValidationError("Pasa el asiento a borrador antes de eliminarlo.")
        asiento.delete()
        return JsonResponse({"ok": True})

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
    return JsonResponse(serializar_asiento(asiento))


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

    # .order_by() vacío: evita que la ordenación del Meta se cuele en el GROUP BY
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
            "codigo": c.codigo,
            "nombre": c.nombre,
            "mov_debe": f"{mov[c.id]['d']:.2f}",
            "mov_haber": f"{mov[c.id]['h']:.2f}",
            "saldo_tras": f"{tras.get(c.id, 0):.2f}",
            "saldo_ejercicio": f"{ejercicio.get(c.id, 0):.2f}",
        } for c in cuentas],
    })