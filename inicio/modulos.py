"""
Las cuatro ramas principales del PMS que aparecen en la rueda de la página de inicio.

Es la única fuente de verdad del menú: la plantilla, el JavaScript (vía json_script)
y la herramienta del copilot `modulos_pms` leen de aquí. Para añadir una sección nueva
a una rama basta con añadirla a su lista `secciones`; si la vista todavía no existe,
se deja `url=None` y aparece como «próximamente».
"""

from dataclasses import dataclass, field

from django.urls import NoReverseMatch, reverse


@dataclass(frozen=True)
class Seccion:
    nombre: str
    url_name: str | None = None  # nombre de la URL de Django; None = todavía no programada
    descripcion: str = ""

    @property
    def url(self) -> str | None:
        if not self.url_name:
            return None
        try:
            return reverse(self.url_name)
        except NoReverseMatch:
            return None


@dataclass(frozen=True)
class Modulo:
    clave: str
    nombre: str
    imagen: str  # ruta dentro de static/
    descripcion: str
    secciones: tuple = field(default_factory=tuple)

    @property
    def disponible(self) -> bool:
        """Una rama está disponible si al menos una de sus secciones tiene vista."""
        return any(s.url for s in self.secciones)

    def a_dict(self) -> dict:
        return {
            "clave": self.clave,
            "nombre": self.nombre,
            "descripcion": self.descripcion,
            "disponible": self.disponible,
            "secciones": [{"nombre": s.nombre, "url": s.url, "descripcion": s.descripcion}
                          for s in self.secciones],
        }


# Orden de la rueda, en el sentido de las agujas del reloj empezando por arriba.
MODULOS: tuple[Modulo, ...] = (
    Modulo(
        "reservas", "Gestión de reservas", "inicio/img/reservas.svg",
        "Reservas, recepción, habitaciones y housekeeping.",
        (
            Seccion("Calendario de reservas", None, "Ocupación por habitación y día"),
            Seccion("Recepción", None, "Entradas y salidas del día"),
            Seccion("Habitaciones", None, "Estado y limpieza"),
        ),
    ),
    Modulo(
        "facturas", "Gestión de facturas", "inicio/img/facturas.svg",
        "Facturas de proveedores y acreedores, con extracción automática.",
        (
            Seccion("Facturas", "invoice_data:index", "Facturas importadas"),
            Seccion("Subir facturas", "invoice_data:upload", "Añadir PDF o imágenes"),
            Seccion("Facturas con IA", "invoice_agent:index", "Extracción con revisión humana"),
        ),
    ),
    Modulo(
        "contabilidad", "Contabilidad", "inicio/img/contabilidad.svg",
        "Libro diario, plan de cuentas, balances y cuentas anuales (PGC 2007).",
        (
            Seccion("Libro diario", "cuadro_cuentas:diario", "Asientos del ejercicio"),
            Seccion("Plan de cuentas", "cuadro_cuentas:plan_cuentas", "Cuentas y subcuentas"),
            Seccion("Extractos", "cuadro_cuentas:extractos", "Movimientos de una cuenta"),
            Seccion("Sumas y saldos", "cuadro_cuentas:sumas_saldos", "Balance de sumas y saldos"),
            Seccion("Cuentas anuales", "cuadro_cuentas:cuentas_anuales", "Balance y pérdidas y ganancias"),
        ),
    ),
    Modulo(
        "analisis", "Análisis", "inicio/img/analisis.svg",
        "Beneficio por mes y departamento, cajas diarias y costes de personal.",
        (
            Seccion("Beneficios", "analisis:resumen", "Ingresos frente a gastos"),
            Seccion("Cajas", "cajas:resumen", "Bar, comedor, chill out y otros cobros"),
            Seccion("Nóminas", "nominas", "Costes de personal"),
        ),
    ),
)


def por_clave(clave: str) -> Modulo | None:
    return next((m for m in MODULOS if m.clave == clave), None)
