"""
Proveedor para APIs compatibles con OpenAI Chat Completions con «tools»:
OpenAI, y servidores locales (Ollama, vLLM, LM Studio) cambiando BASE_URL.
El modelo local tiene que admitir llamadas a funciones (p. ej. Qwen2.5-Instruct).
"""

from copilot_pms.llm.base import (
    ChatProvider,
    ChatResponse,
    ProviderAuthError,
    ProviderBadRequest,
    ProviderError,
    ProviderRateLimited,
    ProviderRefusal,
    ProviderTimeout,
    ProviderUnavailable,
    ToolCall,
)


class OpenAICompatibleChat(ChatProvider):
    def __init__(self, *, model: str, api_key: str = "", base_url: str = "", timeout: float = 120,
                 temperature: float | None = 0, client=None):
        self.model = model
        self.temperature = temperature
        if client is None:
            import openai

            client = openai.OpenAI(api_key=api_key or "sin-clave", base_url=base_url or None,
                                   timeout=timeout, max_retries=0)
        self.client = client
        self.name = "openai_compatible" if base_url else "openai"

    def chat(self, messages, tools):
        import openai

        kwargs = {"model": self.model, "messages": messages}
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
        if self.temperature is not None:
            kwargs["temperature"] = self.temperature
        try:
            completion = self.client.chat.completions.create(**kwargs)
        except openai.APITimeoutError as error:
            raise ProviderTimeout("Tiempo de espera agotado") from error
        except openai.RateLimitError as error:
            raise ProviderRateLimited("Límite de peticiones") from error
        except openai.APIConnectionError as error:
            raise ProviderUnavailable("No se puede conectar con el modelo") from error
        except (openai.AuthenticationError, openai.PermissionDeniedError) as error:
            raise ProviderAuthError("Clave de API no válida o sin permisos") from error
        except (openai.BadRequestError, openai.NotFoundError, openai.UnprocessableEntityError) as error:
            raise ProviderBadRequest(f"Petición rechazada ({getattr(error, 'status_code', '?')})") from error
        except openai.APIStatusError as error:
            if getattr(error, "status_code", 500) >= 500:
                raise ProviderUnavailable(f"Error del proveedor ({error.status_code})") from error
            raise ProviderError(f"Error del proveedor ({error.status_code})") from error

        choice = completion.choices[0]
        if getattr(choice.message, "refusal", None):
            raise ProviderRefusal("El modelo se ha negado a responder")
        usage = getattr(completion, "usage", None)
        return ChatResponse(
            content=choice.message.content or "",
            tool_calls=[ToolCall(c.id, c.function.name, c.function.arguments or "{}")
                        for c in (choice.message.tool_calls or [])],
            model=getattr(completion, "model", self.model) or self.model,
            input_tokens=getattr(usage, "prompt_tokens", 0) or 0,
            output_tokens=getattr(usage, "completion_tokens", 0) or 0,
            truncated=choice.finish_reason == "length",
        )
