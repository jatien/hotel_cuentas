from dataclasses import dataclass
from decimal import Decimal
from itertools import chain

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q, Sum
from django.db.models.functions import Length

from .models import LONGITUD_SUBCUENTA, Apunte, Asiento, CuentaContable, HistorialAsiento
from .nif import validar_nif

LONGITUDES_BASE = (3, 4, 5)


# ---------- asientos ----------

@dataclass
class Linea:
    cuenta: str                      # código de la cuenta
    debe: Decimal = Decimal("0")
    haber: Decimal = Decimal("0")
    concepto: str = ""
    


def asignar_contrapartidas(asiento, recalcular=False):
    """Rellena contrapartidas: si un lado del asiento tiene una sola cuenta,
    esa es la contrapartida de todas las líneas del otro lado.
    Con recalcular=True se borran antes las existentes."""
    apuntes = list(asiento.apuntes.select_related("cuenta"))
    if recalcular:
        for a in apuntes:
            a.contrapartida = None
    lado_debe = [a for a in apuntes if a.debe > 0]
    lado_haber = [a for a in apuntes if a.haber > 0]
    for unico, otro_lado in ((lado_debe, lado_haber), (lado_haber, lado_debe)):
        if len(unico) == 1:
            for a in otro_lado:
                if a.contrapartida_id is None:
                    a.contrapartida = unico[0].cuenta
    Apunte.objects.bulk_update(apuntes, ["contrapartida"])


def numerar_apuntes(asiento):
    """Numera las líneas 1, 2, 3... en el orden en que se presentan: primero el Debe y
    después el Haber, respetando el orden elegido dentro de cada lado."""
    apuntes = sorted(asiento.apuntes.all(), key=lambda a: (a.haber > 0, a.orden, a.id))
    cambiados = []
    for i, a in enumerate(apuntes, 1):
        if a.orden != i:
            a.orden = i
            cambiados.append(a)
    if cambiados:
        Apunte.objects.bulk_update(cambiados, ["orden"])


def renumerar(ejercicio):
    """Numera los asientos del ejercicio sin huecos: apertura, operaciones y ajustes por
    fecha, regularización y cierre. Devuelve True si ha cambiado algún número."""
    asientos = sorted(Asiento.objects.filter(ejercicio=ejercicio),
                      key=lambda a: (Asiento.PRIORIDAD[a.tipo], a.fecha, a.id))
    if all(a.numero == i for i, a in enumerate(asientos, 1)):
        return False
    # dos pasadas para no chocar con la restricción de número único
    desplazamiento = max(a.numero or 0 for a in asientos) + len(asientos)
    for a in asientos:
        a.numero += desplazamiento
    Asiento.objects.bulk_update(asientos, ["numero"])
    for i, a in enumerate(asientos, 1):
        a.numero = i
    Asiento.objects.bulk_update(asientos, ["numero"])
    return True


def saldos_subcuentas(ejercicio, hasta=None, prefijos=None):
    """[(cuenta, saldo)] de las cuentas con movimientos en el ejercicio; saldo = Debe - Haber.
    Solo las que no están a cero, ordenadas por código."""
    qs = Apunte.objects.vigentes().filter(asiento__ejercicio=ejercicio)
    if hasta:
        qs = qs.filter(asiento__fecha__lte=hasta)
    if prefijos:
        filtro = Q()
        for p in prefijos:
            filtro |= Q(cuenta__codigo__startswith=p)
        qs = qs.filter(filtro)
    filas = list(qs.order_by().values("cuenta_id").annotate(d=Sum("debe"), h=Sum("haber")))
    cuentas = CuentaContable.objects.in_bulk([f["cuenta_id"] for f in filas])
    return sorted(
        ((cuentas[f["cuenta_id"]], f["d"] - f["h"]) for f in filas if f["d"] != f["h"]),
        key=lambda x: x[0].codigo,
    )


def validar_lineas(lineas):
    """Comprueba las líneas antes de guardar nada y devuelve {codigo: cuenta}."""
    lineas = list(lineas)
    if len(lineas) < 2:
        raise ValidationError("Un asiento necesita al menos dos líneas.")

    codigos = {l.cuenta for l in lineas}
    cuentas = {c.codigo: c for c in CuentaContable.objects.filter(codigo__in=codigos)}
    if faltan := codigos - cuentas.keys():
        raise ValidationError(f"Cuentas inexistentes: {', '.join(sorted(faltan))}")
    if no_imp := [c for c in cuentas.values() if not c.imputable]:
        raise ValidationError(f"Cuentas no imputables: {', '.join(str(c) for c in no_imp)}")
    if inactivas := [c for c in cuentas.values() if not c.activa]:
        raise ValidationError(f"Cuentas inactivas: {', '.join(str(c) for c in inactivas)}")

    for l in lineas:
        if (l.debe > 0) == (l.haber > 0) or l.debe < 0 or l.haber < 0:
            raise ValidationError(f"Línea {l.cuenta}: importe solo en Debe o solo en Haber, positivo.")

    total_debe = sum(l.debe for l in lineas)
    total_haber = sum(l.haber for l in lineas)
    if total_debe != total_haber:
        raise ValidationError(f"El asiento no cuadra: Debe {total_debe} ≠ Haber {total_haber} "
                              f"(diferencia {abs(total_debe - total_haber)}).")
    return cuentas


@transaction.atomic
def crear_asiento(fecha, concepto, lineas, origen=Asiento.Origen.MANUAL,
                  referencia_origen="", contabilizar=True, tipo=Asiento.Tipo.OPERACION, modelo="",
                  hoja=None, numero_externo="", titulo="", tipo_documento="", usuario=None,
                  permitir_duplicado=False, revierte=None, rectifica=None):
    """Crea el asiento con todas sus líneas en una sola transacción: o se guarda entero o nada."""
    lineas = list(lineas)
    cuentas = validar_lineas(lineas)

    asiento = Asiento(fecha=fecha, concepto=concepto, origen=origen, tipo=tipo, modelo=modelo,
                      referencia_origen=referencia_origen[:100], hoja=hoja,
                      numero_externo=(numero_externo or "")[:60], titulo=(titulo or "")[:120],
                      tipo_documento=tipo_documento, revierte=revierte, rectifica=rectifica,
                      creado_por=usuario if getattr(usuario, "is_authenticated", False) else None)
    asiento.full_clean(exclude=["ejercicio", "numero"], validate_constraints=False)
    asiento.save()

    Apunte.objects.bulk_create([
        Apunte(asiento=asiento, orden=i, cuenta=cuentas[l.cuenta], debe=l.debe, haber=l.haber,
               concepto=l.concepto or concepto)
        for i, l in enumerate(lineas, 1)
    ])
    numerar_apuntes(asiento)
    asignar_contrapartidas(asiento)
    HistorialAsiento.registrar(asiento, HistorialAsiento.Accion.CREAR, usuario,
                               f"{len(lineas)} apuntes, origen {asiento.get_origen_display().lower()}")
    if contabilizar:
        asiento.contabilizar(usuario, permitir_duplicado=permitir_duplicado)
    if renumerar(asiento.ejercicio):
        asiento.refresh_from_db(fields=["numero"])
    return asiento


def _lineas_invertidas(asiento):
    return [Linea(a.cuenta.codigo, debe=a.haber, haber=a.debe, concepto=a.concepto)
            for a in asiento.apuntes.select_related("cuenta").order_by("orden", "id")]


def _comprobar_corregible(asiento, fecha):
    if asiento.estado != Asiento.Estado.CONTABILIZADO:
        raise ValidationError("Solo se revierten o rectifican asientos contabilizados.")
    if asiento.tipo in Asiento.TIPOS_UNICOS:
        raise ValidationError(f"El asiento de {asiento.get_tipo_display().lower()} no se revierte: "
                              "anúlalo y créalo de nuevo.")
    if asiento.reversiones.exclude(estado=Asiento.Estado.ANULADO).exists():
        raise ValidationError(f"El asiento {asiento.numero} ya está revertido.")
    if asiento.revierte_id:
        raise ValidationError("Este asiento ya es una reversión: anúlalo en vez de revertirlo.")
    if fecha < asiento.fecha:
        raise ValidationError("La reversión no puede ser anterior al asiento original.")


@transaction.atomic
def revertir_asiento(asiento, fecha, usuario=None, motivo=""):
    """Asiento nuevo con el Debe y el Haber cambiados, contabilizado y enlazado al original.
    El original no se toca: los dos quedan en el diario y su efecto neto es cero."""
    _comprobar_corregible(asiento, fecha)
    motivo = (motivo or "").strip()
    reversion = crear_asiento(
        fecha, f"Reversión del asiento {asiento.numero}: {asiento.concepto}"[:255], _lineas_invertidas(asiento),
        tipo=Asiento.Tipo.AJUSTE if asiento.tipo == Asiento.Tipo.AJUSTE else Asiento.Tipo.OPERACION,
        numero_externo=asiento.numero_externo, tipo_documento=asiento.tipo_documento,
        titulo=f"Reversión asiento {asiento.numero}", hoja=asiento.hoja, usuario=usuario,
        permitir_duplicado=True, revierte=asiento)
    HistorialAsiento.registrar(asiento, HistorialAsiento.Accion.REVERTIR, usuario,
                               f"Revertido por el asiento {reversion.numero}. {motivo}".strip())
    asiento.refresh_from_db(fields=["numero"])
    return reversion


@transaction.atomic
def rectificar_asiento(asiento, fecha, usuario=None, motivo=""):
    """Revierte el asiento y crea una copia en borrador para registrarlo bien."""
    reversion = revertir_asiento(asiento, fecha, usuario, motivo)
    copia = Asiento(fecha=fecha, concepto=asiento.concepto, titulo=asiento.titulo, tipo=Asiento.Tipo.OPERACION,
                    numero_externo=asiento.numero_externo, tipo_documento=asiento.tipo_documento,
                    fecha_documento=asiento.fecha_documento, hoja=asiento.hoja, rectifica=asiento,
                    creado_por=usuario if getattr(usuario, "is_authenticated", False) else None)
    copia.full_clean(exclude=["ejercicio", "numero"], validate_constraints=False)
    copia.save()
    Apunte.objects.bulk_create([
        Apunte(asiento=copia, orden=a.orden, cuenta=a.cuenta, debe=a.debe, haber=a.haber, concepto=a.concepto)
        for a in asiento.apuntes.all()])
    asignar_contrapartidas(copia)
    HistorialAsiento.registrar(copia, HistorialAsiento.Accion.RECTIFICAR, usuario,
                               f"Rectifica el asiento {asiento.numero}. {motivo}".strip())
    HistorialAsiento.registrar(asiento, HistorialAsiento.Accion.RECTIFICAR, usuario,
                               f"Rectificado: reversión {reversion.numero}, nuevo borrador {copia.pk}. {motivo}".strip())
    renumerar(copia.ejercicio)
    copia.refresh_from_db()
    reversion.refresh_from_db()
    return reversion, copia


@transaction.atomic
def duplicar_asiento(asiento, fecha=None, usuario=None):
    """Copia en borrador de un asiento (para operaciones repetidas)."""
    copia = Asiento(fecha=fecha or asiento.fecha, concepto=asiento.concepto, titulo=asiento.titulo,
                    tipo=Asiento.Tipo.OPERACION if asiento.tipo in Asiento.TIPOS_UNICOS else asiento.tipo,
                    tipo_documento=asiento.tipo_documento, hoja=asiento.hoja,
                    creado_por=usuario if getattr(usuario, "is_authenticated", False) else None)
    copia.full_clean(exclude=["ejercicio", "numero"], validate_constraints=False)
    copia.save()
    Apunte.objects.bulk_create([
        Apunte(asiento=copia, orden=a.orden, cuenta=a.cuenta, debe=a.debe, haber=a.haber, concepto=a.concepto)
        for a in asiento.apuntes.all()])
    asignar_contrapartidas(copia)
    HistorialAsiento.registrar(copia, HistorialAsiento.Accion.CREAR, usuario, f"Copia del asiento {asiento.numero}")
    renumerar(copia.ejercicio)
    copia.refresh_from_db()
    return copia


def puntear(apuntes, estado, usuario=None, nota=None):
    """Marca el punteo de varios apuntes. No toca importes, fechas, cuentas ni asientos."""
    if estado not in Apunte.PUNTEO_MANUAL:
        raise ValidationError("Estado de punteo no válido (la conciliación bancaria se marca desde el banco).")
    from django.utils import timezone
    ahora = timezone.now()
    usuario_ok = usuario if getattr(usuario, "is_authenticated", False) else None
    cambiados = []
    for a in apuntes:
        if a.punteo == Apunte.Punteo.CONCILIADO:
            raise ValidationError(f"El apunte {a.asiento.numero}.{a.orden} está conciliado con el banco.")
        a.punteo = estado
        a.punteado_en = ahora if estado else None
        a.punteado_por = usuario_ok if estado else None
        if nota is not None:
            a.punteo_nota = nota[:255]
        cambiados.append(a)
    Apunte.objects.bulk_update(cambiados, ["punteo", "punteado_en", "punteado_por", "punteo_nota"])
    return len(cambiados)


# ---------- subcuentas de 8 dígitos ----------

def siguiente_codigo(base):
    """Primer código libre de 8 dígitos bajo una cuenta de 3 a 5 dígitos."""
    huecos = LONGITUD_SUBCUENTA - len(base.codigo)
    tomados = set(
        CuentaContable.objects.annotate(largo=Length("codigo"))
        .filter(largo=LONGITUD_SUBCUENTA, codigo__startswith=base.codigo)
        .values_list("codigo", flat=True)
    )
    ultimo = max((int(c[len(base.codigo):]) for c in tomados), default=0)
    for n in chain(range(ultimo + 1, 10 ** huecos), range(1, ultimo + 1)):
        codigo = f"{base.codigo}{n:0{huecos}d}"
        if codigo not in tomados:
            return codigo
    raise ValidationError(f"No quedan códigos libres bajo la cuenta {base.codigo}.")


def buscar_subcuenta_por_nif(nif, bases):
    """Subcuenta existente con ese NIF bajo alguna de las bases indicadas (para facturas)."""
    nif = validar_nif(nif)
    if not nif:
        return None
    return (CuentaContable.objects.filter(nif=nif, padre__codigo__in=list(bases))
            .order_by("codigo").first())


@transaction.atomic
def crear_subcuenta(base, nombre, nif="", codigo=""):
    cuenta_base = CuentaContable.objects.select_for_update().filter(codigo=str(base).strip()).first()
    if not cuenta_base or len(cuenta_base.codigo) not in LONGITUDES_BASE:
        raise ValidationError("Elige una cuenta del plan de 3, 4 o 5 dígitos.")

    nif = validar_nif(nif)
    if nif:
        repetida = CuentaContable.objects.filter(padre=cuenta_base, nif=nif).first()
        if repetida:
            raise ValidationError(f"Ya existe la subcuenta {repetida} con el NIF {nif}.")

    codigo = (codigo or "").strip()
    if codigo:
        if not (codigo.isdigit() and len(codigo) == LONGITUD_SUBCUENTA):
            raise ValidationError(f"El código debe tener {LONGITUD_SUBCUENTA} dígitos.")
        if not codigo.startswith(cuenta_base.codigo):
            raise ValidationError(f"El código debe empezar por {cuenta_base.codigo}.")
    else:
        codigo = siguiente_codigo(cuenta_base)

    subcuenta = CuentaContable(
        codigo=codigo, nombre=" ".join(nombre.split()), nif=nif,
        padre=cuenta_base, imputable=True,
    )
    subcuenta.full_clean()
    subcuenta.save()
    return subcuenta