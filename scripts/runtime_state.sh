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
            raise SystemExit(f"unsupported runtime-state archive member type: {member.name}")
        allowed = name in allowed_files or any(name == root or name.startswith(root + "/") for root in allowed_dirs)
        if not allowed:
            raise SystemExit(f"unexpected runtime-state archive entry: {member.name}")
PY
}

restore_state() {
  if ! gh release view "$TAG" --repo "$REPO" >/dev/null 2>&1; then
    echo "No runtime-state release exists yet; using checkout/bootstrap state."
    return 0
  fi

  local restored=false
  local attempt
  for attempt in 1 2 3; do
    rm -f "$TMP/$ASSET" "$TMP/$ASSET.sha256"
    if gh release download "$TAG" --repo "$REPO" --pattern "$ASSET" --dir "$TMP" --clobber \
      && gh release download "$TAG" --repo "$REPO" --pattern "$ASSET.sha256" --dir "$TMP" --clobber \
      && (cd "$TMP" && sha256sum -c "$ASSET.sha256") \
      && validate_archive "$TMP/$ASSET"; then
      restored=true
      break
    fi
    echo "::warning::Runtime-state restore attempt $attempt failed; release may be updating concurrently." >&2
    sleep $((attempt * 2))
  done

  if [[ "$restored" != "true" ]]; then
    echo "::error::Unable to restore a checksum-valid runtime state after 3 attempts." >&2
    exit 1
  fi

  clear_runtime_state
  tar -xzf "$TMP/$ASSET" -C .
  echo "Restored authoritative runtime state from release tag=$TAG asset=$ASSET"
}

publish_state() {
  local paths=()
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

  if [[ ${#paths[@]} -eq 0 ]]; then
    echo "No runtime state exists to publish."
    return 0
  fi

  for candidate in "${paths[@]}"; do
    if find "$candidate" -type l -print -quit 2>/dev/null | grep -q .; then
      echo "::error::Refusing to publish symlinked runtime state under $candidate" >&2
      exit 1
    fi
  done

  tar -czf "$TMP/$ASSET" "${paths[@]}"
  validate_archive "$TMP/$ASSET"
  (
    cd "$TMP"
    sha256sum "$ASSET" > "$ASSET.sha256"
  )

  python - "$TMP/$ASSET.metadata.json" "$TMP/$ASSET.sha256" <<'PY'
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

out = Path(sys.argv[1])
checksum = Path(sys.argv[2]).read_text(encoding="utf-8").split()[0]
out.write_text(
    json.dumps(
        {
            "schema_version": 1,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "repository": os.getenv("GITHUB_REPOSITORY"),
            "source_sha": os.getenv("GITHUB_SHA"),
            "run_id": os.getenv("GITHUB_RUN_ID"),
            "workflow": os.getenv("GITHUB_WORKFLOW"),
            "asset": os.getenv("RUNTIME_STATE_ASSET", "floor-runtime-state.tar.gz"),
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

  gh release upload "$TAG" \
    "$TMP/$ASSET" \
    "$TMP/$ASSET.sha256" \
    "$TMP/$ASSET.metadata.json" \
    --repo "$REPO" \
    --clobber
  echo "Published checksum-verified rolling runtime state to release tag=$TAG"
}

case "$MODE" in
  restore) restore_state ;;
  publish) publish_state ;;
  *)
    echo "unsupported mode: $MODE" >&2
    exit 2
    ;;
esac
