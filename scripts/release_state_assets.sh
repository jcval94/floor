#!/usr/bin/env bash
# Shared helpers for failure-atomic rolling state stored in GitHub Release assets.
# A writer uploads a unique run-scoped set. Readers only select complete sets,
# so an interrupted multi-file upload never replaces the last valid snapshot.

state_release_json() {
  local repo="$1"
  local tag="$2"
  gh api -H "Accept: application/vnd.github+json" "repos/$repo/releases/tags/$tag"
}

state_latest_complete_payload() {
  local repo="$1"
  local tag="$2"
  local base="$3"
  shift 3
  local release
  release="$(state_release_json "$repo" "$tag")"
  local -a args=(select --base "$base")
  local suffix
  for suffix in "$@"; do
    args+=(--suffix "$suffix")
  done
  printf '%s' "$release" | PYTHONPATH=src python -m utils.release_state_assets "${args[@]}"
}

state_download_set() {
  local repo="$1"
  local tag="$2"
  local base="$3"
  local payload="$4"
  local dir="$5"
  shift 5
  mkdir -p "$dir"
  rm -f "$dir/$base"
  local suffix
  for suffix in "$@"; do
    rm -f "$dir/$base$suffix"
  done

  gh release download "$tag" --repo "$repo" --pattern "$payload" --dir "$dir" --clobber
  mv "$dir/$payload" "$dir/$base"
  for suffix in "$@"; do
    gh release download "$tag" --repo "$repo" --pattern "$payload$suffix" --dir "$dir" --clobber
    mv "$dir/$payload$suffix" "$dir/$base$suffix"
  done
}

state_publish_set() {
  local repo="$1"
  local tag="$2"
  local base="$3"
  local dir="$4"
  shift 4
  local run_id="${GITHUB_RUN_ID:-}"
  if ! [[ "$run_id" =~ ^[0-9]+$ ]]; then
    echo "::error::GITHUB_RUN_ID is required for versioned state publication" >&2
    return 2
  fi

  local payload="$base.run-$run_id"
  local -a upload=()
  cp "$dir/$base" "$dir/$payload"
  upload+=("$dir/$payload")

  local suffix
  for suffix in "$@"; do
    cp "$dir/$base$suffix" "$dir/$payload$suffix"
    upload+=("$dir/$payload$suffix")
  done

  # No --clobber: every state generation is immutable.
  gh release upload "$tag" "${upload[@]}" --repo "$repo"

  local release
  release="$(state_release_json "$repo" "$tag")"
  local -a verify=(is-complete --payload "$payload")
  for suffix in "$@"; do
    verify+=(--suffix "$suffix")
  done
  if ! printf '%s' "$release" | PYTHONPATH=src python -m utils.release_state_assets "${verify[@]}"; then
    echo "::error::Versioned state set is incomplete after upload: $payload" >&2
    return 1
  fi
  printf '%s\n' "$payload"
}

state_prune_versioned_sets() {
  local repo="$1"
  local tag="$2"
  local base="$3"
  local keep="$4"
  shift 4

  local release
  release="$(state_release_json "$repo" "$tag")"
  local -a args=(prune-ids --base "$base" --keep "$keep")
  local suffix
  for suffix in "$@"; do
    args+=(--suffix "$suffix")
  done

  local asset_id
  while IFS= read -r asset_id; do
    [[ -n "$asset_id" ]] || continue
    gh api --method DELETE "repos/$repo/releases/assets/$asset_id"
  done < <(printf '%s' "$release" | PYTHONPATH=src python -m utils.release_state_assets "${args[@]}")
}
