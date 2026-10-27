# Afinado de Qwen2.5-VL-7B-Instruct para Facturas con IA

Esta carpeta **no forma parte de Django**: son los scripts que se ejecutan en un pod de RunPod (GPU
alquilada) para afinar el modelo. Django solo hace tres cosas: **producir** los ejemplos (facturas
revisadas y aprobadas), **exportarlos** y **medir** el modelo resultante. El modelo afinado se usa
después como cualquier otro, cambiando dos líneas del `.env`.

```
 PC del hotel (Django)                         Pod de RunPod (GPU)                     Servidor de casa (16 GB)
 ─────────────────────                         ───────────────────                     ────────────────────────
 1 invoice_agent_import (200 PDF)
 2 worker + revisión + aprobar
 3 invoice_agent_export_dataset ── dataset ──► 4 check_dataset.py
                                              5 serve_vllm.sh base ◄── 6 evaluate (línea base)
                                              7 train_qwen25vl.py (LoRA)
                                              8 serve_vllm.sh merged ◄─ 9 evaluate (afinado)
                                              10 descargar merged/ ─────────────────────► 11 vLLM fp8 (u Ollama)
                                              12 borrar pod y volumen                     12 .env de Django → servidor
```

## Ejecutar el modelo frente a afinarlo

- **Ejecutar (inferencia)**: el modelo lee una factura y devuelve el JSON. Necesita la memoria justa
  para cargarlo. Es lo que hace Django en el día a día.
- **Afinar (entrenamiento)**: se ajusta el modelo con ejemplos correctos para que aprenda tu formato y
  tus proveedores. Necesita bastante más memoria durante un rato, por eso se alquila una GPU.
- **LoRA**: en vez de cambiar los 7.000 millones de parámetros, se entrena un «añadido» pequeño (unas
  decenas de MB) que luego se fusiona con el modelo. Es barato, rápido y difícil de estropear.
- **Afinar no reduce el tamaño.** Qwen2.5-VL-7B ocupa ~16 GB en 16 bits; en la GPU de 16 GB se sirve
  cuantizado en **FP8** (~8 GB, casi sin pérdida, admitido por las RTX 40xx/50xx) o en 4 bits con Ollama.

## Qué aprende el modelo

Cada ejemplo es **exactamente** la petición que Django envía al modelo en producción (mismo prompt de
sistema, mismo texto de las páginas, **una imagen por página**) y, como respuesta, el JSON con los
valores **aprobados por una persona** (no los que dijo el modelo). Por eso:

- El modelo afinado se usa con `INVOICE_AGENT_VISION_MODE=always`, igual que al entrenar.
- Si cambias el prompt (`invoice_agent/prompts.py`, `PROMPT_VERSION`) o el esquema, hay que volver a
  exportar y a entrenar. El `manifest.json` y `merged/factura_ia.json` guardan las versiones.

## Cuántas facturas hacen falta

- **200 facturas** dan para un primer afinado útil: ~150 de entrenamiento, ~20 de validación y ~30 de
  prueba (estas últimas **nunca** se entrenan; sirven para medir).
- Lo que más ayuda no es el número sino la **variedad**: muchos proveedores distintos, escaneos y
  fotos además de PDF digitales, varios tipos de IVA, retenciones, abonos.
- Con más facturas se reexporta y se reentrena desde el modelo base; el reparto train/val/test es
  determinista, así que las facturas de prueba siguen siendo las mismas y los resultados se pueden comparar.
- `--split-by supplier` en la exportación mide algo más exigente: proveedores que el modelo no ha visto.

## Paso a paso

### En el PC del hotel (Django)

1. Importar las facturas (quedan en cola):
   ```powershell
   python manage.py invoice_agent_import "C:\ruta\facturas" --empresa B07794803 --recursive
   ```
2. Extraerlas con el proveedor actual (OpenRouter u OpenAI) arrancando el worker, y **revisarlas y
   aprobarlas** en `/facturas-ia/`. Corregir bien es lo más importante: el modelo aprenderá
   exactamente lo que apruebes, errores incluidos.
3. Exportar el dataset (fuera del repositorio; contiene datos reales):
   ```powershell
   python manage.py invoice_agent_export_dataset C:\datasets\dataset_facturas
   python training\check_dataset.py C:\datasets\dataset_facturas
   ```

### En RunPod

4. **Crear el pod** (lo hago yo si conectas RunPod, ver más abajo, o desde la web):
   - Plantilla oficial **RunPod PyTorch** (CUDA 12.x), **Secure Cloud**.
   - GPU de **48 GB** (L40S, RTX A6000, A40) para LoRA en 16 bits. Con 24 GB (RTX 4090, L4, A5000)
     usa `--load-in-4bit`.
   - **Volumen de red de ~100 GB** montado en `/workspace` (modelo base, modelo fusionado, entornos).
   - Exponer el **puerto HTTP 8000** (para vLLM).
5. Subir el repositorio (o solo `training/`) y el dataset a `/workspace`: `runpodctl send/receive`,
   `scp` por el puerto SSH del pod, o el gestor de ficheros de Jupyter.
6. Preparar el entorno y descargar el modelo base:
   ```bash
   cd /workspace/hotel_cuentas/training && bash setup_pod.sh
   export HF_HOME=/workspace/hf-cache
   /workspace/venv-train/bin/python check_dataset.py /workspace/dataset_facturas
   ```
7. **Línea base** (cuánto acierta el modelo sin afinar):
   ```bash
   bash serve_vllm.sh base         # apunta la clave que imprime
   ```
   En el PC del hotel, con el `.env` apuntando al pod:
   ```
   INVOICE_AGENT_BASE_URL=https://<POD_ID>-8000.proxy.runpod.net/v1
   INVOICE_AGENT_API_KEY=<la clave que imprimió serve_vllm.sh>
   INVOICE_AGENT_MODEL=factura-qwen
   ```
   ```powershell
   python manage.py invoice_agent_evaluate C:\datasets\dataset_facturas\test --vision-mode always --output base.json
   ```
   Para vLLM con `Ctrl+C` antes de entrenar (comparten GPU).
8. **Entrenar**:
   ```bash
   source /workspace/venv-train/bin/activate
   python train_qwen25vl.py --data /workspace/dataset_facturas --output /workspace/outputs/run1
   ```
   Al terminar imprime la pérdida, y en `val_report.json` el porcentaje de respuestas con JSON válido,
   que cumplen el esquema y los aciertos en NIF, número, fecha y totales sobre validación.
9. **Medir el afinado** con el mismo conjunto de prueba:
   ```bash
   QUANT=fp8 bash serve_vllm.sh /workspace/outputs/run1/merged   # como irá en la GPU de 16 GB
   ```
   ```powershell
   python manage.py invoice_agent_evaluate C:\datasets\dataset_facturas\test --vision-mode always --output afinado.json
   ```
   Compara `base.json` y `afinado.json`: aciertos por campo, conciliación, tasa de revisión y latencia.
10. Si mejora, **descargar** `/workspace/outputs/run1/merged` (~16 GB) al servidor de casa
    (`runpodctl send`, o subirlo a un repositorio **privado** de Hugging Face y bajarlo desde allí).
11. **Borrar el pod y el volumen** (se paga por hora mientras existan y contienen facturas reales).

### En el servidor de casa (GPU de 16 GB)

Con 16 GB, sirve `merged-fp8/` (ver «Opción local» más abajo: `cuantizar_fp8.py` y el servicio `serve`).
La cuantización `--quantization fp8` al cargar necesita memoria para los pesos en 16 bits.

```bash
vllm serve /ruta/merged-fp8 --served-model-name factura-qwen \
  --max-model-len 16384 --limit-mm-per-prompt '{"image":6,"video":0}' --api-key <clave>
```
Y en el `.env` de Django: `INVOICE_AGENT_BASE_URL=http://<ip-servidor>:8000/v1`,
`INVOICE_AGENT_MODEL=factura-qwen`, `INVOICE_AGENT_VISION_MODE=always`, `INVOICE_AGENT_PRICE_*=0`.

Alternativa con Ollama: convertir `merged/` a GGUF con `llama.cpp` (`convert_hf_to_gguf.py`, que
genera también el fichero `mmproj` de visión) y cuantizar a `Q4_K_M`. Es más sencillo de mantener,
pero conviene volver a medir con `invoice_agent_evaluate`, porque 4 bits pierden algo más que FP8.

## Opción local: el PC con la RTX 5080 (16 GB, Docker)

El PC nuevo (Ubuntu, RTX 5080 de 16 GB, 32 GB de RAM) puede hacer los pasos 4-11 sin alquilar GPU.
Todo va en contenedores (`training/local/`): el venv de Django usa Python 3.14 y las librerías de
entrenamiento aún no lo soportan, y la RTX 5080 (Blackwell, `sm_120`) necesita PyTorch con CUDA 12.8.

Qué cambia frente a RunPod para que quepa en 16 GB:

| | RunPod (48 GB) | RTX 5080 (16 GB) |
|---|---|---|
| Entrenamiento | LoRA en 16 bits | **QLoRA en 4 bits** (`--load-in-4bit`) |
| Longitud máxima | 12288 tokens | **8192** (≈ facturas de hasta 2-3 páginas; las más largas se descartan) |
| Servir el modelo | vLLM en 16 bits | vLLM con **pesos FP8 cuantizados antes** (`cuantizar_fp8.py`) |
| Línea base | `Qwen/Qwen2.5-VL-7B-Instruct` | `RedHatAI/Qwen2.5-VL-7B-Instruct-FP8-Dynamic` |

La cuantización FP8 «al vuelo» de vLLM (`--quantization fp8`) carga primero los ~16,6 GB en 16 bits
y no cabe; por eso el modelo fusionado se convierte a FP8 en la CPU con `cuantizar_fp8.py`.

```
 PC del hotel (Django)                          PC con RTX 5080 (Docker)
 ─────────────────────                          ────────────────────────
 1-3 importar, revisar, aprobar, exportar ──►  check → serve (base) ◄── evaluate (línea base)
                                                train (QLoRA) → fp8 → serve (afinado) ◄── evaluate
                                                si mejora: serve queda como modelo de producción
```

### Preparar (una vez)

```bash
cd ~/Projects/hotel_cuentas
mkdir -p ~/factura-ia/outputs ~/factura-ia/cache        # con tu usuario, no como root
cp training/local/.env.example training/local/.env      # rellena rutas, HOST_UID/GID y VLLM_API_KEY
# copia aquí el dataset exportado (DATASET_DIR), p. ej. con scp desde el PC del hotel
docker compose -f training/local/compose.yaml build
docker compose -f training/local/compose.yaml run --rm gpu-check
```

`gpu-check` debe mostrar `sm_120`, bf16 y al menos ~14 GiB libres. Si no, cierra lo que use la GPU:
`sudo systemctl stop ollama` (vuelve a arrancarlo al terminar) y el navegador con aceleración.

### Ejecutar

```bash
C="docker compose -f training/local/compose.yaml"
$C run --rm check                 # dataset correcto y cuántos ejemplos superan MAX_SEQ_LEN
$C up serve                       # línea base: Ctrl+C al terminar la evaluación
$C run --rm train                 # QLoRA → OUTPUTS_DIR/run1/{lora,merged,val_report.json}
$C run --rm fp8                   # merged/ → merged-fp8/
SERVE_MODEL=/outputs/run1/merged-fp8 $C up serve
```

En el `.env` de Django (este PC o el del hotel, con `SERVE_BIND` en la IP de la LAN):

```
INVOICE_AGENT_BASE_URL=http://<ip-o-localhost>:8000/v1
INVOICE_AGENT_API_KEY=<VLLM_API_KEY>
INVOICE_AGENT_MODEL=factura-qwen
INVOICE_AGENT_VISION_MODE=always
```

y se mide igual que en RunPod: `invoice_agent_evaluate <dataset>/test --vision-mode always --output base.json`
(y `afinado.json` con el modelo nuevo).

- **Si se corta** (apagón, error de memoria a mitad): `TRAIN_ARGS=--resume` continúa desde el último checkpoint.
- **Si falta memoria (CUDA out of memory)**: baja `MAX_SEQ_LEN` a 6144 (se descartan más facturas largas;
  `check` dice cuántas) o entrena esa ejecución en RunPod con los mismos scripts.
- `serve` usa la imagen `vllm/vllm-openai:latest`; cuando una versión funcione, fíjala en `VLLM_IMAGE`.
- Datos: el dataset, los checkpoints y el modelo contienen facturas reales. Quedan fuera del repositorio
  (`OUTPUTS_DIR`, `CACHE_DIR`) y `serve` solo escucha en `127.0.0.1` salvo que cambies `SERVE_BIND`.

## Parámetros de entrenamiento

| Opción | Por defecto | Cuándo cambiarla |
|---|---|---|
| `--epochs` | 3 | Si `val` empeora en la última época, baja a 2; con muy pocos datos, no subas de 3 |
| `--lr` | 1e-4 | Baja a 5e-5 si la pérdida oscila |
| `--lora-r` / `--lora-alpha` | 16 / 16 | 32/32 con más de ~1.000 facturas |
| `--grad-accum` | 8 | Lote efectivo; con pocos datos, 4 da más pasos |
| `--vision-layers` | no | Probar si falla mucho en escaneos o fotos (más riesgo de sobreajuste) |
| `--resume` | no | Continuar una ejecución cortada desde su último checkpoint |
| `--load-in-4bit` | no (sí en `training/local`) | GPUs de 16-24 GB |
| `--max-seq-len` | 12288 (8192 en `training/local`) | Una página ≈ 4.000 tokens; facturas más largas se descartan |

## Notas

- Los scripts siguen la API documentada de Unsloth para modelos de visión, pero **no se han ejecutado
  en una GPU** al prepararlos (no había GPU disponible). La primera ejecución puede requerir ajustar
  alguna versión de librería; los mensajes de error de Unsloth/TRL suelen indicar el cambio.
- La pérdida baja no garantiza buenas facturas: decide siempre con `invoice_agent_evaluate` sobre test.
- El texto que va al modelo tiene IBAN, tarjetas y correos ocultados; **las imágenes no**. El dataset,
  el pod y el modelo afinado contienen información de proveedores: Secure Cloud, volumen propio y
  borrado al terminar; si subes el modelo a Hugging Face, que sea a un repositorio privado.
