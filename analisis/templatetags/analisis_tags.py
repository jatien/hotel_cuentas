"""Formato español para importes y porcentajes en las plantillas de análisis."""

from decimal import Decimal

from django import template

register = template.Library()


def _es(numero: float, decimales: int) -> str:
    """1234567.8 -> «1.234.567,8» (separador de miles punto, decimal coma)."""
    texto = f"{numero:,.{decimales}f}"
    return texto.replace(",", "\x00").replace(".", ",").replace("\x00", ".")


@register.filter
def euros(valor, decimales=0):
    if valor in (None, ""):
        return "—"
    return f"{_es(float(valor), int(decimales))} €"


@register.filter
def numero(valor, decimales=0):
    if valor in (None, ""):
        return "—"
    return _es(float(valor), int(decimales))


@register.filter
def pct(valor, decimales=1):
    if valor in (None, ""):
        return "—"
    return f"{_es(float(valor), int(decimales))} %"


@register.filter
def negativo(valor):
    """True si el importe es negativo (para pintarlo en otro color)."""
    try:
        return Decimal(str(valor)) < 0
    except Exception:
        return False


@register.filter
def indice(lista, i):
    return lista[i]
