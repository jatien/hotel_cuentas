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

LONGITUD_SUBCUENTA = 8
NIVELES = {1: "Grupo", 2: "Subgrupo", 3: "Cuenta", 4: "Cuenta", 5: "Cuenta", LONGITUD_SUBCUENTA: "Subcuenta"}

class CuentaContable(models.Model):
    codigo = models.CharField("código", max_length=12, unique=True)
    nombre = models.CharField(max_length=200)
    nif = models.CharField("NIF/CIF", max_length=20, blank=True)
    definicion = models.TextField("definición", blank=True)
    ubicacion = models.CharField("ubicación en el balance", max_length=300, blank=True)
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
        return NIVELES.get(len(self.codigo), "?")

    @property
    def es_subcuenta(self):
        return len(self.codigo) == LONGITUD_SUBCUENTA

    def heredado(self, campo):
        """Devuelve (cuenta, valor) del primer antecesor, incluida ella, con ese campo relleno."""
        cuenta = self
        while cuenta and not getattr(cuenta, campo):
            cuenta = cuenta.padre
        return (cuenta, getattr(cuenta, campo)) if cuenta else (None, "")

    def clean(self):
        if not self.codigo.isdigit():
            raise ValidationError({"codigo": "El código solo puede contener dígitos."})
        if len(self.codigo) not in NIVELES:
            raise ValidationError({"codigo": f"El código debe tener de 1 a 5 u {LONGITUD_SUBCUENTA} dígitos."})
        if self.padre and not self.codigo.startswith(self.padre.codigo):
            raise ValidationError({"padre": "El código debe empezar por el código de la cuenta padre."})

    def save(self, *args, **kwargs):
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

    class Tipo(models.TextChoices):
        APERTURA = "apertura", "Apertura"
        OPERACION = "operacion", "Operación"
        AJUSTE = "ajuste", "Ajuste"
        REGULARIZACION = "regularizacion", "Regularización"
        CIERRE = "cierre", "Cierre"

    # Solo puede haber uno de cada uno de estos por ejercicio
    TIPOS_UNICOS = (Tipo.APERTURA, Tipo.REGULARIZACION, Tipo.CIERRE)
    # Orden de numeración dentro del ejercicio: apertura, día a día, regularización, cierre
    PRIORIDAD = {Tipo.APERTURA: 0, Tipo.OPERACION: 1, Tipo.AJUSTE: 1, Tipo.REGULARIZACION: 2, Tipo.CIERRE: 3}

    ejercicio = models.ForeignKey(Ejercicio, on_delete=models.PROTECT, blank=True, related_name="asientos")
    numero = models.PositiveIntegerField("número", null=True, blank=True)
    fecha = models.DateField()
    tipo = models.CharField(max_length=20, choices=Tipo.choices, default=Tipo.OPERACION)
    concepto = models.CharField(max_length=255)
    origen = models.CharField(max_length=20, choices=Origen.choices, default=Origen.MANUAL)
    modelo = models.CharField(max_length=40, blank=True, help_text="Asiento modelo con el que se generó.")
    referencia_origen = models.CharField(
        max_length=100, blank=True, help_text="Documento de origen (nº de factura, 'albaran:123'...)."
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

    @property
    def clase(self):
        """Simple si mueve dos cuentas (una al Debe y otra al Haber), compuesto si mueve tres o más."""
        n = self.apuntes.count()
        return "Simple" if n == 2 else "Compuesto" if n > 2 else "Incompleto"

    def clean(self):
        if not self.fecha:
            return
        ejercicio = self.ejercicio if self.ejercicio_id else Ejercicio.buscar(self.fecha)
        inicio = ejercicio.fecha_inicio if ejercicio else date(self.fecha.year, 1, 1)
        fin = ejercicio.fecha_fin if ejercicio else date(self.fecha.year, 12, 31)

        if ejercicio and ejercicio.cerrado:
            raise ValidationError(f"El ejercicio {ejercicio} está cerrado.")
        if self.tipo == self.Tipo.APERTURA and self.fecha != inicio:
            raise ValidationError(f"El asiento de apertura va el primer día del ejercicio ({inicio:%d/%m/%Y}).")
        if self.tipo in (self.Tipo.REGULARIZACION, self.Tipo.CIERRE) and self.fecha != fin:
            raise ValidationError(
                f"El asiento de {self.get_tipo_display().lower()} va el último día del ejercicio ({fin:%d/%m/%Y}).")

        if ejercicio:
            otros = Asiento.objects.filter(ejercicio=ejercicio).exclude(pk=self.pk)
            if self.tipo in self.TIPOS_UNICOS and otros.filter(tipo=self.tipo).exists():
                raise ValidationError(
                    f"El ejercicio {ejercicio} ya tiene asiento de {self.get_tipo_display().lower()}.")
            if self.tipo != self.Tipo.CIERRE and otros.filter(tipo=self.Tipo.CIERRE).exists():
                raise ValidationError(
                    f"El ejercicio {ejercicio} ya tiene asiento de cierre: bórralo antes de añadir o cambiar asientos.")

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
