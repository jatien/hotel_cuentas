"""
Copias de seguridad cifradas (base de datos + ficheros subidos).

    python manage.py copia_seguridad                    # base de datos y ficheros, y poda
    python manage.py copia_seguridad --solo-bd
    python manage.py copia_seguridad --generar-clave    # crea un par de claves age (la privada, fuera del servidor)
    python manage.py copia_seguridad --verificar cuentas-20261023-0300.dump.age --identidad E:\\clave-copias.txt
    python manage.py copia_seguridad --estado

Programarlo cada noche (Programador de tareas en Windows, cron/systemd en Debian): docs/security.md §5.
Termina con código 1 si alguna copia falla, para que el programador lo marque como error.
"""

import sys
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from seguridad import copias
from seguridad.models import CopiaSeguridad


class Command(BaseCommand):
    help = "Copias de seguridad cifradas con age de la base de datos y de los ficheros subidos."

    def add_arguments(self, parser):
        grupo = parser.add_mutually_exclusive_group()
        grupo.add_argument("--solo-bd", action="store_true", help="Solo la base de datos.")
        grupo.add_argument("--solo-ficheros", action="store_true", help="Solo media/ y private_media/.")
        grupo.add_argument("--generar-clave", action="store_true",
                           help="Genera un par de claves age y lo muestra (no guarda nada).")
        grupo.add_argument("--verificar", metavar="FICHERO",
                           help="Descifra una copia y comprueba que es restaurable (necesita --identidad).")
        grupo.add_argument("--estado", action="store_true", help="Resumen de las últimas copias.")
        parser.add_argument("--identidad", metavar="CLAVE",
                            help="Fichero con la clave privada AGE-SECRET-KEY-… (solo para --verificar).")
        parser.add_argument("--sin-podar", action="store_true", help="No borrar copias antiguas.")

    def handle(self, *args, **o):
        if o["generar_clave"]:
            return self._generar_clave()
        if o["verificar"]:
            return self._verificar(o["verificar"], o["identidad"])
        if o["estado"]:
            return self._estado()

        salidas = []
        if not o["solo_ficheros"]:
            salidas.append(copias.copia_base_datos())
        if not o["solo_bd"]:
            salidas.append(copias.copia_ficheros())

        fallos = 0
        for s in salidas:
            r = s.registro
            if r.estado == CopiaSeguridad.Estado.OK:
                self.stdout.write(self.style.SUCCESS(
                    f"OK   {r.get_tipo_display():18} {r.fichero}  {r.tamano / 1_048_576:.1f} MB  {r.duracion_s:.0f} s"))
            else:
                fallos += 1
                self.stderr.write(self.style.ERROR(f"FALLO {r.get_tipo_display():17} {r.error}"))

        if not o["sin_podar"] and not fallos:
            for nombre in copias.podar(settings.BACKUP_KEEP_DAYS):
                self.stdout.write(f"Borrada copia antigua: {nombre}")
        if fallos:
            sys.exit(1)

    def _generar_clave(self):
        from pyrage import x25519

        clave = x25519.Identity.generate()
        self.stdout.write("Clave PÚBLICA (va en el .env del servidor):\n")
        self.stdout.write(f"  BACKUP_AGE_RECIPIENTS={clave.to_public()}\n\n")
        self.stdout.write("Clave PRIVADA (guárdala FUERA del servidor: USB en la caja fuerte, gestor de contraseñas…).\n"
                          "Sin ella las copias no se pueden restaurar; con ella cualquiera puede leerlas:\n")
        self.stdout.write(f"  {clave}\n")

    def _verificar(self, fichero, identidad):
        if not identidad:
            raise CommandError("--verificar necesita --identidad con la clave privada.")
        ruta = Path(fichero)
        if not ruta.is_absolute() and not ruta.exists():
            ruta = Path(settings.BACKUP_DIR) / ruta
        if not ruta.exists():
            raise CommandError(f"No existe {ruta}")
        try:
            resultado = copias.verificar(ruta, Path(identidad))
        except Exception as exc:
            raise CommandError(f"La copia NO es válida: {exc}") from exc
        self.stdout.write(self.style.SUCCESS(f"Copia válida: {resultado}"))

    def _estado(self):
        for tipo, nombre in CopiaSeguridad.Tipo.choices:
            ultima = CopiaSeguridad.objects.filter(tipo=tipo, estado=CopiaSeguridad.Estado.OK).first()
            if ultima:
                verif = f"verificada {ultima.verificada:%Y-%m-%d}" if ultima.verificada else "sin verificar"
                self.stdout.write(f"{nombre:18} {ultima.creada:%Y-%m-%d %H:%M}  {ultima.fichero}  ({verif})")
            else:
                self.stdout.write(f"{nombre:18} ninguna copia correcta")
