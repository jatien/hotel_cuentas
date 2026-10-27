# Imágenes para afinar Qwen2.5-VL-7B en el PC local (RTX 5080, Blackwell, 16 GB).
#
#   train → Unsloth + QLoRA (PyTorch con CUDA 12.8, necesario para Blackwell / sm_120)
#   fp8   → llm-compressor, para cuantizar el modelo fusionado a FP8 antes de servirlo
#
# Python 3.12 dentro del contenedor: Unsloth, bitsandbytes y vLLM aún no publican ruedas para
# el Python 3.14 del venv de Django. El código de training/ NO se copia: se monta en solo lectura
# desde el repositorio (compose.yaml), así un cambio en los scripts no obliga a reconstruir.
ARG CUDA_IMAGE=nvidia/cuda:12.8.1-cudnn-devel-ubuntu24.04

FROM ${CUDA_IMAGE} AS base
ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_NO_CACHE=1 \
    VIRTUAL_ENV=/opt/venv \
    PATH=/opt/venv/bin:$PATH
# build-essential: Triton compila kernels en tiempo de ejecución y necesita un compilador de C.
RUN apt-get update \
 && apt-get install -y --no-install-recommends python3 python3-dev python3-venv build-essential git ca-certificates \
 && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.9 /uv /usr/local/bin/uv
RUN uv venv /opt/venv --python /usr/bin/python3

FROM base AS train
COPY training/requirements-train.txt /tmp/requirements-train.txt
# --torch-backend=cu128: ruedas de PyTorch compiladas para CUDA 12.8 (las de cu126 no soportan la RTX 50xx).
RUN uv pip install --torch-backend=cu128 torch torchvision \
 && uv pip install --torch-backend=cu128 -r /tmp/requirements-train.txt bitsandbytes huggingface_hub

FROM base AS fp8
RUN uv pip install --torch-backend=cu128 torch torchvision \
 && uv pip install --torch-backend=cu128 llmcompressor
