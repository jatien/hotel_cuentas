"""
Herramientas de informes ya existentes en la aplicación. No recalculan nada por su
cuenta: llaman a las mismas funciones que pintan las pantallas, así el copilot y la
pantalla dan siempre la misma cifra.

- cuentas_anuales      balance y PyG del PGC 2007 (cuadro_cuentas.cuentas_anuales)
- resumen_cajas        ventas por punto de venta y otros cobros (cajas.resumen)
- coste_personal       coste de nóminas por departamento (nominas), sin datos por empleado
"""

from django.db.models import Count, Sum
from django.urls import reverse
from pydantic import BaseModel, ConfigDict, Field

from copilot_pms.models import EstadoLlamada
from copilot_pms.tools.base import NoDisponible, Resultado, herramienta
from copilot_pms.tools.comun import CERO, MONEDA_CONTABILIDAD, consultado_en, importe
from copilot_pms.tools.contabilidad import _entidad, _ejercicio_por_anio


class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --------------------------------------------------------------------------- #
class CuentasAnualesArgs(_Args):
    ejercicio: int | None = Field(None, ge=2000, le=2100, description="Año del ejercicio. Por defecto, el actual.")
    estado: str = Field(pattern="^(balance|pyg)$", description="«balance» o «pyg» (pérdidas y ganancias).")
    modelo: str = Field("normal", pattern="^(normal|abreviado)$")
    detalle: int = Field(1, ge=0, le=2, description=(
        "0 = solo masas principales (A, B, totales); 1 = también epígrafes (I, II...); 2 = partidas."))


@herramienta(
    "cuentas_anuales",
    descripcion="Balance o cuenta de pérdidas y ganancias del PGC 2007 de un ejercicio, con la comparación "
                "con el ejercicio anterior. Solo usa asientos contabilizados.",
    argumentos=CuentasAnualesArgs, dominio="informes",
    permisos=("cuadro_cuentas.view_apunte",),
)
def cuentas_anuales(p: CuentasAnualesArgs, ctx):
    from cuadro_cuentas.cuentas_anuales import calcular_cuentas_anuales

    ejercicio = _ejercicio_por_anio(p.ejercicio, ctx.hoy)
    ca = calcular_cuentas_anuales(ejercicio, p.modelo)
    estado = ca[p.estado]
    secciones = []
    for titulo, total_texto, total_actual, total_anterior, filas in estado.secciones:
        secciones.append({
            "titulo": titulo or estado.titulo,
            "total": {"texto": total_texto, "actual": importe(total_actual), "anterior": importe(total_anterior)}
            if total_texto else None,
            "partidas": [{"ref": f.ref, "texto": f.texto, "actual": importe(f.actual),
                          "anterior": importe(f.anterior), "es_total": f.es_total}
                         for f in filas if f.nivel <= p.detalle and f.visible],
        })
    avisos = []
    if ca["borradores"]:
        avisos.append(f"Hay {ca['borradores']} asientos en borrador que no se incluyen.")
    if not ejercicio.cerrado:
        avisos.append(f"El ejercicio {ejercicio} no está cerrado: cifras provisionales.")
    if estado.sin_clasificar:
        avisos.append(f"{len(estado.sin_clasificar)} subcuentas con saldo no encajan en ninguna partida.")
    if ca["anterior"] is None:
        avisos.append("No hay ejercicio anterior: la columna «anterior» va a cero.")
    datos = {
        "entidad": _entidad(), "moneda": MONEDA_CONTABILIDAD, "ejercicio": ejercicio.anio,
        "ejercicio_anterior": ca["anterior"].anio if ca["anterior"] else None,
        "estado": estado.titulo, "modelo": p.modelo, "secciones": secciones,
        "enlace": f"{reverse('cuadro_cuentas:cuentas_anuales')}?ejercicio={ejercicio.anio}&modelo={p.modelo}",
    }
    if p.estado == "balance":
        datos["descuadre_activo_pasivo"] = importe(estado.secciones[0][2] - estado.secciones[1][2])
    return Resultado(datos=datos, fuente="Contabilidad (cuadro_cuentas): cuentas anuales",
                     fecha_datos=consultado_en(), avisos=avisos)


# --------------------------------------------------------------------------- #
class ResumenCajasArgs(_Args):
    anio: int = Field(ge=2000, le=2100)
    mes: int | None = Field(None, ge=1, le=12, description="Mes (1-12). Sin mes, el año entero.")


@herramienta(
    "resumen_cajas",
    descripcion="Ventas de caja por punto de venta (bar, comedor, chill out) y otros cobros (ecotasa, menús, "
                "facturas, parking) de un año o de un mes. Son cobros de caja diaria, no contabilidad.",
    argumentos=ResumenCajasArgs, dominio="informes", permisos=("cajas.view_cajames",),
)
def resumen_cajas(p: ResumenCajasArgs, ctx):
    from cajas.models import OTROS_COBROS, PUNTOS_VENTA
    from cajas.resumen import anios_disponibles, resumen_anual

    resumen = resumen_anual(p.anio)
    filas = resumen["filas"]
    if not filas:
        raise NoDisponible("SIN_DATOS_CAJAS", f"No hay cajas registradas en {p.anio}.",
                           {"anios_con_datos": anios_disponibles()})

    def bloque(fila):
        return {
            "puntos_venta": {etiqueta: importe(fila[campo]) for campo, etiqueta in PUNTOS_VENTA},
            "total_ventas": importe(fila["total_ventas"]),
            "otros_cobros": {etiqueta: importe(fila[campo]) for campo, etiqueta in OTROS_COBROS},
            "total_otros_cobros": importe(fila["total_cobros"]),
        }

    if p.mes:
        fila = next((f for f in filas if f["mes"] == p.mes), None)
        if fila is None:
            raise NoDisponible("SIN_DATOS_CAJAS", f"El mes {p.mes}/{p.anio} no está registrado en cajas.",
                               {"meses_con_datos": [f["mes"] for f in filas]})
        datos = {"anio": p.anio, "mes": fila["nombre"], **bloque(fila),
                 "enlace": reverse("cajas:detalle", args=[p.anio, p.mes])}
    else:
        datos = {"anio": p.anio, "meses_con_datos": [f["nombre"] for f in filas], **bloque(resumen["total"]),
                 "por_mes": [{"mes": f["nombre"], "total_ventas": importe(f["total_ventas"]),
                              "total_otros_cobros": importe(f["total_cobros"])} for f in filas],
                 "enlace": f"{reverse('cajas:resumen')}?anio={p.anio}"}
    avisos = [] if p.mes or len(filas) == 12 else [f"El año solo tiene {len(filas)} meses registrados."]
    return Resultado(datos={"moneda": "EUR", **datos}, fuente="Cajas diarias (cajas)",
                     fecha_datos=consultado_en(), avisos=avisos)


# --------------------------------------------------------------------------- #
class CostePersonalArgs(_Args):
    anio: int = Field(ge=2000, le=2100)
    mes: int | None = Field(None, ge=1, le=12, description="Mes (1-12). Sin mes, el año entero.")


@herramienta(
    "coste_personal",
    descripcion="Coste de personal (nóminas) por departamento de un año o un mes: coste empresa, extras y "
                "número de empleados. No da datos individuales de empleados.",
    argumentos=CostePersonalArgs, dominio="informes", permisos=("nominas.view_nominamensual",),
)
def coste_personal(p: CostePersonalArgs, ctx):
    from nominas.models import NominaMensual

    qs = NominaMensual.objects.filter(anio=p.anio)
    if p.mes:
        qs = qs.filter(mes=p.mes)
    filas = list(qs.values("departamento").annotate(
        coste=Sum("cost_tot"), extras=Sum("importe_extra"), empleados=Count("empleado", distinct=True),
    ).order_by("departamento"))
    if not filas:
        meses = sorted(set(NominaMensual.objects.filter(anio=p.anio).values_list("mes", flat=True)))
        raise NoDisponible("SIN_DATOS_NOMINAS", "No hay nóminas importadas para ese periodo.",
                           {"meses_con_datos": meses})
    coste = sum((f["coste"] for f in filas), CERO)
    extras = sum((f["extras"] for f in filas), CERO)
    meses = sorted(set(qs.values_list("mes", flat=True)))
    avisos = [] if p.mes or len(meses) == 12 else [f"Solo hay {len(meses)} meses importados en {p.anio}."]
    return Resultado(
        datos={
            "moneda": "EUR", "anio": p.anio, "mes": p.mes, "meses_con_datos": meses,
            "definicion": "Total = coste total empresa + extras.",
            "departamentos": [{"departamento": f["departamento"] or "(sin departamento)",
                               "coste_empresa": importe(f["coste"]), "extras": importe(f["extras"]),
                               "total": importe(f["coste"] + f["extras"]), "empleados": f["empleados"]}
                              for f in filas],
            "total": {"coste_empresa": importe(coste), "extras": importe(extras), "total": importe(coste + extras)},
        },
        fuente="Nóminas (nominas)", fecha_datos=consultado_en(), avisos=avisos,
    )
