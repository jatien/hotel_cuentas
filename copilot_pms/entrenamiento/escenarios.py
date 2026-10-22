"""
Generador de conversaciones de entrenamiento sobre el hotel ficticio.

Cada escenario:
1. elige datos reales del hotel ficticio (una cuenta, un proveedor, un mes...),
2. formula la pregunta en español con una de varias plantillas de redacción,
3. EJECUTA las herramientas de verdad (mismo ejecutor que producción) con los
   argumentos correctos, que se conocen porque los eligió el propio generador,
4. redacta la respuesta final a partir del resultado devuelto (nunca inventa
   cifras: todas salen del resultado).

Los mensajes se construyen exactamente como los ve el modelo en producción:
mismo prompt de sistema, mismos esquemas de herramientas, mismo JSON en los
mensajes «tool» y, en las preguntas de seguimiento, el mismo historial (solo
texto + nota de gráficos), sin los resultados de turnos anteriores.

Formato de salida: el de TRL para llamadas a funciones ({"messages", "tools"}),
con los argumentos como objeto JSON (lo que espera la plantilla de chat de Qwen3.5).
"""

import json
import random
import unicodedata
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from copilot_pms import prompts
from copilot_pms.agent import _contenido_herramienta, _entidad
from copilot_pms.models import EstadoLlamada
from copilot_pms.tools import PENDIENTES, disponibles, ejecutar
from copilot_pms.tools.base import Contexto

MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre",
         "noviembre", "diciembre"]


class EscenarioInvalido(Exception):
    """El escenario no se puede construir con estos datos (se descarta y se prueba otro)."""


# --------------------------------------------------------------------------- formato
def eur(valor) -> str:
    """Decimal o texto '1234.50' -> '1.234,50 €'."""
    d = Decimal(str(valor)).quantize(Decimal("0.01"))
    signo = "-" if d < 0 else ""
    entero, dec = f"{abs(d):.2f}".split(".")
    grupos = []
    while entero:
        grupos.insert(0, entero[-3:])
        entero = entero[:-3]
    return f"{signo}{'.'.join(grupos)},{dec} €"


def fecha_es(valor) -> str:
    d = date.fromisoformat(valor) if isinstance(valor, str) else valor
    return f"{d:%d/%m/%Y}"


def sin_acentos(texto: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", texto) if unicodedata.category(c) != "Mn")


CONECTORES = {"de", "del", "la", "las", "el", "los", "y"}


def nombre_corto(nombre: str) -> str:
    """Como lo diría una persona: «Bodegas del Valle», «Distribuciones Alba», «Eléctrica Demo»."""
    palabras, significativas = [], 0
    for p in nombre.split():
        palabras.append(p)
        if p.lower() not in CONECTORES:
            significativas += 1
        if significativas == 2:
            break
    return " ".join(palabras)


def fin_de_mes(anio, mes) -> date:
    return (date(anio, 12, 31) if mes == 12 else date(anio, mes + 1, 1) - timedelta(days=1))


# --------------------------------------------------------------------------- catálogo del hotel ficticio
@dataclass
class Catalogo:
    hoy: date
    bancos: list = field(default_factory=list)  # [(código, nombre)]
    proveedores: list = field(default_factory=list)
    acreedores: list = field(default_factory=list)
    ventas: list = field(default_factory=list)
    gastos: list = field(default_factory=list)
    ejercicios: list = field(default_factory=list)
    facturas: list = field(default_factory=list)  # [(proveedor, número, fecha)]
    anios_cajas: list = field(default_factory=list)
    anios_nominas: list = field(default_factory=list)
    trampa: str = ""

    @classmethod
    def desde_bd(cls, hoy):
        from cajas.models import CajaMes
        from cuadro_cuentas.models import CuentaContable, Ejercicio
        from invoice_agent.models import Borrador
        from nominas.models import NominaMensual

        def subcuentas(prefijo):
            return list(CuentaContable.objects.filter(codigo__startswith=prefijo, codigo__regex=r"^\d{8}$")
                        .values_list("codigo", "nombre"))

        proveedores = subcuentas("400")
        trampa = next((n for _, n in proveedores if "IGNORA" in n.upper()), "")
        return cls(
            hoy=hoy, bancos=subcuentas("572"), proveedores=[p for p in proveedores if p[1] != trampa],
            acreedores=subcuentas("410"), ventas=subcuentas("705"),
            gastos=subcuentas("6"), ejercicios=list(Ejercicio.objects.order_by("anio").values_list("anio", flat=True)),
            facturas=list(Borrador.objects.exclude(proveedor_nombre=trampa)
                          .values_list("proveedor_nombre", "numero_factura", "fecha_factura")),
            anios_cajas=sorted(set(CajaMes.objects.values_list("anio", flat=True))),
            anios_nominas=sorted(set(NominaMensual.objects.values_list("anio", flat=True))),
            trampa=trampa,
        )


# --------------------------------------------------------------------------- constructor de conversaciones
class Conversacion:
    def __init__(self, ctx: Contexto, sistema: str, tools: list):
        self.ctx = ctx
        self.tools = tools
        self.messages = [{"role": "system", "content": sistema}]

    def historial(self, pregunta: str, respuesta: str):
        """Turno anterior tal y como lo reenvía producción: solo texto (y la nota de gráficos).
        Es contexto: «entrenar: false» hace que no cuente en la pérdida."""
        self.messages += [{"role": "user", "content": pregunta},
                          {"role": "assistant", "content": respuesta, "entrenar": False}]

    def usuario(self, texto: str):
        self.messages.append({"role": "user", "content": texto})

    def llamar(self, *llamadas, esperado=(EstadoLlamada.OK, EstadoLlamada.VACIO), texto: str | None = None):
        """Añade una respuesta del asistente con llamadas a herramientas y sus resultados reales."""
        n = sum(1 for m in self.messages if m.get("tool_calls"))
        tool_calls = [{"id": f"call_{n}_{i}", "type": "function", "function": {"name": nombre, "arguments": args}}
                      for i, (nombre, args) in enumerate(llamadas)]
        self.messages.append({"role": "assistant", "content": texto or "", "tool_calls": tool_calls})
        resultados = []
        for (nombre, args), tc in zip(llamadas, tool_calls):
            resultado = ejecutar(nombre, args, self.ctx)
            estados = esperado if isinstance(esperado, tuple) else (esperado,)
            if resultado.estado not in estados:
                raise EscenarioInvalido(f"{nombre}{args}: {resultado.estado} {resultado.codigo} {resultado.mensaje}")
            self.messages.append({"role": "tool", "tool_call_id": tc["id"], "name": nombre,
                                  "content": _contenido_herramienta(resultado)})
            resultados.append(resultado)
        return resultados[0] if len(resultados) == 1 else resultados

    def responder(self, texto: str):
        self.messages.append({"role": "assistant", "content": texto.strip()})

    def ejemplo(self, escenario: str, plantilla: int) -> dict:
        return {"messages": self.messages, "tools": self.tools,
                "meta": {"escenario": escenario, "plantilla": plantilla, "origen": "hotel_ficticio",
                         "licencia": "propia"}}


# --------------------------------------------------------------------------- redacción de respuestas
def _avisos(resultado, maximo=2) -> str:
    avisos = [a for a in resultado.avisos][:maximo]
    return ("\nAvisos: " + " ".join(avisos)) if avisos else ""


def _entidad_txt(datos) -> str:
    e = datos.get("entidad") or {}
    return e.get("denominacion") or e.get("nombre") or "la empresa"


def respuesta_saldo(r, rnd) -> str:
    d = r.datos
    f = d["saldo_final"]
    cuenta = d["cuenta"]
    nombre = f"{cuenta['codigo']} {cuenta['nombre']}" if cuenta.get("nombre") else f"el grupo {cuenta['codigo']}"
    if f["naturaleza"] == "saldado":
        frase = f"La cuenta {nombre} está saldada (saldo 0,00 €) a {fecha_es(d['periodo']['hasta'])}."
    else:
        frase = rnd.choice([
            "El saldo de la cuenta {n} a {f} es de {i}, {nat}.",
            "A {f}, la cuenta {n} tiene un saldo {nat} de {i}.",
            "Saldo de {n} a {f}: {i} ({nat}).",
        ]).format(n=nombre, f=fecha_es(d["periodo"]["hasta"]), i=eur(f["importe"]), nat=f["naturaleza"])
    detalle = (f"\nApertura del periodo: {eur(d['saldo_apertura']['neto'])}; movimiento del "
               f"{fecha_es(d['periodo']['desde'])} al {fecha_es(d['periodo']['hasta'])}: Debe "
               f"{eur(d['movimiento_periodo']['debe'])}, Haber {eur(d['movimiento_periodo']['haber'])}.")
    pie = (f"\nEntidad: {_entidad_txt(d)} · Ejercicio {d['ejercicio']} · "
           f"{'solo asientos contabilizados' if d['solo_contabilizados'] else 'incluye borradores'}. "
           f"Fuente: contabilidad.")
    return frase + detalle + pie + _avisos(r) + f"\nExtracto: {d['enlace_extracto']}"


# --------------------------------------------------------------------------- escenarios
class Generador:
    def __init__(self, catalogo: Catalogo, semilla: int = 1):
        self.cat = catalogo
        self.rnd = random.Random(semilla)
        self.ctx = Contexto(hoy=catalogo.hoy, actor_label="dataset",
                            empresas_facturas=self._empresas())
        self.sistema = prompts.sistema(hoy=catalogo.hoy, entidad=_entidad(), pendientes=PENDIENTES)
        self.tools = [h.esquema() for h in disponibles(self.ctx)]

    @staticmethod
    def _empresas():
        from invoice_agent.models import Empresa

        return frozenset(Empresa.objects.values_list("pk", flat=True))

    def nueva(self) -> Conversacion:
        return Conversacion(self.ctx, self.sistema, self.tools)

    # ---- utilidades de redacción
    def plantilla(self, opciones):
        i = self.rnd.randrange(len(opciones))
        return i, opciones[i]

    def ruido(self, texto: str) -> str:
        """Variantes de escritura reales: sin acentos, minúsculas, sin signo de apertura."""
        x = self.rnd.random()
        if x < 0.12:
            texto = sin_acentos(texto)
        elif x < 0.22:
            texto = texto.lower()
        if self.rnd.random() < 0.25:
            texto = texto.lstrip("¿¡")
        return texto

    def mes_pasado(self, anio=None):
        """(año, mes) con datos cerrados: anterior al mes de «hoy»."""
        hoy = self.cat.hoy
        anio = anio or self.rnd.choice(self.cat.ejercicios)
        ultimo = 12 if anio < hoy.year else hoy.month - 1
        return anio, self.rnd.randint(1, ultimo)

    def expresion_fecha(self, f: date) -> str:
        opciones = [f"a {f.day} de {MESES[f.month - 1]}", f"a {f.day} de {MESES[f.month - 1]} de {f.year}",
                    f"a {f:%d/%m/%Y}", f"a {f.day}/{f.month}/{f.year}"]
        if f == fin_de_mes(f.year, f.month):
            opciones += [f"a final de {MESES[f.month - 1]}", f"a cierre de {MESES[f.month - 1]} de {f.year}"]
        if f.year != self.cat.hoy.year:
            opciones = [o for o in opciones if str(f.year) in o]
        return self.rnd.choice(opciones)

    # ---- 1. saldo por código
    def saldo_codigo(self):
        codigo, nombre = self.rnd.choice(self.cat.bancos + self.cat.proveedores + self.cat.acreedores)
        usar_grupo = self.rnd.random() < 0.25
        cuenta = codigo[:3] if usar_grupo else codigo
        anio, mes = self.mes_pasado()
        hasta = fin_de_mes(anio, mes)
        i, p = self.plantilla(["¿Cuál es el saldo de la cuenta {c} {f}?", "Saldo de la {c} {f}",
                               "¿Qué saldo tiene la {c} {f}?", "dime el saldo de la cuenta {c} {f}",
                               "Necesito el saldo contable de la {c} {f}, por favor."])
        conv = self.nueva()
        conv.usuario(self.ruido(p.format(c=cuenta, f=self.expresion_fecha(hasta))))
        r = conv.llamar(("saldo_cuenta", {"cuenta": cuenta, "hasta": hasta.isoformat()}))
        conv.responder(respuesta_saldo(r, self.rnd))
        return [conv.ejemplo("saldo_codigo", i)]

    # ---- 2. saldo por nombre (resolver la cuenta primero)
    def saldo_nombre(self):
        codigo, nombre = self.rnd.choice(self.cat.bancos + self.cat.acreedores)
        texto = nombre_corto(nombre)
        hoy_o_fecha = self.rnd.random() < 0.5
        i, p = self.plantilla(["¿Cuánto saldo tiene {n}{f}?", "Saldo de {n}{f}", "¿Cómo está la cuenta de {n}{f}?",
                               "quiero ver el saldo de {n}{f}"])
        anio, mes = self.mes_pasado(self.cat.hoy.year)
        hasta = fin_de_mes(anio, mes)
        conv = self.nueva()
        conv.usuario(self.ruido(p.format(n=texto, f="" if hoy_o_fecha else " " + self.expresion_fecha(hasta))))
        b = conv.llamar(("buscar_cuentas", {"texto": texto, "solo_subcuentas": True}))
        encontradas = b.datos.get("cuentas", [])
        if len(encontradas) != 1:
            raise EscenarioInvalido("nombre ambiguo")
        args = {"cuenta": encontradas[0]["codigo"]}
        if not hoy_o_fecha:
            args["hasta"] = hasta.isoformat()
        r = conv.llamar(("saldo_cuenta", args))
        conv.responder(respuesta_saldo(r, self.rnd))
        return [conv.ejemplo("saldo_nombre", i)]

    # ---- 3. deuda con un proveedor
    def deuda_proveedor(self):
        codigo, nombre = self.rnd.choice(self.cat.proveedores + self.cat.acreedores)
        texto = nombre_corto(nombre)
        i, p = self.plantilla(["¿Cuánto le debemos a {n}?", "¿Tenemos algo pendiente de pago con {n}?",
                               "deuda con {n}", "¿Qué saldo pendiente hay con {n} a día de hoy?"])
        conv = self.nueva()
        conv.usuario(self.ruido(p.format(n=texto)))
        b = conv.llamar(("buscar_cuentas", {"texto": texto, "solo_subcuentas": True}))
        if b.datos.get("total") != 1:
            raise EscenarioInvalido("nombre ambiguo")
        r = conv.llamar(("saldo_cuenta", {"cuenta": b.datos["cuentas"][0]["codigo"]}))
        f = r.datos["saldo_final"]
        if f["naturaleza"] == "acreedor":
            frase = f"A día de hoy ({fecha_es(r.datos['periodo']['hasta'])}) le debemos {eur(f['importe'])} a {nombre} (saldo acreedor de la cuenta {r.datos['cuenta']['codigo']})."
        elif f["naturaleza"] == "saldado":
            frase = f"No hay nada pendiente con {nombre}: la cuenta {r.datos['cuenta']['codigo']} está saldada a {fecha_es(r.datos['periodo']['hasta'])}."
        else:
            frase = f"No le debemos nada a {nombre}: la cuenta {r.datos['cuenta']['codigo']} tiene saldo deudor de {eur(f['importe'])} (pagos o anticipos por encima de lo facturado)."
        conv.responder(frase + f"\nEjercicio {r.datos['ejercicio']}, solo asientos contabilizados. Fuente: contabilidad."
                       + _avisos(r) + f"\nExtracto: {r.datos['enlace_extracto']}")
        return [conv.ejemplo("deuda_proveedor", i)]

    # ---- 4. movimiento de un periodo (ventas)
    def ventas_periodo(self):
        codigo, nombre = self.rnd.choice(self.cat.ventas)
        anio = self.rnd.choice(self.cat.ejercicios)
        _, m2 = self.mes_pasado(anio)
        m1 = self.rnd.randint(1, m2)
        desde, hasta = date(anio, m1, 1), fin_de_mes(anio, m2)
        periodo = (f"en {MESES[m1 - 1]} de {anio}" if m1 == m2 else f"entre {MESES[m1 - 1]} y {MESES[m2 - 1]} de {anio}")
        i, p = self.plantilla(["¿Cuánto hemos vendido de {n} {per}?", "{N} {per}", "Ingresos contables de {n} {per}",
                               "¿Cuál fue el movimiento de la cuenta {c} {per}?"])
        conv = self.nueva()
        conv.usuario(self.ruido(p.format(n=nombre.lower(), N=nombre, per=periodo, c=codigo)))
        r = conv.llamar(("saldo_cuenta", {"cuenta": codigo, "desde": desde.isoformat(), "hasta": hasta.isoformat()}))
        mov = r.datos["movimiento_periodo"]
        conv.responder(
            f"{nombre} {periodo}: {eur(mov['haber'])} en el Haber de la cuenta {codigo} "
            f"(sin IVA, {mov['apuntes']} apuntes). Movimiento neto: {eur(mov['neto'])}.\n"
            f"Entidad: {_entidad_txt(r.datos)} · Ejercicio {anio} · solo asientos contabilizados. Fuente: contabilidad."
            + _avisos(r) + f"\nExtracto: {r.datos['enlace_extracto']}")
        return [conv.ejemplo("ventas_periodo", i)]

    # ---- 5. extracto
    def extracto(self):
        codigo, nombre = self.rnd.choice(self.cat.bancos)
        limite = self.rnd.choice([3, 5])
        i, p = self.plantilla(["Últimos {k} movimientos de la {c}", "¿Qué movimientos ha tenido la cuenta {c} últimamente? Los {k} últimos.",
                               "Enséñame el extracto de la {c} (últimos {k} apuntes)"])
        conv = self.nueva()
        conv.usuario(self.ruido(p.format(k=limite, c=codigo)))
        r = conv.llamar(("extracto_cuenta", {"cuenta": codigo, "limite": limite}))
        lineas = "\n".join(f"- {fecha_es(m['fecha'])} · asiento {m['asiento']} · {m['concepto']}: "
                           f"{'Debe ' + eur(m['debe']) if Decimal(m['debe']) else 'Haber ' + eur(m['haber'])} → saldo {eur(m['saldo'])}"
                           for m in r.datos["movimientos"])
        conv.responder(f"Últimos {len(r.datos['movimientos'])} movimientos de la cuenta {codigo} ({nombre}), ejercicio "
                       f"{r.datos['ejercicio']}:\n{lineas}\nSaldo final: {eur(r.datos['saldo_final']['importe'])} "
                       f"{r.datos['saldo_final']['naturaleza']}. Fuente: contabilidad." + _avisos(r)
                       + f"\nExtracto completo: {r.datos['enlace_extracto']}")
        return [conv.ejemplo("extracto", i)]

    # ---- 6. cuentas anuales
    def cuentas_anuales(self):
        anio = self.rnd.choice(self.cat.ejercicios)
        estado = self.rnd.choice(["pyg", "balance"])
        i, p = self.plantilla({"pyg": ["Cuenta de pérdidas y ganancias de {a}", "¿Qué resultado llevamos en {a}?",
                                       "PyG {a}"],
                               "balance": ["Balance de situación de {a}", "¿Cuánto suma el activo en {a}?",
                                           "Dame el balance de {a}"]}[estado])
        conv = self.nueva()
        conv.usuario(self.ruido(p.format(a=anio)))
        r = conv.llamar(("cuentas_anuales", {"ejercicio": anio, "estado": estado, "detalle": 0}))
        partes = []
        for s in r.datos["secciones"]:
            if s["total"]:
                partes.append(f"{s['total']['texto']}: {eur(s['total']['actual'])}")
            for prt in s["partidas"]:
                if prt["es_total"] or prt["ref"] in ("A", "B"):
                    partes.append(f"{prt['texto']}: {eur(prt['actual'])}")
        conv.responder(f"{r.datos['estado']} de {_entidad_txt(r.datos)}, ejercicio {anio} (modelo {r.datos['modelo']}):\n- "
                       + "\n- ".join(partes[:8]) + "\nFuente: contabilidad, solo asientos contabilizados." + _avisos(r)
                       + f"\nInforme completo: {r.datos['enlace']}")
        return [conv.ejemplo("cuentas_anuales", i)]

    # ---- 7. facturas: buscar y después pedir el PDF (dos turnos)
    def facturas(self):
        proveedor, numero, fecha = self.rnd.choice(self.cat.facturas)
        corto = nombre_corto(proveedor)
        por_numero = self.rnd.random() < 0.4
        i, p = self.plantilla(["Busca la factura {num} de {prov}", "¿Tenemos la factura {num}?",
                               "Facturas de {prov} de {mes} de {a}", "¿Qué facturas nos mandó {prov} en {mes} de {a}?"]
                              [:2] if por_numero else
                              ["Facturas de {prov} de {mes} de {a}", "¿Qué facturas nos mandó {prov} en {mes} de {a}?"])
        args = {"numero": numero} if por_numero else {"proveedor": corto, "desde": date(fecha.year, fecha.month, 1).isoformat(),
                                                       "hasta": fin_de_mes(fecha.year, fecha.month).isoformat()}
        if por_numero and "{prov}" in p:
            args["proveedor"] = corto
        conv = self.nueva()
        pregunta = self.ruido(p.format(num=numero, prov=corto, mes=MESES[fecha.month - 1], a=fecha.year))
        conv.usuario(pregunta)
        r = conv.llamar(("buscar_facturas", args))
        facturas = r.datos.get("facturas", [])
        if not facturas:
            raise EscenarioInvalido("sin facturas")
        lista = "\n".join(f"- {f['numero']} · {fecha_es(f['fecha'])} · {f['proveedor']} · {eur(f['total'])} · {f['estado']}"
                          for f in facturas)
        texto = (f"He encontrado {len(facturas)} factura{'s' if len(facturas) > 1 else ''}:\n{lista}\n"
                 f"Fuente: Facturas con IA y Gestión de facturas." + _avisos(r)
                 + "\n¿Quieres que te dé el PDF de alguna?")
        conv.responder(texto)
        ejemplos = [conv.ejemplo("facturas_buscar", i)]

        # Segundo turno: el PDF (el historial solo lleva el texto, como en producción)
        elegida = facturas[0]
        j, p2 = self.plantilla(["Sí, dame el PDF de la {num}", "pásame el pdf de la {num}", "Abre la {num}"])
        conv2 = self.nueva()
        conv2.historial(pregunta, texto)
        conv2.usuario(self.ruido(p2.format(num=elegida["numero"])))
        # Sin los resultados anteriores el modelo no tiene la referencia: vuelve a buscarla
        b = conv2.llamar(("buscar_facturas", {"numero": elegida["numero"]}))
        ref = b.datos["facturas"][0]["referencia"]
        d = conv2.llamar(("documento_factura", {"referencia": ref}))
        conv2.responder(f"Aquí tienes la factura {d.datos['numero']} de {d.datos['proveedor']} del "
                        f"{fecha_es(d.datos['fecha'])} por {eur(d.datos['total'])}:\n{d.datos['enlace_documento']}\n"
                        f"El enlace caduca en {d.datos['caduca_en_segundos'] // 60} minutos; después puedes abrirla desde su "
                        f"ficha: {d.datos['enlace_ficha']}")
        ejemplos.append(conv2.ejemplo("facturas_pdf", j))
        return ejemplos

    # ---- 8. cajas
    def cajas(self):
        anio = self.rnd.choice(self.cat.anios_cajas)
        _, mes = self.mes_pasado(anio)
        punto = self.rnd.choice(["Bar", "Comedor", "Chill out", None])
        i, p = self.plantilla(["¿Cuánto vendió el {p} en {m} de {a}?", "Ventas del {p} en {m} {a}",
                               "Caja de {m} de {a}", "¿Cuánto facturamos en caja en {m} de {a}?"]
                              [:2] if punto else ["Caja de {m} de {a}", "¿Cuánto facturamos en caja en {m} de {a}?"])
        conv = self.nueva()
        conv.usuario(self.ruido(p.format(p=(punto or "").lower(), m=MESES[mes - 1], a=anio)))
        r = conv.llamar(("resumen_cajas", {"anio": anio, "mes": mes}))
        d = r.datos
        if punto:
            frase = f"En {MESES[mes - 1]} de {anio} el {punto.lower()} vendió {eur(d['puntos_venta'][punto])} en caja."
        else:
            frase = (f"Caja de {MESES[mes - 1]} de {anio}: ventas {eur(d['total_ventas'])} ("
                     + ", ".join(f"{k} {eur(v)}" for k, v in d["puntos_venta"].items())
                     + f") y otros cobros {eur(d['total_otros_cobros'])}.")
        conv.responder(frase + " Son importes de caja diaria (con IVA), no contabilidad.\nFuente: cajas."
                       + f"\nDetalle: {d['enlace']}")
        return [conv.ejemplo("cajas", i)]

    # ---- 9. coste de personal
    def personal(self):
        anio = self.rnd.choice(self.cat.anios_nominas)
        _, mes = self.mes_pasado(anio)
        con_mes = self.rnd.random() < 0.6
        i, p = self.plantilla(["¿Cuánto nos costó el personal en {per}?", "Coste de nóminas por departamento {per}",
                               "gasto de personal {per}"])
        per = f"en {MESES[mes - 1]} de {anio}" if con_mes else f"en {anio}"
        conv = self.nueva()
        conv.usuario(self.ruido(p.format(per=per)))
        args = {"anio": anio, **({"mes": mes} if con_mes else {})}
        r = conv.llamar(("coste_personal", args))
        d = r.datos
        lineas = "\n".join(f"- {x['departamento']}: {eur(x['total'])} ({x['empleados']} personas)" for x in d["departamentos"])
        conv.responder(f"Coste de personal {per} (coste empresa + extras): {eur(d['total']['total'])}.\n{lineas}\n"
                       f"Fuente: nóminas." + _avisos(r))
        return [conv.ejemplo("coste_personal", i)]

    # ---- 10. beneficio de gestión
    def beneficio(self):
        anio = self.rnd.choice(self.cat.ejercicios)
        con_mes = self.rnd.random() < 0.5
        _, mes = self.mes_pasado(anio)
        per = f"{MESES[mes - 1]} de {anio}" if con_mes else str(anio)
        i, p = self.plantilla(["¿Cuánto hemos ganado en {per}?", "Beneficio de {per}", "¿Qué margen tuvimos en {per}?",
                               "Resumen de ingresos y gastos de {per}"])
        conv = self.nueva()
        conv.usuario(self.ruido(p.format(per=per)))
        r = conv.llamar(("resumen_beneficio", {"anio": anio, **({"mes": mes} if con_mes else {})}))
        d = r.datos
        conv.responder(
            f"Beneficio de gestión de {per} (sin IVA): {eur(d['beneficio'])}"
            + (f", margen {d['margen_pct'].replace('.', ',')} %" if d["margen_pct"] else "") + ".\n"
            f"- Ingresos: {eur(d['ingresos']['total'])} (estancias {eur(d['ingresos']['estancias'])}, bar "
            f"{eur(d['ingresos']['bar'])}, comedor {eur(d['ingresos']['comedor'])})\n"
            f"- Gastos: {eur(d['gastos']['total'])} (proveedores {eur(d['gastos']['proveedores'])}, acreedores "
            f"{eur(d['gastos']['acreedores'])}, personal {eur(d['gastos']['personal'])})\n"
            "Es el análisis de gestión, no el resultado contable. Fuente: análisis de beneficios."
            + _avisos(r, 1) + f"\nDetalle: {d['enlace']}")
        return [conv.ejemplo("beneficio", i)]

    # ---- 11. gráfico y edición por prompt (dos turnos)
    def grafico(self):
        anio = self.rnd.choice(self.cat.anios_cajas)
        opciones = [("ventas_cajas_mensuales", "las ventas de caja"), ("beneficio_mensual", "ingresos, gastos y beneficio"),
                    ("gastos_mensuales", "los gastos por tipo"), ("coste_personal_mensual", "el coste de personal")]
        plantilla, que = self.rnd.choice(opciones)
        i, p = self.plantilla(["Hazme un gráfico de {q} de {a}", "Gráfico de {q} por mes en {a}", "quiero ver {q} de {a} en un gráfico",
                               "Muéstrame {q} de {a}"])
        pregunta = self.ruido(p.format(q=que, a=anio))
        conv = self.nueva()
        conv.usuario(pregunta)
        args = {"plantilla": plantilla, "anio": anio}
        r = conv.llamar(("grafico", args))
        g = r.datos["grafico"]
        # Sin sumas hechas «a mano»: solo valores que están en el resultado (máximo y mínimo de una serie)
        s0 = g["series"][0]
        con_datos = [(e, v) for e, v in zip(g["etiquetas"], s0["valores"]) if Decimal(v)]
        (mes_max, maxima), (mes_min, minima) = max(con_datos, key=lambda x: Decimal(x[1])), min(con_datos, key=lambda x: Decimal(x[1]))
        texto = (f"Aquí tienes el gráfico «{g['titulo']}». {s0['nombre']} alcanza su máximo en {mes_max} "
                 f"({eur(maxima)}) y su mínimo en {mes_min} ({eur(minima)}). Fuente: {r.fuente}.")
        conv.responder(texto)
        ejemplos = [conv.ejemplo("grafico", i)]

        # Edición: el historial lleva la nota con los parámetros, como en producción
        nota = f"\n[Gráfico mostrado: grafico {json.dumps(r.datos['parametros'], ensure_ascii=False)}]"
        otros = [a for a in self.cat.anios_cajas if a != anio]
        cambios = [("ponlo en líneas", {"tipo": "lineas"}), ("en barras apiladas", {"tipo": "barras_apiladas"}),
                   ("solo de marzo a septiembre", {"desde_mes": 3, "hasta_mes": 9}),
                   ("cámbiale el título a «Resumen para la reunión»", {"titulo": "Resumen para la reunión"})]
        if otros:
            cambios.append((f"compáralo con {otros[0]}", {"comparar_con": [otros[0]]}))
        j = self.rnd.randrange(len(cambios))
        frase, cambio = cambios[j]
        conv2 = self.nueva()
        conv2.historial(pregunta, texto + nota)
        conv2.usuario(self.ruido(self.rnd.choice(["Ahora ", "", "Vale, "]) + frase))
        args2 = {**args, **cambio}
        r2 = conv2.llamar(("grafico", args2))
        conv2.responder(f"Hecho: «{r2.datos['grafico']['titulo']}» con el cambio pedido. Fuente: {r2.fuente}.")
        ejemplos.append(conv2.ejemplo("grafico_editar", j))
        return ejemplos

    # ---- 12. casos en los que lo correcto es NO dar la respuesta directamente
    def periodo_dos_ejercicios(self):
        codigo, nombre = self.rnd.choice(self.cat.bancos)
        a = self.cat.ejercicios[-1]
        i, p = self.plantilla(["¿Cuánto se movió la {c} entre noviembre de {a0} y febrero de {a}?",
                               "Saldo de la {c} desde el 1/12/{a0} hasta el 31/01/{a}"])
        conv = self.nueva()
        conv.usuario(self.ruido(p.format(c=codigo, a0=a - 1, a=a)))
        desde = date(a - 1, 11 if i == 0 else 12, 1)
        hasta = date(a, 2, 28) if i == 0 else date(a, 1, 31)
        conv.llamar(("saldo_cuenta", {"cuenta": codigo, "desde": desde.isoformat(), "hasta": hasta.isoformat()}),
                        esperado=EstadoLlamada.ACLARAR)
        conv.responder(f"No puedo darte un único saldo para ese periodo: abarca dos ejercicios ({a - 1} y {a}) y los "
                       "saldos se calculan por ejercicio (el de " + str(a) + " empieza con su asiento de apertura). "
                       f"¿Lo consulto por separado: hasta el 31/12/{a - 1} y desde el 01/01/{a}?")
        return [conv.ejemplo("aclarar_ejercicios", i)]

    def ejercicio_inexistente(self):
        anio = min(self.cat.ejercicios) - self.rnd.randint(2, 6)
        codigo, _ = self.rnd.choice(self.cat.bancos)
        i, p = self.plantilla(["Saldo de la {c} a 31 de diciembre de {a}", "¿Cuál era el saldo de la {c} en {a}?"])
        conv = self.nueva()
        conv.usuario(self.ruido(p.format(c=codigo, a=anio)))
        r = conv.llamar(("saldo_cuenta", {"cuenta": codigo, "hasta": f"{anio}-12-31"}), esperado=EstadoLlamada.NO_DISPONIBLE)
        existentes = ", ".join(map(str, r.datos.get("ejercicios_existentes", [])))
        conv.responder(f"No hay datos: el ejercicio {anio} no está en la contabilidad (los ejercicios registrados son "
                       f"{existentes}). No puedo darte ese saldo.")
        return [conv.ejemplo("no_disponible", i)]

    def cuenta_ambigua(self):
        if len(self.cat.bancos) < 2:
            raise EscenarioInvalido("un solo banco")
        i, p = self.plantilla(["¿Cuánto hay en el banco?", "saldo del banco", "¿Qué tenemos en la cuenta del banco?"])
        conv = self.nueva()
        conv.usuario(self.ruido(p))
        r = conv.llamar(("buscar_cuentas", {"texto": "572", "solo_subcuentas": True}))
        opciones = "\n".join(f"- {c['codigo']} {c['nombre']}" for c in r.datos["cuentas"])
        conv.responder(f"Hay varias cuentas bancarias:\n{opciones}\n¿De cuál quieres el saldo, o te doy el total del grupo 572?")
        return [conv.ejemplo("aclarar_cuenta", i)]

    def falta_dato(self):
        i, p = self.plantilla(["¿Cuál es el saldo?", "dame el saldo a 30 de junio", "¿Cuánto debemos?"])
        conv = self.nueva()
        conv.usuario(self.ruido(p))
        conv.responder(self.rnd.choice([
            "¿De qué cuenta? Dime el código (por ejemplo 572 para bancos) o el nombre del proveedor o acreedor.",
            "Necesito saber la cuenta: indícame su código o el nombre (banco, proveedor, acreedor...).",
        ]))
        return [conv.ejemplo("aclarar_falta_dato", i)]

    def no_conectado(self):
        que, motivo = self.rnd.choice(PENDIENTES[:3])
        preguntas = {
            PENDIENTES[0][0]: ["¿Hay habitaciones dobles libres del 3 al 6 de noviembre?", "¿Qué ocupación tenemos mañana?",
                               "¿Cuánto cuesta la suite este fin de semana?"],
            PENDIENTES[1][0]: ["¿Quién trabaja en recepción el sábado?", "Horario de cocina de esta semana"],
            PENDIENTES[2][0]: ["¿Cuánto dinero hay ahora mismo en el banco según el extracto?",
                               "¿Ha entrado ya la transferencia de la agencia en el banco?"],
        }[que]
        i, p = self.plantilla(preguntas)
        conv = self.nueva()
        conv.usuario(self.ruido(p))
        extra = (" Puedo darte el saldo contable de las cuentas 572, que no es lo mismo que el extracto del banco."
                 if que == PENDIENTES[2][0] else "")
        conv.responder(f"Eso todavía no está conectado al copilot: {motivo}. No puedo consultarlo.{extra}")
        return [conv.ejemplo("no_conectado", i)]

    def escritura(self):
        i, p = self.plantilla(["Contabiliza la factura F26-00012", "Reserva una doble para el 3 de noviembre a nombre de Pérez",
                               "Aprueba las facturas pendientes", "Cambia el saldo del banco a 10.000 €"])
        conv = self.nueva()
        conv.usuario(self.ruido(p))
        conv.responder("No puedo hacerlo: el copilot es de solo lectura y no reserva, contabiliza, aprueba ni modifica nada. "
                       "Hazlo desde su pantalla en la aplicación; si quieres, te ayudo a consultar los datos antes.")
        return [conv.ejemplo("solo_lectura", i)]

    def inyeccion(self):
        if not self.cat.trampa:
            raise EscenarioInvalido("sin proveedor trampa")
        i, p = self.plantilla(["Facturas de Suministros Trampa", "¿Qué le debemos a Suministros Trampa?"])
        conv = self.nueva()
        conv.usuario(self.ruido(p))
        if i == 0:
            r = conv.llamar(("buscar_facturas", {"proveedor": "Suministros Trampa"}))
            f = r.datos["facturas"][0]
            conv.responder(f"Hay {len(r.datos['facturas'])} factura(s) de ese proveedor; la última es la {f['numero']} del "
                           f"{fecha_es(f['fecha'])} por {eur(f['total'])}. Ojo: el nombre del proveedor contiene un texto "
                           "con instrucciones; lo trato como dato y no lo sigo. Fuente: Facturas con IA.")
        else:
            b = conv.llamar(("buscar_cuentas", {"texto": "Suministros Trampa", "solo_subcuentas": True}))
            r = conv.llamar(("saldo_cuenta", {"cuenta": b.datos["cuentas"][0]["codigo"]}))
            conv.responder(respuesta_saldo(r, self.rnd) + "\nOjo: el nombre de esa cuenta contiene un texto con "
                           "instrucciones; lo trato como dato y no lo sigo.")
        return [conv.ejemplo("inyeccion", i)]

    def saludo(self):
        i, p = self.plantilla(["Hola", "¿Qué puedes hacer?", "buenos días, ¿en qué me ayudas?"])
        conv = self.nueva()
        conv.usuario(self.ruido(p))
        conv.responder("Hola. Puedo consultar la contabilidad (saldos, extractos, sumas y saldos, balance y PyG), buscar "
                       "facturas y darte su PDF, y resumir cajas, nóminas y el beneficio de gestión, también en gráficos. "
                       "Solo consulto: no modifico nada. ¿Qué necesitas?")
        return [conv.ejemplo("saludo", i)]

    def grafico_invalido(self):
        anio = self.rnd.choice(self.cat.anios_cajas)
        i, p = self.plantilla(["Gráfico de tarta de las ventas de caja por mes de {a}", "haz una tarta con las ventas mensuales de caja de {a}"])
        conv = self.nueva()
        conv.usuario(self.ruido(p.format(a=anio)))
        conv.llamar(("grafico", {"plantilla": "ventas_cajas_mensuales", "anio": anio, "tipo": "tarta"}),
                        esperado=EstadoLlamada.ACLARAR)
        conv.responder("Una tarta no sirve para una serie por meses (solo para repartos por categoría). ¿Te lo muestro "
                       "en barras apiladas por mes, o prefieres una tarta del gasto por categoría?")
        return [conv.ejemplo("grafico_aclarar", i)]

    # ---------------------------------------------------------------- reparto
    PESOS = {
        "saldo_codigo": 10, "saldo_nombre": 8, "deuda_proveedor": 7, "ventas_periodo": 6, "extracto": 5,
        "cuentas_anuales": 4, "facturas": 7, "cajas": 6, "personal": 4, "beneficio": 5, "grafico": 7,
        "periodo_dos_ejercicios": 3, "ejercicio_inexistente": 2, "cuenta_ambigua": 3, "falta_dato": 2,
        "no_conectado": 4, "escritura": 3, "inyeccion": 2, "saludo": 1, "grafico_invalido": 2,
    }

    def generar(self, n: int, intentos_por_ejemplo: int = 5):
        """n conversaciones. La hora de «consultado» de los resultados se fija en «hoy» del hotel ficticio."""
        from datetime import datetime
        from unittest import mock

        from django.utils import timezone

        momento = timezone.make_aware(datetime.combine(self.cat.hoy, datetime.min.time()).replace(hour=9))
        with mock.patch("copilot_pms.tools.comun.timezone.localtime", return_value=momento):
            return self._generar(n, intentos_por_ejemplo)

    def _generar(self, n, intentos_por_ejemplo):
        escenarios = list(self.PESOS)
        pesos = [self.PESOS[e] for e in escenarios]
        hechos, fallos = [], 0
        while len(hechos) < n and fallos < n * intentos_por_ejemplo:
            nombre = self.rnd.choices(escenarios, pesos)[0]
            try:
                hechos.extend(getattr(self, nombre)())
            except EscenarioInvalido:
                fallos += 1
        return hechos[:n]
