# Servir el modelo del copilot: Qwen3.5-9B

Modelo principal del PMS Copilot: **[Qwen/Qwen3.5-9B](https://huggingface.co/Qwen/Qwen3.5-9B)**
(Apache 2.0). Su ficha da 66,1 en BFCL-V4 y 79,1 en TAU2-Bench; admite llamadas a funciones y
razonamiento opcional.

```
 PC del hotel (Django)                        Servidor Debian (GPU 16 GB)
 ─────────────────────                        ───────────────────────────
 .env  COPILOT_BASE_URL ─── HTTP (LAN) ─────► vLLM :8000  (Docker)
       COPILOT_API_KEY                         └─ Qwen3.5-9B en FP8 (~10 GB)
```

## Arrancar el servidor (vLLM, recomendado)

```bash
bash copilot_pms/deploy/serve_qwen35.sh
```

- Usa los analizadores que indica la ficha: `--tool-call-parser qwen3_coder` y `--reasoning-parser qwen3`.
  Con el de razonamiento, lo que piensa el modelo no se mezcla con la respuesta.
- **FP8** para que quepa en 16 GB (en BF16 el modelo ocupa unos 20 GB). Las RTX 40xx/50xx lo ejecutan
  de forma nativa; la GTX 760 actual no sirve para esto.
- El script imprime una clave: va en `COPILOT_API_KEY`. vLLM rechaza peticiones sin ella.
- Hace falta una versión reciente de vLLM (las anteriores a 0.17 no cargan Qwen3.5); la imagen
  `vllm/vllm-openai:latest` la trae.

## Configurar Django

En el `.env` del PC del hotel (ver `.env.example`):

```
COPILOT_BASE_URL=http://IP-DEL-SERVIDOR:8000/v1
COPILOT_API_KEY=<la clave que imprimió el script>
COPILOT_MODEL=Qwen/Qwen3.5-9B
COPILOT_TEMPERATURE=0.7
COPILOT_TOP_P=0.8
COPILOT_TOP_K=20
COPILOT_PRESENCE_PENALTY=1.5
COPILOT_ENABLE_THINKING=False
```

Comprobar sin IA que las herramientas devuelven datos, y después con el modelo:

```powershell
python manage.py copilot ejecutar saldo_cuenta "{\"cuenta\": \"572\"}"
python manage.py copilot preguntar "¿Saldo del banco a 30 de septiembre?"
```

## Razonamiento (thinking)

Qwen3.5 razona por defecto. El copilot lo desactiva (`COPILOT_ENABLE_THINKING=False`) enviando
`chat_template_kwargs: {"enable_thinking": false}`: responde antes y gasta menos contexto. Si la
evaluación muestra que elige mejor las herramientas pensando, se cambia a `True` (y conviene usar el
muestreo de razonamiento de la ficha: `COPILOT_TEMPERATURE=1.0`, `COPILOT_TOP_P=0.95`).

## Alternativa: Ollama

Más sencillo de instalar, pero con menos control:

```bash
ollama pull qwen3.5:9b
```

```
COPILOT_BASE_URL=http://IP-DEL-SERVIDOR:11434/v1
COPILOT_MODEL=qwen3.5:9b
COPILOT_API_KEY=
```

Ollama no tiene clave de API: el puerto 11434 solo debe estar abierto a la red del hotel. Si el
razonamiento llega dentro de la respuesta (`<think>…</think>`), el copilot lo quita antes de mostrarla.

## Seguridad

- Exponer el puerto 8000 solo a la LAN (cortafuegos del servidor); nunca a Internet.
- El modelo no accede a la base de datos: solo recibe la pregunta y los resultados de las herramientas
  que Django ejecuta.
