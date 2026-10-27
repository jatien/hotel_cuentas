"""Fábrica del proveedor configurado. Los tests pueden fijar otro con use_provider()."""

from contextlib import contextmanager

from invoice_agent import conf
from invoice_agent.providers.base import ExtractionProvider

_override: ExtractionProvider | None = None


def get_provider() -> ExtractionProvider:
    if _override is not None:
        return _override
    kind = conf.get("PROVIDER")
    if kind == "openai":
        from invoice_agent.providers.openai_compat import OpenAICompatibleProvider

        return OpenAICompatibleProvider(
            model=conf.get("MODEL"),
            api_key=conf.get("API_KEY"),
            base_url=conf.get("BASE_URL"),
            timeout=float(conf.get("REQUEST_TIMEOUT_SECONDS")),
            supports_vision=bool(conf.get("SUPPORTS_VISION")),
            temperature=conf.get("TEMPERATURE"),
        )
    if kind == "fake":
        from invoice_agent.providers.fake import FakeProvider

        return FakeProvider()
    raise ValueError(f"Proveedor de IA desconocido: {kind!r}")


def get_teacher_provider() -> ExtractionProvider:
    """
    El LLM grande que etiqueta facturas para entrenar (TEACHER_*). Sin TEACHER_MODEL es el
    proveedor normal. La clave principal solo se reutiliza si el profesor está en el mismo servidor:
    nunca se envía la clave de un servicio a otro.
    """
    if _override is not None:
        return _override
    model = conf.get("TEACHER_MODEL")
    if not model:
        return get_provider()
    from invoice_agent.providers.openai_compat import OpenAICompatibleProvider

    base_url = conf.get("TEACHER_BASE_URL")
    api_key = conf.get("TEACHER_API_KEY") or (conf.get("API_KEY") if base_url == conf.get("BASE_URL") else "")
    return OpenAICompatibleProvider(
        model=model,
        api_key=api_key,
        base_url=base_url,
        timeout=float(conf.get("REQUEST_TIMEOUT_SECONDS")),
        supports_vision=True,
        temperature=conf.get("TEMPERATURE"),
    )


@contextmanager
def use_provider(provider: ExtractionProvider):
    global _override
    previous, _override = _override, provider
    try:
        yield provider
    finally:
        _override = previous
