from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_yahoo_e2e_anchors_cycle_to_latest_common_ingested_session() -> None:
    workflow = (
        ROOT / ".github" / "workflows" / "e2e_yahoo_pages_audit.yml"
    ).read_text(encoding="utf-8")

    assert "Resolve deterministic E2E market anchor" in workflow
    assert "latest_common_ingested_session" in workflow
    assert "--checkpoint-at" in workflow
    assert "--required-market-session" in workflow
    assert "steps.market_anchor.outputs.checkpoint_at" in workflow
    assert "steps.market_anchor.outputs.session" in workflow
