"""
Comprobaciones de seguridad que se suman a `manage.py check` (y a `check --deploy`).
"""

from django.conf import settings
from django.core.checks import Error, Warning, register

_LOCALES = {"", "localhost", "127.0.0.1", "::1", "db"}


@register(deploy=True)
def base_de_datos(app_configs, **kwargs):
    avisos = []
    bd = settings.DATABASES.get("default", {})
    host = str(bd.get("HOST") or "")
    sslmode = bd.get("OPTIONS", {}).get("sslmode", "prefer")
    if host not in _LOCALES and sslmode != "verify-full":
        avisos.append(Warning(
            f"PostgreSQL está en otro equipo ({host}) y la conexión no verifica el certificado (sslmode={sslmode}).",
            hint="DB_SSLMODE=verify-full y DB_SSLROOTCERT=<ruta del certificado de la CA> (docs/security.md §5).",
            id="seguridad.W001",
        ))
    if str(bd.get("USER")) == "postgres":
        avisos.append(Warning(
            "La app se conecta a PostgreSQL con el superusuario «postgres».",
            hint="Crear roles propios con deploy/postgres/roles.sql y poner DB_USER=cuentas_app (docs/security.md §5).",
            id="seguridad.W002",
        ))
    return avisos


@register(deploy=True)
def copias(app_configs, **kwargs):
    if not [c for c in settings.BACKUP_AGE_RECIPIENTS if c.strip()]:
        return [Warning(
            "No hay copias de seguridad configuradas (BACKUP_AGE_RECIPIENTS vacío).",
            hint="python manage.py copia_seguridad --generar-clave y programar la copia diaria (docs/security.md §5).",
            id="seguridad.W003",
        )]
    return []


@register()
def claves_de_cifrado(app_configs, **kwargs):
    from seguridad.cifrado import campos_cifrados

    campos = [f"{m._meta.label}.{c.name}" for m, c in campos_cifrados()]
    if campos and not [c for c in settings.FIELD_ENCRYPTION_KEYS if c.strip()]:
        return [Error(
            f"Hay campos cifrados ({', '.join(campos)}) pero falta FIELD_ENCRYPTION_KEYS.",
            hint="python manage.py rotar_cifrado --generar-clave",
            id="seguridad.E001",
        )]
    return []


# --------------------------------------------------------------------------- #
# Llamadas a modelos de IA (invoice_agent, copilot_pms)
# --------------------------------------------------------------------------- #
# Destinos HTTP sin TLS que aun así viajan cifrados: túneles VPN. 100.64.0.0/10 es el rango de
# Tailscale; las redes de WireGuard propias se añaden con OUTBOUND_VPN_NETWORKS.
_TAILSCALE = "100.64.0.0/10"


def _destino(base_url: str):
    import ipaddress
    from urllib.parse import urlsplit

    partes = urlsplit(base_url)
    host = partes.hostname or ""
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
    return partes.scheme, host, ip


def _en_vpn(ip) -> bool:
    import ipaddress

    if ip is None:
        return False
    redes = [_TAILSCALE, *getattr(settings, "OUTBOUND_VPN_NETWORKS", ())]
    return any(ip in ipaddress.ip_network(r.strip(), strict=False) for r in redes if r.strip())


def _es_local(host: str, ip) -> bool:
    if host in {"localhost"} or host.endswith((".lan", ".local", ".internal", ".home.arpa")):
        return True
    return ip is not None and (ip.is_private or ip.is_loopback or ip.is_link_local)


@register(deploy=True)
def modelos_ia(app_configs, **kwargs):
    avisos = []
    servicios = (
        ("Facturas con IA", "INVOICE_AGENT", getattr(settings, "INVOICE_AGENT", {})),
        ("PMS Copilot", "COPILOT", getattr(settings, "COPILOT_PMS", {})),
    )
    for nombre, prefijo, conf in servicios:
        proveedor = str(conf.get("PROVIDER", "")).lower()
        if proveedor in ("", "fake"):
            continue
        base_url = str(conf.get("BASE_URL") or "").strip()
        if not base_url:
            avisos.append(Warning(
                f"{nombre} envía los datos a la API de OpenAI en la nube.",
                hint=(f"Decisión consciente: salen del hotel facturas, nóminas o resultados contables. Para dejarlos "
                      f"dentro, {prefijo}_BASE_URL apuntando al servidor local (docs/security.md §6)."),
                id="seguridad.W010",
            ))
            continue
        esquema, host, ip = _destino(base_url)
        mismo_equipo = host == "localhost" or (ip is not None and ip.is_loopback)
        if mismo_equipo:
            pass  # HTTP dentro del propio equipo: no pasa por la red
        elif esquema == "http" and not _es_local(host, ip) and not _en_vpn(ip):
            avisos.append(Error(
                f"{nombre} usa HTTP sin cifrar hacia {host}, fuera de la red local: la clave y los datos viajan en claro.",
                hint=f"Usar https:// en {prefijo}_BASE_URL o conectar por la VPN (WireGuard/Tailscale).",
                id="seguridad.E010",
            ))
        elif esquema == "http" and not _en_vpn(ip):
            avisos.append(Warning(
                f"{nombre} usa HTTP sin cifrar dentro de la LAN ({host}).",
                hint=("Cualquiera en la red del hotel podría leer las peticiones. Mejor por la VPN (IP de "
                      "WireGuard/Tailscale) o con HTTPS; añade la red de WireGuard a OUTBOUND_VPN_NETWORKS."),
                id="seguridad.W011",
            ))
        if not str(conf.get("API_KEY") or "").strip() and not mismo_equipo:
            avisos.append(Warning(
                f"{nombre}: el servidor del modelo ({host}) no pide clave de API.",
                hint="Arrancar vLLM con --api-key (copilot_pms/deploy/serve_qwen35.sh lo hace) y ponerla en el .env.",
                id="seguridad.W012",
            ))
    return avisos
