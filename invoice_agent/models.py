"""
Modelos de la app invoice_agent (Detección de facturas con IA).

Esquema (una tabla por concepto, ver README de la app):

    Empresa             entidad legal que recibe las facturas (inquilino / tenant)
    Documento           un fichero subido; lleva el estado de la máquina de estados
    Trabajo             cola en base de datos (bandeja de salida transaccional)
    Intento             cada intento de procesado, con tiempos, tokens y coste
    Extraccion          resultado inmutable de un intento correcto (lo que dijo el modelo)
    ExtraccionLinea     líneas de esa extracción
    ExtraccionImpuesto  desglose de impuestos y retenciones de esa extracción
    Borrador            datos revisables por una persona (uno por documento)
    BorradorLinea       líneas revisadas
    BorradorImpuesto    impuestos revisados
    EventoRevision      registro de auditoría de cada acción y corrección

Reglas:
- Importes siempre en NUMERIC/Decimal, nunca float.
- Lo extraído (Extraccion*) no se modifica nunca; las correcciones van al
  Borrador y quedan en EventoRevision con el valor extraído, el anterior y el nuevo.
- Nada de esta app escribe en la contabilidad (cuadro_cuentas).
"""

import uuid

from django.conf import settings
from django.db import models
from django.db.models import Q

MONEY = {"max_digits": 14, "decimal_places": 2}
RATE = {"max_digits": 6, "decimal_places": 3}


# --------------------------------------------------------------------------- #
# Enumeraciones
# --------------------------------------------------------------------------- #
class EstadoDocumento(models.TextChoices):
    """Estados del documento. Las transiciones permitidas están en services/states.py."""

    QUEUED = "queued", "En cola"
    PROCESSING = "processing", "Procesando"
    NEEDS_REVIEW = "needs_review", "Necesita revisión"
    PENDING_APPROVAL = "pending_approval", "Pendiente de aprobación"
    APPROVED = "approved", "Aprobada"
    REJECTED = "rejected", "Rechazada"
    FAILED = "failed", "Error"


class TipoFuente(models.TextChoices):
    PDF = "pdf", "PDF"
    IMAGE = "image", "Imagen"


class MetodoExtraccion(models.TextChoices):
    NATIVE = "native", "Texto nativo del PDF"
    OCR = "ocr", "OCR"
    VISION = "vision", "Visión (imagen al modelo)"
    MIXED = "mixed", "Mixto"


class EstadoTrabajo(models.TextChoices):
    PENDING = "pending", "Pendiente"
    RUNNING = "running", "En ejecución"
    DONE = "done", "Terminado"
    DEAD = "dead", "Fallido definitivamente"


class ResultadoIntento(models.TextChoices):
    RUNNING = "running", "En curso"
    SUCCESS = "success", "Correcto"
    RETRYABLE_ERROR = "retryable_error", "Error transitorio"
    FATAL_ERROR = "fatal_error", "Error definitivo"


class TipoImpuesto(models.TextChoices):
    VAT = "vat", "IVA"
    IGIC = "igic", "IGIC"
    IPSI = "ipsi", "IPSI"
    SURCHARGE = "surcharge", "Recargo de equivalencia"
    WITHHOLDING = "withholding", "Retención (IRPF u otra)"
    OTHER = "other", "Otro impuesto"


class AccionRevision(models.TextChoices):
    UPLOADED = "uploaded", "Subida"
    EXTRACTED = "extracted", "Extraída"
    STATUS = "status", "Cambio de estado"
    CORRECTED = "corrected", "Corrección"
    ACKNOWLEDGED = "acknowledged", "Aviso aceptado"
    APPROVED = "approved", "Aprobada"
    REJECTED = "rejected", "Rechazada"
    REPROCESS = "reprocess", "Reprocesado solicitado"
    FILE_ACCESS = "file_access", "Acceso al original"


# --------------------------------------------------------------------------- #
# Inquilino
# --------------------------------------------------------------------------- #
class Empresa(models.Model):
    """
    Entidad legal que recibe facturas. Todo documento pertenece a una, y
    todas las consultas se filtran por las empresas a las que se tiene acceso
    (services/access.py). `usuarios` solo se usa cuando el login está activo.
    """

    nif = models.CharField("NIF", max_length=20, unique=True)
    nombre = models.CharField(max_length=200)
    activa = models.BooleanField(default=True)
    usuarios = models.ManyToManyField(
        settings.AUTH_USER_MODEL, blank=True, related_name="empresas_facturas_ia"
    )

    class Meta:
        ordering = ["nombre"]
        verbose_name = "empresa"
        verbose_name_plural = "empresas"

    def __str__(self) -> str:
        return f"{self.nombre} ({self.nif})"


# --------------------------------------------------------------------------- #
# Documento y cola
# --------------------------------------------------------------------------- #
class Documento(models.Model):
    """Un fichero recibido. `public_id` es el identificador que ve la API (no enumerable)."""

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    empresa = models.ForeignKey(Empresa, on_delete=models.PROTECT, related_name="documentos")

    # Ruta relativa dentro del almacenamiento privado (nunca bajo MEDIA_ROOT público).
    storage_key = models.CharField(max_length=500)
    original_filename = models.CharField(max_length=255)
    sha256 = models.CharField(max_length=64)
    mime_type = models.CharField(max_length=100)
    size_bytes = models.PositiveBigIntegerField()
    tipo_fuente = models.CharField(max_length=10, choices=TipoFuente.choices)
    page_count = models.PositiveIntegerField(null=True, blank=True)

    estado = models.CharField(
        max_length=20, choices=EstadoDocumento.choices, default=EstadoDocumento.QUEUED, db_index=True
    )
    estado_cambiado = models.DateTimeField(auto_now_add=True)
    error_code = models.CharField(max_length=60, blank=True)
    error_message = models.TextField(blank=True)

    correlation_id = models.UUIDField(default=uuid.uuid4, editable=False)
    subido_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    subido_desde = models.CharField(max_length=100, blank=True, help_text="Identificador del actor (IP o usuario).")
    creado = models.DateTimeField(auto_now_add=True)
    borrar_despues_de = models.DateField(
        null=True, blank=True, help_text="Fecha a partir de la cual la política de retención puede borrar el original."
    )

    class Meta:
        ordering = ["-creado"]
        verbose_name = "documento"
        verbose_name_plural = "documentos"
        constraints = [
            # Idempotencia de la subida: el mismo fichero en la misma empresa es el mismo documento.
            models.UniqueConstraint(fields=["empresa", "sha256"], name="ia_doc_unico_por_empresa_y_hash"),
        ]
        indexes = [models.Index(fields=["empresa", "estado", "-creado"], name="ia_doc_empresa_estado")]

    def __str__(self) -> str:
        return f"{self.original_filename} ({self.get_estado_display()})"


class Trabajo(models.Model):
    """
    Cola en la base de datos. Se crea en la MISMA transacción que el
    documento (patrón de bandeja de salida transaccional): si el commit falla,
    no hay trabajo huérfano; si el commit va bien, el worker lo verá seguro.
    """

    documento = models.ForeignKey(Documento, on_delete=models.CASCADE, related_name="trabajos")
    estado = models.CharField(max_length=10, choices=EstadoTrabajo.choices, default=EstadoTrabajo.PENDING)
    intentos = models.PositiveIntegerField(default=0)
    max_intentos = models.PositiveIntegerField(default=3)
    ejecutar_desde = models.DateTimeField()
    bloqueado_en = models.DateTimeField(null=True, blank=True)
    bloqueado_por = models.CharField(max_length=100, blank=True)
    ultimo_error = models.TextField(blank=True)
    creado = models.DateTimeField(auto_now_add=True)
    terminado = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["ejecutar_desde", "id"]
        constraints = [
            # Nunca dos trabajos activos para el mismo documento (reprocesar dos veces = un solo trabajo).
            models.UniqueConstraint(
                fields=["documento"],
                condition=Q(estado__in=["pending", "running"]),
                name="ia_un_trabajo_activo_por_documento",
            )
        ]
        indexes = [models.Index(fields=["estado", "ejecutar_desde"], name="ia_trabajo_cola")]

    def __str__(self) -> str:
        return f"Trabajo {self.pk} ({self.estado}) de {self.documento_id}"


class Intento(models.Model):
    """Un intento de procesado (preparación + modelo + validación), con métricas."""

    documento = models.ForeignKey(Documento, on_delete=models.CASCADE, related_name="intentos")
    trabajo = models.ForeignKey(Trabajo, null=True, on_delete=models.SET_NULL, related_name="intentos_rel")
    numero = models.PositiveIntegerField()
    resultado = models.CharField(max_length=20, choices=ResultadoIntento.choices, default=ResultadoIntento.RUNNING)
    error_code = models.CharField(max_length=60, blank=True)
    error_message = models.TextField(blank=True)

    proveedor_ia = models.CharField(max_length=60, blank=True)
    modelo = models.CharField(max_length=100, blank=True)
    prompt_version = models.CharField(max_length=40, blank=True)
    schema_version = models.CharField(max_length=40, blank=True)
    metodo = models.CharField(max_length=10, choices=MetodoExtraccion.choices, blank=True)
    preparacion = models.JSONField(
        default=dict, blank=True, help_text="Metadatos de preparación por página (método, caracteres, OCR, rotación)."
    )
    llamadas_modelo = models.PositiveIntegerField(default=0)
    tokens_entrada = models.PositiveIntegerField(default=0)
    tokens_salida = models.PositiveIntegerField(default=0)
    coste_estimado = models.DecimalField(max_digits=12, decimal_places=6, default=0)
    duracion_ms = models.PositiveIntegerField(null=True, blank=True)
    inicio = models.DateTimeField(auto_now_add=True)
    fin = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["documento", "numero"]
        constraints = [models.UniqueConstraint(fields=["documento", "numero"], name="ia_intento_numero_unico")]


# --------------------------------------------------------------------------- #
# Extracción (inmutable)
# --------------------------------------------------------------------------- #
class Extraccion(models.Model):
    """
    Lo que devolvió el modelo para un intento, ya normalizado. Inmutable.
    `evidencias` guarda por campo: estado (present/absent/unreadable/missing),
    confianza declarada por el modelo (low/medium/high, NO es una
    probabilidad calibrada), página y texto de origen.
    """

    documento = models.ForeignKey(Documento, on_delete=models.CASCADE, related_name="extracciones")
    intento = models.OneToOneField(Intento, on_delete=models.CASCADE, related_name="extraccion")
    actual = models.BooleanField(default=True)

    proveedor_nombre = models.CharField(max_length=255, blank=True)
    proveedor_nif = models.CharField(max_length=40, blank=True)
    proveedor_direccion = models.CharField(max_length=500, blank=True)
    proveedor_pais = models.CharField(max_length=2, blank=True)
    cliente_nif = models.CharField(max_length=40, blank=True)

    numero_factura = models.CharField(max_length=100, blank=True)
    fecha_factura = models.DateField(null=True, blank=True)
    fecha_vencimiento = models.DateField(null=True, blank=True)
    moneda = models.CharField(max_length=3, blank=True)
    referencia_pedido = models.CharField(max_length=100, blank=True)

    subtotal_neto = models.DecimalField(**MONEY, null=True, blank=True)
    descuentos = models.DecimalField(**MONEY, null=True, blank=True)
    cargos = models.DecimalField(**MONEY, null=True, blank=True)
    total_impuestos = models.DecimalField(**MONEY, null=True, blank=True)
    total_retenciones = models.DecimalField(**MONEY, null=True, blank=True)
    total_bruto = models.DecimalField(**MONEY, null=True, blank=True)
    importe_a_pagar = models.DecimalField(**MONEY, null=True, blank=True)

    evidencias = models.JSONField(default=dict, blank=True)
    validacion = models.JSONField(default=dict, blank=True, help_text="Errores y avisos de la validación determinista.")
    salida_modelo = models.JSONField(default=dict, blank=True, help_text="Salida estructurada original, para auditoría.")
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["documento", "-creado"]
        constraints = [
            models.UniqueConstraint(
                fields=["documento"], condition=Q(actual=True), name="ia_una_extraccion_actual_por_documento"
            )
        ]


class _LineaBase(models.Model):
    orden = models.PositiveIntegerField()
    descripcion = models.CharField(max_length=500, blank=True)
    cantidad = models.DecimalField(max_digits=14, decimal_places=4, null=True, blank=True)
    precio_unitario = models.DecimalField(max_digits=14, decimal_places=6, null=True, blank=True)
    descuento = models.DecimalField(**MONEY, null=True, blank=True)
    tipo_impositivo = models.DecimalField(**RATE, null=True, blank=True)
    subtotal = models.DecimalField(**MONEY, null=True, blank=True)
    total = models.DecimalField(**MONEY, null=True, blank=True)

    class Meta:
        abstract = True
        ordering = ["orden"]


class _ImpuestoBase(models.Model):
    tipo = models.CharField(max_length=15, choices=TipoImpuesto.choices, default=TipoImpuesto.VAT)
    porcentaje = models.DecimalField(**RATE, null=True, blank=True)
    base = models.DecimalField(**MONEY, null=True, blank=True)
    cuota = models.DecimalField(**MONEY, null=True, blank=True)

    class Meta:
        abstract = True
        ordering = ["tipo", "-porcentaje"]


class ExtraccionLinea(_LineaBase):
    extraccion = models.ForeignKey(Extraccion, on_delete=models.CASCADE, related_name="lineas")
    pagina = models.PositiveIntegerField(null=True, blank=True)

    class Meta(_LineaBase.Meta):
        constraints = [models.UniqueConstraint(fields=["extraccion", "orden"], name="ia_ext_linea_orden")]


class ExtraccionImpuesto(_ImpuestoBase):
    extraccion = models.ForeignKey(Extraccion, on_delete=models.CASCADE, related_name="impuestos")


# --------------------------------------------------------------------------- #
# Borrador revisable
# --------------------------------------------------------------------------- #
class Borrador(models.Model):
    """
    Los datos que revisa y aprueba una persona. Uno por documento (reprocesar
    actualiza este mismo borrador, nunca crea otro). Aprobar NO contabiliza.
    `proveedor_nif_norm` y `numero_norm` son la clave de negocio para duplicados.
    """

    documento = models.OneToOneField(Documento, on_delete=models.CASCADE, related_name="borrador")
    empresa = models.ForeignKey(Empresa, on_delete=models.PROTECT, related_name="borradores")
    extraccion = models.ForeignKey(Extraccion, null=True, on_delete=models.SET_NULL, related_name="borradores")
    proveedor = models.ForeignKey(
        "invoice_data.Proveedor", null=True, blank=True, on_delete=models.SET_NULL, related_name="borradores_ia"
    )

    proveedor_nombre = models.CharField(max_length=255, blank=True)
    proveedor_nif = models.CharField(max_length=40, blank=True)
    proveedor_direccion = models.CharField(max_length=500, blank=True)
    proveedor_pais = models.CharField(max_length=2, blank=True)
    numero_factura = models.CharField(max_length=100, blank=True)
    fecha_factura = models.DateField(null=True, blank=True)
    fecha_vencimiento = models.DateField(null=True, blank=True)
    moneda = models.CharField(max_length=3, blank=True)
    referencia_pedido = models.CharField(max_length=100, blank=True)

    subtotal_neto = models.DecimalField(**MONEY, null=True, blank=True)
    descuentos = models.DecimalField(**MONEY, null=True, blank=True)
    cargos = models.DecimalField(**MONEY, null=True, blank=True)
    total_impuestos = models.DecimalField(**MONEY, null=True, blank=True)
    total_retenciones = models.DecimalField(**MONEY, null=True, blank=True)
    total_bruto = models.DecimalField(**MONEY, null=True, blank=True)
    importe_a_pagar = models.DecimalField(**MONEY, null=True, blank=True)

    proveedor_nif_norm = models.CharField(max_length=40, blank=True)
    numero_norm = models.CharField(max_length=100, blank=True)

    incidencias = models.JSONField(default=list, blank=True, help_text="Errores y avisos de la última validación.")
    avisos_aceptados = models.JSONField(default=list, blank=True, help_text="Códigos de aviso aceptados por el revisor.")
    corregido = models.BooleanField(default=False)

    aprobado_en = models.DateTimeField(null=True, blank=True)
    aprobado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    aprobado_por_label = models.CharField(max_length=150, blank=True)
    rechazado_en = models.DateTimeField(null=True, blank=True)
    motivo_rechazo = models.TextField(blank=True)

    creado = models.DateTimeField(auto_now_add=True)
    actualizado = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "borrador de factura"
        verbose_name_plural = "borradores de factura"
        indexes = [models.Index(fields=["empresa", "proveedor_nif_norm", "numero_norm"], name="ia_borrador_clave")]
        constraints = [
            # La base de datos impide aprobar dos veces la misma factura (mismo proveedor + número) en una empresa.
            models.UniqueConstraint(
                fields=["empresa", "proveedor_nif_norm", "numero_norm"],
                condition=Q(aprobado_en__isnull=False) & ~Q(proveedor_nif_norm="") & ~Q(numero_norm=""),
                name="ia_aprobada_unica_por_clave",
            )
        ]

    def __str__(self) -> str:
        return f"Borrador {self.numero_factura or '?'} de {self.proveedor_nombre or '?'}"


class BorradorLinea(_LineaBase):
    borrador = models.ForeignKey(Borrador, on_delete=models.CASCADE, related_name="lineas")


class BorradorImpuesto(_ImpuestoBase):
    borrador = models.ForeignKey(Borrador, on_delete=models.CASCADE, related_name="impuestos")


class EventoRevision(models.Model):
    """
    Auditoría: solo se añade, nunca se modifica. En una corrección guarda el
    valor extraído por el modelo, el valor anterior y el nuevo.
    """

    documento = models.ForeignKey(Documento, on_delete=models.CASCADE, related_name="eventos")
    borrador = models.ForeignKey(Borrador, null=True, blank=True, on_delete=models.CASCADE, related_name="eventos")
    accion = models.CharField(max_length=20, choices=AccionRevision.choices)
    campo = models.CharField(max_length=60, blank=True)
    valor_extraido = models.JSONField(null=True, blank=True)
    valor_anterior = models.JSONField(null=True, blank=True)
    valor_nuevo = models.JSONField(null=True, blank=True)
    estado_anterior = models.CharField(max_length=20, blank=True)
    estado_nuevo = models.CharField(max_length=20, blank=True)
    comentario = models.TextField(blank=True)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    actor_label = models.CharField(max_length=150)
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["creado", "id"]
        indexes = [models.Index(fields=["documento", "creado"], name="ia_evento_doc")]
