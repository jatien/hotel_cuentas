"""
Herramienta `grafico`: devuelve un gráfico (especificación + datos) a partir de una
plantilla de copilot_pms/graficos.py. La pantalla del chat lo dibuja con Chart.js.

Para «editar» un gráfico (otro tipo, menos series, comparar con otro año...) el
modelo vuelve a llamar a la herramienta con los parámetros cambiados: en el
historial de la conversación ve los parámetros del último gráfico.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from copilot_pms import graficos
from copilot_pms.tools.base import Aclarar, Denegado, NoDisponible, Resultado, herramienta
from copilot_pms.tools.comun import consultado_en, importe

ClavePlantilla = Literal[tuple(graficos.PLANTILLAS)]  # type: ignore[valid-type]
TipoGrafico = Literal[graficos.TIPOS]  # type: ignore[valid-type]


class GraficoArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plantilla: ClavePlantilla = Field(description="Plantilla del gráfico (ver la lista en la descripción).")
    anio: int | None = Field(None, ge=2000, le=2100, description="Año. Por defecto, el actual.")
    series: list[str] | None = Field(None, max_length=8, description=(
        "Series a mostrar, por su nombre exacto (p. ej. [\"Bar\", \"Comedor\"]). Por defecto, las de la plantilla. "
        "Si no sabes los nombres, pide el gráfico sin series: el resultado trae «series_disponibles»."))
    tipo: TipoGrafico | None = Field(None, description=(
        "barras, barras_apiladas, barras_horizontales, lineas, area o tarta (tarta solo con categorías)."))
    comparar_con: list[int] = Field(default_factory=list, max_length=2, description=(
        "Otros años a comparar (solo gráficos mensuales), p. ej. [2025]."))
    desde_mes: int = Field(1, ge=1, le=12)
    hasta_mes: int = Field(12, ge=1, le=12)
    mes: int | None = Field(None, ge=1, le=12, description="Solo un mes (gráficos por departamento o categoría).")
    cuenta: str | None = Field(None, pattern=r"^\d{1,12}$", description="Código de cuenta (saldo_cuenta_mensual).")
    con_iva: bool = Field(False, description="Importes con IVA (plantillas del análisis de beneficios).")
    titulo: str | None = Field(None, max_length=120)

    @field_validator("comparar_con")
    @classmethod
    def _anios(cls, valor):
        if any(not 2000 <= a <= 2100 for a in valor):
            raise ValueError("Años fuera de rango.")
        return valor


def _descripcion() -> str:
    lineas = [f"- {p.clave}: {p.descripcion}" for p in graficos.PLANTILLAS.values()]
    return ("Muestra un gráfico en la pantalla del usuario a partir de una plantilla con datos reales. Para "
            "modificar un gráfico ya mostrado (otro tipo, otras series, comparar años, otro título...) vuelve "
            "a llamar a esta herramienta con los mismos parámetros y solo el cambio pedido. Plantillas:\n"
            + "\n".join(lineas))


@herramienta(
    "grafico", descripcion=_descripcion(), argumentos=GraficoArgs, dominio="graficos",
    permisos_alguno=tuple(sorted({p.permiso for p in graficos.PLANTILLAS.values()})),
)
def grafico(p: GraficoArgs, ctx):
    plantilla = graficos.PLANTILLAS[p.plantilla]
    if not ctx.tiene_permisos((plantilla.permiso,)):
        raise Denegado("PERMISSION_DENIED", "Sin permiso para los datos de esta plantilla.")
    anio = p.anio or ctx.hoy.year
    try:
        g = graficos.construir(p.plantilla, anio, series=p.series, tipo=p.tipo, comparar_con=p.comparar_con,
                               desde_mes=p.desde_mes, hasta_mes=p.hasta_mes, mes=p.mes, cuenta=p.cuenta,
                               con_iva=p.con_iva, titulo=p.titulo, hoy=ctx.hoy)
    except ValueError as error:
        raise Aclarar("PARAMETROS_GRAFICO", str(error))
    except graficos.GraficoNoDisponible as error:
        raise NoDisponible(error.codigo, error.mensaje, error.datos)

    parametros = p.model_dump(exclude_defaults=True)
    parametros["anio"] = anio
    return Resultado(
        datos={
            "grafico": {"tipo": g.tipo, "titulo": g.titulo, "eje": g.eje, "unidad": g.unidad,
                        "etiquetas": g.etiquetas,
                        "series": [{"nombre": s["nombre"], "valores": [importe(v) for v in s["valores"]]}
                                   for s in g.series]},
            "parametros": parametros,
            "series_disponibles": g.disponibles,
            "enlace": plantilla.enlace(anio) if plantilla.enlace else None,
        },
        fuente=plantilla.fuente, fecha_datos=consultado_en(), avisos=g.avisos,
    )
