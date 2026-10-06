from __future__ import annotations

import argparse
import json
import sys
from typing import Any

# These failures are deterministic with respect to the current code/config state.
# Re-dispatching the same workflow at the same HEAD cannot repair them.
_PATTERNS: tuple[tuple[str, str], ...] = (
    (
        "shadow_eod_divergence",
        "Material shadow/EOD divergence:",
    ),
    (
        "league_frozen_contract_changed",
        "Strategy League frozen contract changed; create a new league_id instead of rewriting history",
    ),
    (
        "weekly_model_hash_changed",
        "Frozen Weekly challenger hash changed; create a new league_id instead of rewriting history",
    ),
    (
        "shadow_base_not_t1",
        "Shadow portfolio base is not T-1 authoritative state:",
    ),
)


def classify_failure_text(text: str, *, workflow: str = "") -> dict[str, Any]:
    workflow_name = str(workflow or "")
    if workflow_name not in {"", "eod", "eod.yml"}:
        return {
            "deterministic": False,
            "code": None,
            "workflow": workflow_name,
            "matched_pattern": None,
        }

    for code, pattern in _PATTERNS:
        if pattern in text:
            return {
                "deterministic": True,
                "code": code,
                "workflow": workflow_name or "eod",
                "matched_pattern": pattern,
            }

    return {
        "deterministic": False,
        "code": None,
        "workflow": workflow_name or None,
        "matched_pattern": None,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Classify workflow failure logs for scheduler recovery policy"
    )
    parser.add_argument("command", choices=["classify"])
    parser.add_argument("--workflow", default="")
    args = parser.parse_args()

    text = sys.stdin.read()
    result = classify_failure_text(text, workflow=args.workflow)
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
