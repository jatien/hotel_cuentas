"""Cálculo del resumen de beneficios: ingresos (reservas, bar y comedor) frente a
gastos (proveedores, acreedores y personal), por mes, departamento, cuenta y canal.

Criterios:
- Por defecto todo va SIN IVA (beneficio neto). Con `con_iva=True` se suma el IVA.
  · Reservas: estancias y extras del PMS llevan el 10 % de IVA incluido (lo
    confirma el libro de IVA repercutido: la cuota de las «N/FACTURA» es el
    9,1 % de lo facturado = 10/110). Las tasas turísticas no llevan IVA.
  · Proveedores: base del diario de compras (bruto − dto. p. p. + portes).
  · Acreedores: total de la factura menos su IVA soportado, que se busca en el
    libro de IVA soportado por asiento y documento. En las facturas con
    inversión del sujeto pasivo (el IVA aparece también en el repercutido) el
    total ya es la base.
- Ingreso de una reserva = estancias + extras + tasas, repartido por noches:
  cada noche cuenta en su mes. Las noches posteriores a hoy son «previsto» y
  solo entran en el beneficio si se pide.
- Bar y comedor: facturas «N/FRA BAR/COMEDOR» del IVA repercutido. Solo se
  conoce la cuota, así que la base se ESTIMA con IVA_BAR_COMEDOR. Las facturas
  trimestrales (fecha a fin de trimestre) se reparten entre sus tres meses.
- Acreedores con cuenta 2xx (inmovilizado) = inversiones: aparte, no restan.
- Personal = coste empresa de la nómina (cost_tot) + extras. Si hay hoja de
  extras importada para ese mes se usa; si no, el importe extra de la nómina.
"""

import unicodedata
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

from django.db.models import Count, F, Sum

from .models import (DEPARTAMENTOS, ApunteIva, AsignacionDepartamento, CompraProveedor, ExtraPersonal,
                     FacturaAcreedor, Reserva)

CERO = Decimal("0")
IVA_ALOJAMIENTO = Decimal("0.10")
IVA_BAR_COMEDOR = Decimal("0.10")   # supuesto: cámbialo si el bar factura parte al 21 %
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


def _clave_doc(asiento, documento):
    return (asiento, (documento or "").strip())


# ------------------------------------------------------------------ ingresos

def _noches(reserva: Reserva):
    """Fechas de las noches de la reserva (un uso de día cuenta como una)."""
    n = max(reserva.noches, 1)
    return [reserva.entrada + timedelta(days=i) for i in range(n)]


def _ingresos_reservas(anio: int, hoy: date, incluir_previsto: bool, mes: int | None, con_iva: bool):
    """Reparte cada reserva por noches y acumula por mes y por agencia."""
    inicio, fin = date(anio, 1, 1), date(anio, 12, 31)
    reservas = Reserva.objects.filter(entrada__lte=fin, salida__gte=inicio)
    # El PMS da importes con IVA incluido: para el neto se divide entre 1,10
    factor = Decimal("1") if con_iva else 1 / (1 + IVA_ALOJAMIENTO)

    mensual = {k: _doce() for k in ("estancias", "extras", "tasas", "previsto", "noches_hab")}
    agencias = defaultdict(lambda: {"reservas": 0, "noches_hab": 0, "estancias": CERO, "ingresos": CERO})

    for r in reservas.iterator():
        noches = _noches(r)
        n = len(noches)
        estancias = r.total_estancias * factor / n
        extras = (r.fact_extras + r.pend_extras) * factor / n
        tasas = (r.fact_impuesto + r.pend_tasas) / n          # sin IVA
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

            if (realizado or incluir_previsto) and (mes is None or d.month == mes):
                a = agencias[r.agencia or "Sin agencia"]
                a["noches_hab"] += r.habitaciones
                a["estancias"] += estancias
                a["ingresos"] += estancias + extras + tasas
                cuenta_en_periodo = True

        if cuenta_en_periodo:
            agencias[r.agencia or "Sin agencia"]["reservas"] += 1

    return mensual, agencias


def _meses_de_factura(fecha: date) -> list[int]:
    """Factura a fin de trimestre (30/06, 30/09…) -> los tres meses del trimestre."""
    if fecha.month % 3 == 0 and fecha.day >= 28:
        return [fecha.month - 2, fecha.month - 1, fecha.month]
    return [fecha.month]


def _ingresos_bar_comedor(anio: int, con_iva: bool):
    """{'Bar': [12], 'Comedor': [12]} a partir de las facturas N/FRA del IVA repercutido."""
    series = {"Bar": _doce(), "Comedor": _doce()}
    apuntes = ApunteIva.objects.filter(libro=ApunteIva.Libro.REPERCUTIDO, fecha__year=anio,
                                       concepto__istartswith="N/FRA ")
    for a in apuntes:
        texto = _sin_acentos(a.concepto)
        destino = "Bar" if " bar" in texto else "Comedor" if "comedor" in texto else None
        if not destino:
            continue
        base = a.cuota / IVA_BAR_COMEDOR
        importe = base + a.cuota if con_iva else base
        meses = _meses_de_factura(a.fecha)
        for m in meses:
            series[destino][m - 1] += importe / len(meses)
    return series


# ------------------------------------------------------------------ gastos

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


def _facturas_acreedor(anio: int, con_iva: bool):
    """Facturas del año con su importe (sin IVA salvo que se pida) y el IVA casado."""
    soportado = defaultdict(lambda: CERO)
    for a in ApunteIva.objects.filter(libro=ApunteIva.Libro.SOPORTADO, fecha__year=anio):
        soportado[_clave_doc(a.asiento, a.documento)] += a.cuota
    # Inversión del sujeto pasivo: la misma factura está también en el repercutido
    isp = {_clave_doc(a.asiento, a.documento)
           for a in ApunteIva.objects.filter(libro=ApunteIva.Libro.REPERCUTIDO, fecha__year=anio,
                                             concepto__istartswith="S/FRA")}

    filas, casadas = [], set()
    for f in FacturaAcreedor.objects.filter(fecha__year=anio):
        clave = _clave_doc(f.asiento, f.documento)
        iva = CERO
        if clave in soportado:
            casadas.add(clave)
            if clave not in isp:
                iva = soportado[clave]
        filas.append({
            "fecha": f.fecha, "fichero": f.fichero, "acreedor": f.acreedor, "cuenta": f.cuenta[:3],
            "inversion": f.es_inversion, "iva": iva,
            "importe": f.importe if con_iva else f.importe - iva,
        })
    return filas, soportado, casadas


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
                     con_iva: bool = False, hoy: date | None = None) -> dict:
    hoy = hoy or date.today()
    en_periodo = (lambda m: m == mes) if mes else (lambda m: True)
    filtro_mes = {"fecha__month": mes} if mes else {}

    # --- Ingresos
    ing, agencias = _ingresos_reservas(anio, hoy, incluir_previsto, mes, con_iva)
    bar_comedor = _ingresos_bar_comedor(anio, con_iva)
    ingresos = [ing["estancias"][i] + ing["extras"][i] + ing["tasas"][i]
                + bar_comedor["Bar"][i] + bar_comedor["Comedor"][i]
                + (ing["previsto"][i] if incluir_previsto else CERO) for i in range(12)]

    # --- Proveedores
    compras = CompraProveedor.objects.filter(fecha__year=anio)
    expr_compra = F("total") if con_iva else F("bruto") - F("dto_pp") + F("portes_gastos")
    proveedores = _doce()
    for c in compras.values("fecha__month").annotate(s=Sum(expr_compra)):
        proveedores[c["fecha__month"] - 1] += c["s"] or CERO

    # --- Acreedores (gasto e inversión)
    facturas, soportado, casadas = _facturas_acreedor(anio, con_iva)
    acreedores, inversiones = _doce(), _doce()
    for f in facturas:
        (inversiones if f["inversion"] else acreedores)[f["fecha"].month - 1] += f["importe"]

    # --- Personal: nómina (coste empresa) + extras
    Nomina = _nominas()
    nominas = Nomina.objects.filter(anio=anio) if Nomina else None
    extras_qs = ExtraPersonal.objects.filter(anio=anio)
    meses_con_hoja = set(extras_qs.values_list("mes", flat=True))
    coste_nomina, extras_personal = _doce(), _doce()
    if nominas is not None:
        for fila in nominas.values("mes").annotate(c=Sum("cost_tot"), e=Sum("importe_extra")):
            coste_nomina[fila["mes"] - 1] += fila["c"] or CERO
            if fila["mes"] not in meses_con_hoja:          # sin hoja: extra anotado en la nómina
                extras_personal[fila["mes"] - 1] += fila["e"] or CERO
    for fila in extras_qs.values("mes").annotate(s=Sum("importe")):
        extras_personal[fila["mes"] - 1] += fila["s"] or CERO
    personal = [coste_nomina[i] + extras_personal[i] for i in range(12)]

    gastos = [proveedores[i] + acreedores[i] + personal[i] for i in range(12)]
    beneficio = [ingresos[i] - gastos[i] for i in range(12)]
    acumulado, suma = [], CERO
    for b in beneficio:
        suma += b
        acumulado.append(suma)

    # --- IVA a liquidar (siempre en cuota, independientemente del modo)
    iva_rep, iva_sop = _doce(), _doce()
    for libro, serie in ((ApunteIva.Libro.REPERCUTIDO, iva_rep), (ApunteIva.Libro.SOPORTADO, iva_sop)):
        for fila in ApunteIva.objects.filter(libro=libro, fecha__year=anio).values("fecha__month").annotate(s=Sum("cuota")):
            serie[fila["fecha__month"] - 1] += fila["s"] or CERO
    iva_liquidar = [iva_rep[i] - iva_sop[i] for i in range(12)]

    mensual = {
        "estancias": ing["estancias"], "extras": ing["extras"], "tasas": ing["tasas"],
        "bar": bar_comedor["Bar"], "comedor": bar_comedor["Comedor"],
        "previsto": ing["previsto"], "ingresos": ingresos,
        "proveedores": proveedores, "acreedores": acreedores,
        "coste_nomina": coste_nomina, "extras_personal": extras_personal, "nominas": personal,
        "gastos": gastos, "beneficio": beneficio, "acumulado": acumulado,
        "inversiones": inversiones, "noches_hab": ing["noches_hab"],
        "iva_repercutido": iva_rep, "iva_soportado": iva_sop, "iva_liquidar": iva_liquidar,
    }

    # --- Totales del periodo seleccionado (año completo o un mes)
    idx = [mes - 1] if mes else range(12)
    tot = {k: sum((v[i] for i in idx), CERO) for k, v in mensual.items() if k != "acumulado"}
    tot["margen"] = _pct(tot["beneficio"], tot["ingresos"])
    tot["adr"] = tot["estancias"] / tot["noches_hab"] if tot["noches_hab"] else None
    tot["bar_comedor"] = tot["bar"] + tot["comedor"]

    # --- Por departamento
    asig_prov = _asignaciones(AsignacionDepartamento.Origen.PROVEEDOR)
    asig_acr = _asignaciones(AsignacionDepartamento.Origen.ACREEDOR)
    asig_pers = _asignaciones(AsignacionDepartamento.Origen.PERSONAL)
    deptos = defaultdict(lambda: {"ingresos": CERO, "proveedores": CERO, "acreedores": CERO, "nominas": CERO})

    for nombre in ("Bar", "Comedor"):
        ingreso = sum((bar_comedor[nombre][i] for i in idx), CERO)
        if ingreso:
            deptos[nombre]["ingresos"] += ingreso
    for c in compras.filter(**filtro_mes).values("codigo_proveedor").annotate(s=Sum(expr_compra)):
        deptos[asig_prov.get(str(c["codigo_proveedor"]), SIN_ASIGNAR)]["proveedores"] += c["s"]
    for f in facturas:
        if not f["inversion"] and en_periodo(f["fecha"].month):
            deptos[asig_acr.get(f["fichero"], SIN_ASIGNAR)]["acreedores"] += f["importe"]
    if nominas is not None:
        qs = nominas.filter(mes=mes) if mes else nominas
        for n in qs.values("departamento", "mes").annotate(c=Sum("cost_tot"), e=Sum("importe_extra")):
            extra = CERO if n["mes"] in meses_con_hoja else (n["e"] or CERO)
            deptos[normalizar_departamento(n["departamento"])]["nominas"] += (n["c"] or CERO) + extra
    for e in (extras_qs.filter(mes=mes) if mes else extras_qs).values("persona").annotate(s=Sum("importe")):
        deptos[asig_pers.get(e["persona"], SIN_ASIGNAR)]["nominas"] += e["s"]

    departamentos = []
    for nombre, d in deptos.items():
        total = d["proveedores"] + d["acreedores"] + d["nominas"]
        departamentos.append({"nombre": nombre, **d, "total": total, "pct": _pct(total, tot["gastos"]),
                              "resultado": d["ingresos"] - total if d["ingresos"] else None})
    departamentos.sort(key=lambda d: (d["nombre"] == SIN_ASIGNAR, -d["total"]))

    # --- Acreedores por cuenta (gasto e inversión)
    def por_cuenta(inversion: bool):
        agrupado = defaultdict(lambda: CERO)
        for f in facturas:
            if f["inversion"] == inversion and en_periodo(f["fecha"].month):
                agrupado[f["cuenta"]] += f["importe"]
        total = sum(agrupado.values(), CERO)
        return sorted(({"cuenta": c, "nombre": NOMBRES_CUENTA.get(c, "Otras cuentas"),
                        "importe": s, "pct": _pct(s, total)} for c, s in agrupado.items()),
                      key=lambda x: -x["importe"])

    cuentas_gasto, cuentas_inversion = por_cuenta(False), por_cuenta(True)

    # --- Canales (agencias)
    total_ing_agencias = sum((a["ingresos"] for a in agencias.values()), CERO)
    canales = sorted(({
        "nombre": nombre, **a,
        "adr": a["estancias"] / a["noches_hab"] if a["noches_hab"] else None,
        "pct": _pct(a["ingresos"], total_ing_agencias),
    } for nombre, a in agencias.items() if a["ingresos"] or a["noches_hab"]), key=lambda x: -x["ingresos"])

    # --- Principales proveedores, acreedores y extras de personal
    top_prov = [{"nombre": c["proveedor"], "departamento": asig_prov.get(str(c["codigo_proveedor"]), SIN_ASIGNAR),
                 "documentos": c["n"], "importe": c["s"]}
                for c in compras.filter(**filtro_mes).values("codigo_proveedor", "proveedor")
                .annotate(s=Sum(expr_compra), n=Count("id")).order_by("-s")]
    por_acreedor = defaultdict(lambda: {"documentos": 0, "importe": CERO})
    for f in facturas:
        if not f["inversion"] and en_periodo(f["fecha"].month):
            a = por_acreedor[(f["fichero"], f["acreedor"])]
            a["documentos"] += 1
            a["importe"] += f["importe"]
    top_acr = sorted(({"nombre": acr, "departamento": asig_acr.get(fich, SIN_ASIGNAR), **v}
                      for (fich, acr), v in por_acreedor.items()), key=lambda x: -x["importe"])
    top_prov, top_acr = _top(top_prov), _top(top_acr)
    for lista, total in ((top_prov, tot["proveedores"]), (top_acr, tot["acreedores"])):
        for f in lista:
            f["pct"] = _pct(f["importe"], total)

    extras_personas = [{"nombre": e["persona"], "departamento": asig_pers.get(e["persona"], SIN_ASIGNAR),
                        "meses": e["n"], "importe": e["s"]}
                       for e in (extras_qs.filter(mes=mes) if mes else extras_qs)
                       .values("persona").annotate(s=Sum("importe"), n=Count("id")).order_by("-s")]

    tot["extras_hoja"] = sum((e["importe"] for e in extras_personas), CERO)

    # --- Avisos de calidad de datos
    avisos = _avisos(anio, ingresos, nominas, meses_con_hoja, tot, soportado, casadas, compras, bar_comedor)

    return {
        "anio": anio, "mes": mes, "incluir_previsto": incluir_previsto, "con_iva": con_iva, "hoy": hoy,
        "meses": MESES, "mensual": mensual, "totales": tot,
        "departamentos": departamentos, "cuentas_gasto": cuentas_gasto,
        "cuentas_inversion": cuentas_inversion, "canales": canales,
        "top_proveedores": top_prov, "top_acreedores": top_acr, "extras_personas": extras_personas,
        "avisos": avisos,
    }


def _avisos(anio, ingresos, nominas, meses_con_hoja, tot, soportado, casadas, compras, bar_comedor):
    avisos = []
    pendientes = AsignacionDepartamento.objects.filter(departamento="").count()
    if pendientes:
        avisos.append(f"{pendientes} proveedores, acreedores o personas sin departamento asignado.")

    if nominas is None:
        avisos.append("La app de nóminas no está instalada: el coste de nómina sale a 0.")
    else:
        meses_nomina = set(nominas.values_list("mes", flat=True))
        faltan = sorted({i + 1 for i in range(12) if ingresos[i] > 0} - meses_nomina)
        if faltan:
            avisos.append("Meses con ingresos pero sin nóminas importadas: "
                          + ", ".join(MESES_LARGOS[m - 1] for m in faltan) + ".")
        dobles = sorted(m for m in meses_con_hoja
                        if nominas.filter(mes=m).exclude(importe_extra=0).exists())
        if dobles:
            avisos.append("En " + ", ".join(MESES_LARGOS[m - 1] for m in dobles) + " hay hoja de extras y también "
                          "importe extra en la nómina: se usa solo la hoja para no contarlo dos veces.")

    if not ApunteIva.objects.filter(fecha__year=anio).exists():
        avisos.append("Sin libros de IVA importados: el IVA de los acreedores no se puede descontar.")
    else:
        # IVA soportado de acreedores (cuenta 410) sin factura en los ficheros de acreedores
        huerfanos = defaultdict(lambda: CERO)
        for a in ApunteIva.objects.filter(libro=ApunteIva.Libro.SOPORTADO, fecha__year=anio,
                                          cuenta__startswith="410"):
            if _clave_doc(a.asiento, a.documento) not in casadas:
                huerfanos[a.concepto.removeprefix("S/FRA ").strip()] += a.cuota
        importantes = sorted(((n, c) for n, c in huerfanos.items() if abs(c) >= 100), key=lambda x: -abs(x[1]))
        if importantes:
            lista = ", ".join(f"{n} ({c:,.0f} € de IVA)".replace(",", ".") for n, c in importantes[:5])
            avisos.append("Hay IVA soportado de acreedores cuya factura no está en los ficheros de acreedores "
                          f"(¿falta su mayor?): {lista}.")
        # Control: IVA de proveedores del diario frente al libro (cuentas que no son 410)
        libro = ApunteIva.objects.filter(libro=ApunteIva.Libro.SOPORTADO, fecha__year=anio) \
            .exclude(cuenta__startswith="410").aggregate(s=Sum("cuota"))["s"] or CERO
        diario = compras.aggregate(s=Sum("iva"))["s"] or CERO
        if diario and abs(libro - diario) > max(Decimal("50"), diario * Decimal("0.02")):
            avisos.append(f"El IVA del diario de compras ({diario:,.0f} €) no coincide con el libro de IVA "
                          f"soportado de proveedores ({libro:,.0f} €).".replace(",", "."))
    if tot["bar"] or tot["comedor"]:
        avisos.append(f"Bar y comedor: base estimada a partir de la cuota de IVA al "
                      f"{IVA_BAR_COMEDOR * 100:.0f} %; las facturas trimestrales se reparten entre sus tres meses.")
    if tot["inversiones"]:
        avisos.append("Las facturas de acreedores con cuenta 2xx (inmovilizado) se muestran como "
                      "inversiones y no restan del beneficio.")
    return avisos


def anios_disponibles() -> list[int]:
    anios = {d.year for d in Reserva.objects.dates("entrada", "year")}
    anios |= {d.year for d in CompraProveedor.objects.dates("fecha", "year")}
    anios |= {d.year for d in FacturaAcreedor.objects.dates("fecha", "year")}
    return sorted(anios, reverse=True) or [date.today().year]
