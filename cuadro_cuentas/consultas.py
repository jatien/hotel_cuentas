"""Consultas sobre los apuntes: sumas y saldos por cuenta, extracto (libro mayor) de una
cuenta con saldo acumulado, y buscador de apuntes por importe, texto, cuenta, fechas...

Todas trabajan dentro de un ejercicio. Así los saldos son siempre correctos: el saldo
inicial de un año sale del asiento de apertura y no se mezcla con el cierre del anterior.
"""
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from django.db.models import Count, F, Q, Sum

from .models import LONGITUD_SUBCUENTA, NIVELES, Apunte, Asiento, CuentaContable

CERO = Decimal("0")


def parse_importe(texto):
    """'1.234,56', '1234,56', '1234.56' o '1 234 €' -> Decimal. Cadena vacía -> None."""
    texto = str(texto or "").strip().replace(" ", "").replace("€", "")
    if not texto:
        return None
    if "," in texto:
        texto = texto.replace(".", "").replace(",", ".")
    try:
        return Decimal(texto).quantize(Decimal("0.01"))
    except InvalidOperation:
        raise ValueError(f"Importe no válido: {texto}")


def _base(ejercicio, solo_contabilizados=False, incluir_cierre=False):
    # los asientos anulados nunca suman: se conservan solo para la trazabilidad
    qs = Apunte.objects.vigentes().filter(asiento__ejercicio=ejercicio)
    if solo_contabilizados:
        qs = qs.filter(asiento__estado=Asiento.Estado.CONTABILIZADO)
    if not incluir_cierre:
        # el asiento de cierre deja todo a cero: sin él se ve el saldo real a fin de año
        qs = qs.exclude(asiento__tipo=Asiento.Tipo.CIERRE)
    return qs


# ---------------------------------------------------------------- sumas y saldos

def sumas_y_saldos(ejercicio, prefijo="", solo_contabilizados=False, incluir_cierre=False):
    """Una fila por subcuenta con movimientos: Debe, Haber y saldo (Debe - Haber)."""
    qs = _base(ejercicio, solo_contabilizados, incluir_cierre)
    if prefijo:
        qs = qs.filter(cuenta__codigo__startswith=prefijo)
    filas = (qs.values("cuenta__codigo", "cuenta__nombre")
             .annotate(debe=Sum("debe"), haber=Sum("haber"), n=Count("id"))
             .order_by("cuenta__codigo"))
    filas = [{"codigo": f["cuenta__codigo"], "nombre": f["cuenta__nombre"], "debe": f["debe"],
              "haber": f["haber"], "saldo": f["debe"] - f["haber"], "apuntes": f["n"]} for f in filas]
    totales = {
        "debe": sum((f["debe"] for f in filas), CERO),
        "haber": sum((f["haber"] for f in filas), CERO),
        "deudores": sum((f["saldo"] for f in filas if f["saldo"] > 0), CERO),
        "acreedores": -sum((f["saldo"] for f in filas if f["saldo"] < 0), CERO),
    }
    return filas, totales


# ---------------------------------------------------------------- extracto

@dataclass
class Extracto:
    prefijo: str
    cuenta: CuentaContable | None       # la cuenta si existe con ese código exacto
    varias: bool                        # True si agrupa varias subcuentas (p. ej. "400")
    saldo_inicial: Decimal = CERO
    lineas: list = field(default_factory=list)
    total_debe: Decimal = CERO
    total_haber: Decimal = CERO

    @property
    def saldo_final(self):
        return self.saldo_inicial + self.total_debe - self.total_haber


def extracto(ejercicio, prefijo, desde=None, hasta=None, solo_contabilizados=False, incluir_cierre=False,
             punteo=None):
    """Movimientos de una cuenta (o de todas las que empiezan por `prefijo`) en el ejercicio,
    con el saldo acumulado línea a línea. Saldo positivo = deudor, negativo = acreedor."""
    base = _base(ejercicio, solo_contabilizados, incluir_cierre).filter(cuenta__codigo__startswith=prefijo)
    cuenta = CuentaContable.objects.filter(codigo=prefijo).first()
    resultado = Extracto(prefijo, cuenta, varias=not (cuenta and cuenta.es_subcuenta))

    if desde:
        previo = base.filter(asiento__fecha__lt=desde).aggregate(d=Sum("debe"), h=Sum("haber"))
        resultado.saldo_inicial = (previo["d"] or CERO) - (previo["h"] or CERO)
        base = base.filter(asiento__fecha__gte=desde)
    if hasta:
        base = base.filter(asiento__fecha__lte=hasta)

    saldo = resultado.saldo_inicial
    movimientos = list(base.select_related("asiento", "asiento__hoja", "cuenta", "contrapartida")
                       .order_by("asiento__fecha", "asiento__numero", "orden", "id"))
    contras = contrapartidas_de_apuntes(movimientos)
    for a in movimientos:
        # el saldo acumulado y los totales cuentan todos los movimientos, aunque se filtre el punteo
        saldo += a.debe - a.haber
        resultado.total_debe += a.debe
        resultado.total_haber += a.haber
        if punteo is None or (punteo == "sin" and not a.punteo) or a.punteo == punteo:
            resultado.lineas.append({"apunte": a, "saldo": saldo, "contrapartida": contras[a.id]})
    return resultado


# ---------------------------------------------------------------- contrapartidas

@dataclass
class Contrapartida:
    """Cuenta o cuentas del otro lado del asiento. Única solo si no hay ambigüedad."""
    cuentas: list                   # [(codigo, nombre)] del lado contrario, sin repetir

    @property
    def unica(self):
        return self.cuentas[0] if len(self.cuentas) == 1 else None

    @property
    def multiple(self):
        return len(self.cuentas) > 1

    @property
    def texto(self):
        if not self.cuentas:
            return ""
        if self.unica:
            return f"{self.unica[0]} {self.unica[1]}"
        return f"Varias ({len(self.cuentas)})"

    @property
    def detalle(self):
        return ", ".join(f"{c} {n}" for c, n in self.cuentas)


def contrapartidas(apuntes):
    """{apunte_id: Contrapartida} a partir de todas las líneas de un asiento."""
    lado = {True: [], False: []}
    for a in apuntes:
        lado[a.debe > 0].append(a)
    salida = {}
    for a in apuntes:
        vistas, cuentas = set(), []
        for o in lado[not (a.debe > 0)]:
            if o.cuenta.codigo not in vistas:
                vistas.add(o.cuenta.codigo)
                cuentas.append((o.cuenta.codigo, o.cuenta.nombre))
        salida[a.id] = Contrapartida(cuentas)
    return salida


def contrapartidas_de_apuntes(apuntes):
    """Contrapartidas de apuntes sueltos (mayor, buscador) cargando sus asientos completos."""
    ids = {a.asiento_id for a in apuntes}
    todas = {}
    por_asiento = {}
    for o in Apunte.objects.filter(asiento_id__in=ids).select_related("cuenta").order_by("orden", "id"):
        por_asiento.setdefault(o.asiento_id, []).append(o)
    for lineas in por_asiento.values():
        todas.update(contrapartidas(lineas))
    return {a.id: todas.get(a.id, Contrapartida([])) for a in apuntes}


# ---------------------------------------------------------------- sumas y saldos por periodo

@dataclass
class FilaSumas:
    codigo: str
    nombre: str
    anterior: Decimal = CERO          # saldo antes del periodo (Debe - Haber)
    periodo_debe: Decimal = CERO
    periodo_haber: Decimal = CERO
    apuntes: int = 0

    @property
    def anterior_debe(self):
        return self.anterior if self.anterior > 0 else CERO

    @property
    def anterior_haber(self):
        return -self.anterior if self.anterior < 0 else CERO

    @property
    def acumulado_debe(self):
        return self.anterior_debe + self.periodo_debe

    @property
    def acumulado_haber(self):
        return self.anterior_haber + self.periodo_haber

    @property
    def saldo(self):
        return self.acumulado_debe - self.acumulado_haber

    @property
    def saldo_deudor(self):
        return self.saldo if self.saldo > 0 else CERO

    @property
    def saldo_acreedor(self):
        return -self.saldo if self.saldo < 0 else CERO

    @property
    def sin_movimiento(self):
        return not self.anterior and not self.periodo_debe and not self.periodo_haber


COLUMNAS_SUMAS = ("anterior_debe", "anterior_haber", "periodo_debe", "periodo_haber",
                  "acumulado_debe", "acumulado_haber", "saldo_deudor", "saldo_acreedor")


def _clave(codigo, nivel):
    return codigo[:nivel] if len(codigo) >= nivel else codigo


def _en_rango(codigo, desde, hasta):
    if desde and codigo[:len(desde)] < desde:
        return False
    if hasta and codigo[:len(hasta)] > hasta:
        return False
    return True


def sumas_y_saldos_periodo(ejercicio, desde=None, hasta=None, nivel=LONGITUD_SUBCUENTA, cuenta_desde="",
                           cuenta_hasta="", sin_movimiento=False, incluir_apertura=True, incluir_cierre=False,
                           solo_contabilizados=True):
    """Balance de sumas y saldos del punto 7 de la especificación.

    - Anterior: saldo antes de «desde» (incluye el asiento de apertura).
    - Periodo: movimientos del Debe y del Haber entre «desde» y «hasta» (sin la apertura).
    - Acumulado: anterior + periodo. Saldo: acumulado Debe - acumulado Haber, en su columna.
    Las cuentas de un nivel superior suman las subcuentas que contienen, una sola vez.
    """
    desde = desde or ejercicio.fecha_inicio
    hasta = hasta or ejercicio.fecha_fin
    qs = _base(ejercicio, solo_contabilizados, incluir_cierre).filter(asiento__fecha__lte=hasta)
    if not incluir_apertura:
        qs = qs.exclude(asiento__tipo=Asiento.Tipo.APERTURA)
    es_anterior = Q(asiento__fecha__lt=desde) | Q(asiento__tipo=Asiento.Tipo.APERTURA)
    filas = (qs.values("cuenta__codigo")
             .annotate(ant_d=Sum("debe", filter=es_anterior), ant_h=Sum("haber", filter=es_anterior),
                       per_d=Sum("debe", filter=~es_anterior), per_h=Sum("haber", filter=~es_anterior),
                       n=Count("id"))
             .order_by())

    grupos = {}
    for f in filas:
        codigo = f["cuenta__codigo"]
        if not _en_rango(codigo, cuenta_desde, cuenta_hasta):
            continue
        clave = _clave(codigo, nivel)
        g = grupos.setdefault(clave, FilaSumas(clave, ""))
        g.anterior += (f["ant_d"] or CERO) - (f["ant_h"] or CERO)
        g.periodo_debe += f["per_d"] or CERO
        g.periodo_haber += f["per_h"] or CERO
        g.apuntes += f["n"]

    if sin_movimiento:
        plan = CuentaContable.objects.filter(activa=True)
        plan = plan.filter(codigo__regex=rf"^\d{{{nivel}}}$")
        for codigo in plan.values_list("codigo", flat=True):
            if codigo not in grupos and _en_rango(codigo, cuenta_desde, cuenta_hasta):
                grupos[codigo] = FilaSumas(codigo, "")

    nombres = dict(CuentaContable.objects.filter(codigo__in=grupos).values_list("codigo", "nombre"))
    lista = []
    for clave in sorted(grupos):
        g = grupos[clave]
        g.nombre = nombres.get(clave, "")
        if g.sin_movimiento and not sin_movimiento:
            continue
        lista.append(g)
    totales = {c: sum((getattr(f, c) for f in lista), CERO) for c in COLUMNAS_SUMAS}
    comprobacion = {
        "periodo": totales["periodo_debe"] == totales["periodo_haber"],
        "acumulado": totales["acumulado_debe"] == totales["acumulado_haber"],
        "saldos": totales["saldo_deudor"] == totales["saldo_acreedor"],
    }
    return lista, totales, comprobacion


NIVELES_SUMAS = [(n, f"{n} dígito{'s' if n > 1 else ''} · {NIVELES[n].lower()}") for n in sorted(NIVELES)]


# ---------------------------------------------------------------- comparativa de ejercicios

def comparativa(actual, anterior, nivel=3, cuenta_desde="", cuenta_hasta=""):
    """Saldo de cada cuenta al cierre de dos ejercicios (sin el asiento de cierre), con la
    variación. Solo asientos contabilizados."""
    def saldos(ejercicio):
        if ejercicio is None:
            return {}
        filas, _, _ = sumas_y_saldos_periodo(ejercicio, nivel=nivel, cuenta_desde=cuenta_desde,
                                             cuenta_hasta=cuenta_hasta)
        return {f.codigo: f for f in filas}

    a, b = saldos(actual), saldos(anterior)
    nombres = dict(CuentaContable.objects.filter(codigo__in=set(a) | set(b)).values_list("codigo", "nombre"))
    filas = []
    for codigo in sorted(set(a) | set(b)):
        sa = a[codigo].saldo if codigo in a else CERO
        sb = b[codigo].saldo if codigo in b else CERO
        if not sa and not sb:
            continue
        variacion = sa - sb
        porcentaje = (variacion / abs(sb) * 100).quantize(Decimal("0.1")) if sb else None
        filas.append({"codigo": codigo, "nombre": nombres.get(codigo, ""), "actual": sa, "anterior": sb,
                      "variacion": variacion, "porcentaje": porcentaje})
    return filas


# ---------------------------------------------------------------- diario

def diario(ejercicio, filtros):
    """Asientos del ejercicio en orden cronológico con los filtros del diario.

    Los filtros por cuenta, importe y punteo seleccionan los asientos que tienen algún apunte
    que cumple; el asiento se muestra siempre completo y cuadrado.
    """
    qs = Asiento.objects.filter(ejercicio=ejercicio)
    if filtros.get("desde"):
        qs = qs.filter(fecha__gte=filtros["desde"])
    if filtros.get("hasta"):
        qs = qs.filter(fecha__lte=filtros["hasta"])
    if filtros.get("numero"):
        qs = qs.filter(numero=filtros["numero"])
    if documento := (filtros.get("documento") or "").strip():
        qs = qs.filter(Q(numero_externo__icontains=documento) | Q(referencia_origen__icontains=documento)
                       | Q(adjuntos__numero__icontains=documento)).distinct()
    if texto := (filtros.get("concepto") or "").strip():
        qs = qs.filter(Q(concepto__icontains=texto) | Q(titulo__icontains=texto)
                       | Q(apuntes__concepto__icontains=texto)).distinct()
    if filtros.get("estado"):
        qs = qs.filter(estado=filtros["estado"])
    if filtros.get("tipo"):
        qs = qs.filter(tipo=filtros["tipo"])

    apuntes = Apunte.objects.all()
    filtra_apuntes = False
    if cuenta := (filtros.get("cuenta") or "").strip():
        apuntes = apuntes.filter(cuenta__codigo__startswith=cuenta) if cuenta.isdigit() \
            else apuntes.filter(cuenta__nombre__icontains=cuenta)
        filtra_apuntes = True
    if filtros.get("importe") is not None:
        apuntes = apuntes.filter(Q(debe=filtros["importe"]) | Q(haber=filtros["importe"]))
        filtra_apuntes = True
    punteo = filtros.get("punteo")
    if punteo:
        apuntes = apuntes.filter(punteo="") if punteo == "sin" else apuntes.filter(punteo=punteo)
        filtra_apuntes = True
    if filtra_apuntes:
        qs = qs.filter(id__in=apuntes.values("asiento_id"))

    return (qs.annotate(total_debe=Sum("apuntes__debe"), total_haber=Sum("apuntes__haber"),
                        n_apuntes=Count("apuntes"))
            .select_related("hoja")
            .order_by("fecha", "numero"))


def cargar_apuntes(asientos):
    """Apuntes de una página del diario con su contrapartida calculada."""
    ids = [a.id for a in asientos]
    por_asiento = {}
    for ap in (Apunte.objects.filter(asiento_id__in=ids).select_related("cuenta")
               .order_by("orden", "id")):
        por_asiento.setdefault(ap.asiento_id, []).append(ap)
    for a in asientos:
        lineas = por_asiento.get(a.id, [])
        contras = contrapartidas(lineas)
        a.lineas = [(ap, contras[ap.id]) for ap in lineas]
    return asientos


# ---------------------------------------------------------------- buscador

def buscar_apuntes(datos):
    """Filtra apuntes con los criterios del buscador (diccionario ya validado por el formulario).

    - importe: importe exacto (o ±1 % si «aproximado»), en el Debe o en el Haber
    - importe_min / importe_max: rango de importes
    - lado: "debe", "haber" o "" (los dos)
    - texto: en el concepto del apunte o del asiento, el documento o el nombre de la cuenta
    - cuenta: código o principio de código (400, 4000000...) o parte del nombre
    - desde / hasta, ejercicio, hoja ("sin" = sin hoja), estado, tipo
    """
    qs = (Apunte.objects.annotate(importe=F("debe") + F("haber"))
          .select_related("asiento", "asiento__ejercicio", "asiento__hoja", "cuenta", "contrapartida"))

    importe = datos.get("importe")
    minimo, maximo = datos.get("importe_min"), datos.get("importe_max")
    if importe is not None:
        if datos.get("aproximado"):
            margen = max((importe * Decimal("0.01")).quantize(Decimal("0.01")), Decimal("0.01"))
            minimo, maximo = importe - margen, importe + margen
        else:
            qs = qs.filter(importe=importe)
    if minimo is not None:
        qs = qs.filter(importe__gte=minimo)
    if maximo is not None:
        qs = qs.filter(importe__lte=maximo)

    if datos.get("lado") == "debe":
        qs = qs.filter(debe__gt=0)
    elif datos.get("lado") == "haber":
        qs = qs.filter(haber__gt=0)

    if texto := (datos.get("texto") or "").strip():
        qs = qs.filter(Q(concepto__icontains=texto) | Q(asiento__concepto__icontains=texto)
                       | Q(asiento__titulo__icontains=texto) | Q(asiento__numero_externo__icontains=texto)
                       | Q(asiento__referencia_origen__icontains=texto) | Q(cuenta__nombre__icontains=texto))

    if cuenta := (datos.get("cuenta") or "").strip():
        if cuenta.isdigit():
            qs = qs.filter(cuenta__codigo__startswith=cuenta)
        else:
            qs = qs.filter(Q(cuenta__nombre__icontains=cuenta) | Q(cuenta__nif__iexact=cuenta))

    if datos.get("desde"):
        qs = qs.filter(asiento__fecha__gte=datos["desde"])
    if datos.get("hasta"):
        qs = qs.filter(asiento__fecha__lte=datos["hasta"])
    if datos.get("ejercicio"):
        qs = qs.filter(asiento__ejercicio=datos["ejercicio"])
    if hoja := datos.get("hoja"):
        qs = qs.filter(asiento__hoja__isnull=True) if hoja == "sin" else qs.filter(asiento__hoja_id=hoja)
    if datos.get("estado"):
        qs = qs.filter(asiento__estado=datos["estado"])
    if datos.get("tipo"):
        qs = qs.filter(asiento__tipo=datos["tipo"])

    return qs.order_by("-asiento__fecha", "-asiento__numero", "orden", "id")
