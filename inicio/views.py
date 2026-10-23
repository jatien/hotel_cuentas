import math

from django.shortcuts import render

from inicio.modulos import MODULOS

# Geometría de la rueda, en unidades del viewBox (-145..145 × -100..100).
# Las ramas van sobre una elipse inclinada (lado derecho más alto), como en el boceto.
# rueda.js usa exactamente los mismos valores (los recibe en data-* del contenedor).
ELIPSE = {"rx": 105, "ry": 64, "inclinacion": -10, "ancho": 290, "alto": 200}


def posicion(angulo: float, e: dict = ELIPSE) -> tuple[float, float]:
    """Punto de la elipse para un ángulo de fase (0° = arriba, sentido horario)."""
    t = math.radians(angulo)
    x, y = e["rx"] * math.sin(t), -e["ry"] * math.cos(t)
    a = math.radians(e["inclinacion"])
    return x * math.cos(a) - y * math.sin(a), x * math.sin(a) + y * math.cos(a)


def home(request):
    paso = 360 / len(MODULOS)
    rueda = []
    for i, m in enumerate(MODULOS):
        x, y = posicion(i * paso)
        # Posición inicial en % del contenedor: la página se ve bien aunque el JS no cargue.
        rueda.append({"modulo": m,
                      "x": round(50 + x / ELIPSE["ancho"] * 100, 3),
                      "y": round(50 + y / ELIPSE["alto"] * 100, 3)})
    return render(request, "inicio/home.html", {
        "modulos": MODULOS,
        "rueda": rueda,
        "elipse": ELIPSE,
        "modulos_json": [m.a_dict() for m in MODULOS],
    })
