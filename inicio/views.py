import math

from django.shortcuts import render

from inicio.modulos import MODULOS

# Geometría de la rueda en unidades del viewBox (-100..100). Las imágenes están a radio 72
# y miden 52 unidades, así que cada imagen ocupa unos ±21° del círculo.
RADIO_INTERIOR, HUECO_INTERIOR = 66, 27
RADIO_EXTERIOR, HUECO_EXTERIOR = 78, 23


def _arco(radio: float, desde: float, hasta: float) -> str:
    """Path SVG de un arco en sentido horario; ángulos en grados con 0° arriba."""
    def punto(grados):
        rad = math.radians(grados - 90)
        return f"{radio * math.cos(rad):.2f} {radio * math.sin(rad):.2f}"
    return f"M {punto(desde)} A {radio} {radio} 0 0 1 {punto(hasta)}"


def lineas_rueda(n: int) -> list[list[str]]:
    """Dos arcos paralelos entre cada imagen y la siguiente (la «doble línea» del boceto)."""
    paso = 360 / n
    return [
        [_arco(RADIO_INTERIOR, i * paso + HUECO_INTERIOR, (i + 1) * paso - HUECO_INTERIOR),
         _arco(RADIO_EXTERIOR, i * paso + HUECO_EXTERIOR, (i + 1) * paso - HUECO_EXTERIOR)]
        for i in range(n)
    ]


def flechas_rueda(n: int, radio: float = 93, abertura: float = 12) -> list[str]:
    """Flechitas fuera de la rueda, entre cada par de imágenes, que indican que se puede girar."""
    paso = 360 / n
    flechas = []
    for i in range(n):
        centro = i * paso + paso / 2
        desde, hasta = centro - abertura, centro + abertura
        rad = math.radians(hasta)
        punta_x, punta_y = radio * math.sin(rad), -radio * math.cos(rad)
        tx, ty = math.cos(rad), math.sin(rad)  # tangente en sentido horario
        nx, ny = math.sin(rad), -math.cos(rad)  # normal hacia fuera
        ala1 = f"{punta_x - 6 * tx + 4 * nx:.2f} {punta_y - 6 * ty + 4 * ny:.2f}"
        ala2 = f"{punta_x - 6 * tx - 4 * nx:.2f} {punta_y - 6 * ty - 4 * ny:.2f}"
        flechas.append(f"{_arco(radio, desde, hasta)} M {ala1} L {punta_x:.2f} {punta_y:.2f} L {ala2}")
    return flechas


def home(request):
    paso = 360 / len(MODULOS)
    return render(request, "inicio/home.html", {
        "modulos": MODULOS,
        "rueda": [{"modulo": m, "angulo": round(i * paso, 3), "lineas": lineas}
                  for i, (m, lineas) in enumerate(zip(MODULOS, lineas_rueda(len(MODULOS))))],
        "flechas": flechas_rueda(len(MODULOS)),
        "modulos_json": [m.a_dict() for m in MODULOS],
    })
