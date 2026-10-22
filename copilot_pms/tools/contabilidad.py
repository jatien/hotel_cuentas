"""
Herramientas de contabilidad (app cuadro_cuentas).

Convenios del libro (los mismos que las pantallas de extractos y cuentas anuales):
- Saldo = Debe - Haber. Positivo = deudor, negativo = acreedor.
- Todo se calcula dentro de un ejercicio: el saldo inicial sale del asiento de
  apertura; nunca se mezclan dos ejercicios en un mismo saldo.
- Se excluye el asiento de cierre (lo deja todo a cero).
- Por defecto solo cuentan los asientos contabilizados; los borradores se
  avisan, no se suman en silencio.
- La contabilidad es de una única entidad (DatosEmpresa) y en euros.
"""

from datetime import date
from urllib.parse import urlencode

from django.db.models import Count, Q, Sum
from django.urls import reverse
from pydantic import BaseModel, ConfigDict, Field, model_validator

from copilot_pms.models import EstadoLlamada
from copilot_pms.tools.base import Aclarar, NoDisponible, Resultado, herramienta
from copilot_pms.tools.comun import CERO, MONEDA_CONTABILIDAD, consultado_en, importe, saldo_dict, saldo_neto
from cuadro_cuentas.consultas import extracto, sumas_y_saldos
from cuadro_cuentas.models import Apunte, Asiento, CuentaContable, DatosEmpresa, Ejercicio

PERMISOS = ("cuadro_cuentas.view_apunte", "cuadro_cuentas.view_cuentacontable")
FUENTE = "Contabilidad (cuadro_cuentas): libro diario"
CODIGO = Field(pattern=r"^\d{1,12}$", description=(
    "Código de cuenta o subcuenta, solo dígitos: '572' agrupa todas sus subcuentas, '57200001' es una "
    "subcuenta concreta. Si solo conoces el nombre, llama antes a buscar_cuentas."))


class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _entidad() -> dict:
    datos = DatosEmpresa.actual()
    if datos is None:
        return {"denominacion": None, "nif": None, "nota": "Datos de la empresa sin configurar en contabilidad."}
    return {"denominacion": datos.denominacion, "nif": datos.nif or None}


def _ejercicio_de(fecha: date) -> Ejercicio:
    ejercicio = Ejercicio.buscar(fecha)
    if ejercicio is None:
        anios = list(Ejercicio.objects.values_list("anio", flat=True))
        raise NoDisponible("EJERCICIO_NO_EXISTE",
                           f"No hay ningún ejercicio contable que incluya el {fecha:%d/%m/%Y}.",
                           {"ejercicios_existentes": anios})
    return ejercicio


def _ejercicio_por_anio(anio: int | None, hoy: date) -> Ejercicio:
    if anio is None:
        return _ejercicio_de(hoy)
    ejercicio = Ejercicio.objects.filter(anio=anio).first()
    if ejercicio is None:
        raise NoDisponible("EJERCICIO_NO_EXISTE", f"No existe el ejercicio {anio} en contabilidad.",
                           {"ejercicios_existentes": list(Ejercicio.objects.values_list("anio", flat=True))})
    return ejercicio


def _periodo(desde: date | None, hasta: date | None, hoy: date):
    """Resuelve el periodo y comprueba que cae en un solo ejercicio."""
    hasta = hasta or hoy
    ejercicio = _ejercicio_de(hasta)
    desde = desde or ejercicio.fecha_inicio
    if desde > hasta:
        raise Aclarar("PERIODO_INVERTIDO", "La fecha inicial es posterior a la final.")
    if desde < ejercicio.fecha_inicio:
        raise Aclarar(
            "PERIODO_VARIOS_EJERCICIOS",
            f"El periodo abarca más de un ejercicio. Los saldos se calculan por ejercicio: el {ejercicio} va "
            f"del {ejercicio.fecha_inicio:%d/%m/%Y} al {ejercicio.fecha_fin:%d/%m/%Y}. Pide un periodo dentro "
            "de un único ejercicio o consulta cada ejercicio por separado.")
    return ejercicio, desde, hasta


def _cuenta(codigo: str) -> dict:
    cuentas = CuentaContable.objects.filter(codigo__startswith=codigo)
    if not cuentas.exists():
        raise NoDisponible("CUENTA_NO_EXISTE", f"No existe ninguna cuenta con el código {codigo}.")
    exacta = cuentas.filter(codigo=codigo).first()
    subcuentas = cuentas.filter(codigo__regex=r"^\d{8}$").count()
    return {
        "codigo": codigo,
        "nombre": exacta.nombre if exacta else None,
        "nivel": exacta.nivel if exacta else "prefijo",
        "agrupa_subcuentas": not (exacta and exacta.es_subcuenta),
        "subcuentas_incluidas": subcuentas if not (exacta and exacta.es_subcuenta) else 1,
    }


def _enlace_extracto(ejercicio, cuenta, desde, hasta, solo_contabilizados) -> str:
    params = {"ejercicio": ejercicio.anio, "cuenta": cuenta, "desde": desde.isoformat(),
              "hasta": hasta.isoformat()}
    if solo_contabilizados:
        params["solo_contabilizados"] = "1"
    return f"{reverse('cuadro_cuentas:extractos')}?{urlencode(params)}"


def _avisos_comunes(ejercicio, borradores_excluidos, solo_contabilizados) -> list[str]:
    avisos = []
    if solo_contabilizados and borradores_excluidos:
        avisos.append(f"Hay {borradores_excluidos} apuntes en asientos en borrador que NO se han sumado "
                      "(solo cuentan los contabilizados).")
    if not solo_contabilizados:
        avisos.append("Incluye asientos en borrador: no es un saldo oficial.")
    if not ejercicio.cerrado:
        avisos.append(f"El ejercicio {ejercicio} no está cerrado: los saldos pueden cambiar.")
    return avisos


# --------------------------------------------------------------------------- #
class BuscarCuentasArgs(_Args):
    texto: str = Field(min_length=1, max_length=100, description=(
        "Código (o principio de código), parte del nombre de la cuenta o NIF del tercero."))
    solo_subcuentas: bool = Field(False, description="Solo subcuentas de 8 dígitos (las que llevan apuntes).")
    limite: int = Field(20, ge=1, le=50)


@herramienta(
    "buscar_cuentas",
    descripcion="Busca cuentas del plan contable por código, nombre o NIF. Úsala para resolver el código "
                "exacto cuando el usuario nombra una cuenta (p. ej. «el banco», «proveedor Makro»).",
    argumentos=BuscarCuentasArgs, dominio="contabilidad", permisos=("cuadro_cuentas.view_cuentacontable",),
)
def buscar_cuentas(p: BuscarCuentasArgs, ctx):
    texto = p.texto.strip()
    qs = CuentaContable.objects.filter(activa=True)
    if texto.isdigit():
        qs = qs.filter(codigo__startswith=texto)
    else:
        qs = qs.filter(Q(nombre__icontains=texto) | Q(nif__iexact=texto))
    if p.solo_subcuentas:
        qs = qs.filter(codigo__regex=r"^\d{8}$")
    total = qs.count()
    cuentas = [{"codigo": c.codigo, "nombre": c.nombre, "nivel": c.nivel, "nif": c.nif or None,
                "admite_apuntes": c.imputable} for c in qs.order_by("codigo")[:p.limite]]
    avisos = [f"Se muestran {len(cuentas)} de {total} cuentas; afina la búsqueda."] if total > len(cuentas) else []
    return Resultado(EstadoLlamada.OK if cuentas else EstadoLlamada.VACIO,
                     datos={"cuentas": cuentas, "total": total}, fuente="Contabilidad (cuadro_cuentas): plan de cuentas",
                     fecha_datos=consultado_en(), avisos=avisos)


# --------------------------------------------------------------------------- #
class SaldoCuentaArgs(_Args):
    cuenta: str = CODIGO
    hasta: date | None = Field(None, description="Fecha final incluida (AAAA-MM-DD). Por defecto, hoy.")
    desde: date | None = Field(None, description=(
        "Inicio del periodo (AAAA-MM-DD). Por defecto, el primer día del ejercicio. Debe caer en el mismo "
        "ejercicio que «hasta»."))
    solo_contabilizados: bool = Field(True, description=(
        "True (por defecto) = solo asientos contabilizados, el saldo oficial. False incluye borradores."))


@herramienta(
    "saldo_cuenta",
    descripcion="Saldo de una cuenta o grupo de cuentas en una fecha: saldo de apertura (al inicio del periodo), "
                "movimiento del periodo (Debe, Haber) y saldo final, con su naturaleza (deudor/acreedor).",
    argumentos=SaldoCuentaArgs, dominio="contabilidad", permisos=PERMISOS,
)
def saldo_cuenta(p: SaldoCuentaArgs, ctx):
    ejercicio, desde, hasta = _periodo(p.desde, p.hasta, ctx.hoy)
    cuenta = _cuenta(p.cuenta)

    base = (Apunte.objects.filter(asiento__ejercicio=ejercicio, cuenta__codigo__startswith=p.cuenta,
                                  asiento__fecha__lte=hasta)
            .exclude(asiento__tipo=Asiento.Tipo.CIERRE))
    borradores = base.filter(asiento__estado=Asiento.Estado.BORRADOR).count()
    if p.solo_contabilizados:
        base = base.filter(asiento__estado=Asiento.Estado.CONTABILIZADO)

    # Apertura = asiento de apertura + todo lo anterior a «desde». Periodo = el resto hasta «hasta».
    es_apertura = Q(asiento__tipo=Asiento.Tipo.APERTURA)
    previo = base.filter(es_apertura | Q(asiento__fecha__lt=desde)).aggregate(
        d=Sum("debe"), h=Sum("haber"), n=Count("id"))
    periodo = base.exclude(es_apertura).filter(asiento__fecha__gte=desde).aggregate(
        d=Sum("debe"), h=Sum("haber"), n=Count("id"))

    d0, h0 = previo["d"] or CERO, previo["h"] or CERO
    d1, h1 = periodo["d"] or CERO, periodo["h"] or CERO
    avisos = _avisos_comunes(ejercicio, borradores, p.solo_contabilizados)
    if not (previo["n"] or periodo["n"]):
        avisos.insert(0, "La cuenta no tiene apuntes en este ejercicio hasta esa fecha.")

    return Resultado(
        datos={
            "entidad": _entidad(),
            "moneda": MONEDA_CONTABILIDAD,
            "cuenta": cuenta,
            "ejercicio": ejercicio.anio,
            "periodo": {"desde": desde, "hasta": hasta},
            "saldo_apertura": saldo_dict(d0, h0),
            "movimiento_periodo": {"debe": importe(d1), "haber": importe(h1), "neto": importe(d1 - h1),
                                   "apuntes": periodo["n"]},
            "saldo_final": saldo_dict(d0 + d1, h0 + h1),
            "solo_contabilizados": p.solo_contabilizados,
            "definicion": "Saldo = Debe - Haber; positivo deudor, negativo acreedor. Excluye el asiento de cierre.",
            "enlace_extracto": _enlace_extracto(ejercicio, p.cuenta, desde, hasta, p.solo_contabilizados),
        },
        fuente=FUENTE, fecha_datos=f"{hasta.isoformat()} (consultado {consultado_en()})", avisos=avisos,
    )


# --------------------------------------------------------------------------- #
class SumasSaldosArgs(_Args):
    ejercicio: int | None = Field(None, ge=2000, le=2100, description="Año del ejercicio. Por defecto, el actual.")
    prefijo: str = Field("", pattern=r"^\d{0,12}$", description="Solo cuentas que empiezan por este código.")
    solo_contabilizados: bool = True
    limite: int = Field(100, ge=1, le=300, description="Máximo de filas devueltas (los totales son siempre completos).")


@herramienta(
    "balance_sumas_saldos",
    descripcion="Balance de sumas y saldos (trial balance) de un ejercicio a la fecha actual de los apuntes: "
                "Debe, Haber y saldo por subcuenta, con totales.",
    argumentos=SumasSaldosArgs, dominio="contabilidad", permisos=PERMISOS,
)
def balance_sumas_saldos(p: SumasSaldosArgs, ctx):
    ejercicio = _ejercicio_por_anio(p.ejercicio, ctx.hoy)
    filas, totales = sumas_y_saldos(ejercicio, p.prefijo, solo_contabilizados=p.solo_contabilizados)
    borradores = Apunte.objects.filter(asiento__ejercicio=ejercicio, asiento__estado=Asiento.Estado.BORRADOR)
    if p.prefijo:
        borradores = borradores.filter(cuenta__codigo__startswith=p.prefijo)
    avisos = _avisos_comunes(ejercicio, borradores.count(), p.solo_contabilizados)
    if len(filas) > p.limite:
        avisos.append(f"Se muestran {p.limite} de {len(filas)} subcuentas; los totales incluyen todas.")
    return Resultado(
        EstadoLlamada.OK if filas else EstadoLlamada.VACIO,
        datos={
            "entidad": _entidad(), "moneda": MONEDA_CONTABILIDAD, "ejercicio": ejercicio.anio,
            "prefijo": p.prefijo or None, "solo_contabilizados": p.solo_contabilizados,
            "filas": [{"codigo": f["codigo"], "nombre": f["nombre"], **saldo_dict(f["debe"], f["haber"])}
                      for f in filas[:p.limite]],
            "totales": {k: importe(v) for k, v in totales.items()},
            "cuadra": totales["debe"] == totales["haber"],
            "enlace": f"{reverse('cuadro_cuentas:extractos')}?{urlencode({'ejercicio': ejercicio.anio, 'prefijo': p.prefijo})}",
        },
        fuente=FUENTE, fecha_datos=consultado_en(), avisos=avisos,
    )


# --------------------------------------------------------------------------- #
class ExtractoArgs(_Args):
    cuenta: str = CODIGO
    desde: date | None = Field(None, description="Inicio (AAAA-MM-DD). Por defecto, inicio del ejercicio.")
    hasta: date | None = Field(None, description="Fin incluido (AAAA-MM-DD). Por defecto, hoy.")
    solo_contabilizados: bool = True
    limite: int = Field(30, ge=1, le=200, description="Máximo de movimientos devueltos (los más recientes).")

    @model_validator(mode="after")
    def _orden(self):
        if self.desde and self.hasta and self.desde > self.hasta:
            raise ValueError("«desde» es posterior a «hasta».")
        return self


@herramienta(
    "extracto_cuenta",
    descripcion="Libro mayor de una cuenta: movimientos (fecha, asiento, concepto, contrapartida, Debe, Haber) "
                "con el saldo acumulado, dentro de un ejercicio.",
    argumentos=ExtractoArgs, dominio="contabilidad", permisos=PERMISOS,
)
def extracto_cuenta(p: ExtractoArgs, ctx):
    ejercicio, desde, hasta = _periodo(p.desde, p.hasta, ctx.hoy)
    cuenta = _cuenta(p.cuenta)
    ext = extracto(ejercicio, p.cuenta, desde if desde > ejercicio.fecha_inicio else None, hasta,
                   solo_contabilizados=p.solo_contabilizados)
    lineas = ext.lineas[-p.limite:]
    borradores = Apunte.objects.filter(asiento__ejercicio=ejercicio, cuenta__codigo__startswith=p.cuenta,
                                       asiento__estado=Asiento.Estado.BORRADOR,
                                       asiento__fecha__range=(desde, hasta)).count()
    avisos = _avisos_comunes(ejercicio, borradores, p.solo_contabilizados)
    if len(ext.lineas) > len(lineas):
        avisos.append(f"Se muestran los últimos {len(lineas)} de {len(ext.lineas)} movimientos; "
                      "los totales incluyen todos.")
    return Resultado(
        EstadoLlamada.OK if ext.lineas else EstadoLlamada.VACIO,
        datos={
            "entidad": _entidad(), "moneda": MONEDA_CONTABILIDAD, "cuenta": cuenta, "ejercicio": ejercicio.anio,
            "periodo": {"desde": desde, "hasta": hasta},
            "saldo_inicial": saldo_neto(ext.saldo_inicial),
            "total_debe": importe(ext.total_debe), "total_haber": importe(ext.total_haber),
            "saldo_final": saldo_neto(ext.saldo_final),
            "movimientos": [{
                "fecha": linea["apunte"].asiento.fecha,
                "asiento": linea["apunte"].asiento.numero,
                "estado_asiento": linea["apunte"].asiento.estado,
                "subcuenta": linea["apunte"].cuenta.codigo,
                "concepto": linea["apunte"].concepto or linea["apunte"].asiento.concepto,
                "documento": linea["apunte"].asiento.referencia_origen or None,
                "contrapartida": linea["apunte"].contrapartida.codigo if linea["apunte"].contrapartida else None,
                "debe": importe(linea["apunte"].debe), "haber": importe(linea["apunte"].haber),
                "saldo": importe(linea["saldo"]),
            } for linea in lineas],
            "enlace_extracto": _enlace_extracto(ejercicio, p.cuenta, desde, hasta, p.solo_contabilizados),
        },
        fuente=FUENTE, fecha_datos=f"{hasta.isoformat()} (consultado {consultado_en()})", avisos=avisos,
    )
