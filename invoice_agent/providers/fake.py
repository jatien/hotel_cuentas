"""
Proveedor falso: devuelve respuestas preparadas (texto JSON o excepciones),
en orden. Para tests y para probar la app sin red ni coste.
"""

from invoice_agent.providers.base import ExtractionProvider, ModelRequest, ModelResponse


class FakeProvider(ExtractionProvider):
    name = "fake"

    def __init__(self, responses=None, *, model: str = "fake-model", supports_vision: bool = True):
        self.responses = list(responses or [])
        self.model = model
        self.supports_vision = supports_vision
        self.requests: list[ModelRequest] = []

    def complete(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        if not self.responses:
            raise AssertionError("FakeProvider sin respuestas preparadas")
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        if isinstance(item, ModelResponse):
            return item
        return ModelResponse(content=item, model=self.model, input_tokens=1000, output_tokens=500)
