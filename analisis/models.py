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


class MovimientoAcreedor(models.Model):
    """Un apunte del mayor de un acreedor (cuenta 410), del CSV unificado.

    En las facturas, `contrapartida` es la cuenta de gasto (6xx) o de
    inmovilizado (2xx) y el importe es el TOTAL de la factura, IVA incluido.
    """

    class Tipo(models.TextChoices):
        FACTURA = "factura", "Factura"
        PAGO = "pago", "Pago"
        COBRO = "cobro", "Cobro"
        APERTURA = "apertura", "Apertura"
        OTRO = "otro", "Otro"

    fichero = models.CharField(max_length=60, help_text="Clave del fichero de origen, p. ej. «iberdrola»")
    acreedor = models.CharField(max_length=150)
    fecha = models.DateField()
    documento = models.CharField(max_length=40, blank=True)
    tipo = models.CharField(max_length=10, choices=Tipo.choices)
    concepto = models.CharField(max_length=255, blank=True)
    contrapartida = models.CharField(max_length=12, blank=True)
    debe = _importe()
    haber = _importe()
    asiento = models.PositiveIntegerField(null=True, blank=True)
    linea = models.PositiveIntegerField(null=True, blank=True)

    class Meta:
        ordering = ["fecha", "fichero", "asiento", "linea"]
        verbose_name = "movimiento de acreedor"
        verbose_name_plural = "movimientos de acreedores"
        indexes = [models.Index(fields=["tipo", "fecha"]), models.Index(fields=["contrapartida"])]

    def __str__(self):
        return f"{self.fecha} · {self.acreedor} · {self.get_tipo_display()} {self.importe} €"

    @property
    def importe(self) -> Decimal:
        """Positivo para facturas, negativo para abonos y pagos."""
        return self.haber - self.debe

    @property
    def grupo_cuenta(self) -> str:
        return self.contrapartida[:3]
