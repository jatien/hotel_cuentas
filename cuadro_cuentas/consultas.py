"""Consultas sobre los apuntes: sumas y saldos por cuenta, extracto (libro mayor) de una
cuenta con saldo acumulado, y buscador de apuntes por importe, texto, cuenta, fechas...

Todas trabajan dentro de un ejercicio. Así los saldos son siempre correctos: el saldo
inicial de un año sale del asiento de apertura y no se mezcla con el cierre del anterior.
"""
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from django.db.models import Count, F, Q, Sum

from .models import Apunte, Asiento, CuentaContable

CERO = Decimal("0")


def parse_importe(texto):
    """'1.234,56', '1234,56', '1234.56' o '1 234 €' -> Decimal. Cadena vacía -> None."""
    texto = str(texto or "").strip().replace(" ", "").replace("€", "")
    if not texto:
        return None
    if "," in texto:
        texto = texto.replace(".", "").replace(",", ".")
    try:
        return Decimal(texto).quantize(Decimal("0.01"))
    except InvalidOperation:
        raise ValueError(f"Importe no válido: {texto}")


def _base(ejercicio, solo_contabilizados=False, incluir_cierre=False):
    qs = Apunte.objects.filter(asiento__ejercicio=ejercicio)
    if solo_contabilizados:
        qs = qs.filter(asiento__estado=Asiento.Estado.CONTABILIZADO)
    if not incluir_cierre:
        # el asiento de cierre deja todo a cero: sin él se ve el saldo real a fin de año
        qs = qs.exclude(asiento__tipo=Asiento.Tipo.CIERRE)
    return qs


# ---------------------------------------------------------------- sumas y saldos

def sumas_y_saldos(ejercicio, prefijo="", solo_contabilizados=False, incluir_cierre=False):
    """Una fila por subcuenta con movimientos: Debe, Haber y saldo (Debe - Haber)."""
    qs = _base(ejercicio, solo_contabilizados, incluir_cierre)
    if prefijo:
        qs = qs.filter(cuenta__codigo__startswith=prefijo)
    filas = (qs.values("cuenta__codigo", "cuenta__nombre")
             .annotate(debe=Sum("debe"), haber=Sum("haber"), n=Count("id"))
             .order_by("cuenta__codigo"))
    filas = [{"codigo": f["cuenta__codigo"], "nombre": f["cuenta__nombre"], "debe": f["debe"],
              "haber": f["haber"], "saldo": f["debe"] - f["haber"], "apuntes": f["n"]} for f in filas]
    totales = {
        "debe": sum((f["debe"] for f in filas), CERO),
        "haber": sum((f["haber"] for f in filas), CERO),
        "deudores": sum((f["saldo"] for f in filas if f["saldo"] > 0), CERO),
        "acreedores": -sum((f["saldo"] for f in filas if f["saldo"] < 0), CERO),
    }
    return filas, totales


# ---------------------------------------------------------------- extracto

@dataclass
class Extracto:
    prefijo: str
    cuenta: CuentaContable | None       # la cuenta si existe con ese código exacto
    varias: bool                        # True si agrupa varias subcuentas (p. ej. "400")
    saldo_inicial: Decimal = CERO
    lineas: list = field(default_factory=list)
    total_debe: Decimal = CERO
    total_haber: Decimal = CERO

    @property
    def saldo_final(self):
        return self.saldo_inicial + self.total_debe - self.total_haber


def extracto(ejercicio, prefijo, desde=None, hasta=None, solo_contabilizados=False, incluir_cierre=False):
    """Movimientos de una cuenta (o de todas las que empiezan por `prefijo`) en el ejercicio,
    con el saldo acumulado línea a línea. Saldo positivo = deudor, negativo = acreedor."""
    base = _base(ejercicio, solo_contabilizados, incluir_cierre).filter(cuenta__codigo__startswith=prefijo)
    cuenta = CuentaContable.objects.filter(codigo=prefijo).first()
    resultado = Extracto(prefijo, cuenta, varias=not (cuenta and cuenta.es_subcuenta))

    if desde:
        previo = base.filter(asiento__fecha__lt=desde).aggregate(d=Sum("debe"), h=Sum("haber"))
        resultado.saldo_inicial = (previo["d"] or CERO) - (previo["h"] or CERO)
        base = base.filter(asiento__fecha__gte=desde)
    if hasta:
        base = base.filter(asiento__fecha__lte=hasta)

    saldo = resultado.saldo_inicial
    movimientos = (base.select_related("asiento", "asiento__hoja", "cuenta", "contrapartida")
               .order_by("asiento__fecha", "asiento__numero", "orden", "id"))
    for a in movimientos:
        saldo += a.debe - a.haber
        resultado.total_debe += a.debe
        resultado.total_haber += a.haber
        resultado.lineas.append({"apunte": a, "saldo": saldo})
    return resultado


# ---------------------------------------------------------------- buscador

def buscar_apuntes(datos):
    """Filtra apuntes con los criterios del buscador (diccionario ya validado por el formulario).

    - importe: importe exacto (o ±1 % si «aproximado»), en el Debe o en el Haber
    - importe_min / importe_max: rango de importes
    - lado: "debe", "haber" o "" (los dos)
    - texto: en el concepto del apunte o del asiento, el documento o el nombre de la cuenta
    - cuenta: código o principio de código (400, 4000000...) o parte del nombre
    - desde / hasta, ejercicio, hoja ("sin" = sin hoja), estado, tipo
    """
    qs = (Apunte.objects.annotate(importe=F("debe") + F("haber"))
          .select_related("asiento", "asiento__ejercicio", "asiento__hoja", "cuenta", "contrapartida"))

    importe = datos.get("importe")
    minimo, maximo = datos.get("importe_min"), datos.get("importe_max")
    if importe is not None:
        if datos.get("aproximado"):
            margen = max((importe * Decimal("0.01")).quantize(Decimal("0.01")), Decimal("0.01"))
            minimo, maximo = importe - margen, importe + margen
        else:
            qs = qs.filter(importe=importe)
    if minimo is not None:
        qs = qs.filter(importe__gte=minimo)
    if maximo is not None:
        qs = qs.filter(importe__lte=maximo)

    if datos.get("lado") == "debe":
        qs = qs.filter(debe__gt=0)
    elif datos.get("lado") == "haber":
        qs = qs.filter(haber__gt=0)

    if texto := (datos.get("texto") or "").strip():
        qs = qs.filter(Q(concepto__icontains=texto) | Q(asiento__concepto__icontains=texto)
                       | Q(asiento__referencia_origen__icontains=texto) | Q(cuenta__nombre__icontains=texto))

    if cuenta := (datos.get("cuenta") or "").strip():
        if cuenta.isdigit():
            qs = qs.filter(cuenta__codigo__startswith=cuenta)
        else:
            qs = qs.filter(Q(cuenta__nombre__icontains=cuenta) | Q(cuenta__nif__iexact=cuenta))

    if datos.get("desde"):
        qs = qs.filter(asiento__fecha__gte=datos["desde"])
    if datos.get("hasta"):
        qs = qs.filter(asiento__fecha__lte=datos["hasta"])
    if datos.get("ejercicio"):
        qs = qs.filter(asiento__ejercicio=datos["ejercicio"])
    if hoja := datos.get("hoja"):
        qs = qs.filter(asiento__hoja__isnull=True) if hoja == "sin" else qs.filter(asiento__hoja_id=hoja)
    if datos.get("estado"):
        qs = qs.filter(asiento__estado=datos["estado"])
    if datos.get("tipo"):
        qs = qs.filter(asiento__tipo=datos["tipo"])

    return qs.order_by("-asiento__fecha", "-asiento__numero", "orden", "id")
