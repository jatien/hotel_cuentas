#!/usr/bin/env bash
# Ciclo completo profesor → alumno en el PC de la GPU. Ver training/README.md («Ciclo con Google Drive»).
#
#   training/local/pipeline.sh           # todo: bajar de Drive, etiquetar, subir JSON, entrenar
#   training/local/pipeline.sh datos     # solo bajar, etiquetar, subir JSON y exportar el dataset
#   training/local/pipeline.sh entrenar  # solo entrenar con el dataset ya exportado (check, train, fp8)
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

entrenar() {
  export RUN="${RUN:-run-$(date +%Y%m%d-%H%M)}"
  echo "== Entrenando ($RUN). Para Ollama y cualquier cosa que use la GPU."
  "${COMPOSE[@]}" run --rm check
  "${COMPOSE[@]}" run --rm train
  "${COMPOSE[@]}" run --rm fp8
  echo "Listo: $OUTPUTS_DIR/$RUN/merged-fp8"
  echo "Pruébalo con: SERVE_MODEL=/outputs/$RUN/merged-fp8 docker compose -f $HERE/compose.yaml up serve"
  echo "y mide con invoice_agent_evaluate sobre $DATASET_DIR/test (training/README.md)."
}

case "$STAGE" in
  datos) datos ;;
  entrenar) entrenar ;;
  todo) datos; entrenar ;;
  *) echo "Uso: $0 [todo|datos|entrenar]"; exit 2 ;;
esac
