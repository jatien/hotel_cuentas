from copilot_pms import conf
from copilot_pms.llm.base import ChatProvider, ProviderError


class ConfiguracionModelo(ProviderError):
    """Falta o sobra algo en la configuración del modelo (no se reintenta)."""

    code = "PROVIDER_CONFIG"


def get_provider() -> ChatProvider:
    proveedor = conf.get("PROVIDER")
    if proveedor == "fake":
        from copilot_pms.llm.fake import FakeChat

        return FakeChat()
    if proveedor == "openai":
        from copilot_pms.llm.openai_compat import OpenAICompatibleChat

        modelo, base_url = conf.get("MODEL"), conf.get("BASE_URL")
        if not base_url and "qwen" in modelo.lower():
            raise ConfiguracionModelo(
                f"El modelo {modelo} se sirve en local: falta COPILOT_BASE_URL en el .env "
                "(p. ej. http://IP-DEL-SERVIDOR:8000/v1).")
        return OpenAICompatibleChat(
            model=modelo, api_key=conf.get("API_KEY"), base_url=base_url,
            timeout=conf.get("REQUEST_TIMEOUT_SECONDS"), temperature=conf.get("TEMPERATURE"),
            top_p=conf.get("TOP_P"), top_k=conf.get("TOP_K"), presence_penalty=conf.get("PRESENCE_PENALTY"),
            enable_thinking=conf.get("ENABLE_THINKING"), max_tokens=conf.get("MAX_TOKENS"))
    raise ValueError(f"Proveedor de copilot desconocido: {proveedor}")
