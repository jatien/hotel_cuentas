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
- Bar, comedor, menús y parking: de la app «cajas» (cajas diarias reales).
  Bar = bar + chill out. Los importes de caja llevan IVA: para el neto se divide
  entre 1,10 (parking entre 1,21). Ecotasa y facturas de caja NO se suman: ya
  están en las reservas (tasas turísticas y cobro de las facturas de estancias).
  Si el año no tiene cajas importadas, se usa la estimación anterior: facturas
  «N/FRA BAR/COMEDOR» del IVA repercutido, cuya base se estima con IVA_BAR_COMEDOR. Las facturas
  trimestrales (fecha a fin de trimestre) se reparten entre sus tres meses.
- Acreedores con cuenta 2xx (inmovilizado) = inversiones: aparte, no restan.
- Gastos adicionales (p. ej. el alquiler, que solo aparece en el libro de IVA):
  se crean en el admin y se reparten por días entre sus fechas, como acreedores.
- Peso por canal: solo los canales principales (CANALES_PRINCIPALES); el peso de
  cada uno es su parte sobre la suma de esos canales en el mes.
- Personal = coste empresa de la nómina (cost_tot) + extras. Si hay hoja de
  extras importada para ese mes se usa; si no, el importe extra de la nómina.
"""

import unicodedata
from collections import Counter, defaultdict
from datetime import date, timedelta
from decimal import Decimal

from django.db.models import Count, F, Sum

from .models import (DEPARTAMENTOS, ApunteIva, AsignacionDepartamento, CompraProveedor, DepartamentoAlbaran,
                     ExtraPersonal, FacturaAcreedor, GastoAdicional, Reserva, Tercero)

CERO = Decimal("0")
IVA_ALOJAMIENTO = Decimal("0.10")
IVA_BAR_COMEDOR = Decimal("0.10")   # bar, comedor y menús
IVA_PARKING = Decimal("0.21")
SIN_ASIGNAR = "Sin asignar"
SIN_CATEGORIA = "Otros"
ACREEDORES = "Acreedores (sin departamento)"
# Canales principales para el peso mensual por agencia: (etiqueta, texto que contiene el nombre).
# El orden fija el color de cada canal. Las agencias que no encajan quedan fuera del cálculo.
CANALES_PRINCIPALES = [
    ("Directos", "directo"),
    ("Booking", "booking"),
    ("Expedia", "expedia"),
    ("Jet2", "jet2"),
    ("Sidetours", "sidetours"),
]
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

# En el análisis, algunos departamentos se agrupan dentro de otro. Los datos de
# origen no cambian (el albarán sigue siendo de Lavandería); solo se suman juntos.
AGRUPACION_DEPARTAMENTOS = {
    "Almacén central": "Pisos",
    "Lavandería": "Pisos",
}


def normalizar_departamento(texto: str) -> str:
    """«COCINA», «cocina », «Recepcion» -> nombre canónico; vacío -> «Sin asignar».

    Aplica también AGRUPACION_DEPARTAMENTOS (Lavandería -> Pisos…).
    """
    if not texto or not texto.strip():
        return SIN_ASIGNAR
    nombre = _DEPTOS.get(_sin_acentos(texto), texto.strip().capitalize())
    return AGRUPACION_DEPARTAMENTOS.get(nombre, nombre)


def _doce():
    return [CERO] * 12


def _pct(parte, total):
    return float(parte / total * 100) if total else None


def canal_principal(agencia: str) -> str | None:
    """«111 Expedia Group Way W» y «Expedia Lodging…» -> «Expedia»; agencias pequeñas -> None."""
    texto = _sin_acentos(agencia or "")
    for etiqueta, patron in CANALES_PRINCIPALES:
        if patron in texto:
            return etiqueta
    return None


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
    canales = {c: {"ingresos": _doce(), "noches": _doce()} for c, _ in CANALES_PRINCIPALES}

    for r in reservas.iterator():
        noches = _noches(r)
        n = len(noches)
        estancias = r.total_estancias * factor / n
        extras = (r.fact_extras + r.pend_extras) * factor / n
        tasas = (r.fact_impuesto + r.pend_tasas) / n          # sin IVA
        canal = canal_principal(r.agencia)
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

            if canal and (realizado or incluir_previsto):     # peso por canal: siempre el año entero
                canales[canal]["ingresos"][m] += estancias + extras + tasas
                canales[canal]["noches"][m] += r.habitaciones

            if (realizado or incluir_previsto) and (mes is None or d.month == mes):
                a = agencias[r.agencia or "Sin agencia"]
                a["noches_hab"] += r.habitaciones
                a["estancias"] += estancias
                a["ingresos"] += estancias + extras + tasas
                cuenta_en_periodo = True

        if cuenta_en_periodo:
            agencias[r.agencia or "Sin agencia"]["reservas"] += 1

    return mensual, agencias, canales


def _pesos_canales(canales: dict) -> dict:
    """Peso de cada canal principal por mes, sobre la suma de los canales principales."""
    resultado = {"canales": [c for c, _ in CANALES_PRINCIPALES]}
    for medida in ("ingresos", "noches"):
        totales_mes = [sum((canales[c][medida][i] for c in canales), CERO) for i in range(12)]
        total_anio = sum(totales_mes, CERO)
        resultado[medida] = {
            c: {
                "valores": canales[c][medida],
                "pesos": [_pct(canales[c][medida][i], totales_mes[i]) for i in range(12)],
                "total": sum(canales[c][medida], CERO),
                "peso_anual": _pct(sum(canales[c][medida], CERO), total_anio),
            } for c in canales
        }
        resultado[f"total_{medida}"] = totales_mes
    return resultado


def _meses_de_factura(fecha: date) -> list[int]:
    """Factura a fin de trimestre (30/06, 30/09…) -> los tres meses del trimestre."""
    if fecha.month % 3 == 0 and fecha.day >= 28:
        return [fecha.month - 2, fecha.month - 1, fecha.month]
    return [fecha.month]


def _modelo_cajas():
    """CajaDia de la app cajas, o None si la app no está instalada."""
    try:
        from cajas.models import CajaDia
    except ImportError:
        return None
    return CajaDia


def _ingresos_cajas(anio: int, con_iva: bool):
    """{'Bar', 'Comedor', 'Menús', 'Parking': [12]} desde las cajas diarias, o None si no hay cajas del año."""
    CajaDia = _modelo_cajas()
    if CajaDia is None:
        return None
    dias = CajaDia.objects.filter(caja_mes__anio=anio)
    if not dias.exists():
        return None
    f10 = Decimal("1") if con_iva else 1 / (1 + IVA_BAR_COMEDOR)
    f21 = Decimal("1") if con_iva else 1 / (1 + IVA_PARKING)
    series = {"Bar": _doce(), "Comedor": _doce(), "Menús": _doce(), "Parking": _doce()}
    campos = ("bar", "chill_out", "comedor", "menus", "parking_efectivo", "parking_visa")
    for fila in dias.values("caja_mes__mes").annotate(**{c: Sum(c) for c in campos}):
        m = fila["caja_mes__mes"] - 1
        v = {c: fila[c] or CERO for c in campos}
        series["Bar"][m] += (v["bar"] + v["chill_out"]) * f10
        series["Comedor"][m] += v["comedor"] * f10
        series["Menús"][m] += v["menus"] * f10
        series["Parking"][m] += (v["parking_efectivo"] + v["parking_visa"]) * f21
    return series


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


def _facturas_acreedor(anio: int, con_iva: bool, hoy: date, incluir_previsto: bool):
    """Facturas del año con su importe (sin IVA salvo que se pida) y el IVA casado."""
    soportado, cuenta_iva = defaultdict(lambda: CERO), {}
    for a in ApunteIva.objects.filter(libro=ApunteIva.Libro.SOPORTADO, fecha__year=anio):
        soportado[_clave_doc(a.asiento, a.documento)] += a.cuota
        if a.cuenta.startswith("410"):
            cuenta_iva[_clave_doc(a.asiento, a.documento)] = a.cuenta
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
            "inversion": f.es_inversion, "iva": iva, "documento": (f.fichero, f.documento, f.asiento),
            "cuenta_410": cuenta_iva.get(clave), "importe": f.importe if con_iva else f.importe - iva,
        })

    # Gastos adicionales (alquiler…): repartidos por días entre sus meses. Los meses
    # posteriores al actual son «previsto», con el mismo criterio que las reservas.
    for g in GastoAdicional.objects.filter(desde__lte=date(anio, 12, 31), hasta__gte=date(anio, 1, 1)):
        importe = g.base + g.iva if con_iva else g.base
        for (a, m), fraccion in g.reparto_mensual().items():
            if a != anio or ((a, m) > (hoy.year, hoy.month) and not incluir_previsto):
                continue
            filas.append({
                "fecha": date(a, m, 1), "fichero": f"gasto-{g.pk}", "acreedor": g.acreedor or g.concepto,
                "cuenta": g.cuenta[:3], "inversion": g.cuenta.startswith("2"), "iva": g.iva * fraccion,
                "documento": ("gasto", g.pk), "cuenta_410": None, "importe": importe * fraccion,
            })
        if g.asiento_iva:
            casadas.update(k for k in soportado if k[0] == g.asiento_iva)
    return filas, soportado, casadas


_FORMAS_JURIDICAS = {"sl", "sa", "slu", "sau", "cb", "scp", "sc", "ltd", "limited", "bv", "gmbh", "ag", "sll",
                     "s", "l", "a", "u", "y", "de", "del", "la", "el"}


def _nombre_plano(nombre: str) -> frozenset:
    """Palabras significativas de un nombre de empresa (sin forma jurídica, acentos ni signos)."""
    texto = "".join(c if c.isalnum() else " " for c in _sin_acentos(nombre or ""))
    return frozenset(p for p in texto.split() if p not in _FORMAS_JURIDICAS and len(p) > 1)


class Terceros:
    """Busca la ficha (cuenta, categoría) de proveedores y acreedores."""

    def __init__(self):
        self.por_cuenta = {t.cuenta: t for t in Tercero.objects.all()}
        self.acreedores = [(t, _nombre_plano(t.nombre)) for t in self.por_cuenta.values() if t.tipo == "acreedor"]

    def proveedor(self, codigo: int):
        return self.por_cuenta.get(f"400{codigo:06d}")

    def acreedor(self, cuenta: str | None, nombre: str):
        if cuenta and cuenta in self.por_cuenta:
            return self.por_cuenta[cuenta]
        buscado = _nombre_plano(nombre)
        if not buscado:
            return None
        for t, palabras in self.acreedores:          # mismo nombre
            if palabras == buscado:
                return t
        for t, palabras in self.acreedores:          # uno contiene al otro
            if palabras and (palabras <= buscado or buscado <= palabras):
                return t
        return None

    @staticmethod
    def categoria(tercero) -> str:
        return tercero.categoria if tercero and tercero.categoria else SIN_CATEGORIA


def _compras_con_departamento(compras, expr_compra):
    """Cada línea del diario con su departamento.

    1) el listado por departamento de ese albarán; 2) si no está (albaranes
    posteriores al listado), el departamento habitual del proveedor según los
    listados; 3) la asignación manual del admin; 4) «Sin asignar».
    """
    exactos, habitual = {}, defaultdict(lambda: defaultdict(lambda: CERO))
    for d in DepartamentoAlbaran.objects.all():
        exactos[(d.tipo, d.numero, d.codigo_proveedor)] = d.departamento
        habitual[d.codigo_proveedor][d.departamento] += abs(d.total)
    habitual = {c: max(v, key=v.get) for c, v in habitual.items()}
    manual = _asignaciones(AsignacionDepartamento.Origen.PROVEEDOR)

    filas = []
    for c in compras.annotate(importe=expr_compra).values("tipo", "numero", "codigo_proveedor", "proveedor",
                                                          "fecha", "importe", "total"):
        clave = (c["tipo"], c["numero"], c["codigo_proveedor"])
        if clave in exactos:
            depto, origen = normalizar_departamento(exactos[clave]), "albaran"
        elif c["codigo_proveedor"] in habitual:
            depto, origen = normalizar_departamento(habitual[c["codigo_proveedor"]]), "habitual"
        elif manual.get(str(c["codigo_proveedor"]), SIN_ASIGNAR) != SIN_ASIGNAR:
            depto, origen = manual[str(c["codigo_proveedor"])], "manual"
        else:
            depto, origen = SIN_ASIGNAR, "sin"
        filas.append({**c, "departamento": depto, "origen_depto": origen})
    return filas


def _top(filas, n=10):
    """Las n primeras filas (ya ordenadas) + una fila «Resto» con lo demás."""
    if len(filas) <= n + 1:
        return filas
    resto = filas[n:]
    return filas[:n] + [{
        "nombre": f"Resto ({len(resto)})", "departamento": "", "categoria": "", "documentos": sum(f["documentos"] for f in resto),
        "importe": sum((f["importe"] for f in resto), CERO), "resto": True,
    }]


# ------------------------------------------------------------------ resumen

def calcular_resumen(anio: int, mes: int | None = None, incluir_previsto: bool = False,
                     con_iva: bool = False, hoy: date | None = None) -> dict:
    hoy = hoy or date.today()
    en_periodo = (lambda m: m == mes) if mes else (lambda m: True)
    filtro_mes = {"fecha__month": mes} if mes else {}

    # --- Ingresos
    ing, agencias, canales_mes = _ingresos_reservas(anio, hoy, incluir_previsto, mes, con_iva)
    cajas = _ingresos_cajas(anio, con_iva)
    if cajas is not None:
        fuente_restauracion = "cajas"
        bar_comedor = cajas
    else:
        fuente_restauracion = "iva"
        bar_comedor = {**_ingresos_bar_comedor(anio, con_iva), "Menús": _doce(), "Parking": _doce()}
    ingresos = [ing["estancias"][i] + ing["extras"][i] + ing["tasas"][i]
                + bar_comedor["Bar"][i] + bar_comedor["Comedor"][i]
                + bar_comedor["Menús"][i] + bar_comedor["Parking"][i]
                + (ing["previsto"][i] if incluir_previsto else CERO) for i in range(12)]

    # --- Proveedores
    compras = CompraProveedor.objects.filter(fecha__year=anio)
    expr_compra = F("total") if con_iva else F("bruto") - F("dto_pp") + F("portes_gastos")
    lineas_compra = _compras_con_departamento(compras, expr_compra)
    proveedores = _doce()
    for c in lineas_compra:
        proveedores[c["fecha"].month - 1] += c["importe"] or CERO

    # --- Acreedores (gasto e inversión)
    facturas, soportado, casadas = _facturas_acreedor(anio, con_iva, hoy, incluir_previsto)
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
        "menus": bar_comedor["Menús"], "parking": bar_comedor["Parking"],
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
    tot["bar_comedor"] = tot["bar"] + tot["comedor"] + tot["menus"]   # ingresos con departamento

    # --- Fichas de terceros (categoría) para proveedores y acreedores
    terceros = Terceros()
    ficha_acr = {}                     # fichero -> Tercero (por la cuenta 410 de su IVA o por nombre)
    cuentas_por_fichero = defaultdict(Counter)
    for f in facturas:
        if f["cuenta_410"]:
            cuentas_por_fichero[f["fichero"]][f["cuenta_410"]] += 1
    for f in facturas:
        if f["fichero"] not in ficha_acr:
            cuentas = cuentas_por_fichero.get(f["fichero"])
            cuenta = cuentas.most_common(1)[0][0] if cuentas else None
            ficha_acr[f["fichero"]] = terceros.acreedor(cuenta, f["acreedor"])

    # --- Por departamento: proveedores (por albarán) y personal. Los acreedores
    #     no tienen departamento: van en un bloque aparte.
    asig_pers = _asignaciones(AsignacionDepartamento.Origen.PERSONAL)
    deptos = defaultdict(lambda: {"ingresos": CERO, "proveedores": CERO, "acreedores": CERO, "nominas": CERO})

    for origen, depto in (("Bar", "Bar"), ("Comedor", "Comedor"), ("Menús", "Comedor")):
        ingreso = sum((bar_comedor[origen][i] for i in idx), CERO)
        if ingreso:
            deptos[depto]["ingresos"] += ingreso
    for c in lineas_compra:
        if en_periodo(c["fecha"].month):
            deptos[c["departamento"]]["proveedores"] += c["importe"] or CERO
    if nominas is not None:
        qs = nominas.filter(mes=mes) if mes else nominas
        for n in qs.values("departamento", "mes").annotate(c=Sum("cost_tot"), e=Sum("importe_extra")):
            extra = CERO if n["mes"] in meses_con_hoja else (n["e"] or CERO)
            deptos[normalizar_departamento(n["departamento"])]["nominas"] += (n["c"] or CERO) + extra
    for e in (extras_qs.filter(mes=mes) if mes else extras_qs).values("persona").annotate(s=Sum("importe")):
        deptos[asig_pers.get(e["persona"], SIN_ASIGNAR)]["nominas"] += e["s"]

    departamentos = []
    for nombre, d in deptos.items():
        total = d["proveedores"] + d["nominas"]
        departamentos.append({"nombre": nombre, **d, "total": total, "pct": _pct(total, tot["gastos"]),
                              "resultado": d["ingresos"] - total if d["ingresos"] else None})
    departamentos.sort(key=lambda d: (d["nombre"] == SIN_ASIGNAR, -d["total"]))
    if tot["acreedores"]:
        departamentos.append({"nombre": ACREEDORES, "ingresos": CERO, "proveedores": CERO,
                              "acreedores": tot["acreedores"], "nominas": CERO, "total": tot["acreedores"],
                              "pct": _pct(tot["acreedores"], tot["gastos"]), "resultado": None,
                              "es_acreedores": True})

    # --- Por categoría (Comida, Bebida, Luz, Agencias…): proveedores y acreedores
    cats = defaultdict(lambda: {"proveedores": CERO, "acreedores": CERO})
    for c in lineas_compra:
        if en_periodo(c["fecha"].month):
            cats[Terceros.categoria(terceros.proveedor(c["codigo_proveedor"]))]["proveedores"] += c["importe"] or CERO
    for f in facturas:
        if not f["inversion"] and en_periodo(f["fecha"].month):
            cats[Terceros.categoria(ficha_acr.get(f["fichero"]))]["acreedores"] += f["importe"]
    total_cats = tot["proveedores"] + tot["acreedores"]
    categorias = sorted(({"nombre": n, **v, "total": v["proveedores"] + v["acreedores"],
                          "pct": _pct(v["proveedores"] + v["acreedores"], total_cats)} for n, v in cats.items()),
                        key=lambda x: (x["nombre"] == SIN_CATEGORIA, -x["total"]))

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
        "nombre": nombre, **a, "principal": canal_principal(nombre),
        "adr": a["estancias"] / a["noches_hab"] if a["noches_hab"] else None,
        "pct": _pct(a["ingresos"], total_ing_agencias),
    } for nombre, a in agencias.items() if a["ingresos"] or a["noches_hab"]), key=lambda x: -x["ingresos"])

    # --- Principales proveedores y acreedores
    por_proveedor = defaultdict(lambda: {"documentos": 0, "importe": CERO, "deptos": Counter()})
    for c in lineas_compra:
        if en_periodo(c["fecha"].month):
            p = por_proveedor[(c["codigo_proveedor"], c["proveedor"])]
            p["documentos"] += 1
            p["importe"] += c["importe"] or CERO
            p["deptos"][c["departamento"]] += abs(c["importe"] or CERO)
    top_prov = sorted(({"nombre": nombre, "documentos": v["documentos"], "importe": v["importe"],
                        "departamento": v["deptos"].most_common(1)[0][0] if v["deptos"] else SIN_ASIGNAR,
                        "categoria": Terceros.categoria(terceros.proveedor(cod))}
                       for (cod, nombre), v in por_proveedor.items()), key=lambda x: -x["importe"])
    por_acreedor = defaultdict(lambda: {"docs": set(), "importe": CERO})
    for f in facturas:
        if not f["inversion"] and en_periodo(f["fecha"].month):
            a = por_acreedor[(f["fichero"], f["acreedor"])]
            a["docs"].add(f["documento"])
            a["importe"] += f["importe"]
    top_acr = sorted(({"nombre": acr, "categoria": Terceros.categoria(ficha_acr.get(fich)),
                       "documentos": len(v["docs"]), "importe": v["importe"]}
                      for (fich, acr), v in por_acreedor.items()), key=lambda x: -x["importe"])

    # Revisión interna: terceros con gasto pero sin ficha completa (no se muestra en el panel)
    incompletos = []
    for (cod, nombre), v in por_proveedor.items():
        t = terceros.proveedor(cod)
        if not (t and t.categoria):
            incompletos.append({"tipo": "Proveedor", "nombre": nombre, "cuenta": t.cuenta if t else f"400{cod:06d}",
                                "motivo": "sin categoría" if t else "no está en el listado de terceros",
                                "importe": v["importe"]})
    for (fich, acr), v in por_acreedor.items():
        t = ficha_acr.get(fich)
        if not (t and t.categoria):
            incompletos.append({"tipo": "Acreedor", "nombre": acr, "cuenta": t.cuenta if t else "—",
                                "motivo": "sin categoría" if t else "no está en el listado de terceros",
                                "importe": v["importe"]})
    incompletos.sort(key=lambda x: -abs(x["importe"]))

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
    avisos = _avisos(anio, ingresos, nominas, meses_con_hoja, tot, soportado, casadas, compras, bar_comedor,
                     fuente_restauracion)
    revision = _revision(anio, lineas_compra, incompletos, extras_personas)

    return {
        "anio": anio, "mes": mes, "incluir_previsto": incluir_previsto, "con_iva": con_iva, "hoy": hoy,
        "meses": MESES, "mensual": mensual, "totales": tot,
        "departamentos": departamentos, "cuentas_gasto": cuentas_gasto,
        "cuentas_inversion": cuentas_inversion, "canales": canales,
        "top_proveedores": top_prov, "top_acreedores": top_acr, "extras_personas": extras_personas,
        "pesos_canales": _pesos_canales(canales_mes), "categorias": categorias,
        "avisos": avisos, "revision": revision, "fuente_restauracion": fuente_restauracion,
    }


def _avisos(anio, ingresos, nominas, meses_con_hoja, tot, soportado, casadas, compras, bar_comedor,
            fuente_restauracion):
    avisos = []
    pendientes = AsignacionDepartamento.objects.filter(origen=AsignacionDepartamento.Origen.PERSONAL,
                                                       departamento="").count()
    if pendientes:
        avisos.append(f"{pendientes} personas de la hoja de extras sin departamento asignado.")

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
        # Los pequeños suelen ser correcciones del año anterior: solo se avisa de los grandes
        importantes = sorted(((n, c) for n, c in huerfanos.items() if abs(c) >= 1000), key=lambda x: -abs(x[1]))
        if importantes:
            lista = ", ".join(f"{n} ({c:,.0f} € de IVA)".replace(",", ".") for n, c in importantes[:5])
            avisos.append("Hay IVA soportado de acreedores cuya factura no está en los ficheros de acreedores: "
                          f"{lista}. Si es un gasto real (p. ej. el alquiler), créalo desde el admin de "
                          "«Apuntes de IVA» con la acción «Crear gasto adicional».")
        # Control: IVA de proveedores del diario frente al libro (cuentas que no son 410)
        libro = ApunteIva.objects.filter(libro=ApunteIva.Libro.SOPORTADO, fecha__year=anio) \
            .exclude(cuenta__startswith="410").aggregate(s=Sum("cuota"))["s"] or CERO
        diario = compras.aggregate(s=Sum("iva"))["s"] or CERO
        if diario and abs(libro - diario) > max(Decimal("50"), diario * Decimal("0.02")):
            avisos.append(f"El IVA del diario de compras ({diario:,.0f} €) no coincide con el libro de IVA "
                          f"soportado de proveedores ({libro:,.0f} €).".replace(",", "."))
    if fuente_restauracion == "cajas":
        coma = lambda x: f"{1 + x:.2f}".replace(".", ",")
        avisos.append("Bar (bar + chill out), comedor, menús y parking salen de la app de cajas. Sin IVA se divide "
                      f"entre {coma(IVA_BAR_COMEDOR)} (parking entre {coma(IVA_PARKING)}). La ecotasa y las "
                      "facturas de caja no se suman porque ya están en las reservas.")
        CajaDia = _modelo_cajas()
        meses_caja = set(CajaDia.objects.filter(caja_mes__anio=anio).values_list("caja_mes__mes", flat=True))
        sin_caja = sorted({i + 1 for i in range(12) if ingresos[i] > 0} - meses_caja)
        if sin_caja:
            avisos.append("Meses con ingresos de reservas pero sin cajas importadas: "
                          + ", ".join(MESES_LARGOS[m - 1] for m in sin_caja) + ".")
        # Control: base de cajas frente a la base deducida del IVA repercutido (N/FRA BAR/COMEDOR)
        estimado = _ingresos_bar_comedor(anio, False)
        con_iva_libro = sum(estimado["Bar"]) + sum(estimado["Comedor"])
        if con_iva_libro:
            f10 = 1 / (1 + IVA_BAR_COMEDOR)
            CajaDia_qs = CajaDia.objects.filter(caja_mes__anio=anio)
            agg = CajaDia_qs.aggregate(b=Sum("bar"), c=Sum("chill_out"), m=Sum("comedor"))
            de_cajas = ((agg["b"] or CERO) + (agg["c"] or CERO) + (agg["m"] or CERO)) * f10
            avisos.append(f"Control: bar + chill out + comedor de cajas = {de_cajas:,.0f} € sin IVA; base deducida "
                          f"de las facturas N/FRA BAR/COMEDOR del IVA repercutido = {con_iva_libro:,.0f} €."
                          .replace(",", "."))
    elif tot["bar"] or tot["comedor"]:
        avisos.append(f"Bar y comedor: no hay cajas importadas del año; base estimada a partir de la cuota de IVA al "
                      f"{IVA_BAR_COMEDOR * 100:.0f} %; las facturas trimestrales se reparten entre sus tres meses.")
    if tot["inversiones"]:
        avisos.append("Las facturas de acreedores con cuenta 2xx (inmovilizado) se muestran como "
                      "inversiones y no restan del beneficio.")
    return avisos


def _revision(anio, lineas_compra, incompletos, extras_personas):
    """Datos solo para la página interna de revisión."""
    origen = defaultdict(lambda: {"lineas": 0, "importe": CERO})
    for c in lineas_compra:
        origen[c["origen_depto"]]["lineas"] += 1
        origen[c["origen_depto"]]["importe"] += c["importe"] or CERO
    nombres = {"albaran": "Por su albarán (listado por departamento)",
               "habitual": "Departamento habitual del proveedor (albarán posterior al listado)",
               "manual": "Asignación manual del proveedor en el admin", "sin": "Sin departamento"}
    cobertura = [{"origen": nombres[k], **origen[k]} for k in nombres if k in origen]

    en_diario = {(c["tipo"], c["numero"], c["codigo_proveedor"]) for c in lineas_compra}
    sin_pareja = [d for d in DepartamentoAlbaran.objects.filter(fecha__year=anio)
                  if (d.tipo, d.numero, d.codigo_proveedor) not in en_diario]
    sin_depto_personal = [e for e in extras_personas if e["departamento"] == SIN_ASIGNAR]
    return {"incompletos": incompletos, "cobertura": cobertura, "sin_pareja": sin_pareja,
            "sin_depto_personal": sin_depto_personal}


def anios_disponibles() -> list[int]:
    anios = {d.year for d in Reserva.objects.dates("entrada", "year")}
    anios |= {d.year for d in CompraProveedor.objects.dates("fecha", "year")}
    anios |= {d.year for d in FacturaAcreedor.objects.dates("fecha", "year")}
    return sorted(anios, reverse=True) or [date.today().year]
