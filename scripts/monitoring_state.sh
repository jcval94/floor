#!/usr/bin/env bash
set -euo pipefail

MODE="${1:-restore}"
TAG="${MONITORING_STATE_TAG:-monitoring-state-v1}"
ASSET="${MONITORING_STATE_ASSET:-public_metrics.json}"
REPO="${GITHUB_REPOSITORY:-}"
SOURCE="data/metrics/public_metrics.json"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
source scripts/release_state_assets.sh

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

  local attempt selected
  for attempt in 1 2 3; do
    rm -f "$TMP/$ASSET" "$TMP/$ASSET.sha256"
    selected="${MONITORING_STATE_PAYLOAD_PIN:-}"
    if [[ "$selected" = "legacy" ]]; then
      selected=""
    elif [[ -z "$selected" ]]; then
      if ! selected="$(state_latest_complete_payload "$REPO" "$TAG" "$ASSET" ".sha256")"; then
        sleep $((attempt * 2))
        continue
      fi
    fi

    if [[ -n "$selected" ]]; then
      if ! state_download_set "$REPO" "$TAG" "$ASSET" "$selected" "$TMP" ".sha256"; then
        sleep $((attempt * 2))
        continue
      fi
    else
      if ! gh release download "$TAG" --repo "$REPO" --pattern "$ASSET" --pattern "$ASSET.sha256" --dir "$TMP" --clobber >/dev/null 2>&1; then
        sleep $((attempt * 2))
        continue
      fi
    fi

    if (cd "$TMP" && sha256sum -c "$ASSET.sha256" >/dev/null 2>&1) \
      && python -m json.tool "$TMP/$ASSET" >/dev/null 2>&1; then
      cp "$TMP/$ASSET" "$SOURCE"
      echo "Restored monitoring state tag=$TAG payload=${selected:-legacy}"
      return 0
    fi
    sleep $((attempt * 2))
  done

  echo "::error::Unable to restore checksum-verified monitoring state" >&2
  exit 1
}

publish_state() {
  test -s "$SOURCE" || {
    echo "::error::Monitoring snapshot missing: $SOURCE" >&2
    exit 1
  }
  python -m json.tool "$SOURCE" >/dev/null
  cp "$SOURCE" "$TMP/$ASSET"
  (cd "$TMP" && sha256sum "$ASSET" > "$ASSET.sha256")

  if ! gh release view "$TAG" --repo "$REPO" >/dev/null 2>&1; then
    gh release create "$TAG" --repo "$REPO" --target "${GITHUB_SHA:-main}" \
      --title "Floor monitoring state" \
      --notes "Read-only operational health snapshot, isolated from authoritative trading runtime state." \
      --prerelease
  fi

  published_payload="$(state_publish_set "$REPO" "$TAG" "$ASSET" "$TMP" ".sha256")"
  state_prune_versioned_sets "$REPO" "$TAG" "$ASSET" 2 ".sha256"
  echo "Published isolated monitoring state tag=$TAG payload=$published_payload"
}

case "$MODE" in
  restore) restore_state ;;
  publish) publish_state ;;
  *) echo "unsupported mode: $MODE" >&2; exit 2 ;;
esac
