"""
Ajustes comunes a todos los entornos (ver mysite/settings/__init__.py).

Ningún secreto vive en este fichero: claves, contraseñas y hosts vienen del
.env (python-decouple). Plantilla con todas las variables en .env.example.
"""

from pathlib import Path

from decouple import Csv, config
from django.utils.csp import CSP

BASE_DIR = Path(__file__).resolve().parent.parent.parent


# --- Secretos ------------------------------------------------------------------------
# SECRET_KEY firma sesiones, tokens CSRF y URLs firmadas. dev.py y prod.py deciden qué pasa
# si falta; aquí solo se leen las claves antiguas que se siguen aceptando tras una rotación.
SECRET_KEY_FALLBACKS = config("SECRET_KEY_FALLBACKS", default="", cast=Csv())


# --- Aplicaciones --------------------------------------------------------------------

INSTALLED_APPS = [
    'seguridad.apps.SeguridadConfig',
    'inicio.apps.InicioConfig',
    'invoice_data.apps.InvoiceDataConfig',
    'nominas.apps.NominasConfig',
    'cuadro_cuentas.apps.CuadroCuentasConfig',
    'analisis.apps.AnalisisConfig',
    'cajas.apps.CajasConfig',
    'invoice_agent.apps.InvoiceAgentConfig',
    'copilot_pms.apps.CopilotPmsConfig',
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
]

MIDDLEWARE = [
    # Primero: corrige REMOTE_ADDR y descarta X-Forwarded-* que no vengan del proxy (ver middleware).
    'seguridad.middleware.ProxyClientIPMiddleware',
    'django.middleware.security.SecurityMiddleware',
    # Sirve /static/ desde el propio proceso (también con DEBUG=False), sin depender del proxy.
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'django.middleware.csp.ContentSecurityPolicyMiddleware',
]

ROOT_URLCONF = 'mysite.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.template.context_processors.csp',  # {{ csp_nonce }} en las plantillas
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'mysite.wsgi.application'


# --- Base de datos -------------------------------------------------------------------
# Credenciales en el .env. El usuario debería ser un rol propio de la app, no "postgres".

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': config("DB_NAME", default="cuentas"),
        'USER': config("DB_USER", default="postgres"),
        'PASSWORD': config("DB_PASSWORD"),
        'HOST': config("DB_HOST", default="localhost"),
        'PORT': config("DB_PORT", default="5432"),
        'CONN_MAX_AGE': config("DB_CONN_MAX_AGE", default=60, cast=int),
        'CONN_HEALTH_CHECKS': True,
        # TLS hacia PostgreSQL. En la misma máquina (localhost / red interna de Docker) basta "prefer";
        # si la base de datos está en otro equipo: "verify-full" + DB_SSLROOTCERT (docs/security.md §5).
        'OPTIONS': {
            k: v for k, v in {
                'sslmode': config("DB_SSLMODE", default="prefer"),
                'sslrootcert': config("DB_SSLROOTCERT", default=""),
            }.items() if v
        },
    }
}


# --- Cifrado en reposo y copias de seguridad ----------------------------------------
# Campos cifrados (seguridad.cifrado.CampoCifrado): claves Fernet, la primera cifra y todas descifran.
FIELD_ENCRYPTION_KEYS = config("FIELD_ENCRYPTION_KEYS", default="", cast=Csv())
# Clave HMAC para buscar por igualdad en campos cifrados (índice ciego). Distinta de las anteriores.
BLIND_INDEX_KEY = config("BLIND_INDEX_KEY", default="")

# Copias cifradas con age (python manage.py copia_seguridad). Solo la clave PÚBLICA va aquí.
BACKUP_DIR = config("BACKUP_DIR", default=str(BASE_DIR / "backups"))
BACKUP_AGE_RECIPIENTS = config("BACKUP_AGE_RECIPIENTS", default="", cast=Csv())
BACKUP_KEEP_DAYS = config("BACKUP_KEEP_DAYS", default=30, cast=int)
# Carpeta de pg_dump/pg_restore si no están en el PATH (Windows: C:\Program Files\PostgreSQL\17\bin)
BACKUP_PG_BIN_DIR = config("BACKUP_PG_BIN_DIR", default="")


# --- Llamadas salientes ----------------------------------------------------------------
# Redes de VPN (WireGuard) por las que HTTP sin TLS sí va cifrado; Tailscale (100.64.0.0/10) ya se
# reconoce. Lo usa check --deploy para no avisar de los modelos de IA servidos por la VPN.
OUTBOUND_VPN_NETWORKS = config("OUTBOUND_VPN_NETWORKS", default="", cast=Csv())


# --- Validación de contraseñas -------------------------------------------------------

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]


# --- Internacionalización ------------------------------------------------------------

LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True


# --- Ficheros estáticos y subidos ----------------------------------------------------

STATIC_URL = 'static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'  # destino de collectstatic (en .gitignore)

STORAGES = {
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
}

MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

# Ficheros subidos: a partir de 5 MB se escriben en disco temporal en vez de memoria.
# El tamaño máximo de una petición lo limita el proxy (deploy/caddy/Caddyfile).
FILE_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024
FILE_UPLOAD_PERMISSIONS = 0o640
FILE_UPLOAD_DIRECTORY_PERMISSIONS = 0o750


# --- Proxy inverso -------------------------------------------------------------------
# IPs o redes de los proxies (Caddy) cuyas cabeceras X-Forwarded-* son de fiar. Vacío = sin proxy
# (runserver). prod.py usa 127.0.0.1 por defecto; con Docker, la red interna de compose.
TRUSTED_PROXIES = config("TRUSTED_PROXIES", default="", cast=Csv())

# Extensiones de /media/ que se muestran en el navegador; el resto se descarga como adjunto
# (un .html o .svg subido no puede ejecutar código en la app). Ver seguridad/views.py.
MEDIA_INLINE_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tif", ".tiff"}


# --- Cabeceras de seguridad (todas las respuestas) -----------------------------------
# Las que dependen de HTTPS (cookies seguras, HSTS, redirección) están en prod.py.

SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
SECURE_CROSS_ORIGIN_OPENER_POLICY = "same-origin"
# SAMEORIGIN (y no DENY) porque los PDF de facturas se muestran en un <iframe> de la propia app.
X_FRAME_OPTIONS = "SAMEORIGIN"
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
# El token CSRF lo lee el JavaScript de nominas desde la cookie: no puede ser HttpOnly.
CSRF_COOKIE_SAMESITE = "Lax"


# --- Content Security Policy ---------------------------------------------------------
# Lista blanca de orígenes de scripts, estilos, fuentes… Los <script> en línea llevan
# nonce="{{ csp_nonce }}". Empieza en modo "solo informe" (CSP_ENFORCE=False): el navegador
# avisa en la consola y envía el informe a /seguridad/csp-report/ (log de seguridad) sin bloquear
# nada. Cuando el log quede limpio, CSP_ENFORCE=True la convierte en obligatoria.

_CDN = "https://cdn.jsdelivr.net"
CSP_POLICY = {
    "default-src": [CSP.SELF],
    "script-src": [CSP.SELF, CSP.NONCE, _CDN],
    "style-src": [CSP.SELF, CSP.UNSAFE_INLINE, _CDN, "https://fonts.googleapis.com"],
    "font-src": [CSP.SELF, "https://fonts.gstatic.com", "data:"],
    "img-src": [CSP.SELF, "data:", "blob:"],
    "connect-src": [CSP.SELF],
    "frame-src": [CSP.SELF, "blob:"],
    "object-src": [CSP.NONE],
    "base-uri": [CSP.SELF],
    "form-action": [CSP.SELF],
    "frame-ancestors": [CSP.SELF],
    "report-uri": ["/seguridad/csp-report/"],
}
CSP_ENFORCE = config("CSP_ENFORCE", default=False, cast=bool)
SECURE_CSP = CSP_POLICY if CSP_ENFORCE else {}
SECURE_CSP_REPORT_ONLY = {} if CSP_ENFORCE else CSP_POLICY


# --- Correo --------------------------------------------------------------------------

MAILERS = {
    'default': {
        'BACKEND': 'django.core.mail.backends.console.EmailBackend',
    },
}


# --- Registro (logs) -----------------------------------------------------------------
# Consola para las apps y un fichero aparte (logs/security.log, rotativo) para los eventos
# de seguridad: peticiones sospechosas, CSRF, host no permitido, informes CSP…

LOG_DIR = Path(config("LOG_DIR", default=str(BASE_DIR / "logs")))
LOG_DIR.mkdir(parents=True, exist_ok=True)

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "seguridad": {"format": "%(asctime)s %(levelname)s %(name)s %(message)s"},
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler"},
        "security_file": {
            "class": "logging.handlers.RotatingFileHandler",
            "filename": str(LOG_DIR / "security.log"),
            "maxBytes": 5 * 1024 * 1024,
            "backupCount": 10,
            "encoding": "utf-8",
            "formatter": "seguridad",
        },
    },
    "loggers": {
        "invoice_agent": {"handlers": ["console"], "level": "INFO", "propagate": False},
        "copilot_pms": {"handlers": ["console"], "level": "INFO", "propagate": False},
        # DisallowedHost, SuspiciousFileOperation, SuspiciousOperation…
        "django.security": {"handlers": ["console", "security_file"], "level": "WARNING", "propagate": False},
        # Rechazos por CSRF (403)
        "django.security.csrf": {"handlers": ["console", "security_file"], "level": "WARNING", "propagate": False},
        "seguridad": {"handlers": ["console", "security_file"], "level": "INFO", "propagate": False},
    },
}


# --- Configuración propia de las apps ------------------------------------------------

# Ruta al ejecutable de Tesseract OCR (necesario en Windows si no está en el PATH)
TESSERACT_CMD = config("TESSERACT_CMD", default=r"C:\Program Files\Tesseract-OCR\tesseract.exe")

# Datos de la empresa para filtrar facturas propias (NIF y nombres)
INVOICE_OWN_NIF = ("B07794803", "E07575970")
INVOICE_OWN_NAMES = ("INVENT HOTELES", "CATALINA Y JUAN TORRES ROSELLO")

# Detección de facturas con IA (invoice_agent). Los valores sensibles vienen del fichero .env
# (ver .env.example); el resto de opciones y sus valores por defecto están en invoice_agent/conf.py.
INVOICE_AGENT = {
    "PROVIDER": config("INVOICE_AGENT_PROVIDER", default="openai"),
    "BASE_URL": config("INVOICE_AGENT_BASE_URL", default=""),
    "API_KEY": config("INVOICE_AGENT_API_KEY", default=config("OPENAI_API_KEY", default="")),
    "MODEL": config("INVOICE_AGENT_MODEL", default="gpt-4.1-mini"),
    "SUPPORTS_VISION": config("INVOICE_AGENT_SUPPORTS_VISION", default=True, cast=bool),
    "ALLOW_VISION": config("INVOICE_AGENT_ALLOW_VISION", default=True, cast=bool),
    "VISION_MODE": config("INVOICE_AGENT_VISION_MODE", default="image_only"),
    "PRICE_INPUT_PER_MTOK": config("INVOICE_AGENT_PRICE_INPUT_PER_MTOK", default="0"),
    "PRICE_OUTPUT_PER_MTOK": config("INVOICE_AGENT_PRICE_OUTPUT_PER_MTOK", default="0"),
    "TEACHER_MODEL": config("INVOICE_AGENT_TEACHER_MODEL", default=""),
    "TEACHER_BASE_URL": config("INVOICE_AGENT_TEACHER_BASE_URL", default=""),
    "TEACHER_API_KEY": config("INVOICE_AGENT_TEACHER_API_KEY", default=""),
    "MAX_CONCURRENT_JOBS": config("INVOICE_AGENT_MAX_CONCURRENT_JOBS", default=2, cast=int),
    "REQUIRE_LOGIN": config("INVOICE_AGENT_REQUIRE_LOGIN", default=False, cast=bool),
    "RETENTION_DAYS": config("INVOICE_AGENT_RETENTION_DAYS", default=None, cast=lambda v: int(v) if v else None),
}


# PMS Copilot (copilot_pms): preguntas en lenguaje natural respondidas con herramientas de solo
# lectura. Valores sensibles en .env (ver .env.example); el resto en copilot_pms/conf.py.
def _opcional(cast):
    """Vacío en el .env = None (no se envía el parámetro al modelo)."""
    return lambda v: cast(v) if str(v).strip() else None


COPILOT_PMS = {
    "PROVIDER": config("COPILOT_PROVIDER", default="openai"),
    "BASE_URL": config("COPILOT_BASE_URL", default=""),
    "API_KEY": config("COPILOT_API_KEY", default=""),
    "MODEL": config("COPILOT_MODEL", default="Qwen/Qwen3.5-9B"),
    "TEMPERATURE": config("COPILOT_TEMPERATURE", default="0.7", cast=_opcional(float)),
    "TOP_P": config("COPILOT_TOP_P", default="0.8", cast=_opcional(float)),
    "TOP_K": config("COPILOT_TOP_K", default="20", cast=_opcional(int)),
    "PRESENCE_PENALTY": config("COPILOT_PRESENCE_PENALTY", default="1.5", cast=_opcional(float)),
    "ENABLE_THINKING": config("COPILOT_ENABLE_THINKING", default=False, cast=bool),
    "REQUIRE_LOGIN": config("COPILOT_REQUIRE_LOGIN", default=False, cast=bool),
}
