import math

from django.http import Http404
from django.shortcuts import render

from inicio.modulos import MODULOS, por_clave

# Geometría de la rueda, en unidades del viewBox (-170..170 × -95..95).
# Óvalo alargado con el lado izquierdo más alto. rueda.js usa los mismos valores
# (los recibe en data-* del contenedor); en pantallas estrechas usa un círculo.
ELIPSE = {"rx": 128, "ry": 60, "inclinacion": 12, "ancho": 340, "alto": 190, "img": 52}


def posicion(angulo: float, e: dict = ELIPSE) -> tuple[float, float]:
    """Punto de la elipse para un ángulo de fase (0° = arriba, sentido horario)."""
    t = math.radians(angulo)
    x, y = e["rx"] * math.sin(t), -e["ry"] * math.cos(t)
    a = math.radians(e["inclinacion"])
    return x * math.cos(a) - y * math.sin(a), x * math.sin(a) + y * math.cos(a)


def _contexto(abierto=None, seccion=0):
    paso = 360 / len(MODULOS)
    # Con una rama abierta, la rueda se deja con esa rama arriba (para cuando se vuelva).
    giro = -MODULOS.index(abierto) * paso if abierto else 0
    rueda = []
    for i, m in enumerate(MODULOS):
        x, y = posicion(i * paso + giro)
        # Posición inicial en % del contenedor: la página se ve bien aunque el JS no cargue.
        rueda.append({"modulo": m,
                      "x": round(50 + x / ELIPSE["ancho"] * 100, 3),
                      "y": round(50 + y / ELIPSE["alto"] * 100, 3)})
    activa = None
    if abierto:
        disponibles = [i for i, s in enumerate(abierto.secciones) if s.url]
        if seccion not in disponibles:
            seccion = abierto.indice_inicial
        activa = abierto.secciones[seccion] if seccion is not None else None
    return {
        "modulos": MODULOS,
        "rueda": rueda,
        "giro_inicial": giro,
        "elipse": ELIPSE,
        "abierto": abierto,
        "seccion_activa": activa,
        "modulos_json": [m.a_dict() for m in MODULOS],
    }


def home(request):
    return render(request, "inicio/home.html", _contexto())


def home_modulo(request, clave):
    """La misma página, con una rama ya abierta (enlaces directos, recarga y botón atrás)."""
    modulo = por_clave(clave)
    if modulo is None:
        raise Http404
    try:
        seccion = int(request.GET.get("seccion", ""))
    except ValueError:
        seccion = None
    return render(request, "inicio/home.html", _contexto(modulo, seccion))
