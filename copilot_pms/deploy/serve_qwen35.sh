#!/usr/bin/env bash
# Sirve Qwen3.5-9B para el PMS Copilot con vLLM (API compatible con OpenAI) en Docker.
# Pensado para el servidor Debian con la GPU NVIDIA de 16 GB.
#
#   bash serve_qwen35.sh                     # FP8, como corre en 16 GB
#   QUANT= bash serve_qwen35.sh              # BF16 (necesita ~24 GB de VRAM)
#   MODEL=/modelos/copilot-merged bash serve_qwen35.sh   # un modelo afinado y fusionado
#   BIND_IP=100.101.5.7 bash serve_qwen35.sh             # publicar en la IP de la VPN (Tailscale/WireGuard)
#
# Por defecto el puerto solo se publica en 127.0.0.1: hay que elegir a propósito en qué interfaz
# escucha (la de la VPN, mejor que la de la LAN). Ver docs/security.md §6.
#
# Requisitos: Docker + NVIDIA Container Toolkit. La primera vez descarga el modelo (~20 GB) a
# ~/.cache/huggingface. Django apunta a http://IP-DEL-SERVIDOR:8000/v1 con la clave que se imprime.
set -euo pipefail

MODEL="${MODEL:-Qwen/Qwen3.5-9B}"
NAME="${SERVED_NAME:-Qwen/Qwen3.5-9B}"     # lo que va en COPILOT_MODEL
QUANT="${QUANT-fp8}"                       # FP8: ~10 GB de pesos, casi sin pérdida en RTX 40xx/50xx
MAX_LEN="${MAX_LEN:-32768}"                # sobra para 9 herramientas + resultados + historial
IMAGE="${IMAGE:-vllm/vllm-openai:latest}"  # Qwen3.5 necesita una versión reciente de vLLM
BIND_IP="${BIND_IP:-127.0.0.1}"            # interfaz donde se publica el puerto 8000

if [[ -z "${VLLM_API_KEY:-}" ]]; then
  VLLM_API_KEY="$(python3 -c 'import secrets; print(secrets.token_urlsafe(24))')"
fi
echo "Modelo: $MODEL · nombre servido: $NAME · cuantización: ${QUANT:-ninguna}"
echo "Clave de la API (ponla en COPILOT_API_KEY): $VLLM_API_KEY"
echo "Escuchando en http://$BIND_IP:8000/v1"
if [[ "$BIND_IP" == "0.0.0.0" ]]; then
  echo "AVISO: 0.0.0.0 publica el modelo en todas las interfaces. Limítalo con el cortafuegos (docs/security.md §6)." >&2
fi

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

exec docker run --rm --gpus all --ipc=host -p "$BIND_IP:8000:8000" \
  -v "$HOME/.cache/huggingface:/root/.cache/huggingface" \
  "${VOLUMEN[@]}" \
  "$IMAGE" "${ARGS[@]}"
