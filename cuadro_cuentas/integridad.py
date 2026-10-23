"""Balance de comprobación: controles de integridad de la contabilidad de un ejercicio.

Cada control se calcula sobre todos los registros guardados (nunca sobre lo que se ve en
pantalla) y devuelve un estado: «ok», «aviso» (revisar) o «error» (algo no cuadra).
Los totales del diario, del mayor y de las sumas y saldos se obtienen por caminos
distintos para que una diferencia entre ellos se note.
"""
from dataclasses import dataclass, field
from decimal import Decimal
from urllib.parse import urlencode

from django.db.models import Count, F, Q, Sum
from django.urls import reverse

from .consultas import sumas_y_saldos_periodo
from .models import Apunte, Asiento, Ejercicio

CERO = Decimal("0")
OK, AVISO, ERROR = "ok", "aviso", "error"


@dataclass
class Control:
    clave: str
    titulo: str
    estado: str = OK
    resumen: str = ""
    detalle: list = field(default_factory=list)      # [(texto, url o "")]


def _eur(valor):
    """1.234,56 como en el resto de la aplicación."""
    return f"{valor or CERO:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _url_diario(ejercicio, **params):
    return f"{reverse('cuadro_cuentas:diario')}?{urlencode({'ejercicio': ejercicio.anio, **params})}"


def _url_asiento(asiento_id):
    return reverse("cuadro_cuentas:asiento", args=[asiento_id])


def comprobar(ejercicio):
    contabilizados = Asiento.objects.filter(ejercicio=ejercicio, estado=Asiento.Estado.CONTABILIZADO)
    apuntes = Apunte.objects.contabilizados().filter(asiento__ejercicio=ejercicio)
    controles = []

    # 1. Cada asiento contabilizado cuadra y tiene al menos dos apuntes
    c = Control("cuadre", "Asientos contabilizados cuadrados y con dos o más apuntes")
    malos = (contabilizados.annotate(d=Sum("apuntes__debe"), h=Sum("apuntes__haber"), n=Count("apuntes"))
             .filter(Q(n__lt=2) | ~Q(d=F("h"))).order_by("numero"))
    for a in malos:
        c.detalle.append((f"Asiento {a.numero} ({a.fecha:%d/%m/%Y}): Debe {_eur(a.d or 0)}, Haber {_eur(a.h or 0)}, "
                          f"{a.n} apuntes", _url_asiento(a.id)))
    c.estado = ERROR if c.detalle else OK
    c.resumen = f"{len(c.detalle)} asientos con problemas" if c.detalle else f"{contabilizados.count()} asientos correctos"
    controles.append(c)

    # 2. Diario = mayor = sumas y saldos
    c = Control("totales", "El diario, el mayor y las sumas y saldos dan los mismos totales")
    por_asiento = contabilizados.annotate(d=Sum("apuntes__debe"), h=Sum("apuntes__haber"))
    diario_d = sum((a.d or CERO for a in por_asiento), CERO)
    diario_h = sum((a.h or CERO for a in por_asiento), CERO)
    por_cuenta = apuntes.values("cuenta_id").annotate(d=Sum("debe"), h=Sum("haber")).order_by()
    mayor_d = sum((f["d"] or CERO for f in por_cuenta), CERO)
    mayor_h = sum((f["h"] or CERO for f in por_cuenta), CERO)
    _, totales, comprobacion = sumas_y_saldos_periodo(ejercicio, incluir_cierre=True)
    c.detalle = [
        (f"Diario (suma de asientos): Debe {_eur(diario_d)} · Haber {_eur(diario_h)}", _url_diario(ejercicio)),
        (f"Mayor (suma por cuentas): Debe {_eur(mayor_d)} · Haber {_eur(mayor_h)}", ""),
        (f"Sumas y saldos: saldos deudores {_eur(totales['saldo_deudor'])} · acreedores {_eur(totales['saldo_acreedor'])}",
         reverse("cuadro_cuentas:sumas_saldos") + f"?ejercicio={ejercicio.anio}&cierre=1"),
    ]
    iguales = diario_d == diario_h == mayor_d == mayor_h and comprobacion["saldos"]
    c.estado = OK if iguales else ERROR
    c.resumen = f"Debe = Haber = {_eur(diario_d)}" if iguales else "Los totales no coinciden"
    controles.append(c)

    # 3. Cuentas utilizables
    c = Control("cuentas", "Apuntes en cuentas que admiten movimientos")
    raras = (apuntes.filter(Q(cuenta__imputable=False) | Q(cuenta__activa=False))
             .values("cuenta__codigo", "cuenta__nombre").annotate(n=Count("id")).order_by("cuenta__codigo"))
    c.detalle = [(f"{r['cuenta__codigo']} {r['cuenta__nombre']}: {r['n']} apuntes",
                  _url_diario(ejercicio, cuenta=r["cuenta__codigo"])) for r in raras]
    c.estado = AVISO if c.detalle else OK
    c.resumen = f"{len(c.detalle)} cuentas agrupadoras o inactivas con apuntes" if c.detalle else "Todas correctas"
    controles.append(c)

    # 4. Fechas dentro del ejercicio
    c = Control("fechas", "Fechas dentro del ejercicio")
    fuera = (Asiento.objects.filter(ejercicio=ejercicio)
             .filter(Q(fecha__lt=ejercicio.fecha_inicio) | Q(fecha__gt=ejercicio.fecha_fin)))
    c.detalle = [(f"Asiento {a.numero}: {a.fecha:%d/%m/%Y}", _url_asiento(a.id)) for a in fuera]
    c.estado = ERROR if c.detalle else OK
    c.resumen = f"{len(c.detalle)} asientos fuera de fechas" if c.detalle else \
        f"Del {ejercicio.fecha_inicio:%d/%m/%Y} al {ejercicio.fecha_fin:%d/%m/%Y}"
    controles.append(c)

    # 5. Numeración correlativa sin huecos ni repetidos
    c = Control("numeracion", "Numeración de asientos correlativa")
    numeros = sorted(Asiento.objects.filter(ejercicio=ejercicio).values_list("numero", flat=True))
    huecos = sorted(set(range(1, (numeros[-1] if numeros else 0) + 1)) - set(numeros))
    if huecos:
        c.detalle.append((f"Faltan los números {', '.join(map(str, huecos[:20]))}", ""))
    c.estado = AVISO if c.detalle else OK
    c.resumen = f"{len(numeros)} asientos, del 1 al {numeros[-1]}" if numeros and not huecos else \
        ("Sin asientos" if not numeros else f"{len(huecos)} huecos")
    controles.append(c)

    # 6. Borradores pendientes (no suman en los informes oficiales)
    c = Control("borradores", "Asientos en borrador")
    borradores = Asiento.objects.filter(ejercicio=ejercicio, estado=Asiento.Estado.BORRADOR).order_by("numero")
    c.detalle = [(f"Asiento {a.numero} ({a.fecha:%d/%m/%Y}) {a.concepto}", _url_asiento(a.id))
                 for a in borradores[:30]]
    n = borradores.count()
    c.estado = AVISO if n else OK
    c.resumen = f"{n} borradores sin contabilizar (no suman en balances)" if n else "Ninguno"
    controles.append(c)

    # 7. Reversiones coherentes
    c = Control("reversiones", "Reversiones y anulaciones")
    dobles = Asiento.objects.filter(ejercicio=ejercicio, revierte__estado=Asiento.Estado.ANULADO) \
        .exclude(estado=Asiento.Estado.ANULADO)
    c.detalle = [(f"El asiento {a.numero} revierte al {a.revierte.numero}, que además está anulado: "
                  "su importe se descuenta dos veces", _url_asiento(a.id)) for a in dobles.select_related("revierte")]
    anulados = Asiento.objects.filter(ejercicio=ejercicio, estado=Asiento.Estado.ANULADO).count()
    c.estado = ERROR if c.detalle else OK
    c.resumen = "Doble corrección" if c.detalle else f"{anulados} anulados, conservados sin sumar"
    controles.append(c)

    # 8. La apertura coincide con el cierre del ejercicio anterior
    c = Control("apertura", "La apertura coincide con el cierre del ejercicio anterior")
    anterior = Ejercicio.objects.filter(fecha_fin__lt=ejercicio.fecha_inicio).order_by("-fecha_fin").first()
    apertura = contabilizados.filter(tipo=Asiento.Tipo.APERTURA).first()
    cierre = anterior and Asiento.objects.filter(ejercicio=anterior, tipo=Asiento.Tipo.CIERRE,
                                                 estado=Asiento.Estado.CONTABILIZADO).first()
    if not (apertura and cierre):
        c.estado = OK
        c.resumen = "No aplica (falta la apertura o el cierre anterior)"
    else:
        def netos(asiento):
            return {f["cuenta__codigo"]: f["d"] - f["h"] for f in
                    asiento.apuntes.values("cuenta__codigo").annotate(d=Sum("debe"), h=Sum("haber")).order_by()}
        ab, ci = netos(apertura), netos(cierre)
        for codigo in sorted(set(ab) | set(ci)):
            if ab.get(codigo, CERO) != -ci.get(codigo, CERO):
                c.detalle.append((f"{codigo}: apertura {_eur(ab.get(codigo, CERO))}, cierre {anterior} "
                                  f"{_eur(-ci.get(codigo, CERO))}", _url_asiento(apertura.id)))
        c.estado = AVISO if c.detalle else OK
        c.resumen = f"{len(c.detalle)} cuentas distintas" if c.detalle else "Coinciden cuenta a cuenta"
    controles.append(c)

    # 9. Punteo pendiente (informativo)
    c = Control("punteo", "Punteo")
    pendientes = apuntes.filter(punteo__in=[Apunte.Punteo.REVISAR, Apunte.Punteo.DISCREPANCIA]) \
        .values("punteo").annotate(n=Count("id")).order_by()
    c.detalle = [(f"{Apunte.Punteo(p['punteo']).label}: {p['n']} apuntes",
                  _url_diario(ejercicio, punteo=p["punteo"])) for p in pendientes]
    punteados = apuntes.exclude(punteo="").count()
    c.estado = AVISO if c.detalle else OK
    c.resumen = f"{punteados} de {apuntes.count()} apuntes punteados"
    controles.append(c)

    # 10. Balance de situación cuadrado
    from .cuentas_anuales import calcular_cuentas_anuales
    c = Control("balance", "Balance de situación: activo = patrimonio neto + pasivo")
    balance = calcular_cuentas_anuales(ejercicio, "normal")["balance"]
    activo, pn_pasivo = balance.secciones[0][2], balance.secciones[1][2]
    c.detalle = [(f"{codigo} {nombre}: {_eur(saldo)} sin partida en el modelo", "")
                 for codigo, nombre, saldo in balance.sin_clasificar]
    if activo != pn_pasivo:
        c.detalle.insert(0, (f"Activo {_eur(activo)} · PN y pasivo {_eur(pn_pasivo)} · diferencia {_eur(activo - pn_pasivo)}",
                             reverse("cuadro_cuentas:cuentas_anuales") + f"?ejercicio={ejercicio.anio}"))
    c.estado = ERROR if activo != pn_pasivo else (AVISO if c.detalle else OK)
    c.resumen = f"Activo {_eur(activo)} = PN y pasivo {_eur(pn_pasivo)}" if activo == pn_pasivo else "No cuadra"
    controles.append(c)

    return controles

