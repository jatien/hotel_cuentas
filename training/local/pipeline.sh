#!/usr/bin/env bash
# Ciclo completo profesor → alumno en el PC de la GPU. Ver training/README.md («Ciclo con Google Drive»).
#
#   training/local/pipeline.sh           # todo: bajar de Drive, etiquetar, subir JSON, entrenar
#   training/local/pipeline.sh datos     # solo bajar, etiquetar, subir JSON y exportar el dataset
#   training/local/pipeline.sh entrenar  # entrenar con el dataset ya exportado (check, train, fp8) y evaluar
#   training/local/pipeline.sh evaluar   # solo evaluar: base y último afinado frente a los JSON de referencia
#   SKIP_EVAL=1 training/local/pipeline.sh entrenar   # entrenar sin evaluar al final
#   RETRY_FAILED=1 training/local/pipeline.sh datos   # reintenta también las que quedaron en «Error»
#
# Lee training/local/.env (rutas, RCLONE_REMOTE, EMPRESA_NIF…). Repetirlo es seguro: las facturas ya
# importadas o etiquetadas no se vuelven a procesar ni a pagar.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
STAGE="${1:-todo}"

[[ -f "$HERE/.env" ]] || { echo "Falta $HERE/.env (cópialo de .env.example)"; exit 1; }
set -a; source "$HERE/.env"; set +a
if grep -q "TU_USUARIO" "$HERE/.env"; then
  echo "En $HERE/.env quedan rutas con TU_USUARIO. Cámbialas por tu usuario, p. ej.:"
  echo "  sed -i 's#/home/TU_USUARIO/#/home/$(whoami)/#g' $HERE/.env"
  exit 1
fi

: "${DATASET_DIR:?Define DATASET_DIR en .env}"
: "${DRIVE_DIR:?Define DRIVE_DIR en .env}"
LABELS_DIR="$DRIVE_DIR/${LABELS_SUBDIR:-Invoices_json}"
if [[ -z "${DJANGO_PY:-}" ]]; then
  for candidate in "$REPO/.venv/bin/python" "$REPO/venv/bin/python"; do
    [[ -x "$candidate" ]] && DJANGO_PY="$candidate" && break
  done
fi
COMPOSE=(docker compose -f "$HERE/compose.yaml")

datos() {
  : "${EMPRESA_NIF:?Define EMPRESA_NIF en .env}"
  [[ -n "${DJANGO_PY:-}" ]] || { echo "No encuentro el Python de Django: define DJANGO_PY en .env"; exit 1; }
  mkdir -p "$DRIVE_DIR" "$LABELS_DIR"
  if [[ -n "${RCLONE_REMOTE:-}" ]]; then
    echo "== Bajando facturas y JSON de $RCLONE_REMOTE"
    rclone copy "$RCLONE_REMOTE" "$DRIVE_DIR" --progress \
      --include "*.{pdf,PDF,png,PNG,jpg,JPG,jpeg,JPEG,tif,tiff,webp,json}"
  else
    echo "== RCLONE_REMOTE vacío: uso lo que ya hay en $DRIVE_DIR"
  fi

  echo "== Importar, etiquetar con el profesor y exportar el dataset"
  EXTRA=()
  [[ -n "${MAX_EXTRACT:-}" ]] && EXTRA+=(--max-extract "$MAX_EXTRACT")
  [[ "${RETRY_FAILED:-}" == "1" ]] && EXTRA+=(--retry-failed)
  (cd "$REPO" && "$DJANGO_PY" manage.py invoice_agent_pipeline "$DRIVE_DIR" --empresa "$EMPRESA_NIF" \
      --labels "$LABELS_DIR" --dataset "$DATASET_DIR" "${EXTRA[@]}")

  if [[ -n "${RCLONE_REMOTE:-}" ]]; then
    echo "== Subiendo los JSON nuevos o corregidos a Drive"
    rclone copy "$LABELS_DIR" "$RCLONE_REMOTE/${LABELS_SUBDIR:-Invoices_json}" --include "*.json"
  fi
}

gpu_libre() {
  if command -v systemctl >/dev/null && systemctl is-active --quiet ollama 2>/dev/null; then
    echo "Ollama está en marcha y ocupa la GPU. Páralo antes: sudo systemctl stop ollama"
    exit 1
  fi
}

entrenar() {
  gpu_libre
  export RUN="${RUN:-run-$(date +%Y%m%d-%H%M)}"
  echo "== Entrenando ($RUN)"
  "${COMPOSE[@]}" run --rm check
  "${COMPOSE[@]}" run --rm train
  "${COMPOSE[@]}" run --rm fp8
  echo "Modelo afinado: $OUTPUTS_DIR/$RUN/merged-fp8"
  if [[ "${SKIP_EVAL:-}" == "1" ]]; then
    echo "Evaluación omitida (SKIP_EVAL=1). Cuando quieras: RUN=$RUN $0 evaluar"
  else
    evaluar
  fi
}

# --- Evaluación: base y afinado frente a los JSON de referencia del conjunto de prueba -------------
# Solo mide y escribe informes en OUTPUTS_DIR/<RUN>/. No cambia el .env de Django, ni Drive, ni la BD:
# el modelo afinado no pasa a producción hasta que tú cambies INVOICE_AGENT_MODEL/BASE_URL.
SERVE_HOST="${SERVE_BIND:-127.0.0.1}"
[[ "$SERVE_HOST" == "0.0.0.0" ]] && SERVE_HOST=127.0.0.1

parar_serve() { "${COMPOSE[@]}" rm -sf serve >/dev/null 2>&1 || true; }

arrancar_serve() {  # $1 = modelo (repo de Hugging Face o ruta dentro del contenedor)
  parar_serve
  echo "   Arrancando vLLM con $1…"
  [[ "$1" == /* ]] || echo "   (la primera vez descarga el modelo base, unos 9 GB)"
  SERVE_MODEL="$1" "${COMPOSE[@]}" up -d serve >/dev/null
  local espera=0
  until python3 - "$SERVE_HOST" "$VLLM_API_KEY" <<'PY' 2>/dev/null
import sys, urllib.request
req = urllib.request.Request(f"http://{sys.argv[1]}:8000/v1/models", headers={"Authorization": f"Bearer {sys.argv[2]}"})
urllib.request.urlopen(req, timeout=5)
PY
  do
    if [[ -z "$("${COMPOSE[@]}" ps --status running -q serve)" ]]; then
      echo "vLLM se ha parado. Últimas líneas:"; "${COMPOSE[@]}" logs --tail 40 serve; exit 1
    fi
    (( espera += 10 ))
    if (( espera > ${SERVE_TIMEOUT:-2400} )); then
      echo "vLLM no responde tras ${espera}s."; "${COMPOSE[@]}" logs --tail 40 serve; exit 1
    fi
    sleep 10
  done
}

medir() {  # $1 = informe de salida · usa el modelo que esté sirviendo vLLM
  (cd "$REPO" && INVOICE_AGENT_PROVIDER=openai INVOICE_AGENT_BASE_URL="http://$SERVE_HOST:8000/v1" \
    INVOICE_AGENT_API_KEY="$VLLM_API_KEY" INVOICE_AGENT_MODEL=factura-qwen INVOICE_AGENT_SUPPORTS_VISION=True \
    INVOICE_AGENT_PRICE_INPUT_PER_MTOK=0 INVOICE_AGENT_PRICE_OUTPUT_PER_MTOK=0 \
    "$DJANGO_PY" manage.py invoice_agent_evaluate "$DATASET_DIR/test" --vision-mode "$MODO" --output "$1" > "$1.log")
}

evaluar() {
  gpu_libre
  : "${OUTPUTS_DIR:?Define OUTPUTS_DIR en .env}"
  : "${VLLM_API_KEY:?Define VLLM_API_KEY en .env (python3 -c \"import secrets; print(secrets.token_urlsafe(24))\")}"
  [[ -n "${DJANGO_PY:-}" ]] || { echo "No encuentro el Python de Django: define DJANGO_PY en .env"; exit 1; }
  if [[ -z "${RUN:-}" ]]; then
    local ultimo; ultimo="$(ls -dt "$OUTPUTS_DIR"/run-*/merged-fp8 2>/dev/null | head -1)"
    [[ -n "$ultimo" ]] || { echo "No hay ningún modelo afinado en $OUTPUTS_DIR (ejecuta antes: $0 entrenar)"; exit 1; }
    RUN="$(basename "$(dirname "$ultimo")")"
  fi
  local dir="$OUTPUTS_DIR/$RUN"
  [[ -d "$dir/merged-fp8" ]] || { echo "Falta $dir/merged-fp8"; exit 1; }
  local casos; casos="$(ls "$DATASET_DIR"/test/*.json 2>/dev/null | wc -l)"
  (( casos > 0 )) || { echo "No hay facturas de prueba en $DATASET_DIR/test (ejecuta antes: $0 datos)"; exit 1; }
  MODO="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("vision_mode","image_only"))' "$DATASET_DIR/manifest.json")"
  trap parar_serve EXIT

  # La medida del modelo base se reutiliza mientras el conjunto de prueba sea el mismo.
  local base_modelo="RedHatAI/Qwen2.5-VL-7B-Instruct-FP8-Dynamic"
  local huella; huella="$( (echo "$base_modelo $MODO"; cat "$DATASET_DIR"/test/*.json) | sha256sum | cut -c1-12)"
  local base="$OUTPUTS_DIR/linea-base/base-$huella.json"
  mkdir -p "$OUTPUTS_DIR/linea-base"
  echo "== Evaluando $RUN sobre $casos facturas de prueba (modo $MODO)"
  if [[ -f "$base" ]]; then
    echo "1/2 Modelo base: ya medido con este conjunto de prueba ($base)"
  else
    echo "1/2 Modelo base"
    arrancar_serve "$base_modelo"
    medir "$base"
  fi
  echo "2/2 Modelo afinado"
  arrancar_serve "/outputs/$RUN/merged-fp8"
  medir "$dir/afinado.json"
  parar_serve
  cp "$base" "$dir/base.json"

  echo "== Resultados (referencia: JSON del modelo grande o aprobados)"
  (cd "$REPO" && "$DJANGO_PY" manage.py invoice_agent_compare "$dir/base.json" "$dir/afinado.json" \
     --html "$dir/informe.html" --json "$dir/comparacion.json" --base-label "$base_modelo" --trained-label "$RUN")
  echo
  echo "Ábrelo en el navegador: xdg-open $dir/informe.html"
  echo "Nada ha cambiado en producción, Drive ni la base de datos."
}

case "$STAGE" in
  datos) datos ;;
  entrenar) entrenar ;;
  evaluar) evaluar ;;
  todo) datos; entrenar ;;
  *) echo "Uso: $0 [todo|datos|entrenar|evaluar]"; exit 2 ;;
esac
