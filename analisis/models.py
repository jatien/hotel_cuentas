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


DEPARTAMENTOS = ["Cocina", "Bar", "Comedor", "Almacén central", "Recepción", "Pisos", "Mantenimiento",
                 "Lavandería", "General"]


class AsignacionDepartamento(models.Model):
    """A qué departamento se imputa el gasto de cada proveedor o acreedor.

    Las filas se crean solas al importar (sin departamento); se rellenan desde
    el admin. Lo que no tiene departamento sale como «Sin asignar».
    """

    class Origen(models.TextChoices):
        PROVEEDOR = "proveedor", "Proveedor"
        ACREEDOR = "acreedor", "Acreedor"
        PERSONAL = "personal", "Personal (extras)"

    origen = models.CharField(max_length=10, choices=Origen.choices)
    clave = models.CharField(max_length=60,
                             help_text="Código de proveedor, fichero del acreedor o nombre de la persona")
    nombre = models.CharField(max_length=150)
    departamento = models.CharField(max_length=30, blank=True,
                                    choices=[(d, d) for d in DEPARTAMENTOS])

    class Meta:
        ordering = ["origen", "nombre"]
        verbose_name = "asignación de departamento"
        verbose_name_plural = "departamentos (proveedores, acreedores y personal)"
        constraints = [models.UniqueConstraint(fields=["origen", "clave"], name="asignacion_unica")]

    def __str__(self):
        return f"{self.nombre} → {self.departamento or 'sin asignar'}"


class ApunteIva(models.Model):
    """Un apunte de los libros de IVA (mayor de la 472 soportado o de la 477 repercutido).

    `cuota` va siempre en positivo para el sentido normal del libro: IVA que
    pagamos en el soportado, IVA que cobramos en el repercutido. Los abonos
    quedan en negativo.
    """

    class Libro(models.TextChoices):
        SOPORTADO = "soportado", "IVA soportado"
        REPERCUTIDO = "repercutido", "IVA repercutido"

    libro = models.CharField(max_length=12, choices=Libro.choices)
    fecha = models.DateField()
    documento = models.CharField(max_length=40, blank=True)
    concepto = models.CharField(max_length=255, blank=True)
    cuenta = models.CharField(max_length=12, blank=True, help_text="Contrapartida: 410 acreedor, 400 proveedor, 430 cliente…")
    cuota = _importe()
    asiento = models.PositiveIntegerField(null=True, blank=True)
    linea = models.PositiveIntegerField(null=True, blank=True)

    class Meta:
        ordering = ["libro", "fecha", "asiento", "linea"]
        verbose_name = "apunte de IVA"
        verbose_name_plural = "apuntes de IVA"
        indexes = [models.Index(fields=["libro", "fecha"]), models.Index(fields=["asiento"])]

    def __str__(self):
        return f"{self.get_libro_display()} · {self.fecha} · {self.concepto} · {self.cuota} €"


class ExtraPersonal(models.Model):
    """Extras pagados a una persona en un mes, fuera de la nómina (hoja de extras)."""

    anio = models.PositiveSmallIntegerField()
    mes = models.PositiveSmallIntegerField()
    persona = models.CharField(max_length=100)
    importe = _importe()

    class Meta:
        ordering = ["anio", "mes", "persona"]
        verbose_name = "extra de personal"
        verbose_name_plural = "extras de personal"
        constraints = [models.UniqueConstraint(fields=["anio", "mes", "persona"], name="extra_unico")]

    def __str__(self):
        return f"{self.persona} {self.mes}/{self.anio}: {self.importe} €"


class GastoAdicional(models.Model):
    """Un gasto que no está en los ficheros de acreedores ni de proveedores (p. ej. el alquiler).

    Se reparte por días entre `desde` y `hasta` y entra en el análisis como
    gasto de acreedores (o como inversión si la cuenta es del grupo 2).
    """

    concepto = models.CharField(max_length=150)
    acreedor = models.CharField(max_length=150, blank=True)
    cuenta = models.CharField(max_length=12, default="621", help_text="Cuenta PGC, p. ej. 621 Arrendamientos")
    base = _importe(help_text="Importe sin IVA")
    iva = _importe(help_text="Cuota de IVA")
    desde = models.DateField()
    hasta = models.DateField()
    departamento = models.CharField(max_length=30, blank=True, choices=[(d, d) for d in DEPARTAMENTOS])
    asiento_iva = models.PositiveIntegerField(
        null=True, blank=True,
        help_text="Asiento del libro de IVA soportado al que corresponde (así deja de salir en los avisos)")

    class Meta:
        ordering = ["desde", "concepto"]
        verbose_name = "gasto adicional"
        verbose_name_plural = "gastos adicionales (alquiler…)"

    def __str__(self):
        return f"{self.concepto} · {self.base} € ({self.desde:%d/%m/%Y}–{self.hasta:%d/%m/%Y})"

    def clean(self):
        from django.core.exceptions import ValidationError
        if self.desde and self.hasta and self.hasta < self.desde:
            raise ValidationError("La fecha «hasta» no puede ser anterior a «desde».")

    def reparto_mensual(self) -> dict[tuple[int, int], Decimal]:
        """{(año, mes): fracción del periodo} proporcional a los días de cada mes."""
        from datetime import timedelta
        dias = (self.hasta - self.desde).days + 1
        reparto, d = {}, self.desde
        while d <= self.hasta:
            reparto[(d.year, d.month)] = reparto.get((d.year, d.month), 0) + 1
            d += timedelta(days=1)
        return {k: Decimal(v) / dias for k, v in reparto.items()}


class DepartamentoAlbaran(models.Model):
    """Departamento de cada albarán, según los listados de compras exportados por departamento.

    Se guarda aparte del diario de compras para que reimportar el diario no
    borre los departamentos. El cruce es por tipo + número + código de proveedor.
    """

    tipo = models.CharField(max_length=1)
    numero = models.CharField(max_length=40)
    codigo_proveedor = models.PositiveIntegerField()
    fecha = models.DateField()
    proveedor = models.CharField(max_length=150, blank=True)
    total = _importe()
    departamento = models.CharField(max_length=30)

    class Meta:
        ordering = ["fecha", "numero"]
        verbose_name = "departamento de albarán"
        verbose_name_plural = "departamentos de albaranes"
        constraints = [models.UniqueConstraint(fields=["tipo", "numero", "codigo_proveedor"], name="albaran_unico")]

    def __str__(self):
        return f"{self.numero} · {self.proveedor} → {self.departamento}"


class Tercero(models.Model):
    """Proveedor, acreedor o cliente del plan de cuentas (400…, 410…, 430…) con su categoría."""

    class Tipo(models.TextChoices):
        PROVEEDOR = "proveedor", "Proveedor"
        ACREEDOR = "acreedor", "Acreedor"
        CLIENTE = "cliente", "Cliente"
        OTRO = "otro", "Otro"

    cuenta = models.CharField(max_length=12, unique=True)
    nombre = models.CharField(max_length=150)
    nif = models.CharField(max_length=30, blank=True)
    tipo = models.CharField(max_length=10, choices=Tipo.choices)
    categoria = models.CharField(max_length=60, blank=True, help_text="Comida, Bebida, Luz, Agencias…")
    departamentos = models.CharField(max_length=150, blank=True, help_text="Texto libre del fichero, p. ej. «Cocina, Bar»")

    class Meta:
        ordering = ["cuenta"]
        verbose_name = "tercero (proveedor/acreedor)"
        verbose_name_plural = "terceros (proveedores y acreedores)"

    def __str__(self):
        return f"{self.cuenta} {self.nombre}"

    @property
    def completo(self) -> bool:
        return bool(self.categoria)

    @staticmethod
    def tipo_de_cuenta(cuenta: str) -> str:
        return {"400": "proveedor", "410": "acreedor", "430": "cliente"}.get(cuenta[:3], "otro")
