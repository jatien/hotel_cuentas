"""
Configuración de invoice_agent.

Todo se lee de settings.INVOICE_AGENT (un dict) con estos valores por
defecto; en mysite/settings/base.py los valores sensibles vienen del entorno
(.env), nunca escritos en el código. Ver invoice_agent/README.md.
"""

from decimal import Decimal
from pathlib import Path

from django.conf import settings

DEFAULTS = {
    # Proveedor de IA. "openai" = cualquier API compatible con OpenAI (OpenAI, Ollama, vLLM...)
    # cambiando BASE_URL; "fake" = respuestas fijas para pruebas sin red.
    "PROVIDER": "openai",
    "BASE_URL": "",  # vacío = api.openai.com; p. ej. http://192.168.1.50:11434/v1 para Ollama
    "API_KEY": "",
    "MODEL": "gpt-4.1-mini",
    "SUPPORTS_VISION": True,  # ponlo a False con modelos locales solo de texto
    "TEMPERATURE": 0,  # None para modelos que no aceptan temperature
    "REQUEST_TIMEOUT_SECONDS": 90,
    "PROVIDER_MAX_RETRIES": 3,  # reintentos por llamada ante timeouts / 429 / 5xx
    "BACKOFF_BASE_SECONDS": 2.0,
    "BACKOFF_MAX_SECONDS": 30.0,
    "MALFORMED_OUTPUT_RETRIES": 1,  # reintentos si la salida no cumple el esquema
    "PRICE_INPUT_PER_MTOK": "0",  # coste por millón de tokens (para la métrica de coste); 0 = desconocido
    "PRICE_OUTPUT_PER_MTOK": "0",
    # Modelo «profesor»: un LLM grande (OpenRouter, OpenAI…) que etiqueta las facturas nuevas para
    # entrenar el modelo local (invoice_agent_pipeline). Vacío = el mismo que MODEL/BASE_URL/API_KEY,
    # así el modelo de producción puede pasar a ser el local sin que el profesor cambie.
    "TEACHER_MODEL": "",
    "TEACHER_BASE_URL": "",
    "TEACHER_API_KEY": "",
    # Cola y worker
    "JOB_MAX_ATTEMPTS": 3,
    "JOB_LEASE_SECONDS": 600,  # un trabajo "running" más antiguo se considera abandonado y se recupera
    "JOB_RETRY_DELAY_SECONDS": 60,
    "MAX_CONCURRENT_JOBS": 2,
    # Ficheros
    "PRIVATE_STORAGE_ROOT": "",  # vacío = <BASE_DIR>/private_media (fuera de MEDIA_ROOT)
    "MAX_UPLOAD_BYTES": 20 * 1024 * 1024,
    "MAX_PAGES": 30,
    "MAX_IMAGE_PIXELS": 60_000_000,
    "MALWARE_SCANNER": "",  # ruta "modulo.funcion" que recibe la ruta del fichero y lanza excepción si es malicioso
    "SIGNED_URL_SECONDS": 300,
    "RETENTION_DAYS": None,  # None = conservar siempre; un número = fecha de borrado sugerida
    # Preparación
    "MIN_NATIVE_CHARS_PER_PAGE": 30,
    "OCR_LANG": "spa",
    "OCR_MIN_MEAN_CONFIDENCE": 60,
    "ALLOW_VISION": True,
    # image_only = el modelo de visión lee SOLO las imágenes de las páginas, sin OCR ni texto (por defecto;
    # es como se entrena el modelo local). auto = texto + imagen de páginas difíciles; always = texto +
    # todas las imágenes; never = solo texto. auto/always/never necesitan Tesseract para los escaneos.
    "VISION_MODE": "image_only",
    "VISION_MAX_SIDE_PX": 1600,
    "MAX_TEXT_CHARS": 60_000,
    "REDACT_BEFORE_MODEL": True,
    # Validación
    "TOLERANCE_MINOR_UNITS": 1,  # tolerancia por importe, en unidades mínimas de la moneda (1 céntimo en EUR)
    # Acceso
    "REQUIRE_LOGIN": False,  # False = uso en la LAN sin usuarios, como el resto del proyecto
}


def get(name: str):
    """Valor de configuración: settings.INVOICE_AGENT[name] o el valor por defecto."""
    overrides = getattr(settings, "INVOICE_AGENT", {}) or {}
    if name in overrides:
        return overrides[name]
    return DEFAULTS[name]


def decimal(name: str) -> Decimal:
    return Decimal(str(get(name)))


def private_root() -> Path:
    root = get("PRIVATE_STORAGE_ROOT")
    return Path(root) if root else Path(settings.BASE_DIR) / "private_media"
