from __future__ import annotations

import argparse
import json
import math
import re
from pathlib import Path
from typing import Any

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
    _metrics,
    _prepare_rows,
    _split,
)


TRUTHFUL_PREFIXES = {
    "regime_median": "regime_median_",
    "boosted_stumps": "boosted_stumps_",
    "sequence_linear": "sequence_linear_",
    "regularized_linear": "regularized_linear_",
}
SCORING_VERSION = "classic-current-validation-v1"


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
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _artifact_compatible(artifact: dict[str, Any], horizon: str) -> tuple[bool, str]:
    if horizon not in HORIZON_TARGETS:
        return False, f"unsupported_horizon:{horizon}"

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
        predict_family_delta(family, floor_params, item.features)
        for item in evaluation
    ]
    ceiling_predictions = [
        predict_family_delta(family, ceiling_params, item.features)
        for item in evaluation
    ]
    return _metrics(evaluation, floor_predictions, ceiling_predictions)


def _score(metrics: dict[str, float]) -> tuple[float, float]:
    spread = float(metrics.get("mae_spread_pct", math.inf))
    boundaries = float(metrics.get("mae_floor_pct", math.inf)) + float(
        metrics.get("mae_ceiling_pct", math.inf)
    )
    return spread, boundaries


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
        if candidate_score < existing_score:
            decision = "promote"
            reason = (
                "Candidate improved on the same current leakage-safe validation split: "
                f"existing={existing_score} candidate={candidate_score}."
            )
            active = candidate
        else:
            decision = "challenger_only"
            reason = (
                "Existing champion is at least as good on the same current leakage-safe "
                f"validation split: existing={existing_score} candidate={candidate_score}."
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
    }

    if decision == "challenger_only":
        challenger_path = registry_dir / f"{horizon}_challenger_{_slug(version)}.json"
        candidate["selection"] = selection
        _write_json_atomic(challenger_path, candidate)
        assert previous is not None
        _write_json_atomic(candidate_path, previous)
    else:
        challenger_path = None
        if previous is not None:
            archived_path = registry_dir / (
                f"{horizon}_champion_archived_{_slug(previous.get('version'))}.json"
            )
            if not archived_path.exists():
                _write_json_atomic(archived_path, previous)
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
