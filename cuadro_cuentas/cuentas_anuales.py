"""Cálculo de las cuentas anuales a partir de los apuntes contabilizados.

- El balance usa los saldos al cierre sin el asiento de cierre (que lo deja todo a cero) y
  suma al «Resultado del ejercicio» lo que aún quede en los grupos 6 y 7.
- La cuenta de pérdidas y ganancias y el estado de ingresos y gastos usan los saldos sin la
  regularización ni el cierre.
- Cada subcuenta se asigna a la partida cuyo «Nº de cuentas» encaja con más dígitos. Las
  cuentas que el PGC pone a la vez en el activo y en el pasivo (551, 5525...) van al activo
  si su saldo es deudor y al pasivo si es acreedor.
"""
from dataclasses import dataclass, field
from decimal import Decimal

from django.db.models import Sum

from .models import Apunte, Asiento, Ejercicio
from .modelos_cuentas_anuales import CIFRA_NEGOCIOS, MODELOS, RESULTADO_PYG

CERO = Decimal("0")

# Norma 4ª: dos de las tres circunstancias, durante dos ejercicios consecutivos
LIMITES_ABREVIADO = {
    "balance": {"activo": Decimal("4000000"), "cifra": Decimal("8000000"), "trabajadores": 50},
    "pyg": {"activo": Decimal("11400000"), "cifra": Decimal("22800000"), "trabajadores": 250},
}


def _recorrer(partidas):
    for p in partidas:
        yield p
        yield from _recorrer(p.hijos)


def saldos(ejercicio, excluir_tipos):
    """{codigo: (nombre, Debe - Haber)} de las subcuentas con saldo, solo asientos contabilizados."""
    if ejercicio is None:
        return {}
    filas = (Apunte.objects
             .filter(asiento__ejercicio=ejercicio, asiento__estado=Asiento.Estado.CONTABILIZADO)
             .exclude(asiento__tipo__in=excluir_tipos)
             .values("cuenta__codigo", "cuenta__nombre")
             .annotate(d=Sum("debe"), h=Sum("haber"))
             .order_by())
    return {f["cuenta__codigo"]: (f["cuenta__nombre"], f["d"] - f["h"]) for f in filas if f["d"] != f["h"]}


@dataclass
class Fila:
    ref: str
    texto: str
    cuentas_texto: str
    nivel: int
    actual: Decimal = CERO
    anterior: Decimal = CERO
    es_total: bool = False
    siempre: bool = False
    tiene_hijos: bool = False
    detalle: list = field(default_factory=list)   # [(codigo, nombre, actual, anterior)]

    @property
    def visible(self):
        # Norma 5ª.2: no figuran las partidas sin importe en el ejercicio ni en el anterior
        return self.siempre or self.actual != 0 or self.anterior != 0


@dataclass
class EstadoCalculado:
    clave: str
    titulo: str
    secciones: list          # [(titulo, total_texto, total_actual, total_anterior, [Fila])]
    sin_clasificar: list     # [(codigo, nombre, saldo_actual)]
    valores: dict            # ref -> (actual, anterior)


def _asignar(estado, saldos_por_columna):
    """Reparte cada subcuenta en la partida que le corresponde. Devuelve
    ({ref: {codigo: [nombre, actual, anterior]}}, [subcuentas sin partida])."""
    # cuenta del modelo -> [(seccion, partida)], para detectar las que están en ambos lados
    dueños = {}
    for i, seccion in enumerate(estado.secciones):
        for p in _recorrer(seccion.partidas):
            for c in p.cuentas:
                dueños.setdefault(c, []).append((i, seccion.signo, p.ref))

    asignado, sin_partida = {}, {}
    for columna, datos in enumerate(saldos_por_columna):
        for codigo, (nombre, saldo) in datos.items():
            if codigo[0] not in estado.grupos:
                continue
            prefijo = max((c for c in dueños if codigo.startswith(c)), key=len, default=None)
            destino = None
            if prefijo:
                candidatos = dueños[prefijo]
                if len({signo for _, signo, _ in candidatos}) > 1:   # cuenta mixta activo/pasivo
                    signo = "deudor" if saldo > 0 else "acreedor"
                    candidatos = [c for c in candidatos if c[1] == signo] or candidatos
                destino = candidatos[0][2]
            if destino:
                fila = asignado.setdefault(destino, {}).setdefault(codigo, [nombre, CERO, CERO])
                fila[1 + columna] += saldo
            else:
                sin_partida.setdefault(codigo, [nombre, CERO, CERO])[1 + columna] += saldo
    return asignado, sin_partida


def calcular_estado(estado, actual, anterior, valores_externos=None):
    """Calcula un estado (balance, PyG o ingresos y gastos) para dos ejercicios."""
    excluir = [Asiento.Tipo.CIERRE] if estado.clave == "balance" else [
        Asiento.Tipo.REGULARIZACION, Asiento.Tipo.CIERRE]
    columnas = [saldos(actual, excluir), saldos(anterior, excluir)]
    asignado, sin_partida = _asignar(estado, columnas)
    valores = dict(valores_externos or {})

    # Resultado del ejercicio en el balance: lo que siga en los grupos 6 y 7 (si aún no se ha
    # regularizado) se suma a la cuenta 129
    resultado_pendiente = [
        sum((-s for c, (_, s) in col.items() if c[0] in "67"), CERO) for col in columnas
    ] if estado.clave == "balance" else [CERO, CERO]

    secciones = []
    for seccion in estado.secciones:
        signo = 1 if seccion.signo == "deudor" else -1
        filas = []

        def calcular(p, nivel):
            fila = Fila(p.ref, p.texto, p.cuentas_texto, nivel, siempre=p.siempre,
                        es_total=bool(p.formula), tiene_hijos=bool(p.hijos))
            filas.append(fila)
            if p.formula:
                fila.actual = sum((valores.get(r, (CERO, CERO))[0] for r in p.formula), CERO)
                fila.anterior = sum((valores.get(r, (CERO, CERO))[1] for r in p.formula), CERO)
            else:
                for codigo, (nombre, a, b) in sorted(asignado.get(p.ref, {}).items()):
                    fila.detalle.append((codigo, nombre, signo * a, signo * b))
                    fila.actual += signo * a
                    fila.anterior += signo * b
                if p.resultado_ejercicio:
                    fila.actual += resultado_pendiente[0]
                    fila.anterior += resultado_pendiente[1]
                for h in p.hijos:
                    hijo = calcular(h, nivel + 1)
                    fila.actual += hijo.actual
                    fila.anterior += hijo.anterior
                if p.ref in valores and not p.cuentas and not p.hijos:
                    fila.actual, fila.anterior = valores[p.ref]
            valores[p.ref] = (fila.actual, fila.anterior)
            return fila

        totales = [calcular(p, 0) for p in seccion.partidas]
        total_a = sum((f.actual for f in totales), CERO)
        total_b = sum((f.anterior for f in totales), CERO)
        secciones.append((seccion.titulo, seccion.total, total_a, total_b, filas))

    sin_clasificar = sorted((c, n, a) for c, (n, a, _) in sin_partida.items() if a)
    return EstadoCalculado(estado.clave, estado.titulo, secciones, sin_clasificar, valores)


def ejercicio_anterior(ejercicio):
    return Ejercicio.objects.filter(anio=ejercicio.anio - 1).first() if ejercicio else None


def calcular_cuentas_anuales(ejercicio, modelo="normal"):
    """Balance, PyG y estado de ingresos y gastos reconocidos del ejercicio y del anterior."""
    anterior = ejercicio_anterior(ejercicio)
    estados = MODELOS[modelo]
    pyg = calcular_estado(estados["pyg"], ejercicio, anterior)
    resultado = pyg.valores[RESULTADO_PYG[modelo]]
    return {
        "balance": calcular_estado(estados["balance"], ejercicio, anterior),
        "pyg": pyg,
        "ecpn": calcular_estado(estados["ecpn"], ejercicio, anterior, {"RA": resultado}),
        "anterior": anterior,
        "borradores": Asiento.objects.filter(ejercicio=ejercicio, estado=Asiento.Estado.BORRADOR).count(),
    }


def _cumple(limites, activo, cifra, trabajadores):
    condiciones = [activo <= limites["activo"], cifra <= limites["cifra"],
                   trabajadores is not None and trabajadores <= limites["trabajadores"]]
    return sum(condiciones) >= 2


def evaluar_abreviado(ejercicio):
    """Norma 4ª: si el ejercicio (y el anterior, si existe) permite balance y memoria
    abreviados y cuenta de pérdidas y ganancias abreviada."""
    anterior = ejercicio_anterior(ejercicio)
    balance = calcular_estado(MODELOS["normal"]["balance"], ejercicio, anterior)
    pyg = calcular_estado(MODELOS["normal"]["pyg"], ejercicio, anterior)
    activo = (balance.secciones[0][2], balance.secciones[0][3])
    cifra = pyg.valores[CIFRA_NEGOCIOS]
    trabajadores = (ejercicio.trabajadores_medios, anterior.trabajadores_medios if anterior else None)

    resultado = {"activo": activo[0], "cifra": cifra[0], "trabajadores": trabajadores[0],
                 "dos_ejercicios": anterior is not None}
    for clave, limites in LIMITES_ABREVIADO.items():
        este = _cumple(limites, activo[0], cifra[0], trabajadores[0])
        previo = _cumple(limites, activo[1], cifra[1], trabajadores[1]) if anterior else None
        resultado[clave] = {"este": este, "anterior": previo, "puede": este and previo is not False}
    return resultado