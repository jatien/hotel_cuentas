from datetime import date
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Max, Q, Sum

DEPARTAMENTOS = [
    ("cocina", "Cocina"),
    ("recepcion", "Recepción"),
    ("comedor", "Comedor"),
    ("mantenimiento", "Mantenimiento"),
    ("lavanderia", "Lavandería"),
]


class CuentaContable(models.Model):
    codigo = models.CharField("código", max_length=12, unique=True)
    nombre = models.CharField(max_length=200)
    padre = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.PROTECT, related_name="hijas"
    )
    imputable = models.BooleanField(
        default=True, help_text="Si admite apuntes directos (las cuentas agrupadoras no)."
    )
    activa = models.BooleanField(default=True)

    class Meta:
        ordering = ["codigo"]
        verbose_name = "cuenta contable"
        verbose_name_plural = "cuentas contables"

    def __str__(self):
        return f"{self.codigo} {self.nombre}"

    @property
    def nivel(self):
        return {1: "Grupo", 2: "Subgrupo", 3: "Cuenta"}.get(len(self.codigo), "Subcuenta")

    def clean(self):
        if not self.codigo.isdigit():
            raise ValidationError({"codigo": "El código solo puede contener dígitos."})
        if self.padre and not self.codigo.startswith(self.padre.codigo):
            raise ValidationError({"padre": "El código debe empezar por el código de la cuenta padre."})

    def save(self, *args, **kwargs):
        # Padre automático: el prefijo existente más largo del código
        if self.padre_id is None and len(self.codigo) > 1:
            prefijos = [self.codigo[:i] for i in range(1, len(self.codigo))]
            self.padre = (
                CuentaContable.objects.filter(codigo__in=prefijos).order_by("-codigo").first()
            )
        super().save(*args, **kwargs)


class Ejercicio(models.Model):
    anio = models.PositiveIntegerField("año", unique=True)
    fecha_inicio = models.DateField()
    fecha_fin = models.DateField()
    cerrado = models.BooleanField(default=False)

    class Meta:
        ordering = ["-anio"]
        verbose_name = "ejercicio"
        verbose_name_plural = "ejercicios"

    def __str__(self):
        return str(self.anio)

    @classmethod
    def buscar(cls, fecha):
        return cls.objects.filter(fecha_inicio__lte=fecha, fecha_fin__gte=fecha).first()

    @classmethod
    def para_fecha(cls, fecha):
        """Devuelve el ejercicio de esa fecha; si no existe, crea el año natural."""
        return cls.buscar(fecha) or cls.objects.create(
            anio=fecha.year,
            fecha_inicio=date(fecha.year, 1, 1),
            fecha_fin=date(fecha.year, 12, 31),
        )


class Asiento(models.Model):
    class Origen(models.TextChoices):
        MANUAL = "manual", "Manual"
        ALBARANES = "albaranes", "Albaranes"
        FACTURAS = "facturas", "Facturas"
        NOMINAS = "nominas", "Nóminas"
        BANCO = "banco", "Banco"

    class Estado(models.TextChoices):
        BORRADOR = "borrador", "Borrador"
        CONTABILIZADO = "contabilizado", "Contabilizado"

    ejercicio = models.ForeignKey(Ejercicio, on_delete=models.PROTECT, blank=True, related_name="asientos")
    numero = models.PositiveIntegerField("número", null=True, blank=True)
    fecha = models.DateField()
    concepto = models.CharField(max_length=255)
    origen = models.CharField(max_length=20, choices=Origen.choices, default=Origen.MANUAL)
    referencia_origen = models.CharField(
        max_length=100, blank=True, help_text="Identificador del documento de origen (p. ej. 'albaran:123')."
    )
    estado = models.CharField(max_length=20, choices=Estado.choices, default=Estado.BORRADOR)
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-fecha", "-numero"]
        verbose_name = "asiento"
        verbose_name_plural = "asientos"
        constraints = [
            models.UniqueConstraint(fields=["ejercicio", "numero"], name="asiento_numero_unico_por_ejercicio"),
        ]

    def __str__(self):
        return f"Asiento {self.numero} ({self.fecha:%d/%m/%Y}) {self.concepto}"

    def clean(self):
        if self.fecha:
            ejercicio = self.ejercicio if self.ejercicio_id else Ejercicio.buscar(self.fecha)
            if ejercicio and ejercicio.cerrado:
                raise ValidationError(f"El ejercicio {ejercicio} está cerrado.")

    def save(self, *args, **kwargs):
        if not self.ejercicio_id:
            self.ejercicio = Ejercicio.para_fecha(self.fecha)
        if self.numero is None:
            ultimo = Asiento.objects.filter(ejercicio=self.ejercicio).aggregate(m=Max("numero"))["m"]
            self.numero = (ultimo or 0) + 1
        super().save(*args, **kwargs)

    def totales(self):
        t = self.apuntes.aggregate(debe=Sum("debe"), haber=Sum("haber"))
        return t["debe"] or Decimal("0"), t["haber"] or Decimal("0")

    @property
    def cuadrado(self):
        debe, haber = self.totales()
        return debe == haber and debe > 0

    def contabilizar(self):
        if self.apuntes.count() < 2:
            raise ValidationError("Un asiento necesita al menos dos apuntes.")
        if not self.cuadrado:
            debe, haber = self.totales()
            raise ValidationError(f"El asiento no cuadra: Debe {debe} ≠ Haber {haber}.")
        self.estado = self.Estado.CONTABILIZADO
        self.save(update_fields=["estado"])


class Apunte(models.Model):
    asiento = models.ForeignKey(Asiento, on_delete=models.CASCADE, related_name="apuntes")
    orden = models.PositiveSmallIntegerField(default=0)
    cuenta = models.ForeignKey(CuentaContable, on_delete=models.PROTECT, related_name="apuntes")
    contrapartida = models.ForeignKey(
        CuentaContable, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    concepto = models.CharField(max_length=255, blank=True)
    debe = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"))
    haber = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"))
    departamento = models.CharField(max_length=20, choices=DEPARTAMENTOS, blank=True)

    class Meta:
        ordering = ["asiento", "orden", "id"]
        verbose_name = "apunte"
        verbose_name_plural = "apuntes"
        constraints = [
            models.CheckConstraint(
                condition=Q(debe__gt=0, haber=0) | Q(debe=0, haber__gt=0),
                name="apunte_debe_o_haber",
            ),
        ]

    def __str__(self):
        importe = f"D {self.debe}" if self.debe else f"H {self.haber}"
        return f"{self.cuenta.codigo} {importe}"

    def clean(self):
        debe, haber = self.debe or 0, self.haber or 0
        if debe < 0 or haber < 0:
            raise ValidationError("Los importes no pueden ser negativos.")
        if (debe > 0) == (haber > 0):
            raise ValidationError("Cada apunte debe llevar importe en el Debe o en el Haber (solo uno).")
        if self.cuenta_id and not self.cuenta.imputable:
            raise ValidationError({"cuenta": f"La cuenta {self.cuenta} no admite apuntes directos."})