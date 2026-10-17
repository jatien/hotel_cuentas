"""
Proveedor para cualquier API compatible con OpenAI Chat Completions:
OpenAI, y servidores locales como Ollama o vLLM (cambiando BASE_URL).

Usa salidas estructuradas (response_format json_schema, strict) para que la
respuesta cumpla el esquema; aun así, services/extraction.py la vuelve a
validar con Pydantic porque no todos los servidores compatibles aplican el
esquema con la misma garantía.
"""

import base64

from invoice_agent.providers.base import (
    ExtractionProvider,
    ModelRequest,
    ModelResponse,
    ProviderAuthError,
    ProviderBadRequest,
    ProviderError,
    ProviderRateLimited,
    ProviderRefusal,
    ProviderTimeout,
    ProviderUnavailable,
)


def _retry_after(error) -> float | None:
    response = getattr(error, "response", None)
    headers = getattr(response, "headers", None) or {}
    value = headers.get("retry-after") if hasattr(headers, "get") else None
    try:
        return float(value) if value is not None else None
    except ValueError:
        return None


class OpenAICompatibleProvider(ExtractionProvider):
    name = "openai"

    def __init__(self, *, model: str, api_key: str, base_url: str = "", timeout: float = 90,
                 supports_vision: bool = True, temperature: float | None = 0, client=None):
        self.model = model
        self.supports_vision = supports_vision
        self.temperature = temperature
        if client is None:
            import openai

            client = openai.OpenAI(
                api_key=api_key or "sin-clave",  # los servidores locales no la piden
                base_url=base_url or None,
                timeout=timeout,
                max_retries=0,  # los reintentos los controla extraction.py (acotados y registrados)
            )
        self.client = client
        self.name = "openai" if not base_url else "openai_compatible"

    def _messages(self, request: ModelRequest) -> list[dict]:
        if request.images:
            content = [{"type": "text", "text": request.user_text}]
            for page, jpeg in request.images:
                content.append({"type": "text", "text": f"Imagen de la página {page}:"})
                encoded = base64.b64encode(jpeg).decode("ascii")
                content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{encoded}"}})
        else:
            content = request.user_text
        return [{"role": "system", "content": request.system}, {"role": "user", "content": content}]

    def complete(self, request: ModelRequest) -> ModelResponse:
        import openai

        kwargs = {
            "model": self.model,
            "messages": self._messages(request),
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": request.schema_name, "strict": True, "schema": request.json_schema},
            },
        }
        if self.temperature is not None:
            kwargs["temperature"] = self.temperature
        try:
            completion = self.client.chat.completions.create(**kwargs)
        except openai.APITimeoutError as error:
            raise ProviderTimeout("Tiempo de espera agotado") from error
        except openai.RateLimitError as error:
            raise ProviderRateLimited("Límite de peticiones", retry_after=_retry_after(error)) from error
        except openai.APIConnectionError as error:
            raise ProviderUnavailable("No se puede conectar con el proveedor") from error
        except (openai.AuthenticationError, openai.PermissionDeniedError) as error:
            raise ProviderAuthError("Clave de API no válida o sin permisos") from error
        except (openai.BadRequestError, openai.NotFoundError, openai.UnprocessableEntityError) as error:
            raise ProviderBadRequest(f"Petición rechazada ({getattr(error, 'status_code', '?')})") from error
        except openai.APIStatusError as error:
            if getattr(error, "status_code", 500) >= 500:
                raise ProviderUnavailable(f"Error del proveedor ({error.status_code})") from error
            raise ProviderError(f"Error del proveedor ({error.status_code})") from error

        choice = completion.choices[0]
        refusal = getattr(choice.message, "refusal", None)
        if refusal:
            raise ProviderRefusal("El modelo se ha negado a responder")
        usage = getattr(completion, "usage", None)
        return ModelResponse(
            content=choice.message.content or "",
            model=getattr(completion, "model", self.model) or self.model,
            input_tokens=getattr(usage, "prompt_tokens", 0) or 0,
            output_tokens=getattr(usage, "completion_tokens", 0) or 0,
            truncated=choice.finish_reason == "length",
        )
