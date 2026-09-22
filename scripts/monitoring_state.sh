#!/usr/bin/env bash
set -euo pipefail

MODE="${1:-restore}"
TAG="${MONITORING_STATE_TAG:-monitoring-state-v1}"
ASSET="${MONITORING_STATE_ASSET:-public_metrics.json}"
REPO="${GITHUB_REPOSITORY:-}"
SOURCE="data/metrics/public_metrics.json"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

if [[ -z "$REPO" ]]; then
  echo "::error::GITHUB_REPOSITORY is required" >&2
  exit 2
fi

restore_state() {
  mkdir -p "$(dirname "$SOURCE")"
  if ! gh release view "$TAG" --repo "$REPO" >/dev/null 2>&1; then
    echo "No monitoring-state release exists yet."
    return 0
  fi
  for attempt in 1 2 3; do
    rm -f "$TMP/$ASSET" "$TMP/$ASSET.sha256"
    if gh release download "$TAG" --repo "$REPO" --pattern "$ASSET" --pattern "$ASSET.sha256" --dir "$TMP" >/dev/null 2>&1       && (cd "$TMP" && sha256sum -c "$ASSET.sha256" >/dev/null 2>&1)       && python -m json.tool "$TMP/$ASSET" >/dev/null 2>&1; then
      cp "$TMP/$ASSET" "$SOURCE"
      echo "Restored monitoring state from release tag=$TAG"
      return 0
    fi
    sleep $((attempt * 2))
  done
  echo "::error::Unable to restore checksum-verified monitoring state" >&2
  exit 1
}

publish_state() {
  test -s "$SOURCE" || { echo "::error::Monitoring snapshot missing: $SOURCE" >&2; exit 1; }
  python -m json.tool "$SOURCE" >/dev/null
  cp "$SOURCE" "$TMP/$ASSET"
  (cd "$TMP" && sha256sum "$ASSET" > "$ASSET.sha256")
  if ! gh release view "$TAG" --repo "$REPO" >/dev/null 2>&1; then
    gh release create "$TAG" --repo "$REPO" --target "${GITHUB_SHA:-main}"       --title "Floor monitoring state"       --notes "Read-only operational health snapshot, isolated from authoritative trading runtime state."       --prerelease
  fi
  gh release upload "$TAG" "$TMP/$ASSET" "$TMP/$ASSET.sha256" --repo "$REPO" --clobber
  echo "Published isolated monitoring state tag=$TAG"
}

case "$MODE" in
  restore) restore_state ;;
  publish) publish_state ;;
  *) echo "unsupported mode: $MODE" >&2; exit 2 ;;
esac
