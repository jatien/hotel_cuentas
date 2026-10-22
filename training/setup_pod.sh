#!/usr/bin/env bash
# Prepara un pod de RunPod para afinar y servir Qwen2.5-VL-7B-Instruct.
# Todo se guarda en /workspace (el volumen persistente): entornos, caché de modelos y resultados.
#
#   bash setup_pod.sh          # entrenamiento + vLLM
#   bash setup_pod.sh train    # solo entrenamiento
#   bash setup_pod.sh serve    # solo vLLM
set -euo pipefail
cd "$(dirname "$0")"
WHAT="${1:-all}"
export HF_HOME=/workspace/hf-cache
mkdir -p "$HF_HOME" /workspace/outputs

if [[ "$WHAT" == "all" || "$WHAT" == "train" ]]; then
  python3 -m venv /workspace/venv-train
  /workspace/venv-train/bin/pip install --upgrade pip
  /workspace/venv-train/bin/pip install -r requirements-train.txt
fi
if [[ "$WHAT" == "all" || "$WHAT" == "serve" ]]; then
  python3 -m venv /workspace/venv-serve
  /workspace/venv-serve/bin/pip install --upgrade pip
  /workspace/venv-serve/bin/pip install -r requirements-serve.txt
fi

# Descarga del modelo base (una vez; queda en /workspace/hf-cache).
PY=/workspace/venv-train/bin/python; [[ -x $PY ]] || PY=/workspace/venv-serve/bin/python
$PY - <<'PY'
from huggingface_hub import snapshot_download
snapshot_download("Qwen/Qwen2.5-VL-7B-Instruct")
print("Modelo base descargado.")
PY
nvidia-smi --query-gpu=name,memory.total --format=csv
echo "Listo. Añade 'export HF_HOME=/workspace/hf-cache' a cada terminal nueva."
