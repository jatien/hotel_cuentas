"""Cálculo de los totales mensuales y anuales de cajas."""

from decimal import Decimal

from django.db.models import Sum

from .models import CAMPOS_IMPORTE, CAMPOS_TOTAL_COBROS, MESES, PUNTOS_VENTA, CajaDia, CajaMes

CERO = Decimal("0")


def _con_totales(fila: dict) -> dict:
    fila["total_ventas"] = sum((fila[c] for c, _ in PUNTOS_VENTA), CERO)
    fila["total_cobros"] = sum((fila[c] for c in CAMPOS_TOTAL_COBROS), CERO)
    return fila


def totales_dias(dias) -> dict:
    """Suma por campo de una lista de CajaDia (más total_ventas y total_cobros)."""
    fila = {c: sum((getattr(d, c) for d in dias), CERO) for c in CAMPOS_IMPORTE}
    return _con_totales(fila)


def anios_disponibles() -> list[int]:
    return list(CajaMes.objects.order_by("-anio").values_list("anio", flat=True).distinct())


def resumen_anual(anio: int) -> dict:
    """Una fila por mes existente del año con los totales de cada campo, y el total del año."""
    sumas = (
        CajaDia.objects.filter(caja_mes__anio=anio)
        .values("caja_mes__mes")
        .annotate(**{c: Sum(c) for c in CAMPOS_IMPORTE})
    )
    por_mes = {s["caja_mes__mes"]: s for s in sumas}
    meses_obj = {m.mes: m for m in CajaMes.objects.filter(anio=anio)}

    filas = []
    for num, nombre in MESES:
        if num not in meses_obj:
            continue
        s = por_mes.get(num, {})
        fila = {c: s.get(c) or CERO for c in CAMPOS_IMPORTE}
        fila.update(mes=num, nombre=nombre, caja_mes=meses_obj[num])
        filas.append(_con_totales(fila))

    total = _con_totales({c: sum((f[c] for f in filas), CERO) for c in CAMPOS_IMPORTE})
    tv = total["total_ventas"]
    pesos = {c: (total[c] / tv * 100 if tv else None) for c, _ in PUNTOS_VENTA}
    return {"filas": filas, "total": total, "pesos": pesos}
