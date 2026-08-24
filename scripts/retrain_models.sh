#!/usr/bin/env bash
set -euo pipefail

if [[ "${GITHUB_EVENT_NAME:-}" == "workflow_run" ]]; then
  echo "::error::Automatic retraining execution remains intentionally blocked; use an explicitly approved manual retrain until automatic promotion is separately enabled." >&2
  exit 42
fi

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
  PREVIOUS_DIR="$(mktemp -d)"
  trap 'rm -rf "${PREVIOUS_DIR:-}"' EXIT

  IFS=',' read -r -a HORIZON_TASKS <<< "$horizon_csv"
  for task in "${HORIZON_TASKS[@]}"; do
    champion="$MODELS_DIR/${task}_champion.json"
    if [[ -f "$champion" ]]; then
      cp "$champion" "$PREVIOUS_DIR/${task}_champion.json"
    fi
  done

  PYTHONPATH=src python -m models.train_classic_horizons \
    --dataset "$DATASET_PATH" \
    --output-dir "$MODELS_DIR" \
    --version "$VERSION_TAG" \
    --tasks "$horizon_csv" \
    --training-mode "$TRAINING_MODE"

  # The trainer's within-run winner is only a candidate. Re-evaluate that
  # candidate and the previous champion on the exact same current leakage-safe
  # validation rows before allowing the registry to change.
  PYTHONPATH=src python -m models.classic_champion_gate \
    --dataset "$DATASET_PATH" \
    --registry-dir "$MODELS_DIR" \
    --previous-dir "$PREVIOUS_DIR" \
    --tasks "$horizon_csv" \
    --version "$VERSION_TAG"
fi

if [[ -n "$m3_csv" ]]; then
  PYTHONPATH=src python -m models.run_training \
    --dataset "$DATASET_PATH" \
    --output-dir "$OUTPUT_DIR" \
    --version "$VERSION_TAG" \
    --tasks "$m3_csv" \
    --training-mode "$TRAINING_MODE"
fi
