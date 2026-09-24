#!/usr/bin/env bash
set -euo pipefail

MODE="${1:-restore}"
TAG="${RESEARCH_STATE_TAG:-research-state-v1}"
ASSET="${RESEARCH_STATE_ASSET:-floor-research-state.tar.gz}"
REPO="${GITHUB_REPOSITORY:-}"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
source scripts/release_state_assets.sh

FILES=(
  "data/reports/strategy.json"
  "data/reports/strategy_attribution.json"
  "data/reports/walk_forward_oos.json"
)

if [[ -z "$REPO" ]]; then
  echo "::error::GITHUB_REPOSITORY is required" >&2
  exit 2
fi

validate_archive() {
  python - "$1" <<'PY'
import json
import sys
import tarfile
from pathlib import Path

archive = Path(sys.argv[1])
allowed = {
    "data/reports/strategy.json",
    "data/reports/strategy_attribution.json",
    "data/reports/walk_forward_oos.json",
}
with tarfile.open(archive, "r:gz") as tf:
    members = tf.getmembers()
    if not members:
        raise SystemExit("research-state archive is empty")
    for member in members:
        if member.name not in allowed or not member.isfile():
            raise SystemExit(f"unexpected research-state member: {member.name}")
        extracted = tf.extractfile(member)
        if extracted is None:
            raise SystemExit(f"unable to read {member.name}")
        payload = json.load(extracted)
        if not isinstance(payload, dict):
            raise SystemExit(f"research payload must be an object: {member.name}")
PY
}

restore_state() {
  
  if ! gh release view "$TAG" --repo "$REPO" >/dev/null 2>&1; then
    echo "No research-state release exists yet. Keeping restored runtime research fallback."
    return 0
  fi

  local attempt selected
  for attempt in 1 2 3; do
    rm -f "$TMP/$ASSET" "$TMP/$ASSET.sha256"
    selected="${RESEARCH_STATE_PAYLOAD_PIN:-}"
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
      && validate_archive "$TMP/$ASSET"; then
      while IFS= read -r path; do
        [[ -n "$path" ]] && rm -f "$path"
      done < <(tar -tzf "$TMP/$ASSET")
      tar -xzf "$TMP/$ASSET"
      echo "Restored durable research state from release tag=$TAG payload=${selected:-legacy}"
      return 0
    fi
    sleep $((attempt * 2))
  done

  echo "::error::Unable to restore checksum-verified research state" >&2
  exit 1
}

publish_state() {
  existing=()
  for path in "${FILES[@]}"; do
    if [[ -s "$path" ]]; then
      python -m json.tool "$path" >/dev/null
      existing+=("$path")
    fi
  done
  if [[ "${#existing[@]}" -eq 0 ]]; then
    echo "::error::No research reports are available to publish" >&2
    exit 1
  fi
  tar -czf "$TMP/$ASSET" "${existing[@]}"
  validate_archive "$TMP/$ASSET"
  (cd "$TMP" && sha256sum "$ASSET" > "$ASSET.sha256")

  if ! gh release view "$TAG" --repo "$REPO" >/dev/null 2>&1; then
    gh release create "$TAG" --repo "$REPO" --target "${GITHUB_SHA:-main}" \
      --title "Floor research state" \
      --notes "Durable retrospective and model-OOS research evidence used by GitHub Pages. Isolated from authoritative trading runtime state." \
      --prerelease
  fi

  published_payload="$(state_publish_set "$REPO" "$TAG" "$ASSET" "$TMP" ".sha256")"
  state_prune_versioned_sets "$REPO" "$TAG" "$ASSET" 2 ".sha256"
  echo "Published durable research state tag=$TAG payload=$published_payload files=${#existing[@]}"
}

case "$MODE" in
  restore) restore_state ;;
  publish) publish_state ;;
  *) echo "unsupported mode: $MODE" >&2; exit 2 ;;
esac
