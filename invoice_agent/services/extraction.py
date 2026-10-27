"""
Etapa C: llamada al modelo con salida estructurada, reintentos acotados y
validación de la respuesta con Pydantic.

- Errores transitorios (timeout, 429, 5xx, conexión): hasta PROVIDER_MAX_RETRIES
  reintentos con espera exponencial + aleatoriedad (respeta Retry-After, con tope).
- Errores definitivos (clave inválida, petición rechazada, negativa del modelo): sin reintento.
- Salida que no cumple el esquema o cortada: MALFORMED_OUTPUT_RETRIES
  reintentos indicando al modelo el fallo (sin repetir el documento en el log).
- Nunca se reintenta indefinidamente; si todo falla se lanza ExtractionFailed
  y el worker decide si reprograma el trabajo (también con un máximo).
"""

import random
import time
from dataclasses import dataclass
from decimal import Decimal

from pydantic import ValidationError

from invoice_agent import conf
from invoice_agent.prompts import (
    PROMPT_VERSION,
    build_document_text,
    build_system_prompt,
    prompt_version,
)
from invoice_agent.providers.base import ExtractionProvider, ModelRequest, ProviderError
from invoice_agent.schemas import SCHEMA_VERSION, ExtractionOutput, strict_json_schema
from invoice_agent.services.observability import log_event
from invoice_agent.services.preparation import PreparedDocument


class ExtractionFailed(Exception):
    def __init__(self, code: str, message: str, retryable: bool):
        super().__init__(message)
        self.code, self.message, self.retryable = code, message, retryable
        self.stats: CallStats | None = None


@dataclass
class CallStats:
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    model: str = ""

    def cost(self) -> Decimal:
        per_in = conf.decimal("PRICE_INPUT_PER_MTOK")
        per_out = conf.decimal("PRICE_OUTPUT_PER_MTOK")
        return (Decimal(self.input_tokens) * per_in + Decimal(self.output_tokens) * per_out) / Decimal(1_000_000)


@dataclass
class ExtractionResult:
    output: ExtractionOutput
    raw: dict
    stats: CallStats
    prompt_version: str = PROMPT_VERSION
    schema_version: str = SCHEMA_VERSION


def backoff_delay(attempt: int, retry_after: float | None = None) -> float:
    cap = float(conf.get("BACKOFF_MAX_SECONDS"))
    if retry_after is not None:
        return min(max(retry_after, 0.0), cap)
    base = float(conf.get("BACKOFF_BASE_SECONDS"))
    return min(cap, base * (2**attempt)) * (0.5 + random.random() / 2)


def call_with_retries(provider: ExtractionProvider, request: ModelRequest, stats: CallStats, sleep=time.sleep):
    max_retries = int(conf.get("PROVIDER_MAX_RETRIES"))
    for attempt in range(max_retries + 1):
        stats.calls += 1
        try:
            response = provider.complete(request)
        except ProviderError as error:
            log_event("provider.error", provider=provider.name, code=error.code, attempt=attempt + 1)
            if not error.retryable:
                raise ExtractionFailed(error.code, str(error), retryable=False) from error
            if attempt == max_retries:
                raise ExtractionFailed(error.code, str(error), retryable=True) from error
            sleep(backoff_delay(attempt, error.retry_after))
            continue
        stats.input_tokens += response.input_tokens
        stats.output_tokens += response.output_tokens
        stats.model = response.model
        return response
    raise AssertionError("inalcanzable")


def _validation_summary(error: ValidationError) -> str:
    return "; ".join(f"{'.'.join(str(p) for p in e['loc'])}: {e['type']}" for e in error.errors()[:10])


USER_INSTRUCTION = "Extrae los datos de esta factura según el esquema.\n\n"


def build_request(prepared: PreparedDocument, empresa_nombre: str, empresa_nif: str, with_images: bool) -> ModelRequest:
    """
    La petición exacta que recibe el modelo. La usa también la exportación del
    dataset de entrenamiento, para que el modelo afinado vea en producción lo
    mismo que vio al entrenar (mismo prompt, mismo texto, mismas imágenes).
    """
    document_text = build_document_text(
        prepared, int(conf.get("MAX_TEXT_CHARS")), bool(conf.get("REDACT_BEFORE_MODEL"))
    )
    return ModelRequest(
        system=build_system_prompt(empresa_nombre, empresa_nif),
        user_text=USER_INSTRUCTION + document_text,
        json_schema=strict_json_schema(ExtractionOutput),
        schema_name="factura",
        images=prepared.images if with_images else [],
    )


def run_extraction(
    prepared: PreparedDocument,
    *,
    empresa_nombre: str,
    empresa_nif: str,
    provider: ExtractionProvider,
    sleep=time.sleep,
) -> ExtractionResult:
    if prepared.image_only and not provider.supports_vision:
        # Sin imágenes el modelo no vería nada del documento: nunca se envía una petición vacía.
        raise ExtractionFailed("VISION_REQUIRED", "El modo solo imagen necesita un modelo con visión.", False)
    base_request = build_request(prepared, empresa_nombre, empresa_nif, provider.supports_vision)
    stats = CallStats()
    request = base_request
    repairs = int(conf.get("MALFORMED_OUTPUT_RETRIES"))
    last_problem = ""
    for attempt in range(repairs + 1):
        try:
            response = call_with_retries(provider, request, stats, sleep)
        except ExtractionFailed as error:
            error.stats = stats  # el worker registra tokens y llamadas también en los fallos
            raise
        if response.truncated:
            last_problem = "respuesta cortada por longitud"
        else:
            try:
                output = ExtractionOutput.model_validate_json(response.content)
                return ExtractionResult(output=output, raw=output.model_dump(mode="json"), stats=stats,
                                        prompt_version=prompt_version(prepared))
            except ValidationError as error:
                last_problem = _validation_summary(error)
        log_event("provider.malformed_output", provider=provider.name, attempt=attempt + 1, problem=last_problem)
        request = ModelRequest(
            system=base_request.system,
            user_text=base_request.user_text
            + f"\n\nTu respuesta anterior no cumplía el esquema ({last_problem}). "
            "Devuelve únicamente un JSON válido con todos los campos del esquema.",
            json_schema=base_request.json_schema,
            schema_name=base_request.schema_name,
            images=base_request.images,
        )
    error = ExtractionFailed("MALFORMED_OUTPUT", f"Salida del modelo no válida: {last_problem}", retryable=True)
    error.stats = stats
    raise error
