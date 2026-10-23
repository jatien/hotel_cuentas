from django import template
from django.db.models import Count, Q
from django.urls import reverse

from ..activo import CLAVE_SESION
from ..models import Asiento, DatosEmpresa, Ejercicio

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

MENU = [
    ("Configuración", [("Empresa y ejercicios", "empresa"), ("Cuentas contables", "plan_cuentas"),
                       ("Hojas de trabajo", "hojas")]),
    ("Registro", [("Asientos", "libro_diario"), ("Diario", "diario")]),
    ("Consultas", [("Mayor", "extractos"), ("Buscador de apuntes", "buscador")]),
    ("Balances", [("Sumas y saldos", "sumas_saldos"), ("Balance de comprobación", "comprobacion"),
                  ("Situación, PyG y PN", "cuentas_anuales"), ("Comparativa de ejercicios", "comparativa")]),
]


@register.simple_tag(takes_context=True)
def conta_activo(context):
    """Empresa y ejercicio activos y el menú lateral de la contabilidad."""
    request = context.get("request")
    anio = request.session.get(CLAVE_SESION) if request is not None else None
    ejercicio = context.get("ejercicio") or (Ejercicio.objects.filter(anio=anio).first() if anio else None) \
        or Ejercicio.objects.first()
    menu = [(grupo, [(nombre, reverse(f"cuadro_cuentas:{ruta}")) for nombre, ruta in enlaces]) for grupo, enlaces in MENU]
    return {"empresa": DatosEmpresa.actual(), "ejercicio": ejercicio, "menu": menu}


@register.filter
def importe_vacio(valor):
    """Como «importe» pero deja la celda vacía cuando es cero."""
    return importe(valor) if valor else ""


@register.filter
def add_importe(debe, haber):
    """Diferencia Debe - Haber formateada; vacía si es cero."""
    diferencia = (debe or Decimal("0")) - (haber or Decimal("0"))
    return importe(diferencia) if diferencia else ""


@register.filter
def sangria(codigo):
    """Sangría del código en el árbol del plan según su nivel."""
    orden = {1: 0, 2: 1, 3: 2, 4: 3, 5: 4, 8: 5}
    return orden.get(len(str(codigo)), 0) * 0.8
