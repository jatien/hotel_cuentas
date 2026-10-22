#!/usr/bin/env bash
# Sirve Qwen3.5-9B para el PMS Copilot con vLLM (API compatible con OpenAI) en Docker.
# Pensado para el servidor Debian con la GPU NVIDIA de 16 GB.
#
#   bash serve_qwen35.sh                     # FP8, como corre en 16 GB
#   QUANT= bash serve_qwen35.sh              # BF16 (necesita ~24 GB de VRAM)
#   MODEL=/modelos/copilot-merged bash serve_qwen35.sh   # un modelo afinado y fusionado
#
# Requisitos: Docker + NVIDIA Container Toolkit. La primera vez descarga el modelo (~20 GB) a
# ~/.cache/huggingface. Django apunta a http://IP-DEL-SERVIDOR:8000/v1 con la clave que se imprime.
set -euo pipefail

MODEL="${MODEL:-Qwen/Qwen3.5-9B}"
NAME="${SERVED_NAME:-Qwen/Qwen3.5-9B}"     # lo que va en COPILOT_MODEL
QUANT="${QUANT-fp8}"                       # FP8: ~10 GB de pesos, casi sin pérdida en RTX 40xx/50xx
MAX_LEN="${MAX_LEN:-32768}"                # sobra para 9 herramientas + resultados + historial
IMAGE="${IMAGE:-vllm/vllm-openai:latest}"  # Qwen3.5 necesita una versión reciente de vLLM

if [[ -z "${VLLM_API_KEY:-}" ]]; then
  VLLM_API_KEY="$(python3 -c 'import secrets; print(secrets.token_urlsafe(24))')"
fi
echo "Modelo: $MODEL · nombre servido: $NAME · cuantización: ${QUANT:-ninguna}"
echo "Clave de la API (ponla en COPILOT_API_KEY): $VLLM_API_KEY"

ARGS=(
  --model "$MODEL"
  --served-model-name "$NAME"
  --host 0.0.0.0 --port 8000
  --api-key "$VLLM_API_KEY"
  --max-model-len "$MAX_LEN"
  # Llamadas a funciones y razonamiento con los analizadores que indica la ficha del modelo
  --enable-auto-tool-choice --tool-call-parser qwen3_coder
  --reasoning-parser qwen3
  # El copilot solo envía texto: no reservar memoria para imágenes ni vídeo
  --limit-mm-per-prompt '{"image":0,"video":0}'
)
[[ -n "$QUANT" ]] && ARGS+=(--quantization "$QUANT")

VOLUMEN=()
[[ -d "$MODEL" ]] && VOLUMEN=(-v "$MODEL:$MODEL:ro")

exec docker run --rm --gpus all --ipc=host -p 8000:8000 \
  -v "$HOME/.cache/huggingface:/root/.cache/huggingface" \
  "${VOLUMEN[@]}" \
  "$IMAGE" "${ARGS[@]}"
