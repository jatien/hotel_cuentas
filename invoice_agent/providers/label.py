"""
Proveedor «etiqueta»: devuelve un JSON ya preparado (una etiqueta hecha a mano o revisada)
en lugar de llamar a un modelo. Lo usa `invoice_agent_import --labels` para cargar facturas con
su JSON y revisarlas en la app con la misma validación que una extracción normal.
"""

from invoice_agent.providers.base import ExtractionProvider, ModelRequest, ModelResponse


class LabelProvider(ExtractionProvider):
    name = "etiqueta"
    model = "etiqueta-json"
    supports_vision = False

    def __init__(self, content: str):
        self.content = content

    def complete(self, request: ModelRequest) -> ModelResponse:
        return ModelResponse(content=self.content, model=self.model)
