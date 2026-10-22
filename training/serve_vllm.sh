#!/usr/bin/env bash
# Sirve un modelo con vLLM (API compatible con OpenAI) para probarlo desde Django.
#
#   bash serve_vllm.sh base                          # Qwen2.5-VL-7B-Instruct sin afinar (línea base)
#   bash serve_vllm.sh /workspace/outputs/run1/merged   # el modelo afinado
#   QUANT=fp8 bash serve_vllm.sh ...                 # como correrá en una GPU de 16 GB (RTX 50xx/40xx)
#
# En RunPod expón el puerto HTTP 8000 del pod. La URL pública será
#   https://<POD_ID>-8000.proxy.runpod.net/v1
# y es accesible desde Internet: por eso se exige una clave (VLLM_API_KEY).
set -euo pipefail
MODEL="${1:?Indica 'base' o la carpeta del modelo fusionado}"
[[ "$MODEL" == "base" ]] && MODEL="Qwen/Qwen2.5-VL-7B-Instruct"
NAME="${SERVED_NAME:-factura-qwen}"
export HF_HOME=/workspace/hf-cache
if [[ -z "${VLLM_API_KEY:-}" ]]; then
  export VLLM_API_KEY="$(python3 -c 'import secrets; print(secrets.token_urlsafe(24))')"
fi
echo "Modelo: $MODEL · nombre: $NAME"
echo "Clave de la API (ponla en INVOICE_AGENT_API_KEY): $VLLM_API_KEY"
QUANT_ARGS=()
[[ -n "${QUANT:-}" ]] && QUANT_ARGS=(--quantization "$QUANT")
exec /workspace/venv-serve/bin/vllm serve "$MODEL" \
  --served-model-name "$NAME" \
  --host 0.0.0.0 --port 8000 \
  --api-key "$VLLM_API_KEY" \
  --max-model-len 16384 \
  --limit-mm-per-prompt '{"image":6,"video":0}' \
  "${QUANT_ARGS[@]}"
