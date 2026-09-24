from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONTRACT = ROOT / "config" / "resource_ownership.json"
DEFAULT_WORKFLOWS = ROOT / ".github" / "workflows"


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"ownership contract must be an object: {path}")
    return payload


def discover_resource_writers(
    workflow_dir: Path,
    markers: list[str],
) -> set[str]:
    if not markers:
        raise RuntimeError("resource ownership markers must not be empty")

    writers: set[str] = set()
    for path in sorted([*workflow_dir.glob("*.yml"), *workflow_dir.glob("*.yaml")]):
        text = path.read_text(encoding="utf-8")
        if all(marker in text for marker in markers):
            writers.add(path.name)
    return writers


def validate_resource_ownership(
    contract_path: Path = DEFAULT_CONTRACT,
    workflow_dir: Path = DEFAULT_WORKFLOWS,
) -> list[str]:
    contract = _load_json(contract_path)
    if int(contract.get("schema_version") or 0) != 1:
        return ["resource ownership contract must use schema_version=1"]

    resources = contract.get("resources")
    if not isinstance(resources, dict) or not resources:
        return ["resource ownership contract has no resources"]

    errors: list[str] = []
    for resource, raw_spec in sorted(resources.items()):
        if not isinstance(raw_spec, dict):
            errors.append(f"{resource}: spec must be an object")
            continue

        markers_raw = raw_spec.get("markers")
        allowed_raw = raw_spec.get("allowed_workflows")
        markers = (
            [str(item) for item in markers_raw]
            if isinstance(markers_raw, list)
            else []
        )
        allowed = (
            {str(item) for item in allowed_raw}
            if isinstance(allowed_raw, list)
            else set()
        )
        if not markers:
            errors.append(f"{resource}: markers must not be empty")
            continue
        if not allowed:
            errors.append(f"{resource}: allowed_workflows must not be empty")
            continue

        discovered = discover_resource_writers(workflow_dir, markers)
        unauthorized = sorted(discovered - allowed)
        missing = sorted(allowed - discovered)
        if unauthorized:
            errors.append(
                f"{resource}: unauthorized writers={','.join(unauthorized)}"
            )
        if missing:
            errors.append(
                f"{resource}: declared writers missing marker={','.join(missing)}"
            )

    return errors


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate authoritative workflow resource ownership"
    )
    parser.add_argument("--contract", default=str(DEFAULT_CONTRACT))
    parser.add_argument("--workflow-dir", default=str(DEFAULT_WORKFLOWS))
    args = parser.parse_args()

    errors = validate_resource_ownership(
        Path(args.contract),
        Path(args.workflow_dir),
    )
    if errors:
        print(json.dumps({"status": "FAIL", "errors": errors}, indent=2))
        return 1

    print(json.dumps({"status": "OK", "errors": []}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
