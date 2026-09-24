#!/usr/bin/env bash
set -euo pipefail

MODE="${1:-}"
REPO="${GITHUB_REPOSITORY:-}"
if [[ -z "$MODE" || -z "$REPO" ]]; then
  echo "usage: GITHUB_REPOSITORY=owner/repo GH_TOKEN=... bash scripts/strategy_live_state.sh <publish-base|restore-base|publish-snapshot|restore-snapshot>" >&2
  exit 2
fi

if ! command -v gh >/dev/null 2>&1; then
  echo "gh CLI is required" >&2
  exit 2
fi

source scripts/release_state_assets.sh

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

case "$MODE" in
  publish-base|restore-base)
    TAG="${STRATEGY_LIVE_BASE_TAG:-strategy-live-base-v1}"
    ASSET="${STRATEGY_LIVE_BASE_ASSET:-strategy-live-base.json}"
    SOURCE="data/metrics/strategy_league/live_base.json"
    PIN="${STRATEGY_LIVE_BASE_PAYLOAD_PIN:-}"
    TITLE="Floor Strategy League live base"
    ;;
  publish-snapshot|restore-snapshot)
    TAG="${STRATEGY_LIVE_TAG:-strategy-live-v1}"
    ASSET="${STRATEGY_LIVE_ASSET:-strategy-live.json}"
    SOURCE="data/metrics/strategy_league/live_snapshot.json"
    PIN="${STRATEGY_LIVE_PAYLOAD_PIN:-}"
    TITLE="Floor Strategy League intraday snapshot"
    ;;
  *)
    echo "unsupported mode: $MODE" >&2
    exit 2
    ;;
esac

validate_json() {
  local path="$1"
  python - "$path" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
payload = json.loads(path.read_text(encoding="utf-8"))
if not isinstance(payload, dict):
    raise SystemExit(f"Expected JSON object: {path}")
if not payload.get("league_id"):
    raise SystemExit(f"Missing league_id: {path}")
PY
}

restore_asset() {
  if [[ "$PIN" == "missing" ]]; then
    echo "Pinned Strategy League state is missing; leaving $SOURCE absent."
    return 0
  fi
  if ! gh release view "$TAG" --repo "$REPO" >/dev/null 2>&1; then
    echo "No Strategy League release exists yet tag=$TAG; leaving $SOURCE absent."
    return 0
  fi

  local selected="$PIN"
  if [[ -z "$selected" || "$selected" == "legacy" ]]; then
    selected="$(state_latest_complete_payload "$REPO" "$TAG" "$ASSET" ".sha256")"
  fi
  if [[ -z "$selected" ]]; then
    echo "No complete Strategy League payload exists yet tag=$TAG; leaving $SOURCE absent."
    return 0
  fi

  state_download_set "$REPO" "$TAG" "$ASSET" "$selected" "$TMP" ".sha256"
  (cd "$TMP" && sha256sum -c "$ASSET.sha256")
  validate_json "$TMP/$ASSET"
  mkdir -p "$(dirname "$SOURCE")"
  cp "$TMP/$ASSET" "$SOURCE"
  echo "Restored Strategy League state tag=$TAG payload=$selected source=$SOURCE"
}

publish_asset() {
  if [[ ! -s "$SOURCE" ]]; then
    echo "::error::Strategy League state source is missing: $SOURCE" >&2
    exit 1
  fi
  validate_json "$SOURCE"
  cp "$SOURCE" "$TMP/$ASSET"
  (
    cd "$TMP"
    sha256sum "$ASSET" > "$ASSET.sha256"
  )

  if ! gh release view "$TAG" --repo "$REPO" >/dev/null 2>&1; then
    gh release create "$TAG"       --repo "$REPO"       --target "${GITHUB_SHA:-main}"       --title "$TITLE"       --notes "Compact release-backed state for the Strategy League intraday dashboard. It is observational and never enables execution or promotion."       --prerelease
  fi

  payload="$(state_publish_set "$REPO" "$TAG" "$ASSET" "$TMP" ".sha256")"
  state_prune_versioned_sets "$REPO" "$TAG" "$ASSET" 2 ".sha256"
  echo "Published Strategy League state tag=$TAG payload=$payload source=$SOURCE"
}

case "$MODE" in
  publish-base|publish-snapshot) publish_asset ;;
  restore-base|restore-snapshot) restore_asset ;;
esac
