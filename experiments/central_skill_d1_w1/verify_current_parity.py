from __future__ import annotations

import argparse
import json
from pathlib import Path

import run_experiments as exp


def main() -> None:
    ap = argparse.ArgumentParser(description="Verify frozen current_model experiment parity with FLOOR competition artifacts")
    ap.add_argument("--dataset", required=True)
    ap.add_argument("--models-dir", default="data/training/models")
    ap.add_argument("--tolerance", type=float, default=5e-6)
    args = ap.parse_args()

    df, _ = exp.load_dataset(Path(args.dataset))
    failures = []
    for horizon in ("d1", "w1"):
        frame = exp.prepare_horizon(df, horizon)
        train = frame[frame["split"] == "train"].copy()
        validation = frame[frame["split"] == "validation"].copy()
        train_end = train["date"].max()
        validation_end = validation["date"].max()
        train = train[train[f"target_end_date_{horizon}"] <= train_end]
        validation = validation[validation[f"target_end_date_{horizon}"] <= validation_end]

        model = exp.current_fit(train)
        metrics = exp.prediction_metrics(validation, *exp.current_predict(model, validation))
        artifact = json.loads((Path(args.models_dir) / f"{horizon}_competition.json").read_text(encoding="utf-8"))
        selected = next(x for x in artifact["candidates"] if x["model_id"] == artifact["selected_model_id"])
        expected = float(selected["metrics"]["mae_spread_pct"])
        observed = float(metrics["mae_spread_pct"])
        diff = abs(observed - expected)
        print(f"{horizon}: observed={observed:.12f} expected={expected:.12f} abs_diff={diff:.3e}")
        if diff > args.tolerance:
            failures.append((horizon, observed, expected, diff))

    if failures:
        raise SystemExit(f"Parity failed: {failures}")
    print("parity=OK")


if __name__ == "__main__":
    main()