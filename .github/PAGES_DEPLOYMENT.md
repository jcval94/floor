# GitHub Pages deployment modes (choose one)

This repository can publish the site in two different ways:

1. **GitHub Actions artifact deploy** (recommended): workflow `.github/workflows/pages.yml` builds `site/` and deploys it with `actions/deploy-pages`.
2. **Branch deploy (`docs/`)**: GitHub's automatic **"pages build and deployment"** workflow publishes static files from the branch.

Running both modes at the same time is risky: whichever workflow deploys last wins, and branch deploy can publish stale `docs/data/*` payloads.

## Recommended setup

In repository **Settings → Pages**:

- Set **Source** to **GitHub Actions**.
- Do **not** use **Deploy from a branch** for this repository while `.github/workflows/pages.yml` is active.

If you intentionally switch to branch deploy, you must keep `docs/` synchronized with generated `site/` data before every publish.
