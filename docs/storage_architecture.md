# Storage architecture

## Goal

Git stores source code, configuration, documentation and the small JSON model registry only. Generated market/runtime/training payloads must not be versioned.

## Three storage tiers

1. **Git** — code + `data/training/models/*.json` only. These JSON files are deliberately small and are required for deterministic serving.
2. **Rolling runtime state** — GitHub Release tag `runtime-state-v1`, asset `floor-runtime-state.tar.gz`. It contains the latest market DB, prediction/signal ledgers, run markers, monitoring/reports and SQLite operational state. The archive has a SHA-256 sidecar and a provenance JSON sidecar. Workflows serialize writes through the `floor-runtime-state-writer` concurrency group.
3. **Immutable run evidence** — GitHub Actions artifacts. Intraday/EOD/monitoring/retraining runs upload their logs, databases and generated outputs with finite retention. These are audit evidence, not the source of truth for the next run.

The runtime release is rolling by design: it avoids Git object growth while retaining the state required across ephemeral runners. Reconstructable training datasets and large binary model copies are kept only as Actions artifacts.

## Historical compaction

The repository currently reports roughly 3.8 GB because generated `data/` payloads were committed repeatedly. Deleting the current files does not remove those blobs from Git history.

After all open PRs are merged/closed, run **manual_compact_git_history** from `main` and enter:

`PURGE_GENERATED_DATA_HISTORY`

The workflow:

- refuses to run with open PRs or protected `main`;
- preserves current operational state in `runtime-state-v1`;
- records pre-rewrite refs and repository metadata;
- mirror-clones the repository;
- runs pinned `git-filter-repo` to remove `data/` from every advertised branch/tag history;
- restores only the current lightweight `data/training/models/*.json` registry on rewritten `main`;
- force-pushes rewritten branches/tags;
- uploads an audit report of before/after local object size.

Every commit SHA changes. Existing local clones must be discarded and cloned again.

GitHub can retain unreachable objects or hidden pull-request refs until server-side garbage collection. If GitHub's reported repository size remains materially high after GC, the deterministic fallback is a **fresh-repository migration**: create a new repository from the cleaned current `main` snapshot, recreate required secrets/settings/Pages, verify CI + Pages, then archive this repository. That guarantees a small object database while preserving this repository as the historical archive.

## Rules

- Never `git add` market DBs, persistence DBs, predictions, signals, reports, snapshots, logs, modelable datasets or PKLs.
- Never use Actions artifacts as the only long-lived state required for a 65-session target; their retention is finite.
- Keep the rolling runtime release and per-run audit artifacts conceptually separate.
- Pages restores the rolling state before building, then applies its existing fail-closed publication audit.
- Promotion of model champions remains a separate governed action; moving storage does not imply model promotion.
