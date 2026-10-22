from types import SimpleNamespace as NS

from django.test import SimpleTestCase

from copilot_pms.llm.openai_compat import OpenAICompatibleChat


class _Cliente:
    def __init__(self, completion):
        self.kwargs = None
        self.chat = NS(completions=NS(create=self._create))
        self._completion = completion

    def _create(self, **kwargs):
        self.kwargs = kwargs
        return self._completion


class OpenAICompatTests(SimpleTestCase):
    def test_traduce_llamadas_a_funciones(self):
        llamada = NS(id="call_9", function=NS(name="saldo_cuenta", arguments='{"cuenta": "572"}'))
        completion = NS(model="qwen2.5:7b", usage=NS(prompt_tokens=120, completion_tokens=15),
                        choices=[NS(finish_reason="tool_calls",
                                    message=NS(content=None, tool_calls=[llamada], refusal=None))])
        cliente = _Cliente(completion)
        proveedor = OpenAICompatibleChat(model="qwen2.5:7b", client=cliente)
        tools = [{"type": "function", "function": {"name": "saldo_cuenta", "parameters": {}}}]

        r = proveedor.chat([{"role": "user", "content": "hola"}], tools)

        self.assertEqual(cliente.kwargs["tools"], tools)
        self.assertEqual(cliente.kwargs["tool_choice"], "auto")
        self.assertEqual(cliente.kwargs["temperature"], 0)
        self.assertEqual((r.tool_calls[0].id, r.tool_calls[0].name), ("call_9", "saldo_cuenta"))
        self.assertEqual((r.input_tokens, r.output_tokens, r.content), (120, 15, ""))


def _respuesta(contenido):
    return NS(model="m", usage=None,
              choices=[NS(finish_reason="stop", message=NS(content=contenido, tool_calls=None, refusal=None))])


class Qwen35Tests(SimpleTestCase):
    def test_servidor_local_recibe_top_k_y_thinking(self):
        cliente = _Cliente(_respuesta("Hola"))
        OpenAICompatibleChat(model="Qwen/Qwen3.5-9B", base_url="http://srv:8000/v1", client=cliente,
                             temperature=0.7, top_p=0.8, top_k=20, presence_penalty=1.5,
                             enable_thinking=False).chat([], [])
        self.assertEqual(cliente.kwargs["extra_body"],
                         {"top_k": 20, "chat_template_kwargs": {"enable_thinking": False}})
        self.assertEqual((cliente.kwargs["top_p"], cliente.kwargs["presence_penalty"]), (0.8, 1.5))

    def test_openai_en_la_nube_no_recibe_campos_ajenos(self):
        cliente = _Cliente(_respuesta("Hola"))
        OpenAICompatibleChat(model="gpt-4.1-mini", client=cliente, top_k=20, enable_thinking=False).chat([], [])
        self.assertNotIn("extra_body", cliente.kwargs)

    def test_quita_el_razonamiento_del_contenido(self):
        r = OpenAICompatibleChat(model="q", client=_Cliente(_respuesta("<think>calculo…</think>\nSaldo: 10 €"))).chat([], [])
        self.assertEqual(r.content, "Saldo: 10 €")
        cortado = OpenAICompatibleChat(model="q", client=_Cliente(_respuesta("<think>sin terminar"))).chat([], [])
        self.assertEqual(cortado.content, "")
