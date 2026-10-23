from django.contrib import admin

from seguridad.models import CopiaSeguridad


@admin.register(CopiaSeguridad)
class CopiaSeguridadAdmin(admin.ModelAdmin):
    """Solo lectura: las copias las crea el comando copia_seguridad."""

    list_display = ("creada", "tipo", "estado", "fichero", "tamano", "verificada")
    list_filter = ("tipo", "estado")
    readonly_fields = [f.name for f in CopiaSeguridad._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
