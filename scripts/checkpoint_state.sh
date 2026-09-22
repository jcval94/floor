#!/usr/bin/env bash
set -euo pipefail

MODE="${1:-restore}"
TAG="${CHECKPOINT_STATE_TAG:-checkpoint-state-v1}"
ASSET="${CHECKPOINT_STATE_ASSET:-floor-checkpoint-state.tar.gz}"
REPO="${GITHUB_REPOSITORY:-}"
DIR="data/snapshots/workflow_runs"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

if [[ -z "$REPO" ]]; then
  echo "::error::GITHUB_REPOSITORY is required" >&2
  exit 2
fi

restore_state() {
  mkdir -p "$DIR"
  if ! gh release view "$TAG" --repo "$REPO" >/dev/null 2>&1; then
    echo "No checkpoint-state release exists yet."
    return 0
  fi
  for attempt in 1 2 3; do
    rm -f "$TMP/$ASSET" "$TMP/$ASSET.sha256"
    if gh release download "$TAG" --repo "$REPO" --pattern "$ASSET" --pattern "$ASSET.sha256" --dir "$TMP" >/dev/null 2>&1       && (cd "$TMP" && sha256sum -c "$ASSET.sha256" >/dev/null 2>&1); then
      rm -rf "$DIR"
      mkdir -p "$(dirname "$DIR")"
      tar -xzf "$TMP/$ASSET"
      echo "Restored lightweight checkpoint state from release tag=$TAG"
      return 0
    fi
    sleep $((attempt * 2))
  done
  echo "::error::Unable to restore checksum-verified checkpoint state" >&2
  exit 1
}

publish_state() {
  mkdir -p "$DIR"
  tar -czf "$TMP/$ASSET" "$DIR"
  (cd "$TMP" && sha256sum "$ASSET" > "$ASSET.sha256")
  if ! gh release view "$TAG" --repo "$REPO" >/dev/null 2>&1; then
    gh release create "$TAG" --repo "$REPO" --target "${GITHUB_SHA:-main}"       --title "Floor checkpoint state"       --notes "Lightweight idempotency markers for resilient scheduled polling."       --prerelease
  fi
  gh release upload "$TAG" "$TMP/$ASSET" "$TMP/$ASSET.sha256" --repo "$REPO" --clobber
  echo "Published lightweight checkpoint state tag=$TAG"
}

case "$MODE" in
  restore) restore_state ;;
  publish) publish_state ;;
  *) echo "unsupported mode: $MODE" >&2; exit 2 ;;
esac
