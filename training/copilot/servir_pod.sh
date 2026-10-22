#!/usr/bin/env bash
# Sirve Qwen3.5-9B (base o afinado) con vLLM en el pod, para evaluarlo desde Django con copilot_evaluar.
#
#   bash servir_pod.sh base
#   bash servir_pod.sh /workspace/outputs/copilot1/merged
#
# En RunPod expón el puerto HTTP 8000. La URL será https://<POD_ID>-8000.proxy.runpod.net/v1 y es
# pública: por eso se exige la clave (VLLM_API_KEY), que va en COPILOT_API_KEY.
set -euo pipefail
MODELO="${1:?Indica 'base' o la carpeta del modelo fusionado}"
[[ "$MODELO" == "base" ]] && MODELO="Qwen/Qwen3.5-9B"
export HF_HOME=/workspace/hf-cache
if [[ -z "${VLLM_API_KEY:-}" ]]; then
  export VLLM_API_KEY="$(python3 -c 'import secrets; print(secrets.token_urlsafe(24))')"
fi
echo "Clave de la API (ponla en COPILOT_API_KEY): $VLLM_API_KEY"
exec /workspace/venv-serve/bin/vllm serve "$MODELO" \
  --served-model-name Qwen/Qwen3.5-9B \
  --host 0.0.0.0 --port 8000 --api-key "$VLLM_API_KEY" \
  --max-model-len 16384 \
  --enable-auto-tool-choice --tool-call-parser qwen3_coder --reasoning-parser qwen3 \
  --limit-mm-per-prompt '{"image":0,"video":0}'
