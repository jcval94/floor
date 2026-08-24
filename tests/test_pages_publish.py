from __future__ import annotations

import json
from pathlib import Path

import pytest

from utils.pages_publish import (
    _inject_audit_script,
    model_suite_compatibility,
    select_latest_global_batch,
    validate_prediction_contract,
    validate_published_site,
)
from utils.pages_security import CSP_META, harden_site_html, validate_site_html_security


def _row(symbol: str, horizon: str, as_of: str) -> dict:
    base = {
        "symbol": symbol,
        "horizon": horizon,
        "as_of": as_of,
        "model_version": "suite-v2",
    }
    if horizon == "d1":
        base.update(
            floor_value=98.0,
            ceiling_value=103.0,
            floor_time_bucket="",
            ceiling_time_bucket="",
        )
    elif horizon == "w1":
        base.update(
            floor_value=96.0,
            ceiling_value=105.0,
            floor_time_bucket="2",
            ceiling_time_bucket="4",
        )
    elif horizon == "q1":
        base.update(
            floor_value=94.0,
            ceiling_value=108.0,
            floor_time_bucket="4",
            ceiling_time_bucket="8",
        )
    elif horizon == "m3":
        base.update(
            floor_m3=88.0,
            floor_week_m3=5,
            floor_week_m3_confidence=0.22,
            floor_week_m3_top3=[
                {"week": 5, "probability": 0.22},
                {"week": 6, "probability": 0.18},
                {"week": 4, "probability": 0.15},
            ],
            m3_status="ok",
        )
    return base


def _complete_batch(as_of: str) -> list[dict]:
    return [_row("AAA", horizon, as_of) for horizon in ("d1", "w1", "q1", "m3")]


def test_latest_partial_batch_is_blocked_not_hidden_by_older_complete_batch() -> None:
    older = _complete_batch("2026-08-20T20:00:00+00:00")
    newest_partial = [_row("AAA", "d1", "2026-08-21T20:00:00+00:00")]

    selected, audit = select_latest_global_batch(older + newest_partial, ["AAA"])

    assert selected == []
    assert audit["status"] == "BLOCKED"
    assert audit["as_of"] == "2026-08-21T20:00:00+00:00"
    assert "AAA:q1" in audit["missing_keys"]
    assert audit["reason"] == "latest_global_batch_incomplete"


def test_complete_global_batch_is_exact_and_coherent() -> None:
    rows = _complete_batch("2026-08-21T20:00:00+00:00")
    selected, audit = select_latest_global_batch(rows, ["AAA"])

    assert audit["status"] == "OK"
    assert len(selected) == 4
    assert len({row["as_of"] for row in selected}) == 1
    assert {row["horizon"] for row in selected} == {"d1", "w1", "q1", "m3"}


def test_q1_timing_outside_10_sessions_is_rejected() -> None:
    rows = _complete_batch("2026-08-21T20:00:00+00:00")
    q1 = next(row for row in rows if row["horizon"] == "q1")
    q1["ceiling_time_bucket"] = "45"

    result = validate_prediction_contract(rows, ["AAA"])

    assert result["status"] == "BLOCKED"
    assert any("AAA:q1:invalid_ceiling_timing=45" in item for item in result["errors"])


def test_mixed_model_versions_are_rejected() -> None:
    rows = _complete_batch("2026-08-21T20:00:00+00:00")
    rows[-1]["model_version"] = "different-suite"

    result = validate_prediction_contract(rows, ["AAA"])

    assert result["valid"] is False
    assert any("mixed_or_missing_model_versions" in item for item in result["errors"])


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_model_suite_compatibility_requires_new_statistical_contracts(tmp_path: Path) -> None:
    models = tmp_path / "training" / "models"
    for task in ("d1", "w1", "q1"):
        status = "unavailable_daily_resolution" if task == "d1" else "trained"
        _write_json(
            models / f"{task}_champion.json",
            {
                "model_name": f"baseline_{task}",
                "version": "v2",
                "params": {
                    "schema_version": 2,
                    "floor": {"global": 0.01},
                    "ceiling": {"global": 0.02},
                    "timing": {
                        "schema_version": 2,
                        "horizon": task,
                        "status": status,
                    },
                },
            },
        )
    _write_json(
        models / "value_champion.json",
        {
            "model_name": "m3_value_quantile",
            "version": "v2",
            "params": {"schema_version": 2, "target_space": "relative_floor_delta"},
        },
    )
    _write_json(
        models / "timing_champion.json",
        {
            "model_name": "m3_timing_ordinal",
            "version": "v2",
            "params": {
                "schema_version": 2,
                "model_type": "multinomial_logistic",
                "class_count": 13,
            },
        },
    )

    compatible = model_suite_compatibility(tmp_path)
    assert compatible["status"] == "OK"
    assert compatible["compatible"] is True

    _write_json(
        models / "value_champion.json",
        {
            "model_name": "legacy_absolute",
            "version": "v1",
            "params": {"schema_version": 1},
        },
    )
    incompatible = model_suite_compatibility(tmp_path)
    assert incompatible["status"] == "RETRAIN_REQUIRED"
    assert incompatible["tasks"]["value"]["compatible"] is False


def test_blocked_publication_must_not_expose_forecast_rows(tmp_path: Path) -> None:
    site_data = tmp_path / "site" / "data"
    _write_json(
        site_data / "audit.json",
        {
            "safe_to_deploy": True,
            "publishable_forecasts": False,
            "status": "BLOCKED",
            "expected_prediction_rows": 4,
        },
    )
    _write_json(site_data / "forecasts.json", {"rows": []})
    _write_json(site_data / "opportunities.json", [])
    assert validate_published_site(site_data)["status"] == "BLOCKED"

    _write_json(
        site_data / "forecasts.json",
        {"rows": [_row("AAA", "d1", "2026-08-21T20:00:00+00:00")]},
    )
    with pytest.raises(RuntimeError, match="must suppress actionable forecast rows"):
        validate_published_site(site_data)


def test_audit_overlay_is_injected_once(tmp_path: Path) -> None:
    site = tmp_path / "site"
    site.mkdir()
    html = site / "index.html"
    html.write_text("<html><body><main>ok</main></body></html>", encoding="utf-8")

    assert _inject_audit_script(site) == 1
    assert _inject_audit_script(site) == 0
    text = html.read_text(encoding="utf-8")
    assert text.count("assets/audit.js") == 1


def test_pages_security_injects_strict_csp_once(tmp_path: Path) -> None:
    site = tmp_path / "site"
    site.mkdir()
    html = site / "index.html"
    html.write_text(
        '<html><head><meta charset="utf-8"/></head><body><script type="module" src="assets/app.js"></script></body></html>',
        encoding="utf-8",
    )

    assert harden_site_html(site)["changed"] == 1
    assert harden_site_html(site)["changed"] == 0
    assert validate_site_html_security(site)["validated"] == 1
    text = html.read_text(encoding="utf-8")
    assert text.count(CSP_META) == 1
    assert "script-src 'self'" in text
    assert "object-src 'none'" in text


def test_pages_security_rejects_inline_event_handler(tmp_path: Path) -> None:
    site = tmp_path / "site"
    site.mkdir()
    html = site / "index.html"
    html.write_text(
        '<html><head></head><body><img src="x" onerror="alert(1)"></body></html>',
        encoding="utf-8",
    )
    harden_site_html(site)
    with pytest.raises(RuntimeError, match="inline event handler forbidden"):
        validate_site_html_security(site)


def test_generated_pages_json_is_not_versioned_source_of_truth() -> None:
    ignore = Path(".gitignore").read_text(encoding="utf-8")
    assert "site/data/*.json" in ignore
    assert "docs/data/*.json" in ignore
    assert not Path("site/data/forecasts.json").exists()
    assert not Path("docs/data/forecasts.json").exists()


def test_pages_workflow_publishes_branch_head_not_upstream_start_sha() -> None:
    workflow = Path(".github/workflows/pages.yml").read_text(encoding="utf-8")
    assert "github.event.workflow_run.head_branch" in workflow
    assert "git reset --hard origin/main" in workflow
    assert "utils.pages_publish" in workflow
    assert "utils.pages_security" in workflow
    assert "workflow_run.head_sha || github.sha" not in workflow
