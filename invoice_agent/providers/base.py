"""
Interfaz del proveedor de IA. La lógica de negocio solo conoce esto; cambiar
de OpenAI a un servidor local (u otro proveedor) es escribir otra clase con
el método complete() y registrarla en providers/__init__.py.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class ModelRequest:
    system: str
    user_text: str
    json_schema: dict
    schema_name: str
    images: list[tuple[int, bytes]] = field(default_factory=list)  # (página, JPEG)


@dataclass
class ModelResponse:
    content: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    truncated: bool = False


class ProviderError(Exception):
    """Error del proveedor. `retryable` indica si tiene sentido repetir la llamada."""

    retryable = False
    code = "PROVIDER_ERROR"

    def __init__(self, message: str = "", retry_after: float | None = None):
        super().__init__(message or self.code)
        self.retry_after = retry_after


class ProviderTimeout(ProviderError):
    retryable = True
    code = "PROVIDER_TIMEOUT"


class ProviderRateLimited(ProviderError):
    retryable = True
    code = "PROVIDER_RATE_LIMITED"


class ProviderUnavailable(ProviderError):
    retryable = True
    code = "PROVIDER_UNAVAILABLE"


class ProviderAuthError(ProviderError):
    code = "PROVIDER_AUTH"


class ProviderBadRequest(ProviderError):
    code = "PROVIDER_BAD_REQUEST"


class ProviderRefusal(ProviderError):
    code = "PROVIDER_REFUSAL"


class ExtractionProvider(ABC):
    name: str = "base"
    model: str = ""
    supports_vision: bool = False

    @abstractmethod
    def complete(self, request: ModelRequest) -> ModelResponse:
        """Una llamada al modelo, SIN reintentos (los gestiona services/extraction.py)."""
