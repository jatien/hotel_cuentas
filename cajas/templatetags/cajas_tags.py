"""Formato español para importes en las plantillas de cajas."""

from django import template

register = template.Library()


def _es(numero: float, decimales: int) -> str:
    """1234567.8 -> «1.234.567,80» (separador de miles punto, decimal coma)."""
    texto = f"{numero:,.{decimales}f}"
    return texto.replace(",", "\x00").replace(".", ",").replace("\x00", ".")


@register.filter
def euros(valor, decimales=2):
    if valor in (None, ""):
        return "—"
    return f"{_es(float(valor), int(decimales))} €"


@register.filter
def importe(valor, decimales=2):
    """Importe sin símbolo; los ceros se muestran vacíos para que la tabla respire."""
    if valor in (None, "") or float(valor) == 0:
        return ""
    return _es(float(valor), int(decimales))


@register.filter
def pct(valor, decimales=1):
    if valor in (None, ""):
        return "—"
    return f"{_es(float(valor), int(decimales))} %"


@register.filter
def campo(form, nombre):
    """{{ form|campo:'bar' }} -> el BoundField del campo."""
    return form[nombre]


@register.filter
def valor_input(valor):
    """Valor para un <input> de importe: «156,45», vacío si es 0, o el texto tal cual si no es número."""
    if valor in (None, ""):
        return ""
    try:
        numero = float(valor)
    except (TypeError, ValueError):
        return valor            # lo que escribió el usuario y no es válido: se le devuelve para corregirlo
    if numero == 0:
        return ""
    return f"{numero:.2f}".replace(".", ",")


@register.filter
def attr(objeto, nombre):
    """{{ dia|attr:'bar' }} -> dia.bar (o dict['bar'])."""
    if isinstance(objeto, dict):
        return objeto.get(nombre)
    return getattr(objeto, nombre, None)
