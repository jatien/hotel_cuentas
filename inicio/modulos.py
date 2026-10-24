"""
Las cuatro ramas principales del PMS que aparecen en la rueda de la página de inicio.

Es la única fuente de verdad del menú: la plantilla, el JavaScript (vía json_script)
y la herramienta del copilot `modulos_pms` leen de aquí. Para añadir una sección nueva
a una rama basta con añadirla a su lista `secciones`; si la vista todavía no existe,
se deja `url=None` y aparece como «próximamente».
"""

from dataclasses import dataclass, field

from django.urls import NoReverseMatch, reverse

from cuadro_cuentas.templatetags.cuadro_cuentas_tags import MENU as MENU_CONTABILIDAD


@dataclass(frozen=True)
class Seccion:
    nombre: str
    url_name: str | None = None  # nombre de la URL de Django; None = todavía no programada
    descripcion: str = ""
    grupo: str = ""              # encabezado bajo el que aparece en el desplegable
    principal: bool = False      # la que se abre al entrar en la rama

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
    icono: str  # plantilla SVG en línea (templates/inicio/iconos/), toma los colores del tema
    descripcion: str
    secciones: tuple = field(default_factory=tuple)

    @property
    def disponible(self) -> bool:
        """Una rama está disponible si al menos una de sus secciones tiene vista."""
        return any(s.url for s in self.secciones)

    @property
    def indice_inicial(self) -> int | None:
        """La sección principal si tiene pantalla; si no, la primera que la tenga."""
        disponibles = [i for i, s in enumerate(self.secciones) if s.url]
        principal = next((i for i in disponibles if self.secciones[i].principal), None)
        return principal if principal is not None else (disponibles[0] if disponibles else None)

    @property
    def url_app(self) -> str:
        """Pantalla de la rama dentro del inicio: icono arriba, secciones y la herramienta."""
        return reverse("home_modulo", args=[self.clave])

    def a_dict(self) -> dict:
        return {
            "clave": self.clave,
            "url_app": self.url_app,
            "nombre": self.nombre,
            "descripcion": self.descripcion,
            "disponible": self.disponible,
            "inicial": self.indice_inicial,
            "secciones": [{"nombre": s.nombre, "url": s.url, "descripcion": s.descripcion, "grupo": s.grupo}
                          for s in self.secciones],
        }


# Orden de la rueda, en el sentido de las agujas del reloj empezando por arriba.
MODULOS: tuple[Modulo, ...] = (
    Modulo(
        "reservas", "Gestión de reservas", "inicio/iconos/reservas.svg",
        "Reservas, recepción, habitaciones y housekeeping.",
        (
            Seccion("Calendario de reservas", None, "Ocupación por habitación y día"),
            Seccion("Recepción", None, "Entradas y salidas del día"),
            Seccion("Habitaciones", None, "Estado y limpieza"),
        ),
    ),
    Modulo(
        "facturas", "Gestión de facturas", "inicio/iconos/facturas.svg",
        "Facturas de proveedores y acreedores, con extracción automática.",
        (
            Seccion("Facturas", "invoice_data:index", "Facturas importadas"),
            Seccion("Subir facturas", "invoice_data:upload", "Añadir PDF o imágenes"),
            Seccion("Facturas con IA", "invoice_agent:index", "Extracción con revisión humana"),
        ),
    ),
    Modulo(
        "contabilidad", "Contabilidad", "inicio/iconos/contabilidad.svg",
        "Libro diario, plan de cuentas, balances y cuentas anuales (PGC 2007).",
        # El mismo menú que el lateral propio de la contabilidad (que se oculta dentro del inicio).
        tuple(Seccion(nombre, f"cuadro_cuentas:{ruta}", grupo=grupo, principal=(ruta == "diario"))
              for grupo, enlaces in MENU_CONTABILIDAD for nombre, ruta in enlaces),
    ),
    Modulo(
        "analisis", "Análisis", "inicio/iconos/analisis.svg",
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
