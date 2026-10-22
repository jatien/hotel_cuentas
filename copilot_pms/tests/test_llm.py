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
