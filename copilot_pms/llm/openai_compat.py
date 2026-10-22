"""
Proveedor para APIs compatibles con OpenAI Chat Completions con «tools»:
OpenAI, y servidores locales (vLLM, Ollama, LM Studio) cambiando BASE_URL.

Modelo principal: Qwen3.5-9B servido con vLLM (ver copilot_pms/deploy/). Con un
servidor local se envían además, en extra_body, los parámetros que no existen en
la API de OpenAI: top_k y chat_template_kwargs.enable_thinking (Qwen3.5 piensa por
defecto; para el copilot lo desactivamos salvo que se configure lo contrario).
"""

import re

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
                 temperature: float | None = 0, top_p: float | None = None, top_k: int | None = None,
                 presence_penalty: float | None = None, enable_thinking: bool | None = None,
                 max_tokens: int | None = None, client=None):
        self.model = model
        self.temperature = temperature
        self.top_p = top_p
        self.presence_penalty = presence_penalty
        self.max_tokens = max_tokens
        self.local = bool(base_url)
        # Solo para servidores locales: la API de OpenAI rechaza estos campos.
        self.extra_body = {}
        if self.local and top_k is not None:
            self.extra_body["top_k"] = top_k
        if self.local and enable_thinking is not None:
            self.extra_body["chat_template_kwargs"] = {"enable_thinking": enable_thinking}
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
        if self.top_p is not None:
            kwargs["top_p"] = self.top_p
        if self.presence_penalty is not None:
            kwargs["presence_penalty"] = self.presence_penalty
        if self.max_tokens:
            kwargs["max_tokens"] = self.max_tokens
        if self.extra_body:
            kwargs["extra_body"] = self.extra_body
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
            content=quitar_razonamiento(choice.message.content or ""),
            tool_calls=[ToolCall(c.id, c.function.name, c.function.arguments or "{}")
                        for c in (choice.message.tool_calls or [])],
            model=getattr(completion, "model", self.model) or self.model,
            input_tokens=getattr(usage, "prompt_tokens", 0) or 0,
            output_tokens=getattr(usage, "completion_tokens", 0) or 0,
            truncated=choice.finish_reason == "length",
        )


_PENSAMIENTO = re.compile(r"<think>.*?</think>\s*", re.DOTALL)


def quitar_razonamiento(texto: str) -> str:
    """Quita el razonamiento <think>…</think> si el servidor lo deja en el contenido (Ollama, o
    vLLM sin --reasoning-parser). Si el texto se cortó dentro del razonamiento, no queda respuesta."""
    texto = _PENSAMIENTO.sub("", texto)
    if "<think>" in texto:
        texto = texto.split("<think>", 1)[0]
    return texto.strip()
