"""Modelos de la app cajas.

Un `CajaMes` agrupa las cajas de un mes; cada `CajaDia` es una fila del Excel
de cajas (un día). Se guardan dos bloques de importes:

- Puntos de venta: bar, comedor y chill out (columnas B-D del Excel).
- Otros cobros: ecotasa, menús, facturas, parking efectivo y parking visa
  (columnas H-L). Su total se calcula, no se guarda; la columna G del Excel
  (sin nombre) se ignora.
"""

import calendar
from decimal import Decimal

from django.db import models

MESES = [
    (1, "Enero"), (2, "Febrero"), (3, "Marzo"), (4, "Abril"), (5, "Mayo"), (6, "Junio"),
    (7, "Julio"), (8, "Agosto"), (9, "Septiembre"), (10, "Octubre"), (11, "Noviembre"), (12, "Diciembre"),
]
MESES_NOMBRES = dict(MESES)

# (campo, etiqueta) en el orden en que se muestran en las tablas
PUNTOS_VENTA = [
    ("bar", "Bar"),
    ("comedor", "Comedor"),
    ("chill_out", "Chill out"),
]
OTROS_COBROS = [
    ("ecotasa", "Ecotasa"),
    ("menus", "Menús"),
    ("facturas", "Facturas"),
    ("parking_efectivo", "Parking efectivo"),
    ("parking_visa", "Parking visa"),
]
# Campos que suman el total de otros cobros
CAMPOS_TOTAL_COBROS = [c for c, _ in OTROS_COBROS]

CAMPOS_IMPORTE = [c for c, _ in PUNTOS_VENTA + OTROS_COBROS]


def _importe(verbose_name):
    return models.DecimalField(verbose_name, max_digits=12, decimal_places=2, default=Decimal("0"))


class CajaMes(models.Model):
    anio = models.PositiveSmallIntegerField("año")
    mes = models.PositiveSmallIntegerField(choices=MESES)
    notas = models.TextField(blank=True)
    origen = models.CharField(
        max_length=200, blank=True,
        help_text="Fichero y hoja de la última importación; vacío si se ha creado a mano.",
    )
    creado = models.DateTimeField(auto_now_add=True)
    actualizado = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-anio", "-mes"]
        constraints = [models.UniqueConstraint(fields=["anio", "mes"], name="cajas_mes_unico")]
        verbose_name = "mes de cajas"
        verbose_name_plural = "meses de cajas"

    def __str__(self):
        return f"{MESES_NOMBRES[self.mes]} {self.anio}"

    @property
    def nombre_mes(self):
        return MESES_NOMBRES[self.mes]

    @property
    def dias_del_mes(self):
        return calendar.monthrange(self.anio, self.mes)[1]

    def crear_dias(self):
        """Crea las filas vacías que falten (una por día del mes). Devuelve cuántas ha creado."""
        existentes = set(self.dias.values_list("dia", flat=True))
        nuevos = [CajaDia(caja_mes=self, dia=d) for d in range(1, self.dias_del_mes + 1) if d not in existentes]
        CajaDia.objects.bulk_create(nuevos)
        return len(nuevos)


class CajaDia(models.Model):
    caja_mes = models.ForeignKey(CajaMes, on_delete=models.CASCADE, related_name="dias")
    dia = models.PositiveSmallIntegerField("día")

    # Puntos de venta
    bar = _importe("bar")
    comedor = _importe("comedor")
    chill_out = _importe("chill out")

    # Otros cobros
    ecotasa = _importe("ecotasa")
    menus = _importe("menús")
    facturas = _importe("facturas")
    parking_efectivo = _importe("parking efectivo")
    parking_visa = _importe("parking visa")

    class Meta:
        ordering = ["caja_mes", "dia"]
        constraints = [models.UniqueConstraint(fields=["caja_mes", "dia"], name="cajas_dia_unico")]
        verbose_name = "caja diaria"
        verbose_name_plural = "cajas diarias"

    def __str__(self):
        return f"{self.dia} {self.caja_mes}"

    @property
    def total_ventas(self):
        """Bar + comedor + chill out (columna E del Excel)."""
        return sum((getattr(self, c) for c, _ in PUNTOS_VENTA), Decimal("0"))

    @property
    def total_cobros(self):
        """Ecotasa + menús + facturas + parking efectivo + parking visa."""
        return sum((getattr(self, c) for c in CAMPOS_TOTAL_COBROS), Decimal("0"))

    @property
    def vacio(self):
        return not any(getattr(self, c) for c in CAMPOS_IMPORTE)
