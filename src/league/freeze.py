from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


def _canonical(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256(payload: Any) -> str:
    return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()


def verify_challenger_freeze(
    league_config_path: Path,
    freeze_path: Path,
) -> dict[str, Any]:
    league = json.loads(league_config_path.read_text(encoding="utf-8"))
    freeze = json.loads(freeze_path.read_text(encoding="utf-8"))
    current = league.get("capital_allocation_challenger")
    expected = freeze.get("challenger_config")
    if not isinstance(current, dict) or not isinstance(expected, dict):
        raise RuntimeError("challenger freeze requires object configs")

    expected_hash = str(freeze.get("challenger_config_sha256") or "")
    actual_hash = _sha256(current)
    manifest_hash = _sha256(expected)
    if manifest_hash != expected_hash:
        raise RuntimeError(
            "frozen challenger manifest is internally inconsistent: "
            f"manifest={manifest_hash} expected={expected_hash}"
        )
    if actual_hash != expected_hash or current != expected:
        raise RuntimeError(
            "capital challenger parameters changed after freeze; create a new freeze_id "
            "and a new Strategy League league_id instead of mutating the frozen experiment"
        )
    if str(league.get("league_id") or "") != str(freeze.get("source_league_id") or ""):
        raise RuntimeError("freeze manifest league_id does not match current Strategy League")
    return {
        "status": "FROZEN_OK",
        "freeze_id": freeze.get("freeze_id"),
        "league_id": league.get("league_id"),
        "challenger_config_sha256": actual_hash,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify frozen Capital Allocation Challenger contract")
    parser.add_argument("--league-config", default="config/strategy_league.json")
    parser.add_argument("--freeze", default="config/frozen/capital_challenger_v1.json")
    args = parser.parse_args()
    print(json.dumps(verify_challenger_freeze(Path(args.league_config), Path(args.freeze)), indent=2))


if __name__ == "__main__":
    main()
