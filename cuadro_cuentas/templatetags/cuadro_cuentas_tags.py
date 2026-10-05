from django import template
from django.db.models import Count, Q

from ..models import Asiento, Ejercicio

from decimal import Decimal


register = template.Library()

@register.filter
def importe(valor):
    """Importe al estilo de las cuentas anuales: 1.234,56 y los negativos entre paréntesis."""
    if valor is None or valor == "":
        return ""
    v = Decimal(valor).quantize(Decimal("0.01"))
    texto = f"{abs(v):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"({texto})" if v < 0 else texto




@register.inclusion_tag("cuadro_cuentas/_tarjeta_inicio.html")
def tarjeta_contabilidad():
    """Tarjeta de acceso al Libro diario para la página de inicio."""
    ejercicio = Ejercicio.objects.first()  # el más reciente (ordering = -anio)
    datos = {"total": 0, "borradores": 0}
    if ejercicio:
        datos = Asiento.objects.filter(ejercicio=ejercicio).aggregate(
            total=Count("id"),
            borradores=Count("id", filter=Q(estado=Asiento.Estado.BORRADOR)),
        )
    return {"ejercicio": ejercicio, **datos}