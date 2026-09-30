from django import template
from django.db.models import Count, Q

from ..models import Asiento, Ejercicio

register = template.Library()


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