"""
Capa de herramientas del copilot.

El modelo de IA nunca toca la base de datos: solo puede pedir que se ejecute
una herramienta registrada aquí, con argumentos que se validan con Pydantic
antes de ejecutar nada. Cada herramienta:

- declara su esquema de argumentos (lo que ve el modelo),
- declara los permisos de Django que exige cuando el login está activo,
- declara si escribe (las de escritura están bloqueadas en esta versión),
- devuelve un `Resultado` con estado, datos, fuente y fecha de los datos.

Añadir una capacidad nueva (disponibilidad de habitaciones, horarios...) es
escribir una función y decorarla con `@herramienta(...)`; ni el agente ni las
vistas cambian.
"""

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import date
from typing import Callable

from django.core.serializers.json import DjangoJSONEncoder
from pydantic import BaseModel, ValidationError

from copilot_pms import conf
from copilot_pms.models import EstadoLlamada

logger = logging.getLogger("copilot_pms")


# --------------------------------------------------------------------------- #
# Contexto de quien pregunta
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Contexto:
    """Quién pregunta y a qué tiene acceso. Se construye en el servidor (access.py), nunca
    a partir de datos del cliente o del modelo."""

    hoy: date
    user: object | None = None
    actor_label: str = "sistema"
    login_requerido: bool = False
    empresas_facturas: frozenset = frozenset()  # Empresa (invoice_agent) visibles

    def tiene_permisos(self, permisos) -> bool:
        if not self.login_requerido or not permisos:
            return True
        return self.user is not None and all(self.user.has_perm(p) for p in permisos)


# --------------------------------------------------------------------------- #
# Resultado y errores
# --------------------------------------------------------------------------- #
def a_json(valor):
    """Decimal -> texto, fechas -> ISO: lo que se guarda y lo que ve el modelo."""
    return json.loads(json.dumps(valor, cls=DjangoJSONEncoder))


@dataclass
class Resultado:
    estado: str = EstadoLlamada.OK
    datos: dict = field(default_factory=dict)
    fuente: str = ""
    fecha_datos: str = ""  # hasta qué fecha o en qué momento son válidos los datos
    avisos: list = field(default_factory=list)
    codigo: str = ""
    mensaje: str = ""
    duracion_ms: int = 0

    @property
    def correcto(self) -> bool:
        return self.estado in (EstadoLlamada.OK, EstadoLlamada.VACIO)

    def para_modelo(self) -> dict:
        salida = {"estado": self.estado}
        if self.codigo:
            salida["codigo"] = self.codigo
        if self.mensaje:
            salida["mensaje"] = self.mensaje
        if self.fuente:
            salida["fuente"] = self.fuente
        if self.fecha_datos:
            salida["fecha_datos"] = self.fecha_datos
        if self.avisos:
            salida["avisos"] = self.avisos
        if self.datos:
            salida["datos"] = self.datos
        return a_json(salida)


class ErrorHerramienta(Exception):
    """Lo lanzan las herramientas para devolver un estado distinto de OK sin inventar datos."""

    estado = EstadoLlamada.ERROR

    def __init__(self, codigo: str, mensaje: str, datos: dict | None = None):
        super().__init__(mensaje)
        self.codigo = codigo
        self.mensaje = mensaje
        self.datos = datos or {}


class NoDisponible(ErrorHerramienta):
    """El dato pedido no existe en el sistema (ejercicio sin abrir, documento borrado...)."""

    estado = EstadoLlamada.NO_DISPONIBLE


class Denegado(ErrorHerramienta):
    """Sin permiso para una parte concreta de la herramienta (un origen, una plantilla...)."""

    estado = EstadoLlamada.DENEGADO


class Aclarar(ErrorHerramienta):
    """Falta un parámetro o es ambiguo: el copilot debe preguntar al usuario."""

    estado = EstadoLlamada.ACLARAR


# --------------------------------------------------------------------------- #
# Registro
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Herramienta:
    nombre: str
    descripcion: str
    argumentos: type[BaseModel]
    funcion: Callable
    dominio: str
    permisos: tuple = ()  # hacen falta todos
    escritura: bool = False
    permisos_alguno: tuple = ()  # basta uno (herramientas que juntan varios orígenes)

    def permitida(self, ctx: "Contexto") -> bool:
        if not ctx.tiene_permisos(self.permisos):
            return False
        return not self.permisos_alguno or any(ctx.tiene_permisos((p,)) for p in self.permisos_alguno)

    def esquema(self) -> dict:
        """Definición de la función en el formato de «tools» de Chat Completions."""
        parametros = self.argumentos.model_json_schema()
        parametros.pop("title", None)
        return {
            "type": "function",
            "function": {"name": self.nombre, "description": self.descripcion, "parameters": parametros},
        }


REGISTRO: dict[str, Herramienta] = {}


def herramienta(nombre: str, *, descripcion: str, argumentos: type[BaseModel], dominio: str,
                permisos: tuple = (), permisos_alguno: tuple = (), escritura: bool = False):
    def decorador(funcion):
        if nombre in REGISTRO:
            raise ValueError(f"Herramienta duplicada: {nombre}")
        REGISTRO[nombre] = Herramienta(nombre, descripcion.strip(), argumentos, funcion, dominio,
                                       tuple(permisos), escritura, tuple(permisos_alguno))
        return funcion

    return decorador


def disponibles(ctx: Contexto) -> list[Herramienta]:
    """Herramientas que este contexto puede usar (las únicas que se ofrecen al modelo)."""
    permitir_escritura = conf.get("ALLOW_WRITE_TOOLS")
    return [h for h in REGISTRO.values()
            if h.permitida(ctx) and (permitir_escritura or not h.escritura)]


# --------------------------------------------------------------------------- #
# Ejecutor
# --------------------------------------------------------------------------- #
def _errores_validacion(error: ValidationError) -> list[dict]:
    return [{"campo": ".".join(str(p) for p in e["loc"]) or "(argumentos)", "error": e["msg"]}
            for e in error.errors(include_url=False)]


def ejecutar(nombre: str, argumentos, ctx: Contexto) -> Resultado:
    """Valida y ejecuta una herramienta. Nunca lanza: todo fallo vuelve como Resultado."""
    inicio = time.monotonic()
    resultado = _ejecutar(nombre, argumentos, ctx)
    resultado.duracion_ms = int((time.monotonic() - inicio) * 1000)
    logger.debug(json.dumps({"evento": "herramienta", "nombre": nombre, "estado": resultado.estado,
                            "codigo": resultado.codigo, "ms": resultado.duracion_ms, "actor": ctx.actor_label}))
    return resultado


def _ejecutar(nombre, argumentos, ctx):
    h = REGISTRO.get(nombre)
    if h is None:
        return Resultado(EstadoLlamada.DESCONOCIDA, codigo="TOOL_UNKNOWN",
                         mensaje=f"No existe la herramienta «{nombre}».")
    if h.escritura and not conf.get("ALLOW_WRITE_TOOLS"):
        return Resultado(EstadoLlamada.DENEGADO, codigo="WRITE_DISABLED",
                         mensaje="Las operaciones de escritura no están habilitadas en el copilot.")
    if not h.permitida(ctx):
        return Resultado(EstadoLlamada.DENEGADO, codigo="PERMISSION_DENIED",
                         mensaje="El usuario no tiene permiso para consultar estos datos.")

    if isinstance(argumentos, str):
        try:
            argumentos = json.loads(argumentos or "{}")
        except json.JSONDecodeError:
            return Resultado(EstadoLlamada.INVALIDO, codigo="ARGS_NOT_JSON",
                             mensaje="Los argumentos no son un JSON válido.")
    if not isinstance(argumentos, dict):
        return Resultado(EstadoLlamada.INVALIDO, codigo="ARGS_NOT_OBJECT",
                         mensaje="Los argumentos deben ser un objeto JSON.")
    try:
        params = h.argumentos.model_validate(argumentos)
    except ValidationError as error:
        return Resultado(EstadoLlamada.INVALIDO, codigo="ARGS_INVALID", mensaje="Argumentos no válidos.",
                         datos={"errores": _errores_validacion(error)})

    try:
        resultado = h.funcion(params, ctx)
    except ErrorHerramienta as error:
        return Resultado(error.estado, codigo=error.codigo, mensaje=error.mensaje, datos=a_json(error.datos))
    except Exception:
        logger.exception("Error en la herramienta %s", nombre)
        return Resultado(EstadoLlamada.ERROR, codigo="INTERNAL_ERROR",
                         mensaje="Error interno al consultar el sistema; no hay datos.")
    resultado.datos = a_json(resultado.datos)
    return resultado
