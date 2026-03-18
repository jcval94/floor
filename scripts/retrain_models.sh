#!/usr/bin/env bash
set -euo pipefail

DATASET_PATH="${1:-data/training/modelable_dataset.json}"
OUTPUT_DIR="${2:-data/training}"
VERSION_TAG="${3:-$(date -u +%Y%m%dT%H%M%SZ)}"
TASKS_ARG="${4:-${TASKS:-d1,w1,q1,value,timing}}"
TRAINING_MODE="${5:-standard}"

if [[ ! -f "$DATASET_PATH" ]]; then
  echo "Dataset not found: $DATASET_PATH" >&2
  echo "Provide a modelable dataset JSON with rows and split fields." >&2
  exit 1
fi

MODELS_DIR="$OUTPUT_DIR/models"
METRICS_DIR="$OUTPUT_DIR/metrics"

mkdir -p "$MODELS_DIR" "$METRICS_DIR"

IFS=',' read -r -a REQUESTED_TASKS <<< "$TASKS_ARG"
horizon_csv=""
m3_csv=""

for task in "${REQUESTED_TASKS[@]}"; do
  t="$(echo "$task" | xargs)"
  case "$t" in
    d1|w1|q1)
      horizon_csv="${horizon_csv:+$horizon_csv,}$t"
      ;;
    value|timing)
      m3_csv="${m3_csv:+$m3_csv,}$t"
      ;;
  esac
done

if [[ -n "$horizon_csv" ]]; then
  PYTHONPATH=src python -m models.train_classic_horizons \
    --dataset "$DATASET_PATH" \
    --output-dir "$MODELS_DIR" \
    --version "$VERSION_TAG" \
    --tasks "$horizon_csv" \
    --training-mode "$TRAINING_MODE"
fi

if [[ -n "$m3_csv" ]]; then
  PYTHONPATH=src python -m models.run_training \
    --dataset "$DATASET_PATH" \
    --output-dir "$OUTPUT_DIR" \
    --version "$VERSION_TAG" \
    --tasks "$m3_csv" \
    --training-mode "$TRAINING_MODE"
fi
