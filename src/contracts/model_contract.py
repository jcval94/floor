from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MODEL_CONTRACTS_PATH = REPO_ROOT / "config" / "model_contracts.json"
MODEL_CONTRACT_SCHEMA_VERSION = 1


def _load_registry(path: Path = DEFAULT_MODEL_CONTRACTS_PATH) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if int(payload.get("schema_version") or 0) != MODEL_CONTRACT_SCHEMA_VERSION:
        raise ValueError(
            "model contract registry schema mismatch: "
            f"expected={MODEL_CONTRACT_SCHEMA_VERSION} actual={payload.get('schema_version')}"
        )
    tasks = payload.get("tasks")
    if not isinstance(tasks, dict) or not tasks:
        raise ValueError("model contract registry has no tasks")
    return payload


def _task_spec(task: str, path: Path = DEFAULT_MODEL_CONTRACTS_PATH) -> dict[str, Any]:
    registry = _load_registry(path)
    spec = registry["tasks"].get(task)
    if not isinstance(spec, dict):
        raise ValueError(f"model task has no registered contract: {task}")
    return spec


def build_model_contract(
    task: str,
    *,
    registry_path: Path = DEFAULT_MODEL_CONTRACTS_PATH,
) -> dict[str, Any]:
    """Build the immutable semantic declaration attached to newly trained artifacts."""

    spec = _task_spec(task, registry_path)
    return {
        "schema_version": MODEL_CONTRACT_SCHEMA_VERSION,
        "task": task,
        "contract_id": str(spec["contract_id"]),
        "horizon": str(spec["horizon"]),
        "semantic_role": str(spec["semantic_role"]),
        "directional": bool(spec.get("directional", False)),
        "serving_outputs": deepcopy(spec.get("serving_outputs", {})),
    }


def attach_model_contract(
    payload: dict[str, Any],
    task: str,
    *,
    registry_path: Path = DEFAULT_MODEL_CONTRACTS_PATH,
) -> dict[str, Any]:
    out = deepcopy(payload)
    out["model_contract"] = build_model_contract(task, registry_path=registry_path)
    return out


def validate_model_artifact_contract(
    task: str,
    artifact: dict[str, Any],
    *,
    registry_path: Path = DEFAULT_MODEL_CONTRACTS_PATH,
    allow_legacy: bool = True,
) -> dict[str, Any]:
    """Validate semantic + structural requirements for one model artifact.

    Existing pre-contract champions remain readable during migration, but a
    declared contract is never allowed to be unknown or incomplete. Newly
    trained artifacts attach a contract and therefore must satisfy the stricter
    requirements, including risk geometry for classic horizons.
    """

    spec = _task_spec(task, registry_path)
    errors: list[str] = []
    contract = artifact.get("model_contract")
    if contract is None:
        if allow_legacy:
            return {
                "valid": True,
                "status": "legacy_compatible",
                "task": task,
                "contract_id": spec.get("contract_id"),
                "errors": [],
            }
        return {
            "valid": False,
            "status": "missing_contract",
            "task": task,
            "contract_id": spec.get("contract_id"),
            "errors": ["missing model_contract"],
        }

    if not isinstance(contract, dict):
        errors.append("model_contract must be an object")
        contract = {}

    if int(contract.get("schema_version") or 0) != MODEL_CONTRACT_SCHEMA_VERSION:
        errors.append("model_contract schema_version mismatch")
    if str(contract.get("task") or "") != task:
        errors.append("model_contract task mismatch")
    if str(contract.get("contract_id") or "") != str(spec.get("contract_id") or ""):
        errors.append("model_contract contract_id mismatch")
    if str(contract.get("horizon") or "") != str(spec.get("horizon") or ""):
        errors.append("model_contract horizon mismatch")
    if str(contract.get("semantic_role") or "") != str(spec.get("semantic_role") or ""):
        errors.append("model_contract semantic_role mismatch")
    if bool(contract.get("directional", False)) != bool(spec.get("directional", False)):
        errors.append("model_contract directional semantic mismatch")

    for key in ("model_name", "version", "params", "metrics"):
        if key not in artifact:
            errors.append(f"missing top-level field: {key}")

    params = artifact.get("params")
    if not isinstance(params, dict):
        errors.append("params must be an object")
        params = {}

    expected_schema = int(spec.get("params_schema_version") or 0)
    if expected_schema and int(params.get("schema_version") or 0) != expected_schema:
        errors.append(
            f"params schema_version mismatch: expected={expected_schema}"
        )

    for key in spec.get("required_params", []):
        if key not in params:
            errors.append(f"missing required params field: {key}")
    for key in spec.get("required_new_params", []):
        if key not in params:
            errors.append(f"missing new-artifact params field: {key}")

    if task in {"d1", "w1", "q1"}:
        risk_raw = params.get("risk_geometry")
        if not isinstance(risk_raw, dict):
            if "risk_geometry" in params:
                errors.append("risk_geometry must be an object")
            risk = {}
        else:
            risk = risk_raw
    else:
        risk = {}

    if risk:
        risk_method = str(risk.get("method") or "")
        if risk_method == "validation_residual_quantile":
            target_key = "target_marginal_coverage"
        elif risk_method == "joint_validation_conformal_max_residual":
            target_key = "target_joint_coverage"
        else:
            target_key = ""
            errors.append(
                "risk_geometry method must be validation_residual_quantile "
                "or joint_validation_conformal_max_residual"
            )
        if target_key:
            try:
                target_coverage = float(str(risk.get(target_key)))
            except (TypeError, ValueError):
                target_coverage = -1.0
            if not 0.5 < target_coverage < 1.0:
                errors.append(
                    f"risk_geometry {target_key} must be in (0.5, 1)"
                )
        for key in ("floor_delta_addon", "ceiling_delta_addon"):
            try:
                value = float(str(risk.get(key)))
            except (TypeError, ValueError):
                value = -1.0
            if value < 0.0:
                errors.append(f"risk_geometry {key} must be non-negative")
        try:
            calibration_rows = int(str(risk.get("calibration_rows")))
        except (TypeError, ValueError):
            calibration_rows = 0
        if calibration_rows <= 0:
            errors.append("risk_geometry calibration_rows must be positive")

    return {
        "valid": not errors,
        "status": "declared_valid" if not errors else "declared_invalid",
        "task": task,
        "contract_id": spec.get("contract_id"),
        "errors": errors,
    }
