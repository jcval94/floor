from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_pages_integrity_scan_is_fragment_aware() -> None:
    workflow = (ROOT / ".github" / "workflows" / "pages.yml").read_text(
        encoding="utf-8"
    )
    assert ".split('#', 1)[0]" in workflow
    assert "node --check site/assets/charts.js" in workflow


def test_all_local_html_links_resolve_after_query_and_fragment_stripping() -> None:
    for html in sorted((ROOT / "site").glob("*.html")):
        text = html.read_text(encoding="utf-8")
        for match in re.findall(r'(?:src|href)="([^"]+)"', text):
            if match.startswith(("http://", "https://", "#")):
                continue
            local_path = match.split("?", 1)[0].split("#", 1)[0]
            if not local_path:
                continue
            assert (html.parent / local_path).exists(), (
                f"broken local asset reference {html}: {match}"
            )

def test_pages_publication_lock_only_wraps_real_publish_job() -> None:
    workflow = (ROOT / ".github" / "workflows" / "pages.yml").read_text(
        encoding="utf-8"
    )

    workflow_header = workflow.split("\njobs:\n", 1)[0]
    assert "concurrency:" not in workflow_header

    build_job = workflow.split("\n  build:\n", 1)[1]
    assert "group: pages-eod-publish" in build_job
    assert "cancel-in-progress: false" in build_job
    assert "id: deployment" in build_job
    assert "uses: actions/deploy-pages@v4" in build_job
    assert "\n  deploy:\n" not in workflow

