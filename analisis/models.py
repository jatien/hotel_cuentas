from decimal import Decimal

from django.db import models


def _importe(**kwargs):
    return models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0"), **kwargs)


class Reserva(models.Model):
    """Una reserva del PMS (export «Reservas»). Ingresos por estancias.

    El export separa lo ya facturado de lo pendiente de facturar; se guardan
    las dos partes para poder analizar tanto la facturación como la producción.
    """

    numero = models.PositiveIntegerField(unique=True)
    fecha_reserva = models.DateField()
    agencia_codigo = models.CharField(max_length=20, blank=True)
    agencia = models.CharField(max_length=150)
    tarifa = models.CharField(max_length=50, blank=True)
    no_reembolsable = models.BooleanField(default=False)
    ref_agencia = models.CharField(max_length=60, blank=True)
    estado = models.CharField(max_length=30)
    grupo = models.CharField(max_length=100, blank=True)

    entrada = models.DateField()
    salida = models.DateField()
    habitaciones = models.PositiveSmallIntegerField(default=0)
    adultos = models.PositiveSmallIntegerField(default=0)
    ninos = models.PositiveSmallIntegerField(default=0)
    cunas = models.PositiveSmallIntegerField(default=0)

    deposito = _importe()
    # Ya facturado
    fact_estancias = _importe()
    fact_extras = _importe()
    fact_impuesto = _importe(help_text="Impuesto turístico facturado")
    fact_total = _importe()
    # Pendiente de facturar
    pend_estancias = _importe()
    pend_extras = _importe()
    pend_tasas = _importe(help_text="Impuesto turístico pendiente")
    pend_total = _importe()

    total_estancias = _importe(help_text="Facturado + pendiente")
    facturas = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["entrada", "numero"]
        indexes = [models.Index(fields=["entrada"]), models.Index(fields=["salida"]),
                   models.Index(fields=["agencia"])]

    def __str__(self):
        return f"Reserva {self.numero} · {self.agencia} · {self.entrada:%d/%m}–{self.salida:%d/%m}"

    @property
    def noches(self) -> int:
        return (self.salida - self.entrada).days

    @property
    def total_extras(self) -> Decimal:
        return self.fact_extras + self.pend_extras


class CompraProveedor(models.Model):
    """Una línea del «Diario de compras»: un albarán o una factura de proveedor."""

    class Tipo(models.TextChoices):
        ALBARAN = "A", "Albarán"
        FACTURA = "F", "Factura"

    tipo = models.CharField(max_length=1, choices=Tipo.choices)
    numero = models.CharField(max_length=40)
    fecha = models.DateField()
    codigo_proveedor = models.PositiveIntegerField()
    proveedor = models.CharField(max_length=150)
    nif = models.CharField(max_length=20, blank=True)

    bruto = _importe()
    dto_pp = _importe(help_text="Descuento pronto pago")
    portes_gastos = _importe()
    iva = _importe(help_text="IVA + recargo de equivalencia")
    irpf = _importe()
    total = _importe()

    class Meta:
        ordering = ["fecha", "proveedor"]
        verbose_name = "compra a proveedor"
        verbose_name_plural = "compras a proveedores"
        indexes = [models.Index(fields=["fecha"]), models.Index(fields=["codigo_proveedor"])]

    def __str__(self):
        return f"{self.get_tipo_display()} {self.numero} · {self.proveedor} · {self.total} €"

    @property
    def base(self) -> Decimal:
        """Base imponible (sin IVA)."""
        return self.bruto - self.dto_pp + self.portes_gastos


class FacturaAcreedor(models.Model):
    """Una factura recibida de un acreedor («S/FRA» en su mayor de la cuenta 410).

    `cuenta` es la contrapartida: gasto (6xx) o inmovilizado (2xx). El importe
    es el TOTAL de la factura, IVA incluido; negativo si es un abono.
    """

    fichero = models.CharField(max_length=60, help_text="Clave del fichero de origen, p. ej. «iberdrola»")
    acreedor = models.CharField(max_length=150)
    fecha = models.DateField()
    documento = models.CharField(max_length=40, blank=True)
    cuenta = models.CharField(max_length=12, blank=True)
    importe = _importe()
    asiento = models.PositiveIntegerField(null=True, blank=True)
    linea = models.PositiveIntegerField(null=True, blank=True)

    class Meta:
        ordering = ["fecha", "fichero", "asiento", "linea"]
        verbose_name = "factura de acreedor"
        verbose_name_plural = "facturas de acreedores"
        indexes = [models.Index(fields=["fecha"]), models.Index(fields=["cuenta"])]

    def __str__(self):
        return f"{self.fecha} · {self.acreedor} · {self.importe} €"

    @property
    def es_inversion(self) -> bool:
        """Contrapartida en el grupo 2 (inmovilizado): no es gasto del ejercicio."""
        return self.cuenta.startswith("2")


DEPARTAMENTOS = ["Cocina", "Comedor", "Recepción", "Mantenimiento", "Lavandería", "General"]


class AsignacionDepartamento(models.Model):
    """A qué departamento se imputa el gasto de cada proveedor o acreedor.

    Las filas se crean solas al importar (sin departamento); se rellenan desde
    el admin. Lo que no tiene departamento sale como «Sin asignar».
    """

    class Origen(models.TextChoices):
        PROVEEDOR = "proveedor", "Proveedor"
        ACREEDOR = "acreedor", "Acreedor"

    origen = models.CharField(max_length=10, choices=Origen.choices)
    clave = models.CharField(max_length=60, help_text="Código de proveedor o fichero del acreedor")
    nombre = models.CharField(max_length=150)
    departamento = models.CharField(max_length=30, blank=True,
                                    choices=[(d, d) for d in DEPARTAMENTOS])

    class Meta:
        ordering = ["origen", "nombre"]
        verbose_name = "departamento de proveedor/acreedor"
        verbose_name_plural = "departamentos de proveedores y acreedores"
        constraints = [models.UniqueConstraint(fields=["origen", "clave"], name="asignacion_unica")]

    def __str__(self):
        return f"{self.nombre} → {self.departamento or 'sin asignar'}"
