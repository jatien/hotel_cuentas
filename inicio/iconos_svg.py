"""
Puente entre las ilustraciones del proyecto y Penpot (o cualquier editor de SVG).

En el proyecto, los iconos (templates/inicio/iconos/*.svg) no llevan colores: llevan clases
(«p», «crema», «trazo-acento»...) y el tema de la página les da color. Así un mismo dibujo
sirve para el tema Abrat y para el azul.

En Penpot hace falta lo contrario: colores de verdad. Por eso:

- `exportar(texto)` convierte clases en colores de la PALETA → SVG listo para importar en Penpot.
- `importar(texto)` convierte los colores de la PALETA en clases → SVG listo para el proyecto.

Regla para dibujar en Penpot: usar SOLO los colores de la paleta (están en la biblioteca del
fichero «PMS icons»). Un color que no sea de la paleta se queda tal cual (y el importador avisa).
"""

import re
import xml.etree.ElementTree as ET

SVG_NS = "http://www.w3.org/2000/svg"
ET.register_namespace("", SVG_NS)

# Nombre en Penpot → (color, clase de relleno, clase de trazo)
PALETA = {
    "Tinta": ("#33403E", "relleno-tinta", None),     # trazo por defecto: sin clase
    "Papel": ("#FFFFFF", "p", "trazo-papel"),
    "Crema": ("#FCE8C5", "crema", None),
    "Tinte azul": ("#DDF2FB", "tinte", None),
    "Azul mar": ("#00A4E5", "relleno-acento", "trazo-acento"),
    "Oro": ("#F9B53A", "oro", "trazo-oro"),
    "Pantalla LCD": ("#D6E4C3", "lcd", None),
}
TINTA = PALETA["Tinta"][0]
OPACIDAD_SUAVE = "0.55"
OPACIDAD_SOMBRA = "0.08"

_RELLENO = {clase: color for color, clase, _ in PALETA.values() if clase}
_RELLENO["relleno-oro"] = PALETA["Oro"][0]
_TRAZO = {clase: color for color, _, clase in PALETA.values() if clase}
_TRAZO["digitos"] = TINTA
_COLOR_A_RELLENO = {color.upper(): clase for color, clase, _ in PALETA.values() if clase}
_COLOR_A_TRAZO = {color.upper(): clase for color, _, clase in PALETA.values() if clase}

# Lo que el CSS del proyecto pone en `.icono` (y que Penpot necesita explícito).
RAIZ = {"fill": "none", "stroke": TINTA, "stroke-width": "3",
        "stroke-linecap": "round", "stroke-linejoin": "round"}

_COMENTARIO_DJANGO = re.compile(r"\{#.*?#\}", re.S)


def _tag(el):
    return el.tag.split("}")[-1]


def _leer(texto: str) -> ET.Element:
    return ET.fromstring(_COMENTARIO_DJANGO.sub("", texto))


def _escribir(raiz: ET.Element) -> str:
    ET.indent(raiz, space="  ")
    return ET.tostring(raiz, encoding="unicode") + "\n"


# --------------------------------------------------------------------------- exportar
def exportar(texto: str) -> str:
    """Icono del proyecto (con clases) → SVG con colores reales, para Penpot."""
    raiz = _leer(texto)
    if not raiz.tag.startswith("{"):
        raiz.set("xmlns", SVG_NS)
    raiz.attrib.pop("class", None)
    for k in ("aria-hidden", "focusable"):
        raiz.attrib.pop(k, None)
    raiz.set("width", "160")
    raiz.set("height", "160")
    for k, v in RAIZ.items():
        raiz.set(k, v)
    for el in raiz.iter():
        clases = el.attrib.pop("class", "").split() if el is not raiz else []
        for c in clases:
            if c in _RELLENO:
                el.set("fill", _RELLENO[c])
            if c in _TRAZO:
                el.set("stroke", _TRAZO[c])
            if c == "suave":
                el.set("opacity", OPACIDAD_SUAVE)
            if c == "sombra":
                el.set("fill", TINTA)
                el.set("stroke", "none")
                el.set("opacity", OPACIDAD_SOMBRA)
    return _escribir(raiz)


# --------------------------------------------------------------------------- importar
def _estilo(el) -> dict:
    """Atributos de presentación + style="..." (Penpot usa los dos)."""
    props = {k: v for k, v in el.attrib.items()}
    for trozo in el.attrib.get("style", "").split(";"):
        if ":" in trozo:
            k, v = trozo.split(":", 1)
            props[k.strip()] = v.strip()
    return props


def _hex(valor: str | None) -> str | None:
    if not valor:
        return None
    valor = valor.strip().upper()
    if re.fullmatch(r"#[0-9A-F]{3}", valor):
        valor = "#" + "".join(c * 2 for c in valor[1:])
    return valor if re.fullmatch(r"#[0-9A-F]{6}", valor) else None


def importar(texto: str) -> tuple[str, list[str]]:
    """
    SVG exportado de Penpot (con colores) → icono del proyecto (con clases).
    Devuelve (svg, avisos). Los colores fuera de la paleta se dejan y se avisa.
    """
    raiz = _leer(texto)
    avisos = []

    vb = raiz.get("viewBox", "").split()
    if len(vb) == 4 and (float(vb[2]), float(vb[3])) != (160.0, 160.0):
        avisos.append(f"El tablero mide {vb[2]}×{vb[3]}; los iconos se dibujan en 160×160.")
    raiz.set("viewBox", " ".join(vb) if len(vb) == 4 else "0 0 160 160")

    for el in raiz.iter():
        if el is raiz:
            continue
        props = _estilo(el)
        clases = []
        relleno, trazo = _hex(props.get("fill")), _hex(props.get("stroke"))
        opacidad = props.get("opacity") or props.get("fill-opacity")

        if relleno == TINTA and opacidad and float(opacidad) < 0.2:
            clases.append("sombra")
            relleno = trazo = None
            opacidad = None
            for k in ("fill", "stroke", "opacity", "fill-opacity"):
                el.attrib.pop(k, None)
        if relleno:
            if relleno in _COLOR_A_RELLENO:
                clases.append(_COLOR_A_RELLENO[relleno])
                el.attrib.pop("fill", None)
            else:
                avisos.append(f"<{_tag(el)}> con relleno {relleno}, que no es de la paleta.")
        if trazo:
            if trazo == TINTA:
                el.attrib.pop("stroke", None)              # el trazo por defecto ya es tinta
            elif trazo in _COLOR_A_TRAZO:
                clases.append(_COLOR_A_TRAZO[trazo])
                el.attrib.pop("stroke", None)
            else:
                avisos.append(f"<{_tag(el)}> con trazo {trazo}, que no es de la paleta.")
        if opacidad and 0.2 <= float(opacidad) < 0.95:
            clases.append("suave")
            el.attrib.pop("opacity", None)
            el.attrib.pop("fill-opacity", None)

        # Lo que ya va en el CSS de .icono sobra en cada elemento.
        for k, v in RAIZ.items():
            if el.get(k) == v and k != "fill":
                del el.attrib[k]
        if "style" in el.attrib:
            restos = [t for t in el.attrib["style"].split(";")
                      if ":" in t and t.split(":")[0].strip() not in
                      ("fill", "stroke", "opacity", "fill-opacity", *RAIZ)]
            if restos:
                el.set("style", ";".join(restos))
            else:
                del el.attrib["style"]
        if clases:
            el.set("class", " ".join(dict.fromkeys(clases)))

    for k in ("width", "height", "xmlns:xlink", "version", *RAIZ):
        raiz.attrib.pop(k, None)
    raiz.attrib = {"class": "icono", "viewBox": raiz.get("viewBox"), "aria-hidden": "true",
                   "focusable": "false", **{k: v for k, v in raiz.attrib.items() if k not in ("viewBox",)}}
    svg = _escribir(raiz).replace(f' xmlns="{SVG_NS}"', "", 1)
    return svg, avisos
