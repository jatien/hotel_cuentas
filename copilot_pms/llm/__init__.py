from copilot_pms import conf
from copilot_pms.llm.base import ChatProvider


def get_provider() -> ChatProvider:
    proveedor = conf.get("PROVIDER")
    if proveedor == "fake":
        from copilot_pms.llm.fake import FakeChat

        return FakeChat()
    if proveedor == "openai":
        from copilot_pms.llm.openai_compat import OpenAICompatibleChat

        return OpenAICompatibleChat(model=conf.get("MODEL"), api_key=conf.get("API_KEY"),
                                    base_url=conf.get("BASE_URL"), timeout=conf.get("REQUEST_TIMEOUT_SECONDS"),
                                    temperature=conf.get("TEMPERATURE"))
    raise ValueError(f"Proveedor de copilot desconocido: {proveedor}")
