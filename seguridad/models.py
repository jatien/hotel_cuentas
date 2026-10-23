from django.db import models


class CopiaSeguridad(models.Model):
    """Registro de cada copia de seguridad cifrada (la crea `python manage.py copia_seguridad`).

    Solo guarda metadatos: los ficheros .age están en BACKUP_DIR y únicamente se pueden abrir con
    la clave privada, que no vive en el servidor.
    """

    class Tipo(models.TextChoices):
        BASE_DATOS = "bd", "Base de datos"
        FICHEROS = "ficheros", "Ficheros subidos"

    class Estado(models.TextChoices):
        OK = "ok", "Correcta"
        ERROR = "error", "Error"

    creada = models.DateTimeField(auto_now_add=True, db_index=True)
    tipo = models.CharField(max_length=10, choices=Tipo.choices)
    estado = models.CharField(max_length=10, choices=Estado.choices, default=Estado.OK)
    fichero = models.CharField(max_length=300, blank=True, help_text="Nombre del fichero dentro de BACKUP_DIR.")
    tamano = models.BigIntegerField(default=0, help_text="Bytes del fichero cifrado.")
    sha256 = models.CharField(max_length=64, blank=True, help_text="Huella del fichero cifrado.")
    duracion_s = models.FloatField(default=0)
    error = models.TextField(blank=True)
    verificada = models.DateTimeField(null=True, blank=True,
                                      help_text="Última vez que se descifró y se comprobó que se puede restaurar.")

    class Meta:
        ordering = ["-creada"]
        verbose_name = "copia de seguridad"
        verbose_name_plural = "copias de seguridad"

    def __str__(self):
        return f"{self.get_tipo_display()} {self.creada:%Y-%m-%d %H:%M} ({self.get_estado_display()})"
