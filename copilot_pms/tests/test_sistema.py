import json
from datetime import timedelta

from django.contrib.auth.models import Permission, User
from django.test import TestCase
from django.utils import timezone

from copilot_pms.models import EstadoLlamada
from copilot_pms.tests.utils import ctx
from copilot_pms.tools.base import disponibles, ejecutar
from seguridad.models import CopiaSeguridad


def run(**args):
    return ejecutar("estado_copias_seguridad", json.dumps(args), ctx())


class EstadoCopiasTests(TestCase):
    def _copia(self, tipo, horas, estado="ok", verificada=True):
        c = CopiaSeguridad.objects.create(tipo=tipo, estado=estado, fichero=f"{tipo}-{horas}.age", tamano=5_242_880)
        cuando = timezone.now() - timedelta(hours=horas)
        CopiaSeguridad.objects.filter(pk=c.pk).update(creada=cuando, verificada=cuando if verificada else None)

    def test_sin_copias(self):
        r = run()
        self.assertEqual(r.estado, EstadoLlamada.VACIO)
        self.assertEqual(len(r.avisos), 2)

    def test_todo_correcto(self):
        self._copia("bd", 3)
        self._copia("ficheros", 3)
        r = run()
        self.assertEqual(r.estado, EstadoLlamada.OK)
        self.assertEqual(r.avisos, [])
        bd = r.datos["copias"]["Base de datos"]
        self.assertEqual(bd["tamano_mb"], 5.0)
        self.assertEqual(bd["fallos_periodo"], 0)

    def test_avisa_de_copia_vieja_sin_verificar_y_fallos(self):
        self._copia("bd", 50, verificada=False)
        self._copia("bd", 2, estado="error")
        self._copia("ficheros", 1)
        r = run(dias_historial=7)
        texto = " ".join(r.avisos)
        self.assertIn("más de 26 horas", texto)
        self.assertIn("no se ha verificado", texto)
        self.assertIn("1 copias fallidas", texto)
        self.assertIsNotNone(r.datos["copias"]["Base de datos"]["ultimo_fallo"])

    def test_no_expone_ficheros_ni_claves(self):
        self._copia("bd", 1)
        texto = json.dumps(run().para_modelo())
        self.assertNotIn(".age", texto)
        self.assertNotIn("sha256", texto)

    def test_argumentos_validados(self):
        self.assertEqual(run(dias_historial=0).estado, EstadoLlamada.INVALIDO)
        self.assertEqual(run(ruta="/etc").estado, EstadoLlamada.INVALIDO)

    def test_con_login_exige_permiso(self):
        user = User.objects.create_user("recepcion", password="x")
        self.assertNotIn("estado_copias_seguridad", {h.nombre for h in disponibles(ctx(user=user, login_requerido=True))})
        user.user_permissions.add(Permission.objects.get(codename="view_copiaseguridad"))
        user = User.objects.get(pk=user.pk)
        self.assertIn("estado_copias_seguridad", {h.nombre for h in disponibles(ctx(user=user, login_requerido=True))})
