"""
Modelos de copilot_pms.

    Conversacion         hilo de preguntas de una persona (o puesto de la LAN)
    Mensaje              pregunta del usuario o respuesta del copilot
    LlamadaHerramienta   auditoría de cada herramienta que ejecutó el copilot para una respuesta

Reglas:
- LlamadaHerramienta solo se añade, nunca se modifica: es la prueba de qué datos
  se consultaron, con qué argumentos y qué devolvió el sistema.
- Los resultados de herramientas no se vuelven a enviar al modelo en turnos
  siguientes: si hace falta el dato otra vez, se consulta de nuevo (dato fresco).
"""

import uuid

from django.conf import settings
from django.db import models


class Conversacion(models.Model):
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    titulo = models.CharField(max_length=200, blank=True)
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    actor_label = models.CharField(max_length=150, blank=True, help_text="Usuario o IP desde la que se pregunta.")
    creado = models.DateTimeField(auto_now_add=True)
    actualizado = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-actualizado"]
        verbose_name = "conversación"
        verbose_name_plural = "conversaciones"

    def __str__(self) -> str:
        return self.titulo or f"Conversación {self.public_id}"


class Mensaje(models.Model):
    class Rol(models.TextChoices):
        USUARIO = "user", "Usuario"
        COPILOT = "assistant", "Copilot"

    class Estado(models.TextChoices):
        OK = "ok", "Correcto"
        ERROR = "error", "Error"
        LIMITE = "limite", "Límite de pasos alcanzado"

    conversacion = models.ForeignKey(Conversacion, on_delete=models.CASCADE, related_name="mensajes")
    rol = models.CharField(max_length=10, choices=Rol.choices)
    contenido = models.TextField()
    estado = models.CharField(max_length=10, choices=Estado.choices, default=Estado.OK)
    error_code = models.CharField(max_length=60, blank=True)
    modelo = models.CharField(max_length=100, blank=True)
    prompt_version = models.CharField(max_length=40, blank=True)
    llamadas_modelo = models.PositiveIntegerField(default=0)
    tokens_entrada = models.PositiveIntegerField(default=0)
    tokens_salida = models.PositiveIntegerField(default=0)
    duracion_ms = models.PositiveIntegerField(null=True, blank=True)
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["creado", "id"]
        verbose_name = "mensaje"
        verbose_name_plural = "mensajes"

    def __str__(self) -> str:
        return f"{self.get_rol_display()}: {self.contenido[:60]}"


class EstadoLlamada(models.TextChoices):
    OK = "ok", "Correcto"
    VACIO = "vacio", "Sin resultados"
    NO_DISPONIBLE = "no_disponible", "Dato no disponible"
    ACLARAR = "aclarar", "Falta un dato o es ambiguo"
    INVALIDO = "invalido", "Argumentos no válidos"
    DENEGADO = "denegado", "Sin permiso"
    ERROR = "error", "Error interno"
    DESCONOCIDA = "desconocida", "Herramienta desconocida"


class LlamadaHerramienta(models.Model):
    mensaje = models.ForeignKey(Mensaje, on_delete=models.CASCADE, related_name="llamadas")
    orden = models.PositiveIntegerField()
    herramienta = models.CharField(max_length=80)
    argumentos = models.JSONField(default=dict, blank=True)
    estado = models.CharField(max_length=15, choices=EstadoLlamada.choices)
    codigo = models.CharField(max_length=60, blank=True, help_text="Código del error o del aviso principal.")
    resultado = models.JSONField(default=dict, blank=True)
    fuente = models.CharField(max_length=200, blank=True)
    duracion_ms = models.PositiveIntegerField(default=0)
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["mensaje", "orden"]
        verbose_name = "llamada a herramienta"
        verbose_name_plural = "llamadas a herramientas"
        indexes = [models.Index(fields=["herramienta", "-creado"], name="copilot_llamada_herr")]

    def __str__(self) -> str:
        return f"{self.herramienta} ({self.get_estado_display()})"
