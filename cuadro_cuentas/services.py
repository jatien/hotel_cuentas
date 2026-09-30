from dataclasses import dataclass
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction

from .models import Apunte, Asiento, CuentaContable


@dataclass
class Linea:
    cuenta: str                      # código de la cuenta
    debe: Decimal = Decimal("0")
    haber: Decimal = Decimal("0")
    concepto: str = ""
    departamento: str = ""


def asignar_contrapartidas(asiento):
    """Rellena contrapartidas vacías: si un lado del asiento tiene una sola
    cuenta, esa es la contrapartida de todas las líneas del otro lado."""
    apuntes = list(asiento.apuntes.select_related("cuenta"))
    lado_debe = [a for a in apuntes if a.debe > 0]
    lado_haber = [a for a in apuntes if a.haber > 0]
    for unico, otro_lado in ((lado_debe, lado_haber), (lado_haber, lado_debe)):
        if len(unico) == 1:
            for a in otro_lado:
                if a.contrapartida_id is None:
                    a.contrapartida = unico[0].cuenta
    Apunte.objects.bulk_update(apuntes, ["contrapartida"])


@transaction.atomic
def crear_asiento(fecha, concepto, lineas, origen=Asiento.Origen.MANUAL,
                  referencia_origen="", contabilizar=True):
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

    asiento = Asiento(fecha=fecha, concepto=concepto, origen=origen,
                      referencia_origen=referencia_origen)
    asiento.full_clean(exclude=["ejercicio", "numero"])
    asiento.save()

    Apunte.objects.bulk_create([
        Apunte(asiento=asiento, orden=i, cuenta=cuentas[l.cuenta], debe=l.debe, haber=l.haber,
               concepto=l.concepto or concepto, departamento=l.departamento)
        for i, l in enumerate(lineas)
    ])
    asignar_contrapartidas(asiento)
    if contabilizar:
        asiento.contabilizar()
    return asiento
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