"""
Admin de invoice_agent. Empresas editables; el resto es de solo lectura para
no saltarse la máquina de estados ni la auditoría (las correcciones se hacen
desde la pantalla de revisión o la API).
"""

from django.contrib import admin

from invoice_agent.models import Documento, Empresa, EventoRevision, Intento, Trabajo


@admin.register(Empresa)
class EmpresaAdmin(admin.ModelAdmin):
    list_display = ("nombre", "nif", "activa")
    filter_horizontal = ("usuarios",)


class ReadOnlyAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Documento)
class DocumentoAdmin(ReadOnlyAdmin):
    list_display = ("original_filename", "empresa", "estado", "creado")
    list_filter = ("estado", "empresa")
    search_fields = ("original_filename", "sha256")


@admin.register(Trabajo)
class TrabajoAdmin(ReadOnlyAdmin):
    list_display = ("id", "documento", "estado", "intentos", "ejecutar_desde", "ultimo_error")
    list_filter = ("estado",)


@admin.register(Intento)
class IntentoAdmin(ReadOnlyAdmin):
    list_display = ("documento", "numero", "resultado", "modelo", "metodo", "duracion_ms", "coste_estimado")
    list_filter = ("resultado", "modelo")


@admin.register(EventoRevision)
class EventoRevisionAdmin(ReadOnlyAdmin):
    list_display = ("creado", "documento", "accion", "campo", "actor_label")
    list_filter = ("accion",)
