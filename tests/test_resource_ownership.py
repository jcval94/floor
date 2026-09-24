from __future__ import annotations

import json
from pathlib import Path

from utils.resource_ownership import (
    discover_resource_writers,
    validate_resource_ownership,
)


def test_repository_resource_ownership_contract_is_exact() -> None:
    assert validate_resource_ownership() == []


def test_resource_ownership_rejects_unauthorized_writer(tmp_path: Path) -> None:
    workflows = tmp_path / "workflows"
    workflows.mkdir()
    (workflows / "good.yml").write_text(
        "run: bash scripts/runtime_state.sh publish\n",
        encoding="utf-8",
    )
    (workflows / "bad.yml").write_text(
        "run: bash scripts/runtime_state.sh publish\n",
        encoding="utf-8",
    )
    contract = tmp_path / "ownership.json"
    contract.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "resources": {
                    "runtime": {
                        "markers": ["runtime_state.sh publish"],
                        "allowed_workflows": ["good.yml"],
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    errors = validate_resource_ownership(contract, workflows)
    assert errors == ["runtime: unauthorized writers=bad.yml"]


def test_marker_matching_requires_all_markers(tmp_path: Path) -> None:
    workflows = tmp_path / "workflows"
    workflows.mkdir()
    (workflows / "registry.yml").write_text(
        "git add -f data/training/models/*.json\ngit push\n",
        encoding="utf-8",
    )
    (workflows / "history.yml").write_text(
        "git push\n",
        encoding="utf-8",
    )

    assert discover_resource_writers(
        workflows,
        ["git add -f data/training/models/*.json", "git push"],
    ) == {"registry.yml"}
