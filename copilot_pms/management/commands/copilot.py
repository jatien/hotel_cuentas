"""
PMS Copilot desde la consola.

    python manage.py copilot herramientas
    python manage.py copilot ejecutar saldo_cuenta "{\"cuenta\": \"572\", \"hasta\": \"2026-09-30\"}"
    python manage.py copilot preguntar "¿Saldo de la 572 a 30 de septiembre?"

«ejecutar» llama a una herramienta sin modelo de IA: sirve para comprobar los datos
que recibiría el copilot.
"""

import json

from django.core.management.base import BaseCommand, CommandError

from copilot_pms.access import contexto_sistema
from copilot_pms.agent import PreguntaNoValida, preguntar
from copilot_pms.tools import PENDIENTES, disponibles, ejecutar


class Command(BaseCommand):
    help = "PMS Copilot: listar herramientas, ejecutar una o hacer una pregunta."

    def add_arguments(self, parser):
        sub = parser.add_subparsers(dest="accion", required=True)
        sub.add_parser("herramientas", help="Lista las herramientas y lo que aún no está conectado.")
        ej = sub.add_parser("ejecutar", help="Ejecuta una herramienta con argumentos JSON (sin IA).")
        ej.add_argument("nombre")
        ej.add_argument("argumentos", nargs="?", default="{}")
        pr = sub.add_parser("preguntar", help="Hace una pregunta al copilot (usa el modelo configurado).")
        pr.add_argument("pregunta")

    def handle(self, *args, accion, **opciones):
        ctx = contexto_sistema()
        if accion == "herramientas":
            for h in disponibles(ctx):
                self.stdout.write(f"{h.nombre:22} [{h.dominio}] {h.descripcion}")
            self.stdout.write("\nAún no conectado:")
            for que, motivo in PENDIENTES:
                self.stdout.write(f"  - {que}: {motivo}")
        elif accion == "ejecutar":
            resultado = ejecutar(opciones["nombre"], opciones["argumentos"], ctx)
            self.stdout.write(json.dumps(resultado.para_modelo(), ensure_ascii=False, indent=2))
        else:
            try:
                mensaje = preguntar(opciones["pregunta"], ctx)
            except PreguntaNoValida as error:
                raise CommandError(str(error))
            for llamada in mensaje.llamadas.all():
                self.stdout.write(self.style.NOTICE(
                    f"· {llamada.herramienta} {json.dumps(llamada.argumentos, ensure_ascii=False)} -> {llamada.estado}"))
            self.stdout.write(mensaje.contenido)
