# Afinado de Qwen3.5-9B para el PMS Copilot

Esta carpeta **no forma parte de Django**: son los scripts que se ejecutan en un pod de RunPod (GPU
alquilada) para afinar el modelo del copilot. Django **genera** las conversaciones (sobre un hotel
ficticio) y **mide** el modelo; el modelo afinado se sirve después igual que el base
(`copilot_pms/deploy/`).

```
 PC (Django, base ficticia)                    Pod de RunPod (GPU)                      Servidor de casa (16 GB)
 ──────────────────────────                    ───────────────────                      ────────────────────────
 1 copilot_hotel_ficticio
 2 copilot_dataset ─── dataset_copilot/ ─────► 3 preparar_publicos.py (When2Call,
                                                  ToolACE, Toucan) + mezclar.py
 5 copilot_evaluar (base) ◄────────────────── 4 servir_pod.sh base
                                                6 entrenar_qwen35.py (LoRA bf16)
 8 copilot_evaluar (afinado) ◄──────────────── 7 servir_pod.sh merged
                                                9 descargar merged/ ─────────────────────► 10 serve_qwen35.sh
                                                                                              MODEL=…/merged
```

## Qué aprende el modelo y de dónde salen los datos

| Origen | Licencia | Qué aporta |
|---|---|---|
| **Hotel ficticio** (`copilot_dataset`) | propia, datos inventados | Nuestras 11 herramientas, en español, con el prompt real: saldos, extractos, facturas y PDF, cajas, nóminas, beneficio, gráficos y su edición, y los casos en los que **no** hay que dar la respuesta directamente (aclarar, no conectado, solo lectura, ejercicio inexistente, texto con instrucciones dentro de los datos) |
| [nvidia/When2Call](https://huggingface.co/datasets/nvidia/When2Call) | CC BY 4.0 (uso comercial permitido) | Cuándo no llamar: pedir el dato que falta o decir que no se puede |
| [Team-ACE/ToolACE](https://huggingface.co/datasets/Team-ACE/ToolACE) | Apache 2.0 | Llamadas simples, paralelas y encadenadas sobre miles de APIs |
| [Agent-Ark/Toucan-1.5M](https://huggingface.co/datasets/Agent-Ark/Toucan-1.5M) (config SFT) | Apache 2.0 | Trayectorias reales de varios pasos con herramientas MCP |

- El hotel ficticio es la clave: como las herramientas se ejecutan **de verdad** sobre una base de datos
  inventada, cada conversación tiene argumentos correctos conocidos y respuestas cuyas cifras salen
  todas del resultado (lo comprueba un test). Se pueden generar tantas como haga falta y **no contienen
  ningún dato real**: se pueden subir a git o a un pod sin riesgo.
- Las conversaciones se construyen exactamente como las ve el modelo en producción: mismo prompt de
  sistema, mismos esquemas de herramientas, mismo JSON en los resultados y el mismo historial en las
  preguntas de seguimiento (texto + nota del gráfico mostrado).
- Los datasets públicos no hablan del hotel: enseñan la mecánica. `mezclar.py` los limita a un
  porcentaje (40 % por defecto) y escribe `ATRIBUCIONES.md` (CC BY 4.0 exige citar la fuente).
- APIGen-MT-5k **no** se usa: su licencia (CC BY-NC 4.0, solo investigación) no permite el uso comercial.

### Reparto train / val / test

- **test**: una redacción completa de cada escenario que nunca se ve al entrenar (mide si entiende
  preguntas formuladas de otra forma) + un 5 % aleatorio. Se evalúa desde Django.
- **val**: 6 % aleatorio del dataset propio (pérdida por época y elección del mejor punto).
- **train**: el resto + los públicos.

## Cómo se entrena

- **Formato** (`formato.py`): cada conversación se renderiza con la plantilla de chat de Qwen3.5 (la
  misma que usa vLLM) con `enable_thinking=False`, y la pérdida **solo cuenta en las respuestas del
  asistente** (llamadas y respuestas finales). Sistema, preguntas, esquemas y resultados de herramientas
  quedan fuera; el historial de turnos anteriores también (es contexto).
- **LoRA en bf16** en las capas lineales del modelo de lenguaje (atención, Gated DeltaNet y MLP); el
  codificador de imágenes no se toca. Qwen desaconseja QLoRA de 4 bits en Qwen3.5.
- **GPU**: 48 GB o más (A6000, L40S) o 80 GB (A100/H100). Cada ejemplo propio ocupa ~5–7 mil tokens
  (los esquemas de las 11 herramientas van en todos) y el vocabulario de Qwen3.5 es grande.
- Afinar **no** reduce el tamaño: el modelo fusionado se sirve en FP8 en la GPU de 16 GB, como el base.

## Paso a paso

### 1. En el PC: base de datos ficticia y dataset

```powershell
# .env:  DB_NAME=cuentas_ficticio      (base APARTE; los comandos se niegan a usar otra)
createdb -U postgres cuentas_ficticio
python manage.py migrate
python manage.py copilot_hotel_ficticio
python manage.py copilot_dataset --n 3000 --salida dataset_copilot
# Volver a DB_NAME=cuentas (o quitar la línea) para usar la aplicación normal.
```

`dataset_copilot/manifest.json` guarda la versión del prompt y la huella de los esquemas de las
herramientas: si se cambian (nueva herramienta, nuevo parámetro), se regenera el dataset y se reentrena.

### 2. En el pod

```bash
bash setup_pod.sh                       # entornos + descarga de Qwen3.5-9B
source /workspace/venv-copilot/bin/activate && export HF_HOME=/workspace/hf-cache
# subir dataset_copilot/ (scp o runpodctl send)
python preparar_publicos.py --salida /workspace/publicos --when2call 600 --toolace 1200 --toucan 1200
python mezclar.py --propio /workspace/dataset_copilot --publicos /workspace/publicos --salida /workspace/mezcla
```

### 3. Línea base (antes de entrenar)

```bash
bash servir_pod.sh base                 # imprime la clave
```

En el PC, con `COPILOT_BASE_URL=https://<POD_ID>-8000.proxy.runpod.net/v1` y la clave en `.env`:

```powershell
python manage.py copilot_evaluar --dataset dataset_copilot/test.jsonl --salida eval_base.json
```

Si la línea base ya es buena en todo, no hace falta entrenar: se ajustan el prompt o las descripciones
de las herramientas y se vuelve a medir.

### 4. Entrenar y medir

```bash
python entrenar_qwen35.py --data /workspace/mezcla --output /workspace/outputs/copilot1
bash servir_pod.sh /workspace/outputs/copilot1/merged
```

```powershell
python manage.py copilot_evaluar --dataset dataset_copilot/test.jsonl --salida eval_copilot1.json
```

Métricas de `copilot_evaluar` (paso a paso, con el contexto correcto en cada paso):

| Métrica | Qué mide |
|---|---|
| `llama_cuando_toca` | Llama a alguna herramienta cuando la respuesta esperada es una llamada |
| `herramienta_correcta` | Las herramientas llamadas son las esperadas |
| `argumentos_correctos` | Además, con los argumentos correctos (normalizados con el esquema) |
| `no_llama_cuando_no_toca` | Responde sin herramientas cuando hay que aclarar, negarse o decir que no está conectado |
| `respuestas_con_cifras_inventadas` | Respuestas finales con importes en € que no salen de ningún resultado |

### 5. Llevarlo al servidor de casa

Descargar `/workspace/outputs/copilot1/merged/` (unos 20 GB) y servirlo con
`MODEL=/ruta/merged bash copilot_pms/deploy/serve_qwen35.sh`. `COPILOT_MODEL` no cambia
(el script lo sirve con el mismo nombre). Borrar después el pod y su volumen.

## Ficheros

| Fichero | Para qué |
|---|---|
| `formato.py` | Renderizado con la plantilla de Qwen3.5 y enmascarado de la pérdida |
| `preparar_publicos.py` | Descarga y conversión de When2Call, ToolACE y Toucan |
| `mezclar.py` | Mezcla con el dataset propio y atribuciones |
| `entrenar_qwen35.py` | LoRA bf16, mejor época por pérdida en val, informe y modelo fusionado |
| `setup_pod.sh`, `servir_pod.sh` | Preparar el pod y servir con vLLM |
| `copilot_pms/entrenamiento/` (Django) | Hotel ficticio, escenarios, exportación y evaluación |
