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

PYTHONPATH=src python -m models.run_training \
  --dataset "$DATASET_PATH" \
  --output-dir "$OUTPUT_DIR" \
  --version "$VERSION_TAG" \
  --tasks "$TASKS_ARG" \
  --training-mode "$TRAINING_MODE"
