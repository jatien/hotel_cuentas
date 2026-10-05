"""Asientos modelo: plantillas que generan las líneas del Debe y del Haber a partir de
unos pocos datos (proveedor, base imponible, tipo de IVA...). La línea de contrapartida
(el total al proveedor, al cliente, al banco...) se calcula sola.

Para añadir un modelo nuevo: una función que reciba los datos y devuelva las líneas, y
una entrada en MODELOS con sus campos.
"""
from dataclasses import dataclass
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.db import transaction

from .models import  LONGITUD_SUBCUENTA, Asiento, CuentaContable, Ejercicio
from .services import Linea, crear_asiento, renumerar, saldos_subcuentas

CENTIMO = Decimal("0.01")

IVA = (("21", "21 %"), ("10", "10 %"), ("4", "4 %"), ("0", "Exento o 0 %"))
RETENCION = (("0", "Sin retención"), ("15", "15 % (profesionales)"),
             ("7", "7 % (nuevos profesionales)"), ("19", "19 % (alquileres)"))


@dataclass
class Campo:
    nombre: str
    etiqueta: str
    tipo: str                       # "cuenta", "importe", "opcion" o "texto"
    prefijos: tuple = ()            # cuenta: subcuentas cuyo código empieza por alguno de estos
    opciones: tuple = ()            # opcion: ((valor, etiqueta), ...)
    obligatorio: bool = True
    inicial: str = ""
    ayuda: str = ""


@dataclass
class ModeloAsiento:
    clave: str
    nombre: str
    descripcion: str
    tipo: str                       # Asiento.Tipo
    grupo: str                      # para agruparlos en pantalla
    campos: list
    construir: object               # función (datos, contexto) -> (concepto, [Linea])
    fecha: str = "hoy"              # fecha propuesta: "hoy", "inicio" o "fin" del ejercicio
    con_documento: bool = False     # pide nº de factura o documento


# ---------- utilidades ----------

def redondear(valor):
    return Decimal(valor).quantize(CENTIMO, rounding=ROUND_HALF_UP)


def porcentaje(base, tipo):
    return redondear(base * Decimal(tipo) / 100)


def _importe(texto):
    texto = str(texto or "").strip().replace(" ", "").replace("€", "")
    if "," in texto:
        texto = texto.replace(".", "").replace(",", ".")
    try:
        return redondear(Decimal(texto or "0"))
    except InvalidOperation:
        raise ValidationError(f"Importe no válido: {texto}")


def lineas_sin_ceros(*lineas):
    return [l for l in lineas if l.debe > 0 or l.haber > 0]


def lineas_que_saldan(saldos):
    """Líneas que dejan a cero cada cuenta: la deudora se abona, la acreedora se carga."""
    return [Linea(c.codigo, debe=-s) if s < 0 else Linea(c.codigo, haber=s) for c, s in saldos]


def _detalle(nombre, documento):
    return f"{nombre} {documento}".strip()


# ---------- operaciones ----------

def _factura_proveedor(d, ctx):
    cuota = porcentaje(d["base"], d["iva"])
    total = d["base"] + cuota
    return (f"Factura {_detalle(d['proveedor'].nombre, ctx['documento'])}", lineas_sin_ceros(
        Linea(d["compra"].codigo, debe=d["base"]),
        Linea(d["cuenta_iva"].codigo, debe=cuota),
        Linea(d["proveedor"].codigo, haber=total),            # contrapartida automática
    ))


def _factura_acreedor(d, ctx):
    cuota = porcentaje(d["base"], d["iva"])
    retencion = porcentaje(d["base"], d["retencion"])
    if retencion and not d["cuenta_retencion"]:
        raise ValidationError("Con retención hay que elegir la cuenta de retenciones (4751).")
    total = d["base"] + cuota - retencion
    return (f"Factura {_detalle(d['acreedor'].nombre, ctx['documento'])}", lineas_sin_ceros(
        Linea(d["gasto"].codigo, debe=d["base"]),
        Linea(d["cuenta_iva"].codigo, debe=cuota),
        Linea(d["cuenta_retencion"].codigo if retencion else "", haber=retencion),
        Linea(d["acreedor"].codigo, haber=total),             # contrapartida automática
    ))


def _pago(d, ctx):
    return (f"Pago a {d['tercero'].nombre}", [
        Linea(d["tercero"].codigo, debe=d["importe"]),
        Linea(d["tesoreria"].codigo, haber=d["importe"]),
    ])


def _factura_cliente(d, ctx):
    cuota = porcentaje(d["base"], d["iva"])
    return (f"Factura a {_detalle(d['cliente'].nombre, ctx['documento'])}", lineas_sin_ceros(
        Linea(d["cliente"].codigo, debe=d["base"] + cuota),   # contrapartida automática
        Linea(d["ingreso"].codigo, haber=d["base"]),
        Linea(d["cuenta_iva"].codigo, haber=cuota),
    ))


def _cobro(d, ctx):
    return (f"Cobro de {d['cliente'].nombre}", [
        Linea(d["tesoreria"].codigo, debe=d["importe"]),
        Linea(d["cliente"].codigo, haber=d["importe"]),
    ])


def _venta_contado(d, ctx):
    # El importe cobrado lleva el IVA incluido: se separa la base y la cuota
    base = redondear(d["total"] / (1 + Decimal(d["iva"]) / 100))
    cuota = d["total"] - base
    return (f"Ventas al contado {d['ingreso'].nombre}", lineas_sin_ceros(
        Linea(d["tesoreria"].codigo, debe=d["total"]),
        Linea(d["ingreso"].codigo, haber=base),
        Linea(d["cuenta_iva"].codigo, haber=cuota),
    ))


def _nomina(d, ctx):
    neto = d["bruto"] - d["irpf"] - d["ss_trabajador"]
    if neto <= 0:
        raise ValidationError("Las retenciones no pueden ser mayores que el sueldo bruto.")
    return ("Nóminas", lineas_sin_ceros(
        Linea(d["sueldos"].codigo, debe=d["bruto"]),
        Linea(d["ss_cargo"].codigo, debe=d["ss_empresa"]),
        Linea(d["cuenta_irpf"].codigo, haber=d["irpf"]),
        Linea(d["cuenta_ss"].codigo, haber=d["ss_trabajador"] + d["ss_empresa"]),
        Linea(d["pendiente"].codigo, haber=neto),             # contrapartida automática
    ))


def _pago_nomina(d, ctx):
    return ("Pago de nóminas", [
        Linea(d["pendiente"].codigo, debe=d["importe"]),
        Linea(d["tesoreria"].codigo, haber=d["importe"]),
    ])


def _pago_administracion(d, ctx):
    return (f"Pago {d['deuda'].nombre}", [
        Linea(d["deuda"].codigo, debe=d["importe"]),
        Linea(d["tesoreria"].codigo, haber=d["importe"]),
    ])


def _gasto_bancario(d, ctx):
    return (f"{d['gasto'].nombre} {d['tesoreria'].nombre}", [
        Linea(d["gasto"].codigo, debe=d["importe"]),
        Linea(d["tesoreria"].codigo, haber=d["importe"]),
    ])


# ---------- ajustes ----------

def _amortizacion(d, ctx):
    return (f"Amortización {d['acumulada'].nombre}", [
        Linea(d["dotacion"].codigo, debe=d["importe"]),
        Linea(d["acumulada"].codigo, haber=d["importe"]),
    ])


def _liquidacion_iva(d, ctx):
    ejercicio = _ejercicio(ctx)
    repercutido = saldos_subcuentas(ejercicio, ctx["fecha"], ["477"])
    soportado = saldos_subcuentas(ejercicio, ctx["fecha"], ["472"])
    if not repercutido and not soportado:
        raise ValidationError("No hay IVA pendiente de liquidar a esa fecha.")
    lineas = lineas_que_saldan(repercutido + soportado)
    diferencia = -sum(s for _, s in repercutido) - sum(s for _, s in soportado)
    if diferencia > 0:
        lineas.append(Linea(d["a_pagar"].codigo, haber=diferencia))
    elif diferencia < 0:
        lineas.append(Linea(d["a_compensar"].codigo, debe=-diferencia))
    return ("Liquidación de IVA", lineas)


# ---------- cierre y apertura ----------

def _ejercicio(ctx):
    ejercicio = Ejercicio.buscar(ctx["fecha"])
    if not ejercicio:
        raise ValidationError("No hay asientos en el ejercicio de esa fecha.")
    return ejercicio


def _regularizacion(d, ctx):
    saldos = saldos_subcuentas(_ejercicio(ctx), ctx["fecha"], ["6", "7"])
    if not saldos:
        raise ValidationError("Las cuentas de gastos e ingresos ya están a cero.")
    lineas = lineas_que_saldan(saldos)
    resultado = sum(s for _, s in saldos)  # > 0: más gastos que ingresos (pérdida)
    if resultado > 0:
        lineas.append(Linea(d["resultado"].codigo, debe=resultado))
    elif resultado < 0:
        lineas.append(Linea(d["resultado"].codigo, haber=-resultado))
    return ("Regularización: gastos e ingresos a Resultado del ejercicio", lineas)


def _cierre(d, ctx):
    ejercicio = _ejercicio(ctx)
    if saldos_subcuentas(ejercicio, ctx["fecha"], ["6", "7"]):
        raise ValidationError("Antes del cierre hay que hacer la regularización de gastos e ingresos.")
    if saldos_subcuentas(ejercicio, ctx["fecha"], ["8", "9"]):
        raise ValidationError("Hay saldos en los grupos 8 y 9: este cierre automático no los contempla.")
    saldos = saldos_subcuentas(ejercicio, ctx["fecha"], ["1", "2", "3", "4", "5"])
    if not saldos:
        raise ValidationError("No hay saldos que cerrar.")
    return (f"Cierre del ejercicio {ejercicio}", lineas_que_saldan(saldos))


def _apertura(d, ctx):
    anterior = Ejercicio.objects.filter(fecha_fin=ctx["fecha"] - timedelta(days=1)).first()
    cierre = anterior and anterior.asientos.filter(tipo=Asiento.Tipo.CIERRE).first()
    if not cierre:
        raise ValidationError(
            "No hay asiento de cierre del ejercicio anterior. Si es el primer año en la aplicación, "
            "haz la apertura con el «Asiento libre» de tipo apertura y los saldos iniciales.")
    # la apertura es el cierre del año anterior con el Debe y el Haber intercambiados
    return (f"Apertura del ejercicio {ctx['fecha'].year}", [
        Linea(a.cuenta.codigo, debe=a.haber, haber=a.debe)
        for a in cierre.apuntes.select_related("cuenta")
    ])


def _libre(d, ctx):
    return ("Nuevo asiento", [])


# ---------- catálogo ----------

T = Asiento.Tipo

MODELOS = [
    ModeloAsiento(
        "factura_proveedor", "Factura de proveedor",
        "Compra de mercancías o materias primas. Debe: compras e IVA soportado. Haber: el proveedor por el total.",
        T.OPERACION, "Compras y gastos", con_documento=True, construir=_factura_proveedor, campos=[
            Campo("proveedor", "Proveedor", "cuenta", ("400", "403", "404", "405")),
            Campo("compra", "Cuenta de compras", "cuenta", ("600", "601", "602", "607")),
            Campo("base", "Base imponible", "importe"),
            Campo("iva", "IVA", "opcion", opciones=IVA, inicial="10"),
            Campo("cuenta_iva", "Cuenta de IVA soportado", "cuenta", ("472",)),
            
        ]),
    ModeloAsiento(
        "factura_acreedor", "Factura de servicios",
        "Luz, agua, mantenimiento, asesoría... Debe: el gasto e IVA soportado. Haber: retención, si la hay, "
        "y el acreedor por lo que se le paga.",
        T.OPERACION, "Compras y gastos", con_documento=True, construir=_factura_acreedor, campos=[
            Campo("acreedor", "Acreedor", "cuenta", ("410",)),
            Campo("gasto", "Cuenta de gasto", "cuenta", ("62", "63")),
            Campo("base", "Base imponible", "importe"),
            Campo("iva", "IVA", "opcion", opciones=IVA, inicial="21"),
            Campo("cuenta_iva", "Cuenta de IVA soportado", "cuenta", ("472",)),
            Campo("retencion", "Retención IRPF", "opcion", opciones=RETENCION, inicial="0"),
            Campo("cuenta_retencion", "Cuenta de retenciones", "cuenta", ("4751",), obligatorio=False,
                  ayuda="Solo si hay retención."),
            
        ]),
    ModeloAsiento(
        "pago", "Pago a proveedor o acreedor",
        "Debe: el proveedor o acreedor (se le debe menos). Haber: el banco o la caja.",
        T.OPERACION, "Cobros y pagos", construir=_pago, campos=[
            Campo("tercero", "Proveedor o acreedor", "cuenta", ("40", "41", "52", "17")),
            Campo("tesoreria", "Banco o caja", "cuenta", ("57",)),
            Campo("importe", "Importe pagado", "importe"),
        ]),
    ModeloAsiento(
        "factura_cliente", "Factura a cliente",
        "Agencias, empresas, eventos. Debe: el cliente por el total. Haber: el ingreso e IVA repercutido.",
        T.OPERACION, "Ventas e ingresos", con_documento=True, construir=_factura_cliente, campos=[
            Campo("cliente", "Cliente", "cuenta", ("43", "44")),
            Campo("ingreso", "Cuenta de ingreso", "cuenta", ("70", "75")),
            Campo("base", "Base imponible", "importe"),
            Campo("iva", "IVA", "opcion", opciones=IVA, inicial="10"),
            Campo("cuenta_iva", "Cuenta de IVA repercutido", "cuenta", ("477",)),
            
        ]),
    ModeloAsiento(
        "cobro", "Cobro de cliente",
        "Debe: el banco o la caja. Haber: el cliente (nos debe menos).",
        T.OPERACION, "Cobros y pagos", construir=_cobro, campos=[
            Campo("tesoreria", "Banco o caja", "cuenta", ("57",)),
            Campo("cliente", "Cliente", "cuenta", ("43", "44")),
            Campo("importe", "Importe cobrado", "importe"),
        ]),
    ModeloAsiento(
        "venta_contado", "Venta al contado (caja o TPV)",
        "Lo cobrado en recepción, bar o restaurante, IVA incluido. Se separa solo la base y el IVA.",
        T.OPERACION, "Ventas e ingresos", construir=_venta_contado, campos=[
            Campo("tesoreria", "Caja o banco del TPV", "cuenta", ("57",)),
            Campo("ingreso", "Cuenta de ingreso", "cuenta", ("70", "75")),
            Campo("total", "Total cobrado (IVA incluido)", "importe"),
            Campo("iva", "IVA", "opcion", opciones=IVA, inicial="10"),
            Campo("cuenta_iva", "Cuenta de IVA repercutido", "cuenta", ("477",)),
            
        ]),
    ModeloAsiento(
        "nomina", "Nóminas del mes",
        "Debe: sueldos y Seguridad Social a cargo de la empresa. Haber: IRPF retenido, Seguridad Social "
        "a pagar y el neto pendiente de pagar a los trabajadores.",
        T.OPERACION, "Personal", construir=_nomina, campos=[
            Campo("bruto", "Sueldo bruto", "importe"),
            Campo("ss_empresa", "Seguridad Social a cargo de la empresa", "importe"),
            Campo("irpf", "Retención de IRPF", "importe", obligatorio=False),
            Campo("ss_trabajador", "Seguridad Social del trabajador", "importe", obligatorio=False),
            Campo("sueldos", "Cuenta de sueldos", "cuenta", ("640",)),
            Campo("ss_cargo", "Cuenta de Seguridad Social a cargo", "cuenta", ("642",)),
            Campo("cuenta_irpf", "Cuenta de retenciones", "cuenta", ("4751",)),
            Campo("cuenta_ss", "Cuenta de Seguridad Social acreedora", "cuenta", ("476",)),
            Campo("pendiente", "Cuenta de remuneraciones pendientes", "cuenta", ("465",)),
            
        ]),
    ModeloAsiento(
        "pago_nomina", "Pago de nóminas",
        "Debe: remuneraciones pendientes. Haber: el banco.",
        T.OPERACION, "Personal", construir=_pago_nomina, campos=[
            Campo("pendiente", "Remuneraciones pendientes", "cuenta", ("465",)),
            Campo("tesoreria", "Banco", "cuenta", ("57",)),
            Campo("importe", "Importe pagado", "importe"),
        ]),
    ModeloAsiento(
        "pago_administracion", "Pago a Hacienda o Seguridad Social",
        "IVA, retenciones, impuesto de sociedades o seguros sociales. Debe: la deuda. Haber: el banco.",
        T.OPERACION, "Cobros y pagos", construir=_pago_administracion, campos=[
            Campo("deuda", "Qué se paga", "cuenta", ("475", "476")),
            Campo("tesoreria", "Banco", "cuenta", ("57",)),
            Campo("importe", "Importe pagado", "importe"),
        ]),
    ModeloAsiento(
        "gasto_bancario", "Comisión o gasto bancario",
        "Debe: servicios bancarios o intereses. Haber: el banco.",
        T.OPERACION, "Cobros y pagos", construir=_gasto_bancario, campos=[
            Campo("gasto", "Tipo de gasto", "cuenta", ("626", "662", "669")),
            Campo("tesoreria", "Banco", "cuenta", ("57",)),
            Campo("importe", "Importe", "importe"),
        ]),
    ModeloAsiento(
        "amortizacion", "Amortización",
        "Ajuste de fin de periodo por el desgaste del inmovilizado. Debe: dotación a la amortización. "
        "Haber: amortización acumulada.",
        T.AJUSTE, "Ajustes", fecha="fin", construir=_amortizacion, campos=[
            Campo("dotacion", "Dotación", "cuenta", ("680", "681", "682")),
            Campo("acumulada", "Amortización acumulada", "cuenta", ("280", "281", "282")),
            Campo("importe", "Importe del periodo", "importe"),
        ]),
    ModeloAsiento(
        "liquidacion_iva", "Liquidación de IVA",
        "Ajuste trimestral. Cancela el IVA soportado y el repercutido hasta la fecha y deja la diferencia "
        "a pagar (4750) o a compensar (4700). Los importes salen solos de los saldos.",
        T.AJUSTE, "Ajustes", construir=_liquidacion_iva, campos=[
            Campo("a_pagar", "Cuenta de IVA a pagar", "cuenta", ("4750",)),
            Campo("a_compensar", "Cuenta de IVA a compensar", "cuenta", ("4700",)),
        ]),
    ModeloAsiento(
        "regularizacion", "Regularización (cierre de gastos e ingresos)",
        "Último día del ejercicio. Deja a cero todas las cuentas de gastos (grupo 6) e ingresos (grupo 7) "
        "y lleva la diferencia al resultado del ejercicio (129). Los importes salen solos.",
        T.REGULARIZACION, "Cierre y apertura", fecha="fin", construir=_regularizacion, campos=[
            Campo("resultado", "Resultado del ejercicio", "cuenta", ("129",)),
        ]),
    ModeloAsiento(
        "cierre", "Cierre del ejercicio",
        "Último día del ejercicio, después de la regularización. Deja a cero todas las cuentas de balance "
        "(grupos 1 a 5). Los importes salen solos.",
        T.CIERRE, "Cierre y apertura", fecha="fin", construir=_cierre, campos=[]),
    ModeloAsiento(
        "apertura", "Apertura del ejercicio",
        "Primer día del ejercicio. Repite el cierre del año anterior con el Debe y el Haber intercambiados.",
        T.APERTURA, "Cierre y apertura", fecha="inicio", construir=_apertura, campos=[]),
    ModeloAsiento(
        "libre", "Asiento libre",
        "Un asiento vacío para escribir las líneas a mano. Elige el tipo: operación, ajuste o la apertura "
        "con los saldos iniciales del primer año.",
        T.OPERACION, "Otros", construir=_libre, campos=[
            Campo("tipo", "Tipo de asiento", "opcion", inicial=T.OPERACION,
                  opciones=((T.OPERACION, "Operación"), (T.AJUSTE, "Ajuste"), (T.APERTURA, "Apertura"))),
        ]),
]

MODELOS_POR_CLAVE = {m.clave: m for m in MODELOS}


# ---------- validación y creación ----------

def _validar_campos(modelo, entrada):
    datos, errores = {}, []
    for c in modelo.campos:
        valor = str(entrada.get(c.nombre, "") or "").strip()
        if not valor:
            if c.obligatorio:
                errores.append(f"Falta «{c.etiqueta}».")
            datos[c.nombre] = Decimal("0") if c.tipo == "importe" else (None if c.tipo == "cuenta" else "")
            continue
        if c.tipo == "cuenta":
            cuenta = CuentaContable.objects.filter(codigo=valor, imputable=True, activa=True).first()
            if not cuenta or len(valor) != LONGITUD_SUBCUENTA or not valor.startswith(c.prefijos):
                errores.append(f"«{c.etiqueta}»: elige una subcuenta de {', '.join(c.prefijos)}.")
            datos[c.nombre] = cuenta
        elif c.tipo == "importe":
            try:
                importe = _importe(valor)
            except ValidationError as e:
                errores.append(f"«{c.etiqueta}»: {e.messages[0]}")
                continue
            if importe < 0 or (c.obligatorio and importe == 0):
                errores.append(f"«{c.etiqueta}» debe ser mayor que cero.")
            datos[c.nombre] = importe
        elif c.tipo == "opcion":
            if valor not in {v for v, _ in c.opciones}:
                errores.append(f"«{c.etiqueta}»: opción no válida.")
            datos[c.nombre] = valor
        else:
            datos[c.nombre] = valor[:100]
    if errores:
        raise ValidationError(errores)
    return datos


def preparar(clave, fecha, entrada, concepto="", documento=""):
    """Valida los datos y calcula el asiento sin guardar nada.
    Devuelve (modelo, tipo, concepto, lineas)."""
    modelo = MODELOS_POR_CLAVE.get(clave)
    if not modelo:
        raise ValidationError("Asiento modelo desconocido.")
    datos = _validar_campos(modelo, entrada)
    contexto = {"fecha": fecha, "documento": (documento or "").strip()}
    concepto_auto, lineas = modelo.construir(datos, contexto)
    tipo = (datos.get("tipo") or modelo.tipo) if clave == "libre" else modelo.tipo
    return modelo, tipo, ((concepto or "").strip() or concepto_auto)[:255], lineas


def previsualizar(clave, fecha, entrada, concepto="", documento=""):
    modelo, tipo, concepto, lineas = preparar(clave, fecha, entrada, concepto, documento)
    nombres = dict(CuentaContable.objects.filter(codigo__in=[l.cuenta for l in lineas])
                   .values_list("codigo", "nombre"))
    debe = sum((l.debe for l in lineas), Decimal("0"))
    haber = sum((l.haber for l in lineas), Decimal("0"))
    return {
        "tipo": tipo,
        "concepto": concepto,
        "clase": "Simple" if len(lineas) == 2 else "Compuesto" if len(lineas) > 2 else "",
        "debe": f"{debe:.2f}",
        "haber": f"{haber:.2f}",
        "cuadra": debe == haber and debe > 0,
        "lineas": [{"cuenta": l.cuenta, "nombre": nombres.get(l.cuenta, ""),
                    "debe": f"{l.debe:.2f}", "haber": f"{l.haber:.2f}"} for l in lineas],
    }


@transaction.atomic
def crear_desde_modelo(clave, fecha, entrada, concepto="", documento=""):
    modelo, tipo, concepto, lineas = preparar(clave, fecha, entrada, concepto, documento)
    if clave == "libre":
        # asiento vacío en borrador para completar a mano en el libro diario
        asiento = Asiento(fecha=fecha, concepto=concepto, tipo=tipo, modelo=clave)
        asiento.full_clean(exclude=["ejercicio", "numero"], validate_constraints=False)
        asiento.save()
        if renumerar(asiento.ejercicio):
            asiento.refresh_from_db(fields=["numero"])
        return asiento
    return crear_asiento(fecha, concepto, lineas, tipo=tipo, modelo=clave,
                         referencia_origen=(documento or "").strip())