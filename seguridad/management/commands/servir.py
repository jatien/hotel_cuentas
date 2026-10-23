"""
Arranca la app con Waitress, el servidor WSGI de producción que funciona en Windows.

    python manage.py servir                       # 127.0.0.1:8001, detrás de Caddy
    python manage.py servir --puerto 8002 --hilos 16

Escucha solo en localhost: a la app se llega por Caddy (HTTPS). En Debian/Docker se usa
Gunicorn (deploy/gunicorn.conf.py); este comando también funciona allí si hiciera falta.

Antes de servir comprueba que se está en producción (DJANGO_ENV=prod) y que pasa
"check --deploy"; con --sin-comprobar se salta para pruebas.
"""

import ipaddress

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Sirve la aplicación con Waitress (producción en Windows), solo en localhost, detrás del proxy HTTPS."

    def add_arguments(self, parser):
        parser.add_argument("--host", default="127.0.0.1",
                            help="Interfaz de escucha (por defecto 127.0.0.1; no exponer a la red).")
        parser.add_argument("--puerto", type=int, default=8001)
        parser.add_argument("--hilos", type=int, default=8, help="Peticiones simultáneas (por defecto 8).")
        parser.add_argument("--proxy", default="127.0.0.1",
                            help="IP del proxy (Caddy) cuyas cabeceras X-Forwarded-For/Proto se aceptan.")
        parser.add_argument("--sin-comprobar", action="store_true",
                            help="No exigir DJANGO_ENV=prod ni check --deploy (solo para pruebas).")
        parser.add_argument("--permitir-red", action="store_true",
                            help="Permite escuchar en una IP que no sea de loopback (desaconsejado: "
                                 "sin el proxy no hay HTTPS).")

    def handle(self, *args, **opciones):
        try:
            from waitress import serve
        except ImportError as exc:  # pragma: no cover - depende de la instalación
            raise CommandError("Falta waitress: pip install -r requirements.txt") from exc

        host = opciones["host"]
        try:
            loopback = ipaddress.ip_address(host).is_loopback
        except ValueError:
            loopback = host == "localhost"
        if not loopback and not opciones["permitir_red"]:
            raise CommandError(
                f"--host {host} expondría la app sin HTTPS. Usa 127.0.0.1 y entra por Caddy "
                "(o añade --permitir-red si sabes lo que haces)."
            )

        if not opciones["sin_comprobar"]:
            if settings.DEBUG:
                raise CommandError("DEBUG está activado: pon DJANGO_ENV=prod en el .env para servir en producción.")
            call_command("check", deploy=True, fail_level="ERROR")

        from mysite.wsgi import application

        self.stdout.write(self.style.SUCCESS(
            f"Waitress en http://{host}:{opciones['puerto']} ({opciones['hilos']} hilos). "
            "Entrar siempre por el proxy HTTPS."
        ))
        serve(
            application,
            host=host,
            port=opciones["puerto"],
            threads=opciones["hilos"],
            ident="",                        # sin cabecera Server con versión
            expose_tracebacks=False,
            max_request_body_size=110 * 1024 * 1024,  # algo por encima del límite de Caddy
            channel_timeout=300,             # el copilot puede tardar en responder
            connection_limit=200,
            # Waitress descarta las cabeceras X-Forwarded-* salvo que vengan del proxy indicado;
            # de las de Caddy toma la IP real del cliente (REMOTE_ADDR) y el esquema https.
            trusted_proxy=opciones["proxy"],
            trusted_proxy_count=1,
            trusted_proxy_headers={"x-forwarded-for", "x-forwarded-proto", "x-forwarded-host"},
            clear_untrusted_proxy_headers=True,
        )
