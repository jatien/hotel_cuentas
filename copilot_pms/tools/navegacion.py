"""
Herramienta de navegación: qué ramas tiene el PMS, qué secciones hay en cada una y
dónde está cada pantalla. Lee el mismo registro que pinta la rueda de la página de
inicio (inicio.modulos), así el copilot y el menú nunca se contradicen.

- modulos_pms   ramas del PMS, sus secciones y el enlace a cada pantalla
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from copilot_pms.tools.base import Resultado, herramienta
from copilot_pms.tools.comun import consultado_en
from inicio.modulos import MODULOS

ClaveModulo = Literal[tuple(m.clave for m in MODULOS)]


class ModulosArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    modulo: ClaveModulo | None = Field(None, description=(
        "Rama concreta: " + ", ".join(f"«{m.clave}» ({m.nombre})" for m in MODULOS)
        + ". Sin indicar, devuelve todas."))


@herramienta(
    "modulos_pms",
    descripcion="Ramas del PMS (reservas, facturas, contabilidad, análisis), las secciones de cada una, "
                "si ya están disponibles y el enlace a cada pantalla. Úsala para decir al usuario dónde "
                "consultar o hacer algo en la aplicación.",
    argumentos=ModulosArgs, dominio="navegacion",
    # Con el login activo, solo quien puede ver alguna parte del PMS recibe el menú.
    permisos_alguno=("cuadro_cuentas.view_apunte", "cajas.view_cajames", "nominas.view_nominamensual",
                     "invoice_data.view_invoicedocument", "invoice_agent.view_borrador"),
)
def modulos_pms(p: ModulosArgs, ctx):
    ramas = []
    for m in MODULOS:
        if p.modulo and m.clave != p.modulo:
            continue
        secciones = []
        for s in m.secciones:
            seccion = {"nombre": s.nombre, "descripcion": s.descripcion, "disponible": bool(s.url)}
            if s.url:
                # La vista del chat convierte las claves «enlace_*» en enlaces con ese nombre.
                seccion["enlace_" + s.nombre.replace(" ", "_")] = s.url
            secciones.append(seccion)
        ramas.append({"clave": m.clave, "nombre": m.nombre, "descripcion": m.descripcion,
                      "disponible": m.disponible, "secciones": secciones})
    avisos = [f"«{r['nombre']}» todavía no está programada." for r in ramas if not r["disponible"]]
    return Resultado(datos={"ramas": ramas}, fuente="Menú de la página de inicio",
                     fecha_datos=consultado_en(), avisos=avisos)
