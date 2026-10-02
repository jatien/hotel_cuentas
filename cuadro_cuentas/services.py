from dataclasses import dataclass
from decimal import Decimal
from itertools import chain

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q, Sum
from django.db.models.functions import Length

from .models import LONGITUD_SUBCUENTA, Apunte, Asiento, CuentaContable
from .nif import validar_nif

LONGITUDES_BASE = (3, 4, 5)


# ---------- asientos ----------

@dataclass
class Linea:
    cuenta: str                      # código de la cuenta
    debe: Decimal = Decimal("0")
    haber: Decimal = Decimal("0")
    concepto: str = ""
    departamento: str = ""


def asignar_contrapartidas(asiento, recalcular=False):
    """Rellena contrapartidas: si un lado del asiento tiene una sola cuenta,
    esa es la contrapartida de todas las líneas del otro lado.
    Con recalcular=True se borran antes las existentes."""
    apuntes = list(asiento.apuntes.select_related("cuenta"))
    if recalcular:
        for a in apuntes:
            a.contrapartida = None
    lado_debe = [a for a in apuntes if a.debe > 0]
    lado_haber = [a for a in apuntes if a.haber > 0]
    for unico, otro_lado in ((lado_debe, lado_haber), (lado_haber, lado_debe)):
        if len(unico) == 1:
            for a in otro_lado:
                if a.contrapartida_id is None:
                    a.contrapartida = unico[0].cuenta
    Apunte.objects.bulk_update(apuntes, ["contrapartida"])


def renumerar(ejercicio):
    """Numera los asientos del ejercicio sin huecos: apertura, operaciones y ajustes por
    fecha, regularización y cierre. Devuelve True si ha cambiado algún número."""
    asientos = sorted(Asiento.objects.filter(ejercicio=ejercicio),
                      key=lambda a: (Asiento.PRIORIDAD[a.tipo], a.fecha, a.id))
    if all(a.numero == i for i, a in enumerate(asientos, 1)):
        return False
    # dos pasadas para no chocar con la restricción de número único
    desplazamiento = max(a.numero or 0 for a in asientos) + len(asientos)
    for a in asientos:
        a.numero += desplazamiento
    Asiento.objects.bulk_update(asientos, ["numero"])
    for i, a in enumerate(asientos, 1):
        a.numero = i
    Asiento.objects.bulk_update(asientos, ["numero"])
    return True


def saldos_subcuentas(ejercicio, hasta=None, prefijos=None):
    """[(cuenta, saldo)] de las cuentas con movimientos en el ejercicio; saldo = Debe - Haber.
    Solo las que no están a cero, ordenadas por código."""
    qs = Apunte.objects.filter(asiento__ejercicio=ejercicio)
    if hasta:
        qs = qs.filter(asiento__fecha__lte=hasta)
    if prefijos:
        filtro = Q()
        for p in prefijos:
            filtro |= Q(cuenta__codigo__startswith=p)
        qs = qs.filter(filtro)
    filas = list(qs.order_by().values("cuenta_id").annotate(d=Sum("debe"), h=Sum("haber")))
    cuentas = CuentaContable.objects.in_bulk([f["cuenta_id"] for f in filas])
    return sorted(
        ((cuentas[f["cuenta_id"]], f["d"] - f["h"]) for f in filas if f["d"] != f["h"]),
        key=lambda x: x[0].codigo,
    )


@transaction.atomic
def crear_asiento(fecha, concepto, lineas, origen=Asiento.Origen.MANUAL,
                  referencia_origen="", contabilizar=True, tipo=Asiento.Tipo.OPERACION, modelo=""):
    lineas = list(lineas)
    if len(lineas) < 2:
        raise ValidationError("Un asiento necesita al menos dos líneas.")

    codigos = {l.cuenta for l in lineas}
    cuentas = {c.codigo: c for c in CuentaContable.objects.filter(codigo__in=codigos)}
    if faltan := codigos - cuentas.keys():
        raise ValidationError(f"Cuentas inexistentes: {', '.join(sorted(faltan))}")
    if no_imp := [c for c in cuentas.values() if not c.imputable]:
        raise ValidationError(f"Cuentas no imputables: {', '.join(str(c) for c in no_imp)}")

    for l in lineas:
        if (l.debe > 0) == (l.haber > 0) or l.debe < 0 or l.haber < 0:
            raise ValidationError(f"Línea {l.cuenta}: importe solo en Debe o solo en Haber, positivo.")

    total_debe = sum(l.debe for l in lineas)
    total_haber = sum(l.haber for l in lineas)
    if total_debe != total_haber:
        raise ValidationError(f"El asiento no cuadra: Debe {total_debe} ≠ Haber {total_haber}.")

    asiento = Asiento(fecha=fecha, concepto=concepto, origen=origen, tipo=tipo, modelo=modelo,
                      referencia_origen=referencia_origen[:100])
    asiento.full_clean(exclude=["ejercicio", "numero"], validate_constraints=False)
    asiento.save()

    Apunte.objects.bulk_create([
        Apunte(asiento=asiento, orden=i, cuenta=cuentas[l.cuenta], debe=l.debe, haber=l.haber,
               concepto=l.concepto or concepto, departamento=l.departamento)
        for i, l in enumerate(lineas)
    ])
    asignar_contrapartidas(asiento)
    if contabilizar:
        asiento.contabilizar()
    if renumerar(asiento.ejercicio):
        asiento.refresh_from_db(fields=["numero"])
    return asiento


# ---------- subcuentas de 8 dígitos ----------

def siguiente_codigo(base):
    """Primer código libre de 8 dígitos bajo una cuenta de 3 a 5 dígitos."""
    huecos = LONGITUD_SUBCUENTA - len(base.codigo)
    tomados = set(
        CuentaContable.objects.annotate(largo=Length("codigo"))
        .filter(largo=LONGITUD_SUBCUENTA, codigo__startswith=base.codigo)
        .values_list("codigo", flat=True)
    )
    ultimo = max((int(c[len(base.codigo):]) for c in tomados), default=0)
    for n in chain(range(ultimo + 1, 10 ** huecos), range(1, ultimo + 1)):
        codigo = f"{base.codigo}{n:0{huecos}d}"
        if codigo not in tomados:
            return codigo
    raise ValidationError(f"No quedan códigos libres bajo la cuenta {base.codigo}.")


def buscar_subcuenta_por_nif(nif, bases):
    """Subcuenta existente con ese NIF bajo alguna de las bases indicadas (para facturas)."""
    nif = validar_nif(nif)
    if not nif:
        return None
    return (CuentaContable.objects.filter(nif=nif, padre__codigo__in=list(bases))
            .order_by("codigo").first())


@transaction.atomic
def crear_subcuenta(base, nombre, nif="", codigo=""):
    cuenta_base = CuentaContable.objects.select_for_update().filter(codigo=str(base).strip()).first()
    if not cuenta_base or len(cuenta_base.codigo) not in LONGITUDES_BASE:
        raise ValidationError("Elige una cuenta del plan de 3, 4 o 5 dígitos.")

    nif = validar_nif(nif)
    if nif:
        repetida = CuentaContable.objects.filter(padre=cuenta_base, nif=nif).first()
        if repetida:
            raise ValidationError(f"Ya existe la subcuenta {repetida} con el NIF {nif}.")

    codigo = (codigo or "").strip()
    if codigo:
        if not (codigo.isdigit() and len(codigo) == LONGITUD_SUBCUENTA):
            raise ValidationError(f"El código debe tener {LONGITUD_SUBCUENTA} dígitos.")
        if not codigo.startswith(cuenta_base.codigo):
            raise ValidationError(f"El código debe empezar por {cuenta_base.codigo}.")
    else:
        codigo = siguiente_codigo(cuenta_base)

    subcuenta = CuentaContable(
        codigo=codigo, nombre=" ".join(nombre.split()), nif=nif,
        padre=cuenta_base, imputable=True,
    )
    subcuenta.full_clean()
    subcuenta.save()
    return subcuenta