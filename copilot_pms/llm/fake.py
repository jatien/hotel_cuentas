"""
Proveedor falso para tests y para probar sin modelo: devuelve una secuencia
fija de respuestas y guarda los mensajes que recibió.
"""

from copilot_pms.llm.base import ChatProvider, ChatResponse, ToolCall


class FakeChat(ChatProvider):
    name = "fake"
    model = "fake"

    def __init__(self, respuestas=None):
        # Cada elemento: ChatResponse, una excepción (se lanza) o un str (respuesta final).
        self.respuestas = list(respuestas or [])
        self.llamadas: list[dict] = []

    def chat(self, messages, tools):
        self.llamadas.append({"messages": [dict(m) for m in messages], "tools": tools})
        if not self.respuestas:
            return ChatResponse(content="No tengo una respuesta preparada (proveedor de pruebas).", model=self.model)
        siguiente = self.respuestas.pop(0)
        if isinstance(siguiente, Exception):
            raise siguiente
        if isinstance(siguiente, str):
            return ChatResponse(content=siguiente, model=self.model)
        return siguiente


def llamar(nombre: str, argumentos: str = "{}", id: str = "call_1") -> ChatResponse:
    """Atajo para los tests: respuesta del modelo pidiendo una herramienta."""
    return ChatResponse(tool_calls=[ToolCall(id, nombre, argumentos)], model="fake")
