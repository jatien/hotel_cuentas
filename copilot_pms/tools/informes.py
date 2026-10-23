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


# --------------------------------------------------------------------------- #
class ResumenBeneficioArgs(_Args):
    anio: int = Field(ge=2000, le=2100)
    mes: int | None = Field(None, ge=1, le=12, description="Mes (1-12). Sin mes, el año entero.")
    con_iva: bool = Field(False, description="False (por defecto) = importes sin IVA, el beneficio neto.")
    incluir_previsto: bool = Field(False, description="Sumar las noches futuras ya reservadas (previsto).")


@herramienta(
    "resumen_beneficio",
    descripcion="Análisis de beneficios de gestión (no contable) de un año o un mes: ingresos por origen "
                "(estancias, extras, tasas, bar, comedor, menús, parking), gastos (proveedores, acreedores, "
                "personal), beneficio y margen, gastos por departamento y por categoría, y canales de venta.",
    argumentos=ResumenBeneficioArgs, dominio="informes", permisos=("analisis.view_reserva",),
)
def resumen_beneficio(p: ResumenBeneficioArgs, ctx):
    from analisis.resumen import calcular_resumen

    r = calcular_resumen(p.anio, mes=p.mes, incluir_previsto=p.incluir_previsto, con_iva=p.con_iva, hoy=ctx.hoy)
    t = r["totales"]
    if not (t["ingresos"] or t["gastos"]):
        raise NoDisponible("SIN_DATOS_ANALISIS", f"No hay datos importados en el análisis para {p.anio}.")
    pct = lambda v: f"{v:.1f}" if v is not None else None  # noqa: E731
    return Resultado(
        datos={
            "moneda": "EUR", "anio": p.anio, "mes": p.mes, "con_iva": p.con_iva,
            "incluye_previsto": p.incluir_previsto,
            "definicion": "Beneficio de gestión = ingresos - (proveedores + acreedores + personal). "
                          "Las inversiones (cuentas 2xx) no restan. No es el resultado contable.",
            "ingresos": {"total": importe(t["ingresos"]), "estancias": importe(t["estancias"]),
                         "extras": importe(t["extras"]), "tasas": importe(t["tasas"]), "bar": importe(t["bar"]),
                         "comedor": importe(t["comedor"]), "menus": importe(t["menus"]),
                         "parking": importe(t["parking"]), "previsto": importe(t["previsto"])},
            "gastos": {"total": importe(t["gastos"]), "proveedores": importe(t["proveedores"]),
                       "acreedores": importe(t["acreedores"]), "personal": importe(t["nominas"])},
            "beneficio": importe(t["beneficio"]), "margen_pct": pct(t["margen"]),
            "inversiones": importe(t["inversiones"]),
            "adr": importe(t["adr"]) if t["adr"] is not None else None,
            "noches_habitacion": t["noches_hab"],
            "departamentos": [{"nombre": d["nombre"], "ingresos": importe(d["ingresos"]), "total_gasto": importe(d["total"]),
                               "pct_gasto": pct(d["pct"])} for d in r["departamentos"]],
            "categorias": [{"nombre": c["nombre"], "total": importe(c["total"]), "pct": pct(c["pct"])}
                           for c in r["categorias"][:12]],
            "canales": [{"nombre": c["nombre"], "ingresos": importe(c["ingresos"]), "pct": pct(c["pct"])}
                        for c in r["canales"][:10]],
            "enlace": f"{reverse('analisis:resumen')}?anio={p.anio}" + (f"&mes={p.mes}" if p.mes else ""),
        },
        fuente="Análisis de beneficios (analisis)", fecha_datos=consultado_en(), avisos=list(r["avisos"]),
    )
