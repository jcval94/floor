from __future__ import annotations

import argparse
import json
import math
import re
from copy import deepcopy
from pathlib import Path
from typing import Any

from contracts.model_contract import (
    attach_model_contract,
    validate_model_artifact_contract,
)
from models.classic_horizon_predictor import (
    model_family,
    predict_family_delta,
    validate_family_params,
)
from models.horizon_timing import ALLOWED_CLASSES
from models.train_classic_horizons import (
    FEATURES_BY_FAMILY,
    HORIZON_TARGETS,
    _load_rows,
    _fit_risk_geometry,
    _metrics,
    _prepare_rows,
    _risk_geometry_metrics,
    _split,
)
from models.temporal_cv import purged_chronological_calibration_split


TRUTHFUL_PREFIXES = {
    "robust_range_v3": "robust_range_v3_",
    "regime_median": "regime_median_",
    "boosted_stumps": "boosted_stumps_",
    "sequence_linear": "sequence_linear_",
    "regularized_linear": "regularized_linear_",
}
SCORING_VERSION = "classic-boundary-pareto-v3"
MAX_INTERVAL_COVERAGE_REGRESSION = 0.05


def _mapping(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _load_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    return payload if isinstance(payload, dict) else None


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    tmp.replace(path)


def _slug(value: object) -> str:
    text = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "unknown"))
    return text[:96] or "unknown"


def _number(value: object) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if not isinstance(value, (int, float, str)):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _artifact_compatible(artifact: dict[str, Any], horizon: str) -> tuple[bool, str]:
    if horizon not in HORIZON_TARGETS:
        return False, f"unsupported_horizon:{horizon}"

    contract_check = validate_model_artifact_contract(
        horizon, artifact, allow_legacy=True
    )
    if not contract_check["valid"]:
        return False, "invalid_model_contract:" + ",".join(
            contract_check["errors"]
        )

    model_name = str(artifact.get("model_name") or "")
    family = model_family(model_name)
    truthful_prefix = TRUTHFUL_PREFIXES.get(family)
    if not family or truthful_prefix is None or not model_name.startswith(truthful_prefix):
        return False, "model_name_does_not_match_implemented_family"

    params = _mapping(artifact.get("params"))
    if int(params.get("schema_version") or 0) != 2:
        return False, "missing_schema_version_2"

    floor_params = _mapping(params.get("floor"))
    ceiling_params = _mapping(params.get("ceiling"))
    if not floor_params or not ceiling_params:
        return False, "missing_floor_or_ceiling_params"
    try:
        validate_family_params(family, floor_params)
        validate_family_params(family, ceiling_params)
    except ValueError as exc:
        return False, f"invalid_family_params:{exc}"

    calibration = _mapping(params.get("confidence_calibration"))
    if calibration.get("method") != "validation_empirical_interval_breach":
        return False, "missing_empirical_confidence_calibration"
    breach = _number(calibration.get("breach_probability"))
    rows = _number(calibration.get("evaluation_rows"))
    if breach is None or not 0.0 <= breach <= 1.0 or rows is None or rows <= 0:
        return False, "invalid_empirical_confidence_calibration"

    timing = _mapping(params.get("timing"))
    if int(timing.get("schema_version") or 0) != 2:
        return False, "timing_schema_mismatch"
    if str(timing.get("horizon") or "") != horizon:
        return False, "timing_horizon_mismatch"
    if list(timing.get("classes") or []) != list(ALLOWED_CLASSES[horizon]):
        return False, "timing_class_domain_mismatch"

    status = str(timing.get("status") or "")
    if horizon == "d1":
        if status not in {"trained", "unavailable_daily_resolution"}:
            return False, f"invalid_d1_timing_status:{status or 'missing'}"
    elif status != "trained":
        return False, f"timing_not_trained:{status or 'missing'}"

    if status == "trained":
        for side in ("floor", "ceiling"):
            side_params = _mapping(timing.get(side))
            if not _mapping(side_params.get("global")):
                return False, f"timing_{side}_distribution_missing"

    return True, "compatible"


def _evaluation_rows(dataset_path: Path, horizon: str) -> list[Any]:
    rows = _load_rows(dataset_path)
    _train_raw, validation_raw = _split(rows, horizon)
    floor_col, ceiling_col = HORIZON_TARGETS[horizon]
    feature_names = tuple(
        sorted({name for values in FEATURES_BY_FAMILY.values() for name in values})
    )
    prepared = _prepare_rows(validation_raw, floor_col, ceiling_col, feature_names)
    if not prepared:
        raise ValueError(
            f"No leakage-safe validation rows for classic champion gate horizon={horizon}"
        )
    return prepared


def _evaluate_artifact(
    artifact: dict[str, Any],
    horizon: str,
    evaluation: list[Any],
) -> dict[str, float]:
    compatible, reason = _artifact_compatible(artifact, horizon)
    if not compatible:
        raise ValueError(f"Incompatible classic artifact horizon={horizon}: {reason}")

    family = model_family(str(artifact.get("model_name") or ""))
    params = _mapping(artifact.get("params"))
    floor_params = _mapping(params.get("floor"))
    ceiling_params = _mapping(params.get("ceiling"))
    floor_predictions = [
        predict_family_delta(family, floor_params, item.features, validate=False)
        for item in evaluation
    ]
    ceiling_predictions = [
        predict_family_delta(family, ceiling_params, item.features, validate=False)
        for item in evaluation
    ]
    return _metrics(evaluation, floor_predictions, ceiling_predictions)


def _validation_calibration_and_evaluation_rows(
    dataset_path: Path,
    horizon: str,
) -> tuple[list[Any], list[Any]]:
    rows = _load_rows(dataset_path)
    _train_raw, validation_raw = _split(rows, horizon)
    calibration_raw, evaluation_raw = purged_chronological_calibration_split(
        validation_raw,
        target_end_field=f"target_end_date_{horizon}",
    )
    floor_col, ceiling_col = HORIZON_TARGETS[horizon]
    feature_names = tuple(
        sorted({name for values in FEATURES_BY_FAMILY.values() for name in values})
    )
    calibration = _prepare_rows(
        calibration_raw, floor_col, ceiling_col, feature_names
    )
    evaluation = _prepare_rows(
        evaluation_raw, floor_col, ceiling_col, feature_names
    )
    if not calibration or not evaluation:
        raise ValueError(
            "Classic contract migration requires leakage-safe calibration and "
            f"evaluation rows horizon={horizon}"
        )
    return calibration, evaluation


def _artifact_boundary_predictions(
    artifact: dict[str, Any],
    rows: list[Any],
) -> tuple[list[float], list[float]]:
    family = model_family(str(artifact.get("model_name") or ""))
    params = _mapping(artifact.get("params"))
    floor_params = _mapping(params.get("floor"))
    ceiling_params = _mapping(params.get("ceiling"))
    floor_predictions = [
        predict_family_delta(family, floor_params, item.features, validate=False)
        for item in rows
    ]
    ceiling_predictions = [
        predict_family_delta(family, ceiling_params, item.features, validate=False)
        for item in rows
    ]
    return floor_predictions, ceiling_predictions


def _migrate_incumbent_risk_contract(
    artifact: dict[str, Any],
    *,
    dataset_path: Path,
    horizon: str,
    version: str,
) -> dict[str, Any]:
    """Recalibrate only risk geometry while preserving the central predictor.

    This supports both first-time contract migration and later risk-calibration
    upgrades. Bounds are calibrated against the incumbent's unchanged central
    floor/ceiling parameters on an earlier OOT validation block and evaluated
    on the later block.
    """

    calibration, evaluation = _validation_calibration_and_evaluation_rows(
        dataset_path, horizon
    )
    cal_floor, cal_ceiling = _artifact_boundary_predictions(artifact, calibration)
    risk_geometry = _fit_risk_geometry(
        calibration,
        cal_floor,
        cal_ceiling,
        horizon=horizon,
    )
    eval_floor, eval_ceiling = _artifact_boundary_predictions(artifact, evaluation)
    risk_metrics = _risk_geometry_metrics(
        evaluation,
        eval_floor,
        eval_ceiling,
        risk_geometry,
    )

    migrated = deepcopy(artifact)
    previous_version = str(migrated.get("version") or "")
    migrated["version"] = f"{version}-risk-contract"
    params = deepcopy(_mapping(migrated.get("params")))
    params["risk_geometry"] = risk_geometry
    migrated["params"] = params
    metrics = deepcopy(_mapping(migrated.get("metrics")))
    metrics.update(risk_metrics)
    migrated["metrics"] = metrics
    migrated["contract_migration"] = {
        "from_version": previous_version,
        "central_model_preserved": True,
        "central_params_preserved": True,
        "risk_only_migration": True,
        "risk_schema_version": risk_geometry.get("schema_version"),
        "risk_calibration_policy_version": risk_geometry.get(
            "calibration_policy_version"
        ),
        "calibration_rows": len(calibration),
        "evaluation_rows": len(evaluation),
        "test_used_for_selection": False,
    }
    migrated = attach_model_contract(migrated, horizon)

    check = validate_model_artifact_contract(
        horizon, migrated, allow_legacy=False
    )
    if not check["valid"]:
        raise RuntimeError(
            "Migrated incumbent failed model contract: "
            + ",".join(check["errors"])
        )
    return migrated


def _migrate_legacy_incumbent_contract(
    artifact: dict[str, Any],
    *,
    dataset_path: Path,
    horizon: str,
    version: str,
) -> dict[str, Any]:
    return _migrate_incumbent_risk_contract(
        artifact,
        dataset_path=dataset_path,
        horizon=horizon,
        version=version,
    )


def _score(metrics: dict[str, float]) -> tuple[float, float]:
    spread = float(metrics.get("mae_spread_pct", math.inf))
    boundaries = float(metrics.get("mae_floor_pct", math.inf)) + float(
        metrics.get("mae_ceiling_pct", math.inf)
    )
    return spread, boundaries


def _strictly_dominates_boundaries_and_spread(
    candidate: dict[str, float],
    existing: dict[str, float],
) -> bool:
    """Require a challenger to improve floor, ceiling, and range width separately."""

    keys = ("mae_floor_pct", "mae_ceiling_pct", "mae_spread_pct")
    return all(float(candidate.get(key, math.inf)) < float(existing.get(key, math.inf)) for key in keys)


def _score_json(score: tuple[float, float] | None) -> list[float] | None:
    if score is None:
        return None
    return [round(score[0], 12), round(score[1], 12)]


def _update_competition(
    registry_dir: Path,
    horizon: str,
    *,
    decision: str,
    candidate: dict[str, Any],
    active: dict[str, Any],
    candidate_score: tuple[float, float],
    existing_score: tuple[float, float] | None,
    reason: str,
) -> None:
    path = registry_dir / f"{horizon}_competition.json"
    payload = _load_json(path) or {"horizon": horizon}
    payload["registry_decision"] = decision
    payload["registry_reason"] = reason
    payload["candidate_model_id"] = candidate.get("model_name")
    payload["candidate_version"] = candidate.get("version")
    payload["active_champion_model_id"] = active.get("model_name")
    payload["active_champion_version"] = active.get("version")
    payload["candidate_current_validation_score"] = _score_json(candidate_score)
    payload["existing_current_validation_score"] = _score_json(existing_score)
    payload["promotion_scoring_version"] = SCORING_VERSION
    payload["promotion_validation_split"] = "validation"
    payload["test_used_for_promotion"] = False
    _write_json_atomic(path, payload)


def gate_one_horizon(
    *,
    dataset_path: Path,
    registry_dir: Path,
    previous_dir: Path,
    horizon: str,
    version: str,
) -> dict[str, Any]:
    candidate_path = registry_dir / f"{horizon}_champion.json"
    previous_path = previous_dir / f"{horizon}_champion.json"
    candidate = _load_json(candidate_path)
    if candidate is None:
        raise RuntimeError(f"Classic trainer did not produce {candidate_path}")

    challenger_path: Path | None = None
    candidate_ok, candidate_reason = _artifact_compatible(candidate, horizon)
    previous = _load_json(previous_path)
    previous_ok = False
    previous_reason = "missing_previous_champion"
    if previous is not None:
        previous_ok, previous_reason = _artifact_compatible(previous, horizon)

    if not candidate_ok:
        challenger_path = registry_dir / (
            f"{horizon}_challenger_invalid_{_slug(version)}.json"
        )
        candidate["selection"] = {
            "decision": "reject_invalid_candidate",
            "reason": candidate_reason,
            "scoring_version": SCORING_VERSION,
            "test_used_for_selection": False,
        }
        _write_json_atomic(challenger_path, candidate)
        if previous is not None and previous_ok:
            _write_json_atomic(candidate_path, previous)
            _update_competition(
                registry_dir,
                horizon,
                decision="reject_invalid_candidate",
                candidate=candidate,
                active=previous,
                candidate_score=(math.inf, math.inf),
                existing_score=None,
                reason=candidate_reason,
            )
            return {
                "horizon": horizon,
                "decision": "reject_invalid_candidate",
                "reason": candidate_reason,
                "active_version": previous.get("version"),
                "challenger_path": str(challenger_path),
            }
        raise RuntimeError(
            f"Invalid {horizon} candidate and no compatible previous champion: {candidate_reason}"
        )

    evaluation = _evaluation_rows(dataset_path, horizon)
    candidate_metrics = _evaluate_artifact(candidate, horizon, evaluation)
    candidate_score = _score(candidate_metrics)
    existing_score: tuple[float, float] | None = None

    if previous is None:
        decision = "promote_first"
        reason = "No previous classic champion exists."
        active = candidate
    elif not previous_ok:
        decision = "promote_schema_migration"
        reason = f"Previous champion is statistically/structurally incompatible: {previous_reason}."
        active = candidate
    else:
        existing_metrics = _evaluate_artifact(previous, horizon, evaluation)
        existing_score = _score(existing_metrics)
        coverage_delta = float(candidate_metrics["test_interval_coverage"]) - float(
            existing_metrics["test_interval_coverage"]
        )
        strict_error_dominance = _strictly_dominates_boundaries_and_spread(
            candidate_metrics, existing_metrics
        )
        coverage_guard_pass = coverage_delta >= -MAX_INTERVAL_COVERAGE_REGRESSION
        candidate_contract = validate_model_artifact_contract(
            horizon, candidate, allow_legacy=True
        )
        previous_contract = validate_model_artifact_contract(
            horizon, previous, allow_legacy=True
        )
        legacy_contract_migration = (
            candidate_contract["status"] == "declared_valid"
            and previous_contract["status"] == "legacy_compatible"
        )
        candidate_risk = _mapping(
            _mapping(candidate.get("params")).get("risk_geometry")
        )
        previous_risk = _mapping(
            _mapping(previous.get("params")).get("risk_geometry")
        )
        candidate_risk_schema = int(candidate_risk.get("schema_version") or 0)
        previous_risk_schema = int(previous_risk.get("schema_version") or 0)
        risk_contract_upgrade = (
            candidate_contract["status"] == "declared_valid"
            and candidate_risk.get("method")
            == "joint_validation_conformal_max_residual"
            and (
                previous_risk.get("method")
                != "joint_validation_conformal_max_residual"
                or candidate_risk_schema > previous_risk_schema
            )
        )
        if strict_error_dominance and coverage_guard_pass:
            decision = "promote"
            reason = (
                "Candidate strictly improved floor MAE, ceiling MAE, and spread MAE "
                "while keeping interval-coverage regression within five percentage "
                "points on the same current leakage-safe validation split: "
                f"existing={existing_score} candidate={candidate_score}."
            )
            active = candidate
        elif legacy_contract_migration:
            decision = "promote_contract_migration"
            reason = (
                "Candidate did not earn central-model promotion, but the incumbent "
                "is pre-contract. Preserve the incumbent central predictor exactly "
                "and calibrate risk geometry on leakage-safe validation data so the "
                "serving registry can migrate without accepting a central-MAE regression."
            )
            active = _migrate_incumbent_risk_contract(
                previous,
                dataset_path=dataset_path,
                horizon=horizon,
                version=version,
            )
        elif risk_contract_upgrade:
            decision = "promote_risk_calibration_migration"
            reason = (
                "Central challenger did not earn promotion, but it carries a newer "
                "risk-calibration contract. Preserve the incumbent central predictor "
                "exactly and recalibrate only its risk geometry on leakage-safe "
                "temporal calibration data."
            )
            active = _migrate_incumbent_risk_contract(
                previous,
                dataset_path=dataset_path,
                horizon=horizon,
                version=version,
            )
        else:
            decision = "challenger_only"
            reason = (
                "Candidate did not strictly improve all of floor MAE, ceiling MAE, and "
                "spread MAE with the five-point coverage guard on the same current "
                "leakage-safe validation split: "
                f"existing={existing_score} candidate={candidate_score}."
            )
            active = previous

    selection = {
        "decision": decision,
        "reason": reason,
        "scoring_version": SCORING_VERSION,
        "selection_split": "validation",
        "test_used_for_selection": False,
        "candidate_current_validation_score": _score_json(candidate_score),
        "existing_current_validation_score": _score_json(existing_score),
        "candidate_current_validation_rows": len(evaluation),
        "max_interval_coverage_regression": MAX_INTERVAL_COVERAGE_REGRESSION,
    }
    if previous is not None and previous_ok:
        selection["candidate_interval_coverage_delta"] = (
            float(candidate_metrics["test_interval_coverage"])
            - float(existing_metrics["test_interval_coverage"])
        )

    if decision == "challenger_only":
        challenger_path = registry_dir / f"{horizon}_challenger_{_slug(version)}.json"
        candidate["selection"] = selection
        _write_json_atomic(challenger_path, candidate)
        assert previous is not None
        _write_json_atomic(candidate_path, previous)
    else:
        if previous is not None:
            archived_path = registry_dir / (
                f"{horizon}_champion_archived_{_slug(previous.get('version'))}.json"
            )
            if not archived_path.exists():
                _write_json_atomic(archived_path, previous)

        if decision in {
            "promote_contract_migration",
            "promote_risk_calibration_migration",
        }:
            challenger_path = registry_dir / f"{horizon}_challenger_{_slug(version)}.json"
            challenger_selection = {
                **selection,
                "decision": "challenger_only_central_model",
                "reason": (
                    "Central challenger did not strictly dominate; incumbent central "
                    "predictor was preserved while only its risk calibration migrated."
                ),
            }
            candidate["selection"] = challenger_selection
            _write_json_atomic(challenger_path, candidate)
            active["selection"] = selection
            _write_json_atomic(candidate_path, active)
        else:
            challenger_path = None
            candidate["selection"] = selection
            _write_json_atomic(candidate_path, candidate)
            active = candidate

    _update_competition(
        registry_dir,
        horizon,
        decision=decision,
        candidate=candidate,
        active=active,
        candidate_score=candidate_score,
        existing_score=existing_score,
        reason=reason,
    )
    return {
        "horizon": horizon,
        "decision": decision,
        "reason": reason,
        "candidate_score": _score_json(candidate_score),
        "existing_score": _score_json(existing_score),
        "active_version": active.get("version"),
        "active_model_name": active.get("model_name"),
        "challenger_path": str(challenger_path) if challenger_path else None,
    }


def run_gate(
    dataset_path: Path,
    registry_dir: Path,
    previous_dir: Path,
    tasks: list[str],
    version: str,
) -> list[dict[str, Any]]:
    results = []
    for horizon in tasks:
        if horizon not in HORIZON_TARGETS:
            raise ValueError(f"Unsupported classic champion gate task: {horizon}")
        results.append(
            gate_one_horizon(
                dataset_path=dataset_path,
                registry_dir=registry_dir,
                previous_dir=previous_dir,
                horizon=horizon,
                version=version,
            )
        )
    return results


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compare classic candidates and historical champions on the same validation split"
    )
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--registry-dir", required=True)
    parser.add_argument("--previous-dir", required=True)
    parser.add_argument("--tasks", required=True)
    parser.add_argument("--version", required=True)
    args = parser.parse_args()
    tasks = [part.strip() for part in args.tasks.split(",") if part.strip()]
    results = run_gate(
        Path(args.dataset),
        Path(args.registry_dir),
        Path(args.previous_dir),
        tasks,
        args.version,
    )
    print(json.dumps({"results": results}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
