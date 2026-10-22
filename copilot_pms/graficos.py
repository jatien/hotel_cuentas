"""
Plantillas de gráficos del copilot.

Un gráfico es una ESPECIFICACIÓN validada, nunca código: el modelo (o la galería
sin IA) elige una plantilla y unos parámetros (año, series, tipo, meses,
comparación con otros años, título) y aquí se calculan los datos con las mismas
funciones que las pantallas de la aplicación. «Editar» un gráfico por prompt es
volver a pedirlo con otros parámetros; el navegador lo dibuja con Chart.js.

Añadir una plantilla = una función que devuelve (etiquetas, {serie: [Decimal]}) y
una entrada en PLANTILLAS.
"""

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Callable

from django.db.models import Q, Sum
from django.urls import reverse

CERO = Decimal("0")
MESES = ["Ene", "Feb", "Mar", "Abr", "May", "Jun", "Jul", "Ago", "Sep", "Oct", "Nov", "Dic"]

MENSUAL = "mensual"
CATEGORIAS = "categorias"
TIPOS = ("barras", "barras_apiladas", "barras_horizontales", "lineas", "area", "tarta")
TIPOS_ETIQUETAS = {"barras": "Barras", "barras_apiladas": "Barras apiladas", "barras_horizontales": "Barras horizontales",
                   "lineas": "Líneas", "area": "Área", "tarta": "Tarta"}
MAX_SERIES = 8

PERMISO_ANALISIS = "analisis.view_reserva"
PERMISO_CAJAS = "cajas.view_cajames"
PERMISO_NOMINAS = "nominas.view_nominamensual"
PERMISO_CONTABILIDAD = "cuadro_cuentas.view_apunte"


class GraficoNoDisponible(Exception):
    def __init__(self, codigo, mensaje, datos=None):
        super().__init__(mensaje)
        self.codigo, self.mensaje, self.datos = codigo, mensaje, datos or {}


@dataclass(frozen=True)
class Plantilla:
    clave: str
    titulo: str
    descripcion: str
    eje: str  # MENSUAL o CATEGORIAS
    datos: Callable  # (anio, opciones) -> (etiquetas, {serie: [Decimal]})
    tipo_defecto: str
    series_defecto: tuple = ()  # vacío = todas
    permiso: str = ""
    fuente: str = ""
    enlace: Callable | None = None  # (anio) -> url
    requiere_cuenta: bool = False
    usa_iva: bool = False
    usa_mes: bool = False  # las de categorías pueden limitarse a un mes


# --------------------------------------------------------------------------- análisis de beneficios
def _resumen(anio, op):
    from analisis.resumen import calcular_resumen

    return calcular_resumen(anio, mes=op.get("mes"), con_iva=op.get("con_iva", False))


def _analisis_mensual(claves):
    def datos(anio, op):
        m = _resumen(anio, op)["mensual"]
        return MESES, {nombre: list(m[clave]) for nombre, clave in claves}
    return datos


def _analisis_categorias(lista, series):
    def datos(anio, op):
        filas = _resumen(anio, op)[lista]
        return [f["nombre"] for f in filas], {nombre: [f[clave] for f in filas] for nombre, clave in series}
    return datos


# --------------------------------------------------------------------------- cajas, nóminas, contabilidad
def _cajas(anio, op):
    from cajas.models import OTROS_COBROS, PUNTOS_VENTA, CajaDia

    campos = PUNTOS_VENTA + OTROS_COBROS
    filas = {f["caja_mes__mes"]: f for f in CajaDia.objects.filter(caja_mes__anio=anio)
             .values("caja_mes__mes").annotate(**{c: Sum(c) for c, _ in campos})}
    return MESES, {etiqueta: [(filas.get(m, {}).get(c) or CERO) for m in range(1, 13)] for c, etiqueta in campos}


def _nominas(anio, op):
    from nominas.models import NominaMensual

    series = {}
    for f in (NominaMensual.objects.filter(anio=anio).values("departamento", "mes")
              .annotate(c=Sum("cost_tot"), e=Sum("importe_extra"))):
        nombre = f["departamento"] or "(sin departamento)"
        series.setdefault(nombre, [CERO] * 12)[f["mes"] - 1] += (f["c"] or CERO) + (f["e"] or CERO)
    return MESES, dict(sorted(series.items(), key=lambda kv: -sum(kv[1])))


def _saldo_cuenta(anio, op):
    """Saldo (Debe - Haber) a fin de cada mes, asientos contabilizados, sin el asiento de cierre."""
    from cuadro_cuentas.models import Apunte, Asiento, CuentaContable, Ejercicio

    cuenta = op["cuenta"]
    ejercicio = Ejercicio.objects.filter(anio=anio).first()
    if ejercicio is None:
        raise GraficoNoDisponible("EJERCICIO_NO_EXISTE", f"No existe el ejercicio {anio} en contabilidad.")
    if not CuentaContable.objects.filter(codigo__startswith=cuenta).exists():
        raise GraficoNoDisponible("CUENTA_NO_EXISTE", f"No existe ninguna cuenta con el código {cuenta}.")
    base = (Apunte.objects.filter(asiento__ejercicio=ejercicio, cuenta__codigo__startswith=cuenta,
                                  asiento__estado=Asiento.Estado.CONTABILIZADO)
            .exclude(asiento__tipo=Asiento.Tipo.CIERRE))
    por_mes = {f["asiento__fecha__month"]: (f["d"] or CERO) - (f["h"] or CERO)
               for f in base.values("asiento__fecha__month").annotate(d=Sum("debe"), h=Sum("haber"))}
    saldo, serie = CERO, []
    for m in range(1, 13):
        saldo += por_mes.get(m, CERO)
        serie.append(saldo)
    nombre = CuentaContable.objects.filter(codigo=cuenta).values_list("nombre", flat=True).first()
    return MESES, {f"{cuenta} {nombre}" if nombre else f"Cuenta {cuenta}": serie}


def _pyg_mensual(anio, op):
    """Ingresos (grupo 7, Haber - Debe) y gastos (grupo 6, Debe - Haber) contabilizados por mes."""
    from cuadro_cuentas.models import Apunte, Asiento

    base = (Apunte.objects.filter(asiento__ejercicio__anio=anio, asiento__estado=Asiento.Estado.CONTABILIZADO)
            .exclude(asiento__tipo__in=[Asiento.Tipo.REGULARIZACION, Asiento.Tipo.CIERRE]))
    ingresos, gastos = [CERO] * 12, [CERO] * 12
    for f in (base.filter(Q(cuenta__codigo__startswith="6") | Q(cuenta__codigo__startswith="7"))
              .values("asiento__fecha__month", "cuenta__codigo").annotate(d=Sum("debe"), h=Sum("haber"))):
        i = f["asiento__fecha__month"] - 1
        if f["cuenta__codigo"].startswith("7"):
            ingresos[i] += f["h"] - f["d"]
        else:
            gastos[i] += f["d"] - f["h"]
    return MESES, {"Ingresos (grupo 7)": ingresos, "Gastos (grupo 6)": gastos,
                   "Resultado": [ingresos[i] - gastos[i] for i in range(12)]}


def _url(nombre, consulta=""):
    return lambda anio: f"{reverse(nombre)}?{consulta.format(anio=anio)}" if consulta else reverse(nombre)


PLANTILLAS = {p.clave: p for p in [
    Plantilla("beneficio_mensual", "Ingresos, gastos y beneficio por mes",
              "Ingresos, gastos, beneficio y beneficio acumulado de cada mes (análisis de beneficios).",
              MENSUAL, _analisis_mensual([("Ingresos", "ingresos"), ("Gastos", "gastos"),
                                          ("Beneficio", "beneficio"), ("Beneficio acumulado", "acumulado")]),
              "barras", ("Ingresos", "Gastos", "Beneficio"), PERMISO_ANALISIS, "Análisis de beneficios (analisis)",
              _url("analisis:resumen", "anio={anio}"), usa_iva=True),
    Plantilla("gastos_mensuales", "Gastos por tipo y mes",
              "Proveedores, acreedores y personal de cada mes.",
              MENSUAL, _analisis_mensual([("Proveedores", "proveedores"), ("Acreedores", "acreedores"),
                                          ("Personal", "nominas")]),
              "barras_apiladas", (), PERMISO_ANALISIS, "Análisis de beneficios (analisis)",
              _url("analisis:resumen", "anio={anio}"), usa_iva=True),
    Plantilla("ingresos_mensuales", "Ingresos por origen y mes",
              "Estancias, extras, tasas turísticas, bar, comedor, menús, parking y previsto de cada mes.",
              MENSUAL, _analisis_mensual([("Estancias", "estancias"), ("Extras", "extras"), ("Tasas", "tasas"),
                                          ("Bar", "bar"), ("Comedor", "comedor"), ("Menús", "menus"),
                                          ("Parking", "parking")]),
              "barras_apiladas", (), PERMISO_ANALISIS, "Análisis de beneficios (analisis)",
              _url("analisis:resumen", "anio={anio}"), usa_iva=True),
    Plantilla("gastos_por_departamento", "Gastos por departamento",
              "Proveedores, acreedores y personal por departamento del año (o de un mes).",
              CATEGORIAS, _analisis_categorias("departamentos", [("Proveedores", "proveedores"),
                                                                  ("Acreedores", "acreedores"),
                                                                  ("Personal", "nominas")]),
              "barras_horizontales", (), PERMISO_ANALISIS, "Análisis de beneficios (analisis)",
              _url("analisis:resumen", "anio={anio}"), usa_iva=True, usa_mes=True),
    Plantilla("gastos_por_categoria", "Gastos por categoría",
              "Proveedores y acreedores por categoría (comida, bebida, luz...) del año (o de un mes).",
              CATEGORIAS, _analisis_categorias("categorias", [("Proveedores", "proveedores"),
                                                               ("Acreedores", "acreedores")]),
              "barras_horizontales", (), PERMISO_ANALISIS, "Análisis de beneficios (analisis)",
              _url("analisis:resumen", "anio={anio}"), usa_iva=True, usa_mes=True),
    Plantilla("ventas_cajas_mensuales", "Ventas de caja por mes",
              "Ventas de bar, comedor y chill out (y otros cobros: ecotasa, menús, facturas, parking) por mes.",
              MENSUAL, _cajas, "barras_apiladas", ("Bar", "Comedor", "Chill out"), PERMISO_CAJAS,
              "Cajas diarias (cajas)", _url("cajas:resumen", "anio={anio}")),
    Plantilla("coste_personal_mensual", "Coste de personal por departamento y mes",
              "Coste empresa + extras de nóminas por departamento y mes.",
              MENSUAL, _nominas, "barras_apiladas", (), PERMISO_NOMINAS, "Nóminas (nominas)",
              _url("nominas")),
    Plantilla("saldo_cuenta_mensual", "Evolución del saldo de una cuenta",
              "Saldo (Debe - Haber) a fin de cada mes de una cuenta o grupo, asientos contabilizados. "
              "Necesita «cuenta».",
              MENSUAL, _saldo_cuenta, "lineas", (), PERMISO_CONTABILIDAD, "Contabilidad (cuadro_cuentas)",
              _url("cuadro_cuentas:extractos", "ejercicio={anio}"), requiere_cuenta=True),
    Plantilla("resultado_contable_mensual", "Ingresos y gastos contables por mes",
              "Grupo 7 (ingresos), grupo 6 (gastos) y resultado por mes según la contabilidad contabilizada.",
              MENSUAL, _pyg_mensual, "barras", ("Ingresos (grupo 7)", "Gastos (grupo 6)"), PERMISO_CONTABILIDAD,
              "Contabilidad (cuadro_cuentas)", _url("cuadro_cuentas:cuentas_anuales", "ejercicio={anio}")),
]}


# --------------------------------------------------------------------------- construcción
@dataclass
class Grafico:
    tipo: str
    titulo: str
    etiquetas: list
    series: list  # [{"nombre", "valores": [Decimal]}]
    eje: str
    unidad: str = "EUR"
    avisos: list = field(default_factory=list)
    disponibles: list = field(default_factory=list)  # nombres de todas las series de la plantilla


def _ultimo_mes_con_datos(series: dict) -> int:
    ultimo = 0
    for valores in series.values():
        for i, v in enumerate(valores):
            if v:
                ultimo = max(ultimo, i + 1)
    return ultimo


def construir(clave: str, anio: int, *, series=None, tipo=None, comparar_con=(), desde_mes=1, hasta_mes=12,
              mes=None, cuenta=None, con_iva=False, titulo=None, hoy: date | None = None) -> Grafico:
    """Calcula el gráfico. Lanza GraficoNoDisponible (sin datos) o ValueError (parámetros incompatibles)."""
    p = PLANTILLAS[clave]
    tipo = tipo or p.tipo_defecto
    avisos = []
    if p.requiere_cuenta and not cuenta:
        raise ValueError("Esta plantilla necesita «cuenta» (código, p. ej. 572).")
    if p.eje == MENSUAL and tipo == "tarta":
        raise ValueError("Una tarta solo tiene sentido con categorías (departamentos, categorías), no por meses.")
    if p.eje == CATEGORIAS and comparar_con:
        raise ValueError("La comparación entre años solo está disponible en los gráficos mensuales.")
    if desde_mes > hasta_mes:
        raise ValueError("«desde_mes» es posterior a «hasta_mes».")

    opciones = {"cuenta": cuenta, "con_iva": con_iva, "mes": mes if p.usa_mes else None}
    anios = [anio, *[a for a in comparar_con if a != anio]]
    etiquetas, salida = None, []
    for a in anios:
        etiquetas, por_serie = p.datos(a, opciones)
        disponibles = list(por_serie)
        elegidas = list(series) if series else [s for s in (p.series_defecto or disponibles) if s in disponibles]
        desconocidas = [s for s in elegidas if s not in por_serie]
        if desconocidas:
            raise ValueError(f"Series no disponibles: {', '.join(desconocidas)}. Disponibles: {', '.join(disponibles)}.")
        for nombre in elegidas:
            salida.append({"nombre": f"{nombre} {a}" if len(anios) > 1 else nombre, "valores": por_serie[nombre],
                           "_serie": nombre, "_anio": a})

    if len(salida) > MAX_SERIES:
        raise ValueError(f"Demasiadas series ({len(salida)}); como máximo {MAX_SERIES}. Elige menos series o años.")
    if not any(v for s in salida for v in s["valores"]):
        raise GraficoNoDisponible("SIN_DATOS", f"No hay datos para «{p.titulo}» en {', '.join(map(str, anios))}.")

    if p.eje == MENSUAL:
        hoy = hoy or date.today()
        if anio == hoy.year and not comparar_con and hasta_mes == 12:
            # El año en curso se corta en el último mes con datos (como la pantalla de análisis)
            hasta_mes = max(_ultimo_mes_con_datos({s["nombre"]: s["valores"] for s in salida}), desde_mes)
        etiquetas = etiquetas[desde_mes - 1:hasta_mes]
        for s in salida:
            s["valores"] = s["valores"][desde_mes - 1:hasta_mes]
    if tipo == "tarta":
        if len(salida) > 1:
            total = [sum((s["valores"][i] for s in salida), CERO) for i in range(len(etiquetas))]
            avisos.append("En la tarta se suman las series: " + ", ".join(s["nombre"] for s in salida) + ".")
            salida = [{"nombre": "Total", "valores": total}]
        negativos = [e for e, v in zip(etiquetas, salida[0]["valores"]) if v < 0]
        if negativos:
            raise ValueError("Hay valores negativos (" + ", ".join(negativos) + "): una tarta no puede mostrarlos.")

    for s in salida:
        s.pop("_serie", None)
        s.pop("_anio", None)
    sufijo = f" {anio}" + (f" frente a {', '.join(map(str, comparar_con))}" if comparar_con else "")
    if mes and p.usa_mes:
        sufijo += f" ({MESES[mes - 1].lower()})"
    return Grafico(tipo=tipo, titulo=titulo or p.titulo + sufijo, etiquetas=list(etiquetas), series=salida,
                   eje=p.eje, avisos=avisos, disponibles=disponibles)
