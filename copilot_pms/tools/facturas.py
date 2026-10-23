"""
Herramientas de facturas.

Dos orígenes, cada uno con su referencia para que no se confundan:
- «ia:<uuid>»  Facturas con IA (invoice_agent). Separadas por empresa: solo se ven las
               empresas del contexto. El original se entrega con una URL firmada de
               corta duración (la misma que usa la pantalla de revisión) y el acceso
               queda auditado en EventoRevision.
- «ocr:<id>»   Gestión de facturas (invoice_data, OCR + reglas). Sin separación por
               empresa; se enlaza a su ficha en la aplicación.

Las herramientas solo leen: nunca aprueban, corrigen ni contabilizan.
"""

import uuid
from datetime import date
from decimal import Decimal

from django.db.models import Q
from django.urls import reverse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from copilot_pms.models import EstadoLlamada
from copilot_pms.tools.base import Aclarar, Denegado, NoDisponible, Resultado, herramienta
from copilot_pms.tools.comun import consultado_en, importe

ORIGEN_IA = "ia"
ORIGEN_OCR = "ocr"
PERMISO_IA = ("invoice_agent.view_borrador",)
PERMISO_OCR = ("invoice_data.view_invoicedocument",)


class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _origenes_permitidos(ctx, origen: str) -> list[str]:
    pedidos = [ORIGEN_IA, ORIGEN_OCR] if origen == "todas" else [origen]
    permitidos = [o for o in pedidos if ctx.tiene_permisos(PERMISO_IA if o == ORIGEN_IA else PERMISO_OCR)]
    if not permitidos:
        raise Denegado("PERMISSION_DENIED", "Sin permiso para consultar facturas.")
    return permitidos


# --------------------------------------------------------------------------- #
class BuscarFacturasArgs(_Args):
    proveedor: str | None = Field(None, max_length=200, description="Nombre (o parte) o NIF del proveedor.")
    numero: str | None = Field(None, max_length=100, description="Número de factura (o parte).")
    desde: date | None = Field(None, description="Fecha de factura desde (AAAA-MM-DD).")
    hasta: date | None = Field(None, description="Fecha de factura hasta, incluida (AAAA-MM-DD).")
    total: Decimal | None = Field(None, ge=0, max_digits=14, decimal_places=2,
                                  description="Total de la factura exacto, en euros.")
    origen: str = Field("todas", pattern="^(todas|ia|ocr)$", description=(
        "«ia» = Facturas con IA, «ocr» = Gestión de facturas, «todas» = ambas."))
    limite: int = Field(10, ge=1, le=50)

    @field_validator("proveedor", "numero")
    @classmethod
    def _limpiar(cls, valor):
        return valor.strip() or None if valor else None


def _buscar_ia(p, ctx) -> tuple[int, list]:
    from invoice_agent.models import Borrador

    qs = Borrador.objects.filter(empresa_id__in=ctx.empresas_facturas).select_related("documento", "empresa")
    if p.proveedor:
        qs = qs.filter(Q(proveedor_nombre__icontains=p.proveedor) | Q(proveedor_nif__iexact=p.proveedor)
                       | Q(proveedor_nif_norm__iexact=p.proveedor.replace(" ", "").replace("-", "").upper()))
    if p.numero:
        qs = qs.filter(numero_factura__icontains=p.numero)
    if p.desde:
        qs = qs.filter(fecha_factura__gte=p.desde)
    if p.hasta:
        qs = qs.filter(fecha_factura__lte=p.hasta)
    if p.total is not None:
        qs = qs.filter(total_bruto=p.total)
    total = qs.count()
    filas = [{
        "referencia": f"{ORIGEN_IA}:{b.documento.public_id}",
        "origen": "Facturas con IA",
        "entidad": {"nombre": b.empresa.nombre, "nif": b.empresa.nif},
        "proveedor": b.proveedor_nombre or None, "proveedor_nif": b.proveedor_nif or None,
        "numero": b.numero_factura or None, "fecha": b.fecha_factura,
        "total": importe(b.total_bruto) if b.total_bruto is not None else None,
        "moneda": b.moneda or None,
        "estado": b.documento.get_estado_display(),
        "archivo": b.documento.original_filename,
    } for b in qs.order_by("-fecha_factura", "-creado")[:p.limite]]
    return total, filas


def _buscar_ocr(p) -> tuple[int, list]:
    from invoice_data.models import InvoiceDocument

    qs = InvoiceDocument.objects.select_related("datos", "proveedor")
    if p.proveedor:
        qs = qs.filter(Q(datos__proveedor_nombre__icontains=p.proveedor) | Q(proveedor__nombre__icontains=p.proveedor)
                       | Q(datos__proveedor_cif__iexact=p.proveedor) | Q(proveedor__cif__iexact=p.proveedor))
    if p.numero:
        qs = qs.filter(datos__numero_factura__icontains=p.numero)
    if p.desde:
        qs = qs.filter(datos__fecha_factura__gte=p.desde)
    if p.hasta:
        qs = qs.filter(datos__fecha_factura__lte=p.hasta)
    if p.total is not None:
        qs = qs.filter(datos__total=p.total)
    total = qs.count()
    filas = []
    for d in qs.order_by("-datos__fecha_factura", "-imported_at")[:p.limite]:
        datos = getattr(d, "datos", None)
        filas.append({
            "referencia": f"{ORIGEN_OCR}:{d.pk}",
            "origen": "Gestión de facturas",
            "entidad": None,
            "proveedor": d.nombre_proveedor or None,
            "proveedor_nif": (datos.proveedor_cif if datos else "") or (d.proveedor.cif if d.proveedor else None),
            "numero": (datos.numero_factura if datos else "") or None,
            "fecha": datos.fecha_factura if datos else None,
            "total": importe(datos.total) if datos and datos.total is not None else None,
            "moneda": "EUR",
            "estado": ("confirmada" if datos.confirmado else datos.get_estado_display()) if datos else "sin extraer",
            "tipo": d.get_tipo_entidad_display(), "departamento": d.departamento or None,
            "archivo": d.original_filename,
        })
    return total, filas


@herramienta(
    "buscar_facturas",
    descripcion="Busca facturas recibidas por proveedor, número, fechas o total. Devuelve metadatos y la "
                "referencia de cada una (para pedir el documento con documento_factura).",
    argumentos=BuscarFacturasArgs, dominio="facturas", permisos_alguno=PERMISO_IA + PERMISO_OCR,
)
def buscar_facturas(p: BuscarFacturasArgs, ctx):
    if not any([p.proveedor, p.numero, p.desde, p.hasta, p.total is not None]):
        raise Aclarar("FALTAN_CRITERIOS", "Indica al menos un criterio: proveedor, número, fechas o total.")
    origenes = _origenes_permitidos(ctx, p.origen)
    resultados, totales, avisos = [], {}, []
    if ORIGEN_IA in origenes:
        totales["facturas_ia"], filas = _buscar_ia(p, ctx)
        resultados += filas
    if ORIGEN_OCR in origenes:
        totales["gestion_facturas"], filas = _buscar_ocr(p)
        resultados += filas
    if p.origen == "todas" and len(origenes) == 1:
        avisos.append("Solo se ha consultado un origen: no hay permiso para el otro.")
    encontrados = sum(totales.values())
    if encontrados > len(resultados):
        avisos.append(f"Se muestran {len(resultados)} de {encontrados} facturas; afina la búsqueda.")
    if p.origen == "todas" and len(origenes) == 2:
        avisos.append("Una misma factura puede figurar en los dos orígenes.")
    return Resultado(EstadoLlamada.OK if resultados else EstadoLlamada.VACIO,
                     datos={"facturas": resultados, "encontradas_por_origen": totales},
                     fuente="Facturas con IA (invoice_agent) y Gestión de facturas (invoice_data)",
                     fecha_datos=consultado_en(), avisos=avisos)


# --------------------------------------------------------------------------- #
class DocumentoFacturaArgs(_Args):
    referencia: str = Field(pattern=r"^(ia:[0-9a-fA-F-]{32,36}|ocr:\d+)$", description=(
        "Referencia devuelta por buscar_facturas, p. ej. «ia:3f0c…» u «ocr:42». Nunca la inventes."))


def _documento_ia(public_id: str, ctx):
    from invoice_agent.models import AccionRevision, Documento, EventoRevision
    from invoice_agent.services.storage import make_token, path_for
    from invoice_agent import conf as conf_ia

    if not ctx.tiene_permisos(PERMISO_IA):
        raise Denegado("PERMISSION_DENIED", "Sin permiso para ver facturas con IA.")
    try:
        public_id = uuid.UUID(public_id)
    except ValueError:
        raise NoDisponible("DOCUMENTO_NO_ENCONTRADO", "No hay ninguna factura con esa referencia.")
    documento = (Documento.objects.filter(public_id=public_id, empresa_id__in=ctx.empresas_facturas)
                 .select_related("empresa").first())
    if documento is None:  # mismo mensaje exista o no en otra empresa: no se revela nada
        raise NoDisponible("DOCUMENTO_NO_ENCONTRADO", "No hay ninguna factura con esa referencia.")
    if not documento.storage_key or not path_for(documento.storage_key).is_file():
        raise NoDisponible("ORIGINAL_NO_DISPONIBLE",
                           "El original ya no está guardado (política de retención); quedan sus datos.")
    EventoRevision.objects.create(documento=documento, accion=AccionRevision.FILE_ACCESS,
                                  actor=ctx.user if getattr(ctx.user, "pk", None) else None,
                                  actor_label=f"copilot · {ctx.actor_label}"[:150])
    borrador = getattr(documento, "borrador", None)
    caduca = int(conf_ia.get("SIGNED_URL_SECONDS"))
    return {
        "referencia": f"{ORIGEN_IA}:{documento.public_id}",
        "entidad": {"nombre": documento.empresa.nombre, "nif": documento.empresa.nif},
        "proveedor": borrador.proveedor_nombre if borrador else None,
        "numero": borrador.numero_factura if borrador else None,
        "fecha": borrador.fecha_factura if borrador else None,
        "total": importe(borrador.total_bruto) if borrador and borrador.total_bruto is not None else None,
        "estado": documento.get_estado_display(),
        "archivo": documento.original_filename, "tipo": documento.mime_type,
        "enlace_documento": reverse("invoice_agent:original", args=[make_token(documento.public_id)]),
        "caduca_en_segundos": caduca,
        "enlace_ficha": reverse("invoice_agent:detalle", args=[documento.public_id]),
    }, [f"El enlace al PDF caduca en {caduca // 60} minutos; después, ábrelo desde la ficha."]


def _documento_ocr(pk: int, ctx):
    from invoice_data.models import InvoiceDocument

    if not ctx.tiene_permisos(PERMISO_OCR):
        raise Denegado("PERMISSION_DENIED", "Sin permiso para ver la gestión de facturas.")
    documento = InvoiceDocument.objects.select_related("datos", "proveedor").filter(pk=pk).first()
    if documento is None:
        raise NoDisponible("DOCUMENTO_NO_ENCONTRADO", "No hay ninguna factura con esa referencia.")
    if not documento.file or not documento.file.storage.exists(documento.file.name):
        raise NoDisponible("ORIGINAL_NO_DISPONIBLE", "El fichero original no está en el servidor.")
    datos = getattr(documento, "datos", None)
    return {
        "referencia": f"{ORIGEN_OCR}:{documento.pk}",
        "entidad": None,
        "proveedor": documento.nombre_proveedor or None,
        "numero": (datos.numero_factura if datos else "") or None,
        "fecha": datos.fecha_factura if datos else None,
        "total": importe(datos.total) if datos and datos.total is not None else None,
        "archivo": documento.original_filename,
        "enlace_ficha": reverse("invoice_data:detail", args=[documento.pk]),
    }, ["El PDF se abre desde la ficha de la factura en Gestión de facturas."]


@herramienta(
    "documento_factura",
    descripcion="Devuelve el enlace seguro al documento (PDF o imagen) de una factura a partir de su referencia "
                "de buscar_facturas, con sus metadatos para comprobar que es la factura pedida.",
    argumentos=DocumentoFacturaArgs, dominio="facturas", permisos_alguno=PERMISO_IA + PERMISO_OCR,
)
def documento_factura(p: DocumentoFacturaArgs, ctx):
    origen, _, ident = p.referencia.partition(":")
    if origen == ORIGEN_IA:
        datos, avisos = _documento_ia(ident, ctx)
        fuente = "Facturas con IA (invoice_agent): almacenamiento privado"
    else:
        datos, avisos = _documento_ocr(int(ident), ctx)
        fuente = "Gestión de facturas (invoice_data)"
    return Resultado(datos=datos, fuente=fuente, fecha_datos=consultado_en(), avisos=avisos)
