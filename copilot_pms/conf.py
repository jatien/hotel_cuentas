"""
Configuración de copilot_pms.

Se lee de settings.COPILOT_PMS (un dict) con estos valores por defecto; en
mysite/settings.py los valores sensibles vienen del .env. Mismo patrón que
invoice_agent/conf.py.
"""

from django.conf import settings

DEFAULTS = {
    # Proveedor del modelo. "openai" = cualquier API compatible con OpenAI Chat Completions
    # con llamadas a funciones (OpenAI, Ollama, vLLM, LM Studio...) cambiando BASE_URL.
    # "fake" = sin red, para pruebas.
    "PROVIDER": "openai",
    "BASE_URL": "",
    "API_KEY": "",
    "MODEL": "Qwen/Qwen3.5-9B",  # modelo principal, servido en local (vLLM); ver copilot_pms/deploy/
    # Muestreo recomendado por Qwen para Qwen3.5 sin razonamiento (modo instruct). None = no se envía.
    # top_k solo se envía a servidores locales. Con OpenAI en la nube usa TEMPERATURE=0 y el resto None.
    "TEMPERATURE": 0.7,
    "TOP_P": 0.8,
    "TOP_K": 20,
    "PRESENCE_PENALTY": 1.5,
    "MAX_TOKENS": 4096,
    # Qwen3.5 razona por defecto. False = respuestas más rápidas; True si la evaluación muestra que
    # elige mejor las herramientas pensando. Solo se envía a servidores locales.
    "ENABLE_THINKING": False,
    "REQUEST_TIMEOUT_SECONDS": 120,
    "PROVIDER_MAX_RETRIES": 2,  # reintentos de la llamada al modelo ante timeouts / 429 / 5xx
    "BACKOFF_SECONDS": 2.0,
    # Bucle del agente
    "MAX_PASOS": 6,  # vueltas modelo -> herramientas -> modelo por pregunta
    "MAX_LLAMADAS_HERRAMIENTA": 12,  # llamadas a herramientas por pregunta
    "TURNOS_HISTORIAL": 6,  # pares pregunta/respuesta anteriores que se envían al modelo
    "MAX_CARACTERES_RESULTADO": 20_000,  # tamaño máximo del resultado de una herramienta enviado al modelo
    # Acceso. False = uso en la LAN sin usuarios, como el resto del proyecto.
    # True = hace falta sesión y cada herramienta exige su permiso de Django.
    "REQUIRE_LOGIN": False,
    # Herramientas de escritura (reservas, asientos...). Ninguna existe todavía; aunque se
    # registre alguna, el ejecutor la rechaza mientras esto sea False.
    "ALLOW_WRITE_TOOLS": False,
}


def get(name: str):
    overrides = getattr(settings, "COPILOT_PMS", {}) or {}
    if name in overrides:
        return overrides[name]
    return DEFAULTS[name]
