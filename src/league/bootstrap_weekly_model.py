from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from models.train_weekly_opportunity import train_weekly_opportunity_model


def bootstrap_weekly_model(dataset_path: Path, output_path: Path, version: str) -> dict:
    payload = json.loads(dataset_path.read_text(encoding="utf-8"))
    rows = payload.get("rows", []) if isinstance(payload, dict) else []
    train = [row for row in rows if row.get("split") == "train" and row.get("split_eligible_q1") is True]
    valid = [row for row in rows if row.get("split") == "validation" and row.get("split_eligible_q1") is True]
    if not train or not valid:
        raise RuntimeError(
            "Weekly bootstrap requires leakage-safe train and validation q1 rows; "
            f"train={len(train)} validation={len(valid)}"
        )

    artifact = train_weekly_opportunity_model(train, valid, version=version, tune=True)
    result = asdict(artifact)
    params = result.get("params", {})
    if params.get("canonical_serving_enabled") is not False:
        raise RuntimeError("Weekly challenger bootstrap refused: canonical serving must remain disabled")
    result["league_bootstrap"] = {
        "source": str(dataset_path),
        "train_rows": len(train),
        "validation_rows": len(valid),
        "automatic_promotion": False,
        "paper_or_live_execution_enabled": False,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Train only the frozen Weekly Opportunity challenger for Strategy League")
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--version", required=True)
    args = parser.parse_args()
    result = bootstrap_weekly_model(Path(args.dataset), Path(args.output), args.version)
    print(
        json.dumps(
            {
                "status": "OK",
                "model_name": result.get("model_name"),
                "version": result.get("version"),
                "output": args.output,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
