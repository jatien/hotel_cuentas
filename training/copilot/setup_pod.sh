#!/usr/bin/env bash
# Prepara un pod de RunPod para afinar y servir Qwen3.5-9B para el PMS Copilot.
# Todo queda en /workspace (volumen persistente).
#
#   bash setup_pod.sh          # entrenamiento + vLLM
#   bash setup_pod.sh train    # solo entrenamiento
#   bash setup_pod.sh serve    # solo vLLM
set -euo pipefail
cd "$(dirname "$0")"
QUE="${1:-all}"
export HF_HOME=/workspace/hf-cache
mkdir -p "$HF_HOME" /workspace/outputs

if [[ "$QUE" == "all" || "$QUE" == "train" ]]; then
  python3 -m venv /workspace/venv-copilot
  /workspace/venv-copilot/bin/pip install --upgrade pip
  /workspace/venv-copilot/bin/pip install -r requirements.txt
  /workspace/venv-copilot/bin/pip install flash-linear-attention causal-conv1d || \
    echo "Sin kernels acelerados de Gated DeltaNet: funciona igual, más despacio."
fi
if [[ "$QUE" == "all" || "$QUE" == "serve" ]]; then
  python3 -m venv /workspace/venv-serve
  /workspace/venv-serve/bin/pip install --upgrade pip
  /workspace/venv-serve/bin/pip install "vllm>=0.17"
fi

PY=/workspace/venv-copilot/bin/python; [[ -x $PY ]] || PY=/workspace/venv-serve/bin/python
$PY - <<'PY'
from huggingface_hub import snapshot_download
snapshot_download("Qwen/Qwen3.5-9B")
print("Modelo base descargado.")
PY
nvidia-smi --query-gpu=name,memory.total --format=csv
echo "Listo. Añade 'export HF_HOME=/workspace/hf-cache' a cada terminal nueva."
