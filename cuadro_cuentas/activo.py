"""Empresa y ejercicio activos: los elige el usuario en «Empresa y ejercicios» (o con
?ejercicio=AAAA en cualquier hoja) y se aplican a todas las hojas de la contabilidad."""
from .models import Ejercicio

CLAVE_SESION = "contabilidad_ejercicio"


def ejercicio_activo(request):
    """(ejercicios, ejercicio elegido). El ?ejercicio= de la URL manda y se recuerda."""
    ejercicios = Ejercicio.objects.all()
    anio = request.GET.get("ejercicio") or request.POST.get("ejercicio_activo")
    ejercicio = None
    if anio and str(anio).isdigit():
        ejercicio = ejercicios.filter(anio=anio).first()
    if ejercicio is None:
        guardado = request.session.get(CLAVE_SESION)
        ejercicio = ejercicios.filter(anio=guardado).first() if guardado else None
    if ejercicio is None:
        ejercicio = ejercicios.first()
    if ejercicio is not None and request.session.get(CLAVE_SESION) != ejercicio.anio:
        request.session[CLAVE_SESION] = ejercicio.anio
    return ejercicios, ejercicio

