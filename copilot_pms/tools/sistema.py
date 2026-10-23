"""
Herramientas sobre el estado del propio sistema (no sobre los datos del hotel).

- estado_copias_seguridad   última copia correcta de la base de datos y de los ficheros,
                            si está verificada y si hay fallos recientes (seguridad.CopiaSeguridad)

Solo metadatos: nunca da acceso a las copias ni a las claves.
"""

from datetime import timedelta

from django.utils import timezone
from pydantic import BaseModel, ConfigDict, Field

from copilot_pms.models import EstadoLlamada
from copilot_pms.tools.base import Resultado, herramienta
from copilot_pms.tools.comun import consultado_en

# Una copia diaria: si la última correcta tiene más de 26 h, algo ha fallado.
HORAS_MAXIMAS = 26
# Una copia sin verificar más de 30 días no garantiza que se pueda restaurar.
DIAS_VERIFICACION = 30


class EstadoCopiasArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dias_historial: int = Field(7, ge=1, le=90, description="Días hacia atrás para contar copias y fallos.")


def _fecha(valor):
    return timezone.localtime(valor).isoformat(timespec="minutes") if valor else None


@herramienta(
    "estado_copias_seguridad",
    descripcion="Estado de las copias de seguridad cifradas: fecha de la última copia correcta de la base de "
                "datos y de los ficheros subidos, tamaño, si se ha verificado que se puede restaurar y cuántos "
                "fallos ha habido en los últimos días. No da acceso a las copias.",
    argumentos=EstadoCopiasArgs, dominio="sistema",
    permisos=("seguridad.view_copiaseguridad",),
)
def estado_copias_seguridad(p: EstadoCopiasArgs, ctx):
    from seguridad.models import CopiaSeguridad

    ahora = timezone.now()
    desde = ahora - timedelta(days=p.dias_historial)
    tipos, avisos = {}, []
    for tipo, nombre in CopiaSeguridad.Tipo.choices:
        ultima = CopiaSeguridad.objects.filter(tipo=tipo, estado=CopiaSeguridad.Estado.OK).first()
        recientes = CopiaSeguridad.objects.filter(tipo=tipo, creada__gte=desde)
        fallos = recientes.filter(estado=CopiaSeguridad.Estado.ERROR)
        tipos[nombre] = {
            "ultima_correcta": _fecha(ultima.creada) if ultima else None,
            "horas_desde_ultima": round((ahora - ultima.creada).total_seconds() / 3600, 1) if ultima else None,
            "tamano_mb": round(ultima.tamano / 1_048_576, 1) if ultima else None,
            "verificada": _fecha(ultima.verificada) if ultima else None,
            "copias_correctas_periodo": recientes.filter(estado=CopiaSeguridad.Estado.OK).count(),
            "fallos_periodo": fallos.count(),
            "ultimo_fallo": _fecha(fallos.first().creada) if fallos.exists() else None,
        }
        if not ultima:
            avisos.append(f"{nombre}: no hay ninguna copia correcta registrada.")
            continue
        if ahora - ultima.creada > timedelta(hours=HORAS_MAXIMAS):
            avisos.append(f"{nombre}: la última copia correcta tiene más de {HORAS_MAXIMAS} horas.")
        if not ultima.verificada or ahora - ultima.verificada > timedelta(days=DIAS_VERIFICACION):
            avisos.append(f"{nombre}: la última copia no se ha verificado en {DIAS_VERIFICACION} días.")
        if fallos.exists():
            avisos.append(f"{nombre}: {fallos.count()} copias fallidas en los últimos {p.dias_historial} días.")

    if not any(t["ultima_correcta"] or t["fallos_periodo"] for t in tipos.values()):
        return Resultado(EstadoLlamada.VACIO, datos={"copias": tipos}, avisos=avisos,
                         fuente="Seguridad: registro de copias", fecha_datos=consultado_en(),
                         mensaje="Todavía no se ha hecho ninguna copia de seguridad.")
    return Resultado(datos={"periodo_dias": p.dias_historial, "copias": tipos}, avisos=avisos,
                     fuente="Seguridad: registro de copias (python manage.py copia_seguridad)",
                     fecha_datos=consultado_en())
