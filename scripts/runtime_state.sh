#!/usr/bin/env bash
set -euo pipefail

MODE="${1:-}"
TAG="${RUNTIME_STATE_TAG:-runtime-state-v1}"
ASSET="${RUNTIME_STATE_ASSET:-floor-runtime-state.tar.gz}"
REPO="${GITHUB_REPOSITORY:-}"

if [[ -z "$MODE" || -z "$REPO" ]]; then
  echo "usage: GITHUB_REPOSITORY=owner/repo GH_TOKEN=... bash scripts/runtime_state.sh <restore|publish>" >&2
  exit 2
fi

if ! command -v gh >/dev/null 2>&1; then
  echo "gh CLI is required" >&2
  exit 2
fi

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

allowed_entry() {
  local entry="$1"
  [[ "$entry" != /* ]] || return 1
  [[ "$entry" != *"../"* && "$entry" != ".." ]] || return 1
  case "$entry" in
    data/predictions|data/predictions/*|
    data/signals|data/signals/*|
    data/orders|data/orders/*|
    data/trades|data/trades/*|
    data/snapshots|data/snapshots/*|
    data/reports|data/reports/*|
    data/metrics|data/metrics/*|
    data/persistence|data/persistence/*|
    data/training/reviews.jsonl|
    data/training/review_summary_latest.json)
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

restore_state() {
  if ! gh release view "$TAG" --repo "$REPO" >/dev/null 2>&1; then
    echo "No runtime-state release exists yet; using checkout/bootstrap state."
    return 0
  fi

  gh release download "$TAG" --repo "$REPO" --pattern "$ASSET" --dir "$TMP" --clobber
  gh release download "$TAG" --repo "$REPO" --pattern "$ASSET.sha256" --dir "$TMP" --clobber
  (
    cd "$TMP"
    sha256sum -c "$ASSET.sha256"
  )

  while IFS= read -r entry; do
    if ! allowed_entry "$entry"; then
      echo "Refusing unsafe/unexpected runtime-state archive entry: $entry" >&2
      exit 1
    fi
  done < <(tar -tzf "$TMP/$ASSET")

  tar -xzf "$TMP/$ASSET" -C .
  echo "Restored runtime state from release tag=$TAG asset=$ASSET"
}

publish_state() {
  local paths=()
  local candidate
  for candidate in \
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

  if [[ ${#paths[@]} -eq 0 ]]; then
    echo "No runtime state exists to publish."
    return 0
  fi

  tar -czf "$TMP/$ASSET" "${paths[@]}"
  (
    cd "$TMP"
    sha256sum "$ASSET" > "$ASSET.sha256"
  )

  python - "$TMP/$ASSET.metadata.json" <<'PY'
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

out = Path(sys.argv[1])
out.write_text(
    json.dumps(
        {
            "schema_version": 1,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "repository": os.getenv("GITHUB_REPOSITORY"),
            "source_sha": os.getenv("GITHUB_SHA"),
            "run_id": os.getenv("GITHUB_RUN_ID"),
            "workflow": os.getenv("GITHUB_WORKFLOW"),
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

  gh release upload "$TAG" \
    "$TMP/$ASSET" \
    "$TMP/$ASSET.sha256" \
    "$TMP/$ASSET.metadata.json" \
    --repo "$REPO" \
    --clobber
  echo "Published rolling runtime state to release tag=$TAG"
}

case "$MODE" in
  restore) restore_state ;;
  publish) publish_state ;;
  *)
    echo "unsupported mode: $MODE" >&2
    exit 2
    ;;
esac
