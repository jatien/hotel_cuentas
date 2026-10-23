from datetime import date
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Max, Q, Sum
from django.utils import timezone

LONGITUD_SUBCUENTA = 8
NIVELES = {1: "Grupo", 2: "Subgrupo", 3: "Cuenta", 4: "Cuenta", 5: "Cuenta", LONGITUD_SUBCUENTA: "Subcuenta"}

GRUPOS_PGC = {
    "1": "Financiación básica", "2": "Activo no corriente", "3": "Existencias",
    "4": "Acreedores y deudores por operaciones comerciales", "5": "Cuentas financieras",
    "6": "Compras y gastos", "7": "Ventas e ingresos", "8": "Gastos imputados al patrimonio neto",
    "9": "Ingresos imputados al patrimonio neto",
}


class Naturaleza(models.TextChoices):
    ACTIVO = "activo", "Activo"
    CORRECTORA = "correctora", "Correctora de activo"
    PATRIMONIO_NETO = "patrimonio_neto", "Patrimonio neto"
    PASIVO = "pasivo", "Pasivo"
    MIXTA = "mixta", "Activo o pasivo según su saldo"
    GASTO = "gasto", "Gasto"
    INGRESO = "ingreso", "Ingreso"
    GASTO_PN = "gasto_pn", "Gasto imputado al PN"
    INGRESO_PN = "ingreso_pn", "Ingreso imputado al PN"


# Naturaleza según el PGC 2007 (gana el prefijo más largo). Se puede cambiar cuenta a cuenta.
NATURALEZA_PGC = {
    "1": Naturaleza.PATRIMONIO_NETO, "14": Naturaleza.PASIVO, "15": Naturaleza.PASIVO,
    "16": Naturaleza.PASIVO, "17": Naturaleza.PASIVO, "18": Naturaleza.PASIVO,
    "2": Naturaleza.ACTIVO, "28": Naturaleza.CORRECTORA, "29": Naturaleza.CORRECTORA,
    "3": Naturaleza.ACTIVO, "39": Naturaleza.CORRECTORA,
    "40": Naturaleza.PASIVO, "41": Naturaleza.PASIVO, "43": Naturaleza.ACTIVO, "44": Naturaleza.ACTIVO,
    "46": Naturaleza.MIXTA, "460": Naturaleza.ACTIVO, "465": Naturaleza.PASIVO, "466": Naturaleza.PASIVO,
    "47": Naturaleza.MIXTA, "470": Naturaleza.ACTIVO, "471": Naturaleza.ACTIVO, "472": Naturaleza.ACTIVO,
    "473": Naturaleza.ACTIVO, "474": Naturaleza.ACTIVO, "475": Naturaleza.PASIVO, "476": Naturaleza.PASIVO,
    "477": Naturaleza.PASIVO, "479": Naturaleza.PASIVO,
    "48": Naturaleza.MIXTA, "480": Naturaleza.ACTIVO, "485": Naturaleza.PASIVO,
    "49": Naturaleza.CORRECTORA, "499": Naturaleza.PASIVO,
    "50": Naturaleza.PASIVO, "51": Naturaleza.PASIVO, "52": Naturaleza.PASIVO,
    "53": Naturaleza.ACTIVO, "54": Naturaleza.ACTIVO, "55": Naturaleza.MIXTA, "56": Naturaleza.MIXTA,
    "560": Naturaleza.PASIVO, "561": Naturaleza.PASIVO, "565": Naturaleza.ACTIVO, "566": Naturaleza.ACTIVO,
    "57": Naturaleza.ACTIVO, "58": Naturaleza.MIXTA, "59": Naturaleza.CORRECTORA,
    "6": Naturaleza.GASTO, "7": Naturaleza.INGRESO, "8": Naturaleza.GASTO_PN, "9": Naturaleza.INGRESO_PN,
}


def formato_importe(valor):
    """1.234,56"""
    return f"{valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def naturaleza_pgc(codigo):
    for largo in range(len(codigo), 0, -1):
        if codigo[:largo] in NATURALEZA_PGC:
            return NATURALEZA_PGC[codigo[:largo]]
    return ""


class TipoDocumento(models.TextChoices):
    FACTURA_RECIBIDA = "factura_recibida", "Factura recibida"
    FACTURA_EMITIDA = "factura_emitida", "Factura emitida"
    RECIBO = "recibo", "Recibo"
    EXTRACTO = "extracto", "Extracto bancario"
    NOMINA = "nomina", "Nómina"
    ALBARAN = "albaran", "Albarán"
    OTRO = "otro", "Otro justificante"


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
    naturaleza = models.CharField(
        max_length=20, choices=Naturaleza.choices, blank=True,
        help_text="Vacío = la que marca el PGC 2007 para su código.")

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

    @property
    def naturaleza_efectiva(self):
        return self.naturaleza or naturaleza_pgc(self.codigo)

    @property
    def naturaleza_label(self):
        valor = self.naturaleza_efectiva
        return Naturaleza(valor).label if valor else ""

    @property
    def grupo(self):
        return self.codigo[:1]

    @property
    def grupo_label(self):
        return GRUPOS_PGC.get(self.grupo, "")

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

class DatosEmpresa(models.Model):
    """Empresa cuya contabilidad se lleva (identificación de la norma 2ª.3). Por ahora un único
    registro: el plan de cuentas y la numeración de asientos son de una sola entidad."""
    codigo = models.CharField("código", max_length=10, default="01")
    denominacion = models.CharField("denominación social", max_length=200)
    nif = models.CharField("NIF", max_length=20, blank=True)
    forma_juridica = models.CharField("forma jurídica", max_length=100, blank=True,
                                      help_text="Sociedad limitada, sociedad anónima, comunidad de bienes...")
    domicilio = models.CharField(max_length=255, blank=True)
    actividad = models.CharField(max_length=255, blank=True, help_text="Por ejemplo: explotación hotelera.")
    moneda = models.CharField("moneda funcional", max_length=3, default="EUR",
                              help_text="Código ISO 4217. Todos los importes se registran en esta moneda.")

    class Meta:
        verbose_name = "datos de la empresa"
        verbose_name_plural = "datos de la empresa"

    def __str__(self):
        return self.denominacion

    @classmethod
    def actual(cls):
        return cls.objects.first()
    
class Ejercicio(models.Model):
    empresa = models.ForeignKey(DatosEmpresa, on_delete=models.PROTECT, null=True, blank=True,
                                related_name="ejercicios")
    anio = models.PositiveIntegerField("año", unique=True)
    fecha_inicio = models.DateField()
    fecha_fin = models.DateField()
    cerrado = models.BooleanField(default=False)
    trabajadores_medios = models.DecimalField(
        "número medio de trabajadores", max_digits=7, decimal_places=2, null=True, blank=True,
        help_text="Para saber si se pueden presentar cuentas anuales abreviadas (norma 4ª).")
    fecha_formulacion = models.DateField(
        "fecha de formulación", null=True, blank=True,
        help_text="Fecha en que se formulan las cuentas anuales (como máximo, tres meses tras el cierre).")

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
            empresa=DatosEmpresa.actual(),
            anio=fecha.year,
            fecha_inicio=date(fecha.year, 1, 1),
            fecha_fin=date(fecha.year, 12, 31),
        )

    def clean(self):
        if self.fecha_inicio and self.fecha_fin and self.fecha_inicio > self.fecha_fin:
            raise ValidationError("La fecha de cierre es anterior a la de inicio.")
        if self.fecha_inicio and self.fecha_fin:
            solapa = Ejercicio.objects.filter(fecha_inicio__lte=self.fecha_fin,
                                              fecha_fin__gte=self.fecha_inicio).exclude(pk=self.pk)
            if solapa.exists():
                raise ValidationError(f"Las fechas se solapan con el ejercicio {solapa.first()}.")


class Hoja(models.Model):
    """Hoja de trabajo: agrupa asientos (por ejemplo «Bancos», «Compras», «Revisar») para
    trabajar con ellos por separado en el libro diario."""

    class Color(models.TextChoices):
        AZUL = "#2D4FC4", "Azul"
        VERDE = "#0E7A6D", "Verde"
        GRANATE = "#9B3D5B", "Granate"
        AMBAR = "#B7791F", "Ámbar"
        MORADO = "#6B46C1", "Morado"
        GRIS = "#5B6878", "Gris"

    nombre = models.CharField(max_length=80, unique=True)
    descripcion = models.CharField("descripción", max_length=255, blank=True)
    color = models.CharField(max_length=7, choices=Color.choices, default=Color.AZUL)

    class Meta:
        ordering = ["nombre"]
        verbose_name = "hoja"
        verbose_name_plural = "hojas"

    def __str__(self):
        return self.nombre


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
        ANULADO = "anulado", "Anulado"

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
    hoja = models.ForeignKey(Hoja, on_delete=models.SET_NULL, null=True, blank=True, related_name="asientos")
    fecha = models.DateField()
    tipo = models.CharField(max_length=20, choices=Tipo.choices, default=Tipo.OPERACION)
    titulo = models.CharField("título", max_length=120, blank=True)
    concepto = models.CharField(max_length=255)
    numero_externo = models.CharField("número", max_length=60, blank=True, db_index=True,
                                      help_text="Referencia externa: nº de factura, recibo...")
    tipo_documento = models.CharField("tipo de documento", max_length=20, choices=TipoDocumento.choices,
                                      blank=True)
    fecha_documento = models.DateField("fecha del documento", null=True, blank=True)
    origen = models.CharField(max_length=20, choices=Origen.choices, default=Origen.MANUAL)
    modelo = models.CharField(max_length=40, blank=True, help_text="Asiento modelo con el que se generó.")
    referencia_origen = models.CharField(
        max_length=100, blank=True, help_text="Documento de origen (nº de factura, 'albaran:123'...)."
    )
    estado = models.CharField(max_length=20, choices=Estado.choices, default=Estado.BORRADOR)
    creado = models.DateTimeField(auto_now_add=True)
    creado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
                                   blank=True, related_name="+")
    contabilizado_en = models.DateTimeField("fecha de contabilización", null=True, blank=True)
    contabilizado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
                                          blank=True, related_name="+")
    anulado_en = models.DateTimeField(null=True, blank=True)
    motivo_anulacion = models.CharField("motivo de la anulación", max_length=255, blank=True)
    revierte = models.ForeignKey("self", on_delete=models.PROTECT, null=True, blank=True,
                                 related_name="reversiones", help_text="Asiento que este anula con signo contrario.")
    rectifica = models.ForeignKey("self", on_delete=models.PROTECT, null=True, blank=True,
                                  related_name="rectificaciones", help_text="Asiento al que sustituye.")

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
            otros = (Asiento.objects.filter(ejercicio=ejercicio).exclude(pk=self.pk)
                     .exclude(estado=self.Estado.ANULADO))
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

    @property
    def editable(self):
        """Solo los borradores de un ejercicio abierto se modifican. Lo contabilizado se
        corrige con una reversión o una rectificación, nunca en silencio."""
        return self.estado == self.Estado.BORRADOR and not self.ejercicio.cerrado

    @property
    def titulo_o_concepto(self):
        return self.titulo or self.concepto

    def posibles_duplicados(self):
        """Asientos vigentes del ejercicio que parecen la misma operación registrada dos veces:
        mismo número externo e importe, o (sin número) misma fecha, concepto, importe y cuentas."""
        debe, _ = self.totales()
        cuentas = set(self.apuntes.values_list("cuenta_id", flat=True))
        otros = (Asiento.objects.filter(ejercicio=self.ejercicio)
                 .exclude(pk=self.pk).exclude(estado=self.Estado.ANULADO)
                 .exclude(tipo__in=self.TIPOS_UNICOS)
                 .annotate(total=Sum("apuntes__debe")).filter(total=debe))
        if self.numero_externo:
            return list(otros.filter(numero_externo__iexact=self.numero_externo))
        candidatos = otros.filter(fecha=self.fecha, numero_externo="", concepto__iexact=self.concepto)
        return [o for o in candidatos.prefetch_related("apuntes")
                if {x.cuenta_id for x in o.apuntes.all()} == cuentas]

    def errores_contabilizacion(self):
        """Comprobaciones previas a contabilizar (punto 6.3 de la especificación)."""
        errores = []
        apuntes = list(self.apuntes.select_related("cuenta"))
        if len(apuntes) < 2:
            errores.append("Un asiento necesita al menos dos apuntes.")
        for i, a in enumerate(sorted(apuntes, key=lambda x: (x.orden, x.id)), 1):
            if not a.cuenta.imputable:
                errores.append(f"Apunte {i}: la cuenta {a.cuenta.codigo} no admite apuntes directos.")
            if not a.cuenta.activa:
                errores.append(f"Apunte {i}: la cuenta {a.cuenta.codigo} está inactiva.")
            if (a.debe > 0) == (a.haber > 0) or a.debe < 0 or a.haber < 0:
                errores.append(f"Apunte {i}: importe solo en el Debe o solo en el Haber, positivo.")
        debe = sum((a.debe for a in apuntes), Decimal("0"))
        haber = sum((a.haber for a in apuntes), Decimal("0"))
        if apuntes and debe != haber:
            errores.append(f"El asiento no cuadra: Debe {formato_importe(debe)} ≠ Haber {formato_importe(haber)} "
                           f"(diferencia {formato_importe(abs(debe - haber))}).")
        if not (self.concepto or "").strip():
            errores.append("Falta el concepto del asiento.")
        if not (self.ejercicio.fecha_inicio <= self.fecha <= self.ejercicio.fecha_fin):
            errores.append(f"La fecha {self.fecha:%d/%m/%Y} no pertenece al ejercicio {self.ejercicio}.")
        if self.ejercicio.cerrado:
            errores.append(f"El ejercicio {self.ejercicio} está cerrado.")
        return errores

    def contabilizar(self, usuario=None, permitir_duplicado=False):
        if self.estado == self.Estado.ANULADO:
            raise ValidationError("Un asiento anulado no se puede contabilizar.")
        if self.estado == self.Estado.CONTABILIZADO:
            return
        if errores := self.errores_contabilizacion():
            raise ValidationError(errores)
        if not permitir_duplicado and (duplicados := self.posibles_duplicados()):
            lista = ", ".join(f"nº {d.numero} del {d.fecha:%d/%m/%Y}" for d in duplicados[:3])
            raise ValidationError(f"Posible asiento duplicado ({lista}). Revísalo o confirma que no es un duplicado.",
                                  code="duplicado")
        self.estado = self.Estado.CONTABILIZADO
        self.contabilizado_en = timezone.now()
        self.contabilizado_por = _usuario(usuario)
        self.save(update_fields=["estado", "contabilizado_en", "contabilizado_por"])
        HistorialAsiento.registrar(self, HistorialAsiento.Accion.CONTABILIZAR, usuario)

    def anular(self, motivo, usuario=None):
        """Deja el asiento sin efecto conservándolo (con su número) para la trazabilidad.
        Deja de sumar en diario, mayor y balances."""
        motivo = (motivo or "").strip()
        if self.estado != self.Estado.CONTABILIZADO:
            raise ValidationError("Solo se anulan asientos contabilizados; un borrador se puede eliminar.")
        if self.ejercicio.cerrado:
            raise ValidationError(f"El ejercicio {self.ejercicio} está cerrado.")
        if not motivo:
            raise ValidationError("Indica el motivo de la anulación.")
        if self.reversiones.exclude(estado=self.Estado.ANULADO).exists():
            raise ValidationError("Este asiento ya está revertido: anula antes la reversión.")
        self.estado = self.Estado.ANULADO
        self.anulado_en = timezone.now()
        self.motivo_anulacion = motivo[:255]
        self.save(update_fields=["estado", "anulado_en", "motivo_anulacion"])
        HistorialAsiento.registrar(self, HistorialAsiento.Accion.ANULAR, usuario, motivo)


def _usuario(usuario):
    return usuario if usuario is not None and getattr(usuario, "is_authenticated", False) else None


class ApunteQuerySet(models.QuerySet):
    def vigentes(self):
        """Apuntes que cuentan: todo menos los asientos anulados."""
        return self.exclude(asiento__estado=Asiento.Estado.ANULADO)

    def contabilizados(self):
        return self.filter(asiento__estado=Asiento.Estado.CONTABILIZADO)


class Apunte(models.Model):
    class Punteo(models.TextChoices):
        SIN = "", "Sin puntear"
        PUNTEADO = "punteado", "Punteado"
        REVISAR = "revisar", "Pendiente de revisión"
        DISCREPANCIA = "discrepancia", "Discrepancia"
        CONCILIADO = "conciliado", "Conciliado con el banco"

    # El punteo manual nunca marca «conciliado»: eso lo hará la conciliación bancaria formal
    PUNTEO_MANUAL = (Punteo.SIN, Punteo.PUNTEADO, Punteo.REVISAR, Punteo.DISCREPANCIA)

    objects = ApunteQuerySet.as_manager()

    asiento = models.ForeignKey(Asiento, on_delete=models.CASCADE, related_name="apuntes")
    orden = models.PositiveSmallIntegerField(default=0)
    cuenta = models.ForeignKey(CuentaContable, on_delete=models.PROTECT, related_name="apuntes")
    contrapartida = models.ForeignKey(
        CuentaContable, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    concepto = models.CharField(max_length=255, blank=True)
    debe = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"))
    haber = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"))
    punteo = models.CharField(max_length=20, choices=Punteo.choices, blank=True, default="", db_index=True)
    punteo_nota = models.CharField("nota del punteo", max_length=255, blank=True)
    punteado_en = models.DateTimeField(null=True, blank=True)
    punteado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
                                     blank=True, related_name="+")

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


class Adjunto(models.Model):
    """Documento justificativo de un asiento (factura, recibo, extracto, nómina...). Se guarda
    aparte de la cabecera y de las líneas."""
    asiento = models.ForeignKey(Asiento, on_delete=models.CASCADE, related_name="adjuntos")
    tipo = models.CharField(max_length=20, choices=TipoDocumento.choices, default=TipoDocumento.OTRO)
    numero = models.CharField("número", max_length=60, blank=True)
    archivo = models.FileField(upload_to="contabilidad/%Y/%m/", blank=True)
    enlace = models.CharField(max_length=500, blank=True, help_text="Enlace a un documento de otra app.")
    nombre = models.CharField(max_length=200, blank=True)
    subido_en = models.DateTimeField(auto_now_add=True)
    subido_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
                                   blank=True, related_name="+")

    class Meta:
        ordering = ["subido_en"]
        verbose_name = "documento adjunto"
        verbose_name_plural = "documentos adjuntos"

    def __str__(self):
        return self.nombre or self.numero or self.get_tipo_display()

    @property
    def url(self):
        return self.archivo.url if self.archivo else self.enlace

    def clean(self):
        if not self.archivo and not self.enlace and not self.numero:
            raise ValidationError("Adjunta un archivo, un enlace o al menos el número del documento.")


class HistorialAsiento(models.Model):
    """Registro de las acciones relevantes sobre los asientos. No se borra aunque se borre
    el asiento (queda su número y ejercicio)."""

    class Accion(models.TextChoices):
        CREAR = "crear", "Creación"
        MODIFICAR = "modificar", "Modificación"
        CONTABILIZAR = "contabilizar", "Contabilización"
        ANULAR = "anular", "Anulación"
        REVERTIR = "revertir", "Reversión"
        RECTIFICAR = "rectificar", "Rectificación"
        BORRAR = "borrar", "Borrado de borrador"
        PUNTEO = "punteo", "Punteo"
        DOCUMENTO = "documento", "Documento"

    asiento = models.ForeignKey(Asiento, on_delete=models.SET_NULL, null=True, blank=True,
                                related_name="historial")
    ejercicio = models.PositiveIntegerField(null=True, blank=True)
    numero = models.PositiveIntegerField(null=True, blank=True)
    accion = models.CharField(max_length=20, choices=Accion.choices)
    detalle = models.TextField(blank=True)
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
                                blank=True, related_name="+")
    fecha = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-fecha", "-id"]
        verbose_name = "historial de asiento"
        verbose_name_plural = "historial de asientos"

    def __str__(self):
        return f"{self.get_accion_display()} asiento {self.numero}/{self.ejercicio}"

    @classmethod
    def registrar(cls, asiento, accion, usuario=None, detalle=""):
        return cls.objects.create(asiento=asiento if asiento.pk else None, ejercicio=asiento.ejercicio.anio,
                                  numero=asiento.numero, accion=accion, detalle=detalle,
                                  usuario=_usuario(usuario))


class PreferenciaTabla(models.Model):
    """Columnas visibles, orden y anchos de una tabla para un usuario. Solo cambia cómo se ve."""
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    tabla = models.CharField(max_length=40)
    columnas = models.JSONField(default=dict)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["usuario", "tabla"], name="preferencia_tabla_unica")]
