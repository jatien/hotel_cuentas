from django.contrib import admin

from copilot_pms.models import Conversacion, LlamadaHerramienta, Mensaje


class MensajeInline(admin.TabularInline):
    model = Mensaje
    extra = 0
    fields = ("rol", "contenido", "estado", "error_code", "modelo", "duracion_ms", "creado")
    readonly_fields = fields
    can_delete = False


@admin.register(Conversacion)
class ConversacionAdmin(admin.ModelAdmin):
    list_display = ("titulo", "actor_label", "creado", "actualizado")
    search_fields = ("titulo", "actor_label")
    inlines = [MensajeInline]


@admin.register(LlamadaHerramienta)
class LlamadaHerramientaAdmin(admin.ModelAdmin):
    """Auditoría: solo lectura."""

    list_display = ("herramienta", "estado", "codigo", "duracion_ms", "creado")
    list_filter = ("herramienta", "estado")
    readonly_fields = [f.name for f in LlamadaHerramienta._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
