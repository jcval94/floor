from __future__ import annotations

import json
from pathlib import Path

from floor.training.governance import block_automatic_retrain


def test_governance_separates_recommendation_authorization_and_execution(
    tmp_path: Path,
) -> None:
    path = tmp_path / "review_summary_latest.json"
    path.write_text(
        json.dumps(
            {
                "suite_recommendation": "RETRAIN_NOW",
                "tasks_for_auto_retrain": ["value"],
                "models": {
                    "value": {"recommendation": "RETRAIN_NOW", "auto_retrain": True},
                    "timing": {"recommendation": "SKIP_RETRAIN", "auto_retrain": False},
                },
            }
        ),
        encoding="utf-8",
    )

    payload = block_automatic_retrain(path)

    assert payload["tasks_for_auto_retrain_requested"] == ["value"]
    assert payload["tasks_for_auto_retrain"] == []
    assert payload["retraining_control"] == {
        "recommendation": "RETRAIN_NOW",
        "recommended_tasks": ["value"],
        "authorization": "BLOCKED_BY_GOVERNANCE",
        "authorized_tasks": [],
        "execution": "NOT_RUN",
    }
    assert payload["models"]["value"]["retrain_authorization"] == "BLOCKED_BY_GOVERNANCE"
    assert payload["models"]["value"]["retrain_execution"] == "NOT_RUN"
    assert payload["models"]["timing"]["retrain_authorization"] == "NOT_REQUIRED"

def test_retrain_execute_request_can_only_approve_governed_recommendations() -> None:
    workflow = Path(".github/workflows/retrain_execute.yml").read_text(encoding="utf-8")

    assert "push:" in workflow
    assert "retrain_execute_request.json" in workflow
    assert 'if event_name == "push":' in workflow
    assert "force = False" in workflow
    assert 'request.get("approve_recommended") is True' in workflow
    assert 'payload.get("tasks_for_auto_retrain_requested", [])' in workflow
    assert "audited_request_approval_of_recommendation" in workflow

