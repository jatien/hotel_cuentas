"""
Registro de herramientas del copilot. Importar este paquete registra todas.

Para añadir una capacidad: crear un módulo con funciones decoradas con
`@herramienta(...)` e importarlo aquí. Cuando exista, quitarla de PENDIENTES.
"""

from copilot_pms.tools import contabilidad, facturas, graficos, informes, navegacion, sistema  # noqa: F401  (registran las herramientas)
from copilot_pms.tools.base import REGISTRO, Contexto, Resultado, disponibles, ejecutar  # noqa: F401

# Lo que el copilot debe saber que TODAVÍA no puede consultar, para decirlo en vez de inventar.
PENDIENTES = [
    ("Disponibilidad de habitaciones, tarifas y reservas",
     "la app de reservas aún no tiene inventario de habitaciones ni conexión con el motor de reservas"),
    ("Horarios y cuadrantes del personal",
     "nóminas solo guarda costes mensuales; no hay cuadrantes de turnos"),
    ("Extractos bancarios y saldo disponible en banco",
     "los extractos del banco aún no se importan; solo existe el saldo contable de las cuentas 572"),
    ("Cualquier operación de escritura (reservar, contabilizar, aprobar facturas)",
     "el copilot es de solo lectura"),
]
