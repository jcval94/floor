#!/usr/bin/env bash
set -euo pipefail

MODE="${1:-}"
TAG="${RUNTIME_STATE_TAG:-runtime-state-v1}"
ASSET="${RUNTIME_STATE_ASSET:-floor-runtime-state.tar.gz}"
MAX_MB="${RUNTIME_STATE_MAX_MB:-500}"
RETENTION_INTERVAL_SECONDS="${RUNTIME_STATE_RETENTION_INTERVAL_SECONDS:-86400}"
FORCE_RETENTION="${RUNTIME_STATE_FORCE_RETENTION:-false}"
REPO="${GITHUB_REPOSITORY:-}"
TOKEN_FILE="${RUNTIME_STATE_TOKEN_FILE:-${RUNNER_TEMP:-.}/floor-runtime-state-restore-token.json}"

if [[ -z "$MODE" || -z "$REPO" ]]; then
  echo "usage: GITHUB_REPOSITORY=owner/repo GH_TOKEN=... bash scripts/runtime_state.sh <restore|publish>" >&2
  exit 2
fi

if ! command -v gh >/dev/null 2>&1; then
  echo "gh CLI is required" >&2
  exit 2
fi

if ! [[ "$MAX_MB" =~ ^[1-9][0-9]*$ ]]; then
  echo "RUNTIME_STATE_MAX_MB must be a positive integer, got: $MAX_MB" >&2
  exit 2
fi
export RUNTIME_STATE_MAX_MB="$MAX_MB"
if ! [[ "$RETENTION_INTERVAL_SECONDS" =~ ^[0-9]+$ ]]; then
  echo "RUNTIME_STATE_RETENTION_INTERVAL_SECONDS must be a non-negative integer, got: $RETENTION_INTERVAL_SECONDS" >&2
  exit 2
fi
if [[ "$FORCE_RETENTION" != "true" && "$FORCE_RETENTION" != "false" ]]; then
  echo "RUNTIME_STATE_FORCE_RETENTION must be true or false, got: $FORCE_RETENTION" >&2
  exit 2
fi

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
source scripts/release_state_assets.sh

clear_runtime_state() {
  # A release restore is authoritative. Remove only operational paths; never
  # touch Git-managed champion JSON under data/training/models/.
  rm -rf \
    data/market \
    data/predictions \
    data/signals \
    data/orders \
    data/trades \
    data/snapshots \
    data/reports \
    data/metrics \
    data/persistence
  rm -f \
    data/training/reviews.jsonl \
    data/training/review_summary_latest.json
}

validate_archive() {
  local archive="$1"
  python - "$archive" <<'PY'
import sys
import tarfile
from pathlib import PurePosixPath

archive = sys.argv[1]
allowed_dirs = {
    "data/market",
    "data/predictions",
    "data/signals",
    "data/orders",
    "data/trades",
    "data/snapshots",
    "data/reports",
    "data/metrics",
    "data/persistence",
}
allowed_files = {
    "data/training/reviews.jsonl",
    "data/training/review_summary_latest.json",
}

with tarfile.open(archive, "r:gz") as tf:
    for member in tf.getmembers():
        name = member.name.rstrip("/")
        path = PurePosixPath(name)
        if not name or path.is_absolute() or ".." in path.parts:
            raise SystemExit(f"unsafe runtime-state archive entry: {member.name}")
        if member.issym() or member.islnk():
            raise SystemExit(f"links are forbidden in runtime-state archive: {member.name}")
        if not (member.isdir() or member.isfile()):
            raise SystemExit(
                f"unsupported runtime-state archive member type: {member.name}"
            )
        allowed = name in allowed_files or any(
            name == root or name.startswith(root + "/") for root in allowed_dirs
        )
        if not allowed:
            raise SystemExit(f"unexpected runtime-state archive entry: {member.name}")
PY
}

validate_sqlite_state() {
  local checkpoint="${1:-false}"
  python - "$checkpoint" <<'PY'
import sqlite3
import sys
from pathlib import Path

checkpoint = sys.argv[1].lower() == "true"
for path in (Path("data/market/market_data.sqlite"), Path("data/persistence/app.sqlite")):
    if not path.exists():
        continue
    with sqlite3.connect(path) as conn:
        if checkpoint:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchall()
        rows = conn.execute("PRAGMA quick_check").fetchall()
    if not rows or any(str(row[0]).lower() != "ok" for row in rows):
        raise SystemExit(f"SQLite quick_check failed for {path}: {rows[:10]}")
    print(f"sqlite_ok={path} checkpointed={checkpoint}")
PY
}

collect_paths() {
  paths=()
  local candidate
  for candidate in \
    data/market \
    data/predictions \
    data/signals \
    data/orders \
    data/trades \
    data/snapshots \
    data/reports \
    data/metrics \
    data/persistence \
    data/training/reviews.jsonl \
    data/training/review_summary_latest.json; do
    [[ -e "$candidate" ]] && paths+=("$candidate")
  done
}

restore_state() {
  mkdir -p "$(dirname "$TOKEN_FILE")"
  if ! gh release view "$TAG" --repo "$REPO" >/dev/null 2>&1; then
    PYTHONPATH=src python -m utils.runtime_state_cas write-token \
      --token "$TOKEN_FILE" \
      --remote-missing
    echo "No runtime-state release exists yet; using checkout/bootstrap state."
    return 0
  fi

  local restored=false
  local attempt selected
  for attempt in 1 2 3; do
    rm -f "$TMP/$ASSET" "$TMP/$ASSET.sha256" "$TMP/$ASSET.metadata.json"
    selected=""
    if ! selected="$(state_latest_complete_payload "$REPO" "$TAG" "$ASSET" ".sha256" ".metadata.json")"; then
      echo "::warning::Runtime-state generation lookup attempt $attempt failed." >&2
      sleep $((attempt * 2))
      continue
    fi

    if [[ -n "$selected" ]]; then
      if ! state_download_set "$REPO" "$TAG" "$ASSET" "$selected" "$TMP" ".sha256" ".metadata.json"; then
        echo "::warning::Runtime-state versioned download attempt $attempt failed payload=$selected." >&2
        sleep $((attempt * 2))
        continue
      fi
    else
      # Backward-compatible migration path for the pre-generation release layout.
      if ! gh release download "$TAG" --repo "$REPO" --pattern "$ASSET" --dir "$TMP" --clobber \
        || ! gh release download "$TAG" --repo "$REPO" --pattern "$ASSET.sha256" --dir "$TMP" --clobber \
        || ! gh release download "$TAG" --repo "$REPO" --pattern "$ASSET.metadata.json" --dir "$TMP" --clobber; then
        echo "::warning::Runtime-state legacy download attempt $attempt failed." >&2
        sleep $((attempt * 2))
        continue
      fi
    fi

    if (cd "$TMP" && sha256sum -c "$ASSET.sha256") \
      && validate_archive "$TMP/$ASSET" \
      && PYTHONPATH=src python -m utils.runtime_state_cas write-token \
        --token "$TOKEN_FILE" \
        --metadata "$TMP/$ASSET.metadata.json" \
        --checksum "$TMP/$ASSET.sha256"; then
      restored=true
      break
    fi

    echo "::warning::Runtime-state restore attempt $attempt failed validation." >&2
    sleep $((attempt * 2))
  done

  if [[ "$restored" != "true" ]]; then
    echo "::error::Unable to restore a checksum-valid runtime state after 3 attempts." >&2
    exit 1
  fi

  clear_runtime_state
  tar -xzf "$TMP/$ASSET" -C .
  validate_sqlite_state false
  echo "Restored authoritative runtime state from release tag=$TAG asset=$ASSET"
}

publish_state() {
  local paths=()
  collect_paths
  if [[ ${#paths[@]} -eq 0 ]]; then
    echo "No runtime state exists to publish."
    return 0
  fi
  if [[ ! -s "$TOKEN_FILE" ]]; then
    echo "::error::Runtime-state publish refused: restore token missing. Run restore first." >&2
    exit 1
  fi

  local parent_generation next_generation parent_sha
  local parent_result="$TMP/runtime-state-parent.tsv"
  if gh release view "$TAG" --repo "$REPO" >/dev/null 2>&1; then
    mkdir -p "$TMP/current"
    rm -f "$TMP/current/$ASSET.sha256" "$TMP/current/$ASSET.metadata.json"
    selected="$(state_latest_complete_payload "$REPO" "$TAG" "$ASSET" ".sha256" ".metadata.json")"
    if [[ -n "$selected" ]]; then
      gh release download "$TAG" --repo "$REPO" \
        --pattern "$selected.sha256" --dir "$TMP/current" --clobber
      gh release download "$TAG" --repo "$REPO" \
        --pattern "$selected.metadata.json" --dir "$TMP/current" --clobber
      mv "$TMP/current/$selected.sha256" "$TMP/current/$ASSET.sha256"
      mv "$TMP/current/$selected.metadata.json" "$TMP/current/$ASSET.metadata.json"
    else
      gh release download "$TAG" --repo "$REPO" \
        --pattern "$ASSET.sha256" --dir "$TMP/current" --clobber
      gh release download "$TAG" --repo "$REPO" \
        --pattern "$ASSET.metadata.json" --dir "$TMP/current" --clobber
    fi
    if ! PYTHONPATH=src python -m utils.runtime_state_cas verify-parent \
      --token "$TOKEN_FILE" \
      --metadata "$TMP/current/$ASSET.metadata.json" \
      --checksum "$TMP/current/$ASSET.sha256" \
      --format tsv > "$parent_result"; then
      echo "::error::Runtime-state CAS parent verification failed." >&2
      exit 1
    fi
  else
    if ! PYTHONPATH=src python -m utils.runtime_state_cas verify-parent \
      --token "$TOKEN_FILE" \
      --remote-missing \
      --format tsv > "$parent_result"; then
      echo "::error::Runtime-state CAS genesis verification failed." >&2
      exit 1
    fi
  fi
  read -r parent_generation next_generation parent_sha < "$parent_result"
  echo "runtime_state_parent_generation=$parent_generation next_generation=$next_generation parent_sha256=$parent_sha"

  local frontier_at frontier_event
  local frontier_result="$TMP/runtime-state-frontier.tsv"
  local -a frontier_args=(resolve-frontier --format tsv)
  if [[ -s "$TMP/current/$ASSET.metadata.json" ]]; then
    frontier_args+=(--metadata "$TMP/current/$ASSET.metadata.json")
  fi
  if [[ -n "${RUNTIME_STATE_CHECKPOINT_AT:-}" ]]; then
    frontier_args+=(
      --marker-dir data/snapshots/workflow_runs
      --checkpoint-at "$RUNTIME_STATE_CHECKPOINT_AT"
      --event "${RUNTIME_STATE_CHECKPOINT_EVENT:-}"
    )
  fi
  if ! PYTHONPATH=src python -m utils.runtime_state_cas "${frontier_args[@]}" > "$frontier_result"; then
    echo "::error::Runtime-state checkpoint frontier validation failed." >&2
    exit 1
  fi
  read -r frontier_at frontier_event < "$frontier_result"
  echo "runtime_state_checkpoint_frontier=$frontier_at event=$frontier_event"

  # Retention horizons are measured in months/years. Avoid rescanning the
  # full runtime state on every intraday publish; missing/stale reports still
  # fail toward running the compaction pass.
  if [[ "$FORCE_RETENTION" == "true" ]]; then
    PYTHONPATH=src python -m floor.runtime_retention --data-dir data \
      --if-due-seconds "$RETENTION_INTERVAL_SECONDS" --force
  else
    PYTHONPATH=src python -m floor.runtime_retention --data-dir data \
      --if-due-seconds "$RETENTION_INTERVAL_SECONDS"
  fi
  collect_paths

  local candidate
  for candidate in "${paths[@]}"; do
    if find "$candidate" -type l -print -quit 2>/dev/null | grep -q .; then
      echo "::error::Refusing to publish symlinked runtime state under $candidate" >&2
      exit 1
    fi
  done

  # Fold committed WAL pages into the main DB files before archiving. This
  # keeps the rolling release smaller and ensures the archived DBs are
  # independently integrity-checked before replacing the previous state.
  validate_sqlite_state true

  tar -czf "$TMP/$ASSET" "${paths[@]}"
  validate_archive "$TMP/$ASSET"

  local asset_bytes max_bytes
  asset_bytes=$(stat -c%s "$TMP/$ASSET")
  max_bytes=$((MAX_MB * 1024 * 1024))
  if (( asset_bytes > max_bytes )); then
    echo "::error::Refusing to replace runtime-state release: asset=${asset_bytes} bytes exceeds cap=${max_bytes} bytes (${MAX_MB} MB)." >&2
    exit 1
  fi

  (
    cd "$TMP"
    sha256sum "$ASSET" > "$ASSET.sha256"
  )

  python - "$TMP/$ASSET.metadata.json" "$TMP/$ASSET.sha256" "$asset_bytes" "$next_generation" "$parent_sha" "$frontier_at" "$frontier_event" <<'PY'
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

out = Path(sys.argv[1])
checksum = Path(sys.argv[2]).read_text(encoding="utf-8").split()[0]
asset_bytes = int(sys.argv[3])
generation = int(sys.argv[4])
parent_sha = None if sys.argv[5] == "-" else sys.argv[5]
frontier_at = None if sys.argv[6] == "-" else sys.argv[6]
frontier_event = None if sys.argv[7] == "-" else sys.argv[7]
out.write_text(
    json.dumps(
        {
            "schema_version": 4,
            "generation": generation,
            "parent_sha256": parent_sha,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "repository": os.getenv("GITHUB_REPOSITORY"),
            "source_sha": os.getenv("GITHUB_SHA"),
            "run_id": os.getenv("GITHUB_RUN_ID"),
            "workflow": os.getenv("GITHUB_WORKFLOW"),
            "asset": os.getenv("RUNTIME_STATE_ASSET", "floor-runtime-state.tar.gz"),
            "asset_bytes": asset_bytes,
            "max_asset_mb": int(os.getenv("RUNTIME_STATE_MAX_MB", "500")),
            "retention_report": "data/metrics/runtime_retention_latest.json",
            "checkpoint_frontier": {
                "checkpoint_at": frontier_at,
                "event": frontier_event,
            },
            "sha256": checksum,
        },
        indent=2,
        sort_keys=True,
    ) + "\n",
    encoding="utf-8",
)
PY

  if ! gh release view "$TAG" --repo "$REPO" >/dev/null 2>&1; then
    gh release create "$TAG" \
      --repo "$REPO" \
      --target "${GITHUB_SHA:-main}" \
      --title "Floor runtime state" \
      --notes "Rolling operational state kept outside Git history. Generated outputs remain reconstructable/auditable through Actions artifacts." \
      --prerelease
  fi

  published_payload="$(state_publish_set "$REPO" "$TAG" "$ASSET" "$TMP" ".sha256" ".metadata.json")"
  state_prune_versioned_sets "$REPO" "$TAG" "$ASSET" 2 ".sha256" ".metadata.json"
  PYTHONPATH=src python -m utils.runtime_state_cas write-token \
    --token "$TOKEN_FILE" \
    --metadata "$TMP/$ASSET.metadata.json" \
    --checksum "$TMP/$ASSET.sha256"
  echo "Published checksum-verified rolling runtime state tag=$TAG generation=$next_generation payload=$published_payload bytes=$asset_bytes cap_mb=$MAX_MB"
}

case "$MODE" in
  restore) restore_state ;;
  publish) publish_state ;;
  *)
    echo "unsupported mode: $MODE" >&2
    exit 2
    ;;
esac
