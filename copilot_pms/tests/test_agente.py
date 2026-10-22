import json

from django.contrib.auth.models import Permission, User
from django.test import override_settings
from pydantic import BaseModel

from copilot_pms.agent import MENSAJE_LIMITE, PreguntaNoValida, preguntar
from copilot_pms.llm.base import ChatResponse, ProviderAuthError, ProviderUnavailable, ToolCall
from copilot_pms.llm.fake import FakeChat, llamar
from copilot_pms.models import Conversacion, EstadoLlamada, Mensaje
from copilot_pms.tests.utils import ContabilidadBase, ctx
from copilot_pms.tools import base as tools_base
from copilot_pms.tools.base import Resultado, disponibles, ejecutar

SIN_ESPERAS = override_settings(COPILOT_PMS={"BACKOFF_SECONDS": 0, "PROVIDER": "fake"})


@SIN_ESPERAS
class AgenteTests(ContabilidadBase):
    def test_pregunta_con_herramienta(self):
        proveedor = FakeChat([llamar("saldo_cuenta", '{"cuenta": "572", "hasta": "2026-06-30"}'),
                              "Saldo deudor de 1.500,00 €."])
        mensaje = preguntar("¿Saldo del banco a 30 de junio?", ctx(), proveedor=proveedor)

        self.assertEqual(mensaje.estado, Mensaje.Estado.OK)
        self.assertEqual(mensaje.contenido, "Saldo deudor de 1.500,00 €.")
        self.assertEqual(mensaje.llamadas_modelo, 2)
        llamada = mensaje.llamadas.get()
        self.assertEqual((llamada.herramienta, llamada.estado), ("saldo_cuenta", EstadoLlamada.OK))
        self.assertEqual(llamada.argumentos, {"cuenta": "572", "hasta": "2026-06-30"})
        self.assertEqual(llamada.resultado["datos"]["saldo_final"]["neto"], "1500.00")

        # El modelo recibió el sistema, las herramientas y el resultado como mensaje «tool»
        primera, segunda = proveedor.llamadas
        self.assertEqual(primera["messages"][0]["role"], "system")
        self.assertIn("Hoy es 22/10/2026", primera["messages"][0]["content"])
        self.assertIn("Hotel Prueba S.L.", primera["messages"][0]["content"])
        self.assertIn("saldo_cuenta", [t["function"]["name"] for t in primera["tools"]])
        tool_msg = segunda["messages"][-1]
        self.assertEqual(tool_msg["role"], "tool")
        self.assertEqual(json.loads(tool_msg["content"])["estado"], "ok")

    def test_historial_en_la_misma_conversacion(self):
        primero = preguntar("Hola", ctx(), proveedor=FakeChat(["Hola, ¿qué necesitas?"]))
        proveedor = FakeChat(["Vale."])
        preguntar("Y el saldo?", ctx(), primero.conversacion, proveedor=proveedor)
        roles = [m["role"] for m in proveedor.llamadas[0]["messages"]]
        self.assertEqual(roles, ["system", "user", "assistant", "user"])
        self.assertEqual(primero.conversacion.mensajes.count(), 4)

    def test_modelo_caido_no_inventa(self):
        proveedor = FakeChat([ProviderUnavailable("x"), ProviderUnavailable("x"), ProviderUnavailable("x")])
        mensaje = preguntar("¿Saldo?", ctx(), proveedor=proveedor)
        self.assertEqual(mensaje.estado, Mensaje.Estado.ERROR)
        self.assertEqual(mensaje.error_code, "PROVIDER_UNAVAILABLE")
        self.assertIn("No se ha dado ningún dato", mensaje.contenido)
        self.assertEqual(len(proveedor.llamadas), 3)  # 1 + 2 reintentos

    def test_error_no_transitorio_no_se_reintenta(self):
        proveedor = FakeChat([ProviderAuthError("x")])
        mensaje = preguntar("¿Saldo?", ctx(), proveedor=proveedor)
        self.assertEqual(mensaje.error_code, "PROVIDER_AUTH")
        self.assertEqual(len(proveedor.llamadas), 1)

    def test_limite_de_pasos(self):
        proveedor = FakeChat([llamar("buscar_cuentas", '{"texto": "banco"}', id=f"c{i}") for i in range(10)])
        mensaje = preguntar("bucle", ctx(), proveedor=proveedor)
        self.assertEqual(mensaje.estado, Mensaje.Estado.LIMITE)
        self.assertEqual(mensaje.contenido, MENSAJE_LIMITE)
        self.assertEqual(mensaje.llamadas.count(), 6)

    def test_errores_de_herramienta_vuelven_al_modelo(self):
        varias = ChatResponse(tool_calls=[ToolCall("a", "inventada", "{}"), ToolCall("b", "saldo_cuenta", "{malo")])
        proveedor = FakeChat([varias, "No puedo consultarlo."])
        mensaje = preguntar("x", ctx(), proveedor=proveedor)
        estados = list(mensaje.llamadas.values_list("estado", flat=True))
        self.assertEqual(estados, [EstadoLlamada.DESCONOCIDA, EstadoLlamada.INVALIDO])
        self.assertEqual(mensaje.llamadas.last().argumentos, {"_crudo": "{malo"})

    def test_respuesta_vacia(self):
        mensaje = preguntar("x", ctx(), proveedor=FakeChat([""]))
        self.assertEqual(mensaje.estado, Mensaje.Estado.ERROR)

    def test_pregunta_vacia(self):
        with self.assertRaises(PreguntaNoValida):
            preguntar("   ", ctx(), proveedor=FakeChat())
        self.assertFalse(Conversacion.objects.exists())


class _Nada(BaseModel):
    pass


class PermisosTests(ContabilidadBase):
    def test_herramientas_de_escritura_bloqueadas(self):
        tools_base.REGISTRO["reservar_prueba"] = tools_base.Herramienta(
            "reservar_prueba", "test", _Nada, lambda p, c: Resultado(), "reservas", escritura=True)
        try:
            self.assertNotIn("reservar_prueba", [h.nombre for h in disponibles(ctx())])
            r = ejecutar("reservar_prueba", "{}", ctx())
            self.assertEqual((r.estado, r.codigo), (EstadoLlamada.DENEGADO, "WRITE_DISABLED"))
        finally:
            del tools_base.REGISTRO["reservar_prueba"]

    def test_con_login_solo_las_herramientas_permitidas(self):
        user = User.objects.create_user("recepcion", password="x")
        sin_permisos = ctx(user=user, login_requerido=True)
        self.assertEqual(disponibles(sin_permisos), [])
        r = ejecutar("saldo_cuenta", '{"cuenta": "572"}', sin_permisos)
        self.assertEqual(r.estado, EstadoLlamada.DENEGADO)

        for codename in ("view_apunte", "view_cuentacontable"):
            user.user_permissions.add(Permission.objects.get(codename=codename, content_type__app_label="cuadro_cuentas"))
        user = User.objects.get(pk=user.pk)  # recarga la caché de permisos
        con_permisos = ctx(user=user, login_requerido=True)
        nombres = {h.nombre for h in disponibles(con_permisos)}
        self.assertIn("saldo_cuenta", nombres)
        self.assertNotIn("coste_personal", nombres)
        self.assertEqual(ejecutar("saldo_cuenta", '{"cuenta": "572"}', con_permisos).estado, EstadoLlamada.OK)


@override_settings(COPILOT_PMS={"PROVIDER": "openai", "MODEL": "Qwen/Qwen3.5-9B", "BASE_URL": ""})
class ConfiguracionTests(ContabilidadBase):
    def test_qwen_sin_servidor_configurado(self):
        mensaje = preguntar("¿Saldo?", ctx())
        self.assertEqual((mensaje.estado, mensaje.error_code), (Mensaje.Estado.ERROR, "PROVIDER_CONFIG"))
        self.assertIn("No se ha dado ningún dato", mensaje.contenido)
