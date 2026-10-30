"""Formato español para importes y porcentajes en las plantillas de análisis."""

from decimal import Decimal

from django import template

from mysite.formato import numero_es

register = template.Library()


@register.filter
def euros(valor, decimales=0):
    if valor in (None, ""):
        return "—"
    return f"{numero_es(float(valor), int(decimales))} €"


@register.filter
def numero(valor, decimales=0):
    if valor in (None, ""):
        return "—"
    return numero_es(float(valor), int(decimales))


@register.filter
def pct(valor, decimales=1):
    if valor in (None, ""):
        return "—"
    return f"{numero_es(float(valor), int(decimales))} %"


@register.filter
def negativo(valor):
    """True si el importe es negativo (para pintarlo en otro color)."""
    try:
        return Decimal(str(valor)) < 0
    except Exception:
        return False

