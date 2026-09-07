from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from league.attribution import build_attribution_report


def _load(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def publish_research_payloads(
    *,
    data_dir: Path,
    site_data_dir: Path,
    league_config_path: Path,
) -> dict[str, Any]:
    oos_source = data_dir / "reports" / "walk_forward_oos.json"
    retrospective_attr_source = data_dir / "reports" / "strategy_attribution.json"
    league_cfg = _load(league_config_path)
    league_id = str(league_cfg.get("league_id") or "")
    prospective_history = (
        data_dir / "metrics" / "strategy_league" / "runs" / league_id / "history.jsonl"
        if league_id
        else Path("__missing__")
    )

    oos = _load(oos_source)
    if not oos:
        oos = {
            "schema_version": 1,
            "status": "WAITING",
            "evidence_type": "historical_walk_forward_model_oos_fixed_strategy",
            "historical_model_out_of_sample": True,
            "prospective_evidence": False,
            "rows": [],
            "fold_reports": [],
        }
    retrospective_attr = _load(retrospective_attr_source)
    if not retrospective_attr:
        retrospective_attr = {
            "schema_version": 1,
            "status": "WAITING",
            "member": "capital_allocation_challenger",
            "source_attribution": [],
            "realized_trades": [],
            "exposure": {},
        }
    prospective_attr = build_attribution_report(prospective_history)
    prospective_attr["evidence_type"] = "prospective_shadow_paper_attribution"
    prospective_attr["prospective_evidence"] = True

    _write(site_data_dir / "walk_forward_oos.json", oos)
    _write(site_data_dir / "strategy_attribution.json", retrospective_attr)
    _write(site_data_dir / "strategy_league_attribution.json", prospective_attr)
    return {
        "oos_status": oos.get("status"),
        "retrospective_attribution_status": retrospective_attr.get("status"),
        "prospective_attribution_status": prospective_attr.get("status"),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Publish research/OOS/attribution payloads to Pages")
    parser.add_argument("--data-dir", default="data")
    parser.add_argument("--site-data-dir", default="site/data")
    parser.add_argument("--league-config", default="config/strategy_league.json")
    args = parser.parse_args()
    print(json.dumps(publish_research_payloads(
        data_dir=Path(args.data_dir),
        site_data_dir=Path(args.site_data_dir),
        league_config_path=Path(args.league_config),
    ), ensure_ascii=False))


if __name__ == "__main__":
    main()
