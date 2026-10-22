"""
Interfaz del modelo de chat con llamadas a funciones. El agente solo conoce esto:
cambiar de OpenAI a un modelo local (Ollama, vLLM con Qwen...) es cambiar BASE_URL,
y otro proveedor es otra clase con chat().

Los errores son los mismos que en invoice_agent (retryable o no).
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from invoice_agent.providers.base import (  # noqa: F401  (se reexportan)
    ProviderAuthError,
    ProviderBadRequest,
    ProviderError,
    ProviderRateLimited,
    ProviderRefusal,
    ProviderTimeout,
    ProviderUnavailable,
)


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: str  # JSON tal cual lo devuelve el modelo; lo valida el ejecutor


@dataclass
class ChatResponse:
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    truncated: bool = False


class ChatProvider(ABC):
    name: str = "base"
    model: str = ""

    @abstractmethod
    def chat(self, messages: list[dict], tools: list[dict]) -> ChatResponse:
        """Una llamada al modelo, SIN reintentos (los gestiona el agente)."""
