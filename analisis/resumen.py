"""Cálculo del resumen de beneficios: ingresos (reservas) frente a gastos
(proveedores, acreedores y nóminas), por mes, departamento, cuenta y canal.

Criterios:
- Importes con IVA, tal como vienen en las fuentes (los acreedores solo traen
  el total de la factura, así que todo se compara en total).
- Ingreso de una reserva = estancias + extras + tasas. Se suma por componentes
  porque a veces las tasas no van incluidas en «Total Estancias».
- El ingreso se reparte por noches: cada noche cuenta en su mes. Las noches
  posteriores a hoy son «previsto» y solo entran en el beneficio si se pide.
- Gasto de proveedores = total del diario de compras (albaranes + facturas).
- Gasto de acreedores = facturas con cuenta de gasto. Las de cuenta 2xx
  (inmovilizado) son inversiones: se muestran aparte, no restan del beneficio.
- Gasto de nóminas = coste empresa (cost_tot) + importe extra.
"""

import unicodedata
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

from django.db.models import Count, F, Sum
from django.db.models.functions import ExtractMonth

from .models import DEPARTAMENTOS, AsignacionDepartamento, CompraProveedor, FacturaAcreedor, Reserva

CERO = Decimal("0")
SIN_ASIGNAR = "Sin asignar"
MESES = ["Ene", "Feb", "Mar", "Abr", "May", "Jun", "Jul", "Ago", "Sep", "Oct", "Nov", "Dic"]
MESES_LARGOS = ["Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio", "Julio", "Agosto",
                "Septiembre", "Octubre", "Noviembre", "Diciembre"]

# Nombres del PGC 2007 para las cuentas que aparecen como contrapartida de acreedores
NOMBRES_CUENTA = {
    "600": "Compras de mercaderías",
    "601": "Compras de materias primas",
    "602": "Compras de otros aprovisionamientos",
    "621": "Arrendamientos y cánones",
    "622": "Reparaciones y conservación",
    "623": "Servicios de profesionales independientes",
    "624": "Transportes",
    "625": "Primas de seguros",
    "626": "Servicios bancarios",
    "627": "Publicidad, propaganda y RR. PP.",
    "628": "Suministros",
    "629": "Otros servicios",
    "631": "Otros tributos",
    "211": "Construcciones",
    "212": "Instalaciones técnicas",
    "213": "Maquinaria",
    "214": "Utillaje",
    "215": "Otras instalaciones",
    "216": "Mobiliario",
    "217": "Equipos para procesos de información",
    "218": "Elementos de transporte",
    "219": "Otro inmovilizado material",
}


def _sin_acentos(texto: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", texto)
                   if unicodedata.category(c) != "Mn").lower().strip()


_DEPTOS = {_sin_acentos(d): d for d in DEPARTAMENTOS}


def normalizar_departamento(texto: str) -> str:
    """«COCINA», «cocina », «Recepcion» -> nombre canónico; vacío -> «Sin asignar»."""
    if not texto or not texto.strip():
        return SIN_ASIGNAR
    return _DEPTOS.get(_sin_acentos(texto), texto.strip().capitalize())


def _doce():
    return [CERO] * 12


def _pct(parte, total):
    return float(parte / total * 100) if total else None


# ------------------------------------------------------------------ ingresos

def _noches(reserva: Reserva):
    """Fechas de las noches de la reserva (un uso de día cuenta como una)."""
    n = max(reserva.noches, 1)
    return [reserva.entrada + timedelta(days=i) for i in range(n)]


def _ingresos(anio: int, hoy: date, incluir_previsto: bool, mes: int | None):
    """Reparte cada reserva por noches y acumula por mes y por agencia."""
    inicio, fin = date(anio, 1, 1), date(anio, 12, 31)
    reservas = Reserva.objects.filter(entrada__lte=fin, salida__gte=inicio)

    mensual = {k: _doce() for k in ("estancias", "extras", "tasas", "previsto", "noches_hab")}
    agencias = defaultdict(lambda: {"reservas": 0, "noches_hab": 0, "estancias": CERO, "ingresos": CERO})

    for r in reservas.iterator():
        noches = _noches(r)
        n = len(noches)
        estancias = r.total_estancias / n
        extras = (r.fact_extras + r.pend_extras) / n
        tasas = (r.fact_impuesto + r.pend_tasas) / n
        cuenta_en_periodo = False

        for d in noches:
            if d.year != anio:
                continue
            m = d.month - 1
            realizado = d <= hoy
            if realizado:
                mensual["estancias"][m] += estancias
                mensual["extras"][m] += extras
                mensual["tasas"][m] += tasas
                mensual["noches_hab"][m] += r.habitaciones
            else:
                mensual["previsto"][m] += estancias + extras + tasas

            # Desglose por agencia: periodo seleccionado y criterio de previsto
            if (realizado or incluir_previsto) and (mes is None or d.month == mes):
                a = agencias[r.agencia or "Sin agencia"]
                a["noches_hab"] += r.habitaciones
                a["estancias"] += estancias
                a["ingresos"] += estancias + extras + tasas
                cuenta_en_periodo = True

        if cuenta_en_periodo:
            agencias[r.agencia or "Sin agencia"]["reservas"] += 1

    return mensual, agencias


# ------------------------------------------------------------------ gastos

def _por_mes(qs, campo_fecha, expr):
    serie = _doce()
    for fila in qs.annotate(m=ExtractMonth(campo_fecha)).values("m").annotate(s=Sum(expr)):
        serie[fila["m"] - 1] += fila["s"] or CERO
    return serie


def _nominas():
    """Importa el modelo de nóminas solo si la app está instalada."""
    try:
        from nominas.models import NominaMensual
    except ImportError:
        return None
    return NominaMensual


def _asignaciones(origen):
    return {a.clave: normalizar_departamento(a.departamento)
            for a in AsignacionDepartamento.objects.filter(origen=origen)}


def _top(filas, n=10):
    """Las n primeras filas (ya ordenadas) + una fila «Resto» con lo demás."""
    if len(filas) <= n + 1:
        return filas
    resto = filas[n:]
    return filas[:n] + [{
        "nombre": f"Resto ({len(resto)})", "departamento": "", "documentos": sum(f["documentos"] for f in resto),
        "importe": sum((f["importe"] for f in resto), CERO), "resto": True,
    }]


# ------------------------------------------------------------------ resumen

def calcular_resumen(anio: int, mes: int | None = None, incluir_previsto: bool = False,
                     hoy: date | None = None) -> dict:
    hoy = hoy or date.today()
    filtro_mes = {"fecha__month": mes} if mes else {}

    # --- Ingresos
    ing, agencias = _ingresos(anio, hoy, incluir_previsto, mes)
    ingresos = [ing["estancias"][i] + ing["extras"][i] + ing["tasas"][i]
                + (ing["previsto"][i] if incluir_previsto else CERO) for i in range(12)]

    # --- Gastos
    compras = CompraProveedor.objects.filter(fecha__year=anio)
    facturas = FacturaAcreedor.objects.filter(fecha__year=anio)
    gasto_acr = facturas.exclude(cuenta__startswith="2")
    inversion_acr = facturas.filter(cuenta__startswith="2")
    Nomina = _nominas()
    nominas = Nomina.objects.filter(anio=anio) if Nomina else None

    proveedores = _por_mes(compras, "fecha", "total")
    acreedores = _por_mes(gasto_acr, "fecha", "importe")
    inversiones = _por_mes(inversion_acr, "fecha", "importe")
    personal = _doce()
    if nominas is not None:
        for fila in nominas.values("mes").annotate(s=Sum(F("cost_tot") + F("importe_extra"))):
            personal[fila["mes"] - 1] += fila["s"] or CERO

    gastos = [proveedores[i] + acreedores[i] + personal[i] for i in range(12)]
    beneficio = [ingresos[i] - gastos[i] for i in range(12)]
    acumulado, suma = [], CERO
    for b in beneficio:
        suma += b
        acumulado.append(suma)

    mensual = {
        "estancias": ing["estancias"], "extras": ing["extras"], "tasas": ing["tasas"],
        "previsto": ing["previsto"], "ingresos": ingresos,
        "proveedores": proveedores, "acreedores": acreedores, "nominas": personal,
        "gastos": gastos, "beneficio": beneficio, "acumulado": acumulado,
        "inversiones": inversiones, "noches_hab": ing["noches_hab"],
    }

    # --- Totales del periodo seleccionado (año completo o un mes)
    idx = [mes - 1] if mes else range(12)
    tot = {k: sum((v[i] for i in idx), CERO) for k, v in mensual.items() if k != "acumulado"}
    tot["margen"] = _pct(tot["beneficio"], tot["ingresos"])
    tot["adr"] = tot["estancias"] / tot["noches_hab"] if tot["noches_hab"] else None

    # --- Por departamento (solo gastos: el ingreso es todo alojamiento)
    asig_prov = _asignaciones(AsignacionDepartamento.Origen.PROVEEDOR)
    asig_acr = _asignaciones(AsignacionDepartamento.Origen.ACREEDOR)
    deptos = defaultdict(lambda: {"proveedores": CERO, "acreedores": CERO, "nominas": CERO})

    for f in compras.filter(**filtro_mes).values("codigo_proveedor").annotate(s=Sum("total")):
        deptos[asig_prov.get(str(f["codigo_proveedor"]), SIN_ASIGNAR)]["proveedores"] += f["s"]
    for f in gasto_acr.filter(**filtro_mes).values("fichero").annotate(s=Sum("importe")):
        deptos[asig_acr.get(f["fichero"], SIN_ASIGNAR)]["acreedores"] += f["s"]
    if nominas is not None:
        qs = nominas.filter(mes=mes) if mes else nominas
        for f in qs.values("departamento").annotate(s=Sum(F("cost_tot") + F("importe_extra"))):
            deptos[normalizar_departamento(f["departamento"])]["nominas"] += f["s"] or CERO

    total_gastos = tot["gastos"]
    departamentos = []
    for nombre, d in deptos.items():
        total = d["proveedores"] + d["acreedores"] + d["nominas"]
        departamentos.append({"nombre": nombre, **d, "total": total, "pct": _pct(total, total_gastos)})
    # Orden: departamentos conocidos por importe; «Sin asignar» siempre al final
    departamentos.sort(key=lambda d: (d["nombre"] == SIN_ASIGNAR, -d["total"]))

    # --- Acreedores por cuenta (gasto e inversión)
    def por_cuenta(qs):
        filas = []
        for f in qs.filter(**filtro_mes).values("cuenta").annotate(s=Sum("importe")):
            filas.append((f["cuenta"][:3], f["s"]))
        agrupado = defaultdict(lambda: CERO)
        for c, s in filas:
            agrupado[c] += s
        total = sum(agrupado.values(), CERO)
        return sorted(({"cuenta": c, "nombre": NOMBRES_CUENTA.get(c, "Otras cuentas"),
                        "importe": s, "pct": _pct(s, total)} for c, s in agrupado.items()),
                      key=lambda x: -x["importe"])

    cuentas_gasto = por_cuenta(gasto_acr)
    cuentas_inversion = por_cuenta(inversion_acr)

    # --- Canales (agencias)
    total_ing_agencias = sum((a["ingresos"] for a in agencias.values()), CERO)
    canales = sorted(({
        "nombre": nombre, **a,
        "adr": a["estancias"] / a["noches_hab"] if a["noches_hab"] else None,
        "pct": _pct(a["ingresos"], total_ing_agencias),
    } for nombre, a in agencias.items() if a["ingresos"] or a["noches_hab"]), key=lambda x: -x["ingresos"])

    # --- Principales proveedores y acreedores
    top_prov = [{"nombre": f["proveedor"], "departamento": asig_prov.get(str(f["codigo_proveedor"]), SIN_ASIGNAR),
                 "documentos": f["n"], "importe": f["s"]}
                for f in compras.filter(**filtro_mes).values("codigo_proveedor", "proveedor")
                .annotate(s=Sum("total"), n=Count("id")).order_by("-s")]
    top_acr = [{"nombre": f["acreedor"], "departamento": asig_acr.get(f["fichero"], SIN_ASIGNAR),
                "documentos": f["n"], "importe": f["s"]}
               for f in gasto_acr.filter(**filtro_mes).values("fichero", "acreedor")
               .annotate(s=Sum("importe"), n=Count("id")).order_by("-s")]
    top_prov, top_acr = _top(top_prov), _top(top_acr)
    for lista, total in ((top_prov, tot["proveedores"]), (top_acr, tot["acreedores"])):
        for f in lista:
            f["pct"] = _pct(f["importe"], total)

    # --- Avisos de calidad de datos
    avisos = []
    pendientes = AsignacionDepartamento.objects.filter(departamento="").count()
    if pendientes:
        avisos.append(f"{pendientes} proveedores/acreedores sin departamento asignado.")
    if nominas is None:
        avisos.append("La app de nóminas no está instalada: el gasto de personal sale a 0.")
    else:
        meses_nomina = set(nominas.values_list("mes", flat=True))
        meses_con_ingresos = {i + 1 for i in range(12) if ingresos[i] > 0}
        faltan = sorted(meses_con_ingresos - meses_nomina)
        if faltan:
            avisos.append("Meses con ingresos pero sin nóminas importadas: "
                          + ", ".join(MESES_LARGOS[m - 1] for m in faltan) + ".")
    if tot["inversiones"]:
        avisos.append("Las facturas de acreedores con cuenta 2xx (inmovilizado) se muestran como "
                      "inversiones y no restan del beneficio.")

    return {
        "anio": anio, "mes": mes, "incluir_previsto": incluir_previsto, "hoy": hoy,
        "meses": MESES, "mensual": mensual, "totales": tot,
        "departamentos": departamentos, "cuentas_gasto": cuentas_gasto,
        "cuentas_inversion": cuentas_inversion, "canales": canales,
        "top_proveedores": top_prov, "top_acreedores": top_acr, "avisos": avisos,
    }


def anios_disponibles() -> list[int]:
    anios = {d.year for d in Reserva.objects.dates("entrada", "year")}
    anios |= {d.year for d in CompraProveedor.objects.dates("fecha", "year")}
    anios |= {d.year for d in FacturaAcreedor.objects.dates("fecha", "year")}
    return sorted(anios, reverse=True) or [date.today().year]
