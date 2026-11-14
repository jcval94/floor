"""Frozen adapter contract tests, including exact reuse of FLOOR inference."""

from datetime import datetime, timedelta, timezone
import pytest

pytest.importorskip("mcp")
pytest.importorskip("sklearn")
from ai_lab.contracts import Plan
from ai_lab.frozen_models import (
    REQUIRED_INPUTS,
    evaluate_frozen,
    validate_frozen_inputs,
    resolve,
)
from ai_lab.recipes import validate_rows
from ai_lab.store import Store
from models.classic_horizon_predictor import (
    build_runtime_features,
    predict_family_delta,
    model_family,
)


def fixture():
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    rows = []
    for i in range(310):
        ts = (start + timedelta(days=i)).isoformat()
        r = {f: 0.01 for f in REQUIRED_INPUTS}
        r.update(
            {
                "symbol": "SYNTHETIC",
                "timestamp": ts,
                "available_at": ts,
                "close": 100.0,
                "open": 99.0,
                "high": 102.0,
                "low": 98.0,
                "volume": 1000.0,
                "atr_14": 2.0,
                "floor_w1": 97.0 + 0.1 * (i % 4),
                "ceiling_w1": 104.0,
                "target_end_date_w1": (start + timedelta(days=i + 5)).isoformat(),
                "horizon_complete_w1": True,
                "feature_available_at": {f: ts for f in REQUIRED_INPUTS},
            }
        )
        rows.append(r)
    manifest = {
        "target": "floor_w1",
        "horizon_sessions": 5,
        "features": ["atr_14"],
        "materialized_features": list(REQUIRED_INPUTS),
        "feature_definition_version": "floor-feature-builder/full-v2-benchmark-joined",
        "temporal_evidence": "synthetic_fixture",
    }
    plan = Plan(
        question="Compara los tres modelos congelados",
        recipe="frozen_model_comparison",
        dataset_id="0" * 64,
        features=["atr_14"],
    )
    return rows, manifest, plan


def test_exact_serving_head_and_no_fake_oos():
    rows, m, p = fixture()
    validate_rows(rows, m, p)
    result = evaluate_frozen(rows, m, p)
    assert result["comparability"]["out_of_sample_verified"] is False
    assert (
        result["comparability"]["evidence_type"]
        == "post_artifact_reconstructed_holdout"
    )
    assert len(result["metrics"]) == 5
    assert len({r["sample_size"] for r in result["metrics"]}) == 1
    model, artifact = resolve(p.frozen_model_ids)[0]
    record = next(r for r in result["predictions"] if r["model"] == model["id"])
    row = next(r for r in rows if r["timestamp"] == record["timestamp"])
    expected = predict_family_delta(
        model_family(artifact["model_name"]),
        artifact["params"]["floor"],
        build_runtime_features(row),
    )
    assert record["prediction"] == expected
    assert all(
        r["timestamp"] > result["comparability"]["cutoff"]
        for r in result["predictions"]
    )
    assert all(r["fit_seconds"] == 0 for r in result["costs"])


def test_unknown_path_and_missing_features_fail_closed():
    rows, m, p = fixture()
    with pytest.raises(ValueError, match="FROZEN_MODEL_NOT_REGISTERED"):
        resolve(["../../etc/passwd", "w1_champion"])
    with pytest.raises(ValueError, match="MATERIALIZATION_REQUIRED"):
        validate_frozen_inputs(rows, {**m, "materialized_features": []}, p)
    rows[-1]["feature_available_at"]["atr_14"] = "2027-01-01T00:00:00+00:00"
    with pytest.raises(ValueError, match="AVAILABILITY_INVALID"):
        validate_frozen_inputs(rows, m, p)


def test_repeated_frozen_evaluation_is_deterministic():
    rows, m, p = fixture()
    a, b = evaluate_frozen(rows, m, p), evaluate_frozen(rows, m, p)
    assert a["metrics"] == b["metrics"]
    assert a["predictions"] == b["predictions"]
    assert a["model_assets"] == b["model_assets"]


def test_model_and_config_assets_are_part_of_acceptance(tmp_path):
    from ai_lab.provenance import code_identity

    identity = code_identity()
    assert "data/training/models/w1_champion.json" in identity["asset_hashes"]
    assert "config/costs.yaml" in identity["asset_hashes"]
    rows, m, p = fixture()
    s = Store(tmp_path)
    dataset = s.register(rows, m)
    run = s.submit(p.model_copy(update={"dataset_id": dataset}), "operator", "one")
    assert (
        s.get(run["run_id"])["acceptance_provenance"]["asset_hashes"]
        == identity["asset_hashes"]
    )


def test_idempotence_survives_added_optional_contract_fields(tmp_path):
    import json

    rows, manifest, _ = fixture()
    store = Store(tmp_path)
    dataset = store.register(rows, manifest)
    plan = Plan(
        question="Compara los modelos registrados",
        recipe="model_comparison",
        dataset_id=dataset,
        features=["atr_14"],
    )
    old = store.submit(plan, "operator", "upgrade-retry")
    with store.transaction() as c:
        payload = json.loads(
            c.execute(
                "SELECT plan FROM runs WHERE run_id=?", (old["run_id"],)
            ).fetchone()[0]
        )
        payload.pop("frozen_model_ids")  # optional field did not exist in contract 1.0
        c.execute(
            "UPDATE runs SET plan=? WHERE run_id=?",
            (json.dumps(payload), old["run_id"]),
        )
    retried = store.submit(plan, "operator", "upgrade-retry")
    assert retried["run_id"] == old["run_id"]
    with store.connect() as c:
        assert c.execute("SELECT count(*) FROM runs").fetchone()[0] == 1
