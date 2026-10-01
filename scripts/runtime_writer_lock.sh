#!/usr/bin/env bash
set -euo pipefail

MODE="${1:-}"
REPO="${GITHUB_REPOSITORY:-}"
RUN_ID="${GITHUB_RUN_ID:-}"
SHA="${GITHUB_SHA:-main}"
WORKFLOW="${GITHUB_WORKFLOW:-unknown}"
JOB="${GITHUB_JOB:-unknown}"
TAG="${RUNTIME_WRITER_LOCK_TAG:-runtime-writer-lock-v1}"
POLL_SECONDS="${RUNTIME_WRITER_LOCK_POLL_SECONDS:-5}"
MAX_WAIT_SECONDS="${RUNTIME_WRITER_LOCK_MAX_WAIT_SECONDS:-1800}"
STALE_SECONDS="${RUNTIME_WRITER_LOCK_STALE_SECONDS:-5400}"
TOKEN_FILE="${RUNTIME_WRITER_LOCK_TOKEN_FILE:-${RUNNER_TEMP:-.}/floor-runtime-writer-lock.owner}"

if [[ -z "$MODE" || -z "$REPO" || -z "$RUN_ID" ]]; then
  echo "usage: GITHUB_REPOSITORY=owner/repo GITHUB_RUN_ID=... GH_TOKEN=... bash scripts/runtime_writer_lock.sh <acquire|release>" >&2
  exit 2
fi

if ! command -v gh >/dev/null 2>&1 || ! command -v jq >/dev/null 2>&1; then
  echo "runtime writer lock requires gh and jq" >&2
  exit 2
fi

if ! [[ "$POLL_SECONDS" =~ ^[1-9][0-9]*$ ]]   || ! [[ "$MAX_WAIT_SECONDS" =~ ^[1-9][0-9]*$ ]]   || ! [[ "$STALE_SECONDS" =~ ^[1-9][0-9]*$ ]]; then
  echo "lock timing values must be positive integers" >&2
  exit 2
fi

lock_json() {
  gh api "repos/$REPO/releases/tags/$TAG" 2>/dev/null
}

lock_owner() {
  jq -r '(.body // "{}" | fromjson? // {}) | .run_id // empty'
}

safe_delete_if_owner() {
  local expected_owner="$1"
  local current current_owner release_id
  if ! current="$(lock_json)"; then
    return 0
  fi
  current_owner="$(printf '%s' "$current" | lock_owner)"
  if [[ "$current_owner" != "$expected_owner" ]]; then
    echo "Lock ownership changed; refusing delete expected=$expected_owner actual=$current_owner"
    return 1
  fi
  release_id="$(jq -r '.id // empty' <<<"$current")"
  if [[ -z "$release_id" ]]; then
    echo "::warning::Runtime writer lock has no release id; refusing unsafe delete." >&2
    return 1
  fi
  # Delete the exact release object observed above, never "whatever currently
  # owns this tag". This prevents an old waiter/owner from deleting a newly
  # acquired lock if ownership changes between the read and delete.
  gh api -X DELETE "repos/$REPO/releases/$release_id" >/dev/null 2>&1 || {
    echo "::warning::Failed deleting runtime writer lock release_id=$release_id owner=$expected_owner" >&2
    return 1
  }
  echo "Released stale/completed runtime writer lock owner_run_id=$expected_owner release_id=$release_id"
}

acquire() {
  mkdir -p "$(dirname "$TOKEN_FILE")"
  local started now elapsed body current owner status created_at created_epoch age

  body="$(jq -cn     --arg run_id "$RUN_ID"     --arg workflow "$WORKFLOW"     --arg job "$JOB"     --arg sha "$SHA"     '{schema_version:1,run_id:$run_id,workflow:$workflow,job:$job,sha:$sha}')"
  started="$(date -u +%s)"

  while true; do
    if gh release create "$TAG"       --repo "$REPO"       --target "$SHA"       --title "Floor runtime writer lock"       --notes "$body"       --prerelease >/dev/null 2>&1; then
      printf '%s\n' "$RUN_ID" > "$TOKEN_FILE"
      echo "Acquired runtime writer lock tag=$TAG run_id=$RUN_ID workflow=$WORKFLOW job=$JOB"
      return 0
    fi

    if current="$(lock_json)"; then
      owner="$(printf '%s' "$current" | lock_owner)"

      # Idempotent recovery if this same run acquired the lock before a step retry.
      if [[ "$owner" == "$RUN_ID" ]]; then
        printf '%s\n' "$RUN_ID" > "$TOKEN_FILE"
        echo "Runtime writer lock already belongs to this run_id=$RUN_ID"
        return 0
      fi

      status=""
      if [[ -n "$owner" ]]; then
        status="$(gh api "repos/$REPO/actions/runs/$owner" --jq '.status' 2>/dev/null || true)"
      fi
      if [[ "$status" == "completed" ]]; then
        echo "Reclaiming lock from completed run_id=$owner"
        safe_delete_if_owner "$owner" || true
        continue
      fi

      created_at="$(jq -r '.created_at // empty' <<<"$current")"
      if [[ -n "$created_at" ]]; then
        created_epoch="$(date -u -d "$created_at" +%s 2>/dev/null || echo 0)"
        now="$(date -u +%s)"
        age=$(( now - created_epoch ))
        if (( created_epoch > 0 && age >= STALE_SECONDS )); then
          echo "::warning::Reclaiming stale runtime writer lock age_seconds=$age owner_run_id=${owner:-unknown}" >&2
          safe_delete_if_owner "$owner" || true
          continue
        fi
      fi

      echo "Runtime writer lock busy owner_run_id=${owner:-unknown} owner_status=${status:-unknown}; waiting."
    fi

    now="$(date -u +%s)"
    elapsed=$(( now - started ))
    if (( elapsed >= MAX_WAIT_SECONDS )); then
      echo "::error::Timed out waiting for runtime writer lock after ${elapsed}s tag=$TAG" >&2
      exit 1
    fi
    sleep "$POLL_SECONDS"
  done
}

release() {
  local token current owner release_id
  if [[ ! -s "$TOKEN_FILE" ]]; then
    echo "Runtime writer lock token absent; nothing to release."
    return 0
  fi
  token="$(tr -d '[:space:]' < "$TOKEN_FILE")"
  if [[ "$token" != "$RUN_ID" ]]; then
    echo "::warning::Local runtime writer lock token belongs to run_id=$token, not current run_id=$RUN_ID; refusing release." >&2
    return 0
  fi
  if ! current="$(lock_json)"; then
    rm -f "$TOKEN_FILE"
    echo "Runtime writer lock release already absent."
    return 0
  fi
  owner="$(printf '%s' "$current" | lock_owner)"
  if [[ "$owner" != "$RUN_ID" ]]; then
    rm -f "$TOKEN_FILE"
    echo "::warning::Remote runtime writer lock belongs to run_id=${owner:-unknown}; current run will not delete it." >&2
    return 0
  fi
  release_id="$(jq -r '.id // empty' <<<"$current")"
  if [[ -z "$release_id" ]]; then
    echo "::warning::Runtime writer lock has no release id; refusing unsafe delete." >&2
    return 1
  fi
  gh api -X DELETE "repos/$REPO/releases/$release_id" >/dev/null
  rm -f "$TOKEN_FILE"
  echo "Released runtime writer lock tag=$TAG run_id=$RUN_ID release_id=$release_id"
}

case "$MODE" in
  acquire) acquire ;;
  release) release ;;
  *)
    echo "unsupported mode: $MODE" >&2
    exit 2
    ;;
esac
