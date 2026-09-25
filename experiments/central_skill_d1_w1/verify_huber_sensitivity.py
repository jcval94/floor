from __future__ import annotations

import argparse
from pathlib import Path
import warnings

import pandas as pd
from sklearn.exceptions import ConvergenceWarning
from sklearn.impute import SimpleImputer
from sklearn.linear_model import HuberRegressor
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

import run_experiments as exp


def huber_factory(max_iter: int, tol: float):
    def _factory() -> Pipeline:
        return Pipeline([
            ("imputer", SimpleImputer(strategy="median")),
            ("scaler", StandardScaler()),
            ("model", HuberRegressor(epsilon=1.35, alpha=1e-4, max_iter=max_iter, tol=tol)),
        ])
    return _factory


def evaluate(dataset: Path, max_iter: int, tol: float) -> pd.DataFrame:
    exp.make_huber = huber_factory(max_iter, tol)
    df, _ = exp.load_dataset(dataset)
    rows = []
    for horizon in ("d1", "w1"):
        frame = exp.prepare_horizon(df, horizon)
        pretest = frame[frame["split"].isin(["train", "validation"])].copy()
        test = frame[frame["split"] == "test"].copy()
        test_start = test["date"].min()
        pretest = pretest[pretest[f"target_end_date_{horizon}"] < test_start]
        for fold in exp.make_outer_folds(pretest, horizon):
            model = exp.residual_fit(fold["train"], "huber")
            pred = exp.residual_predict(model, fold["valid"])
            atr_model = exp.atr_fit(fold["train"])
            atr_pred = exp.atr_predict(atr_model, fold["valid"])
            m = exp.prediction_metrics(fold["valid"], *pred)
            a = exp.prediction_metrics(fold["valid"], *atr_pred)
            rows.append({"horizon": horizon, "split": f"fold{fold['fold']}", "skill": exp.skill(m["mae_spread_pct"], a["mae_spread_pct"])})
        model = exp.residual_fit(pretest, "huber")
        pred = exp.residual_predict(model, test)
        atr_model = exp.atr_fit(pretest)
        atr_pred = exp.atr_predict(atr_model, test)
        m = exp.prediction_metrics(test, *pred)
        a = exp.prediction_metrics(test, *atr_pred)
        rows.append({"horizon": horizon, "split": "test", "skill": exp.skill(m["mae_spread_pct"], a["mae_spread_pct"])})
    return pd.DataFrame(rows)


def main() -> None:
    ap = argparse.ArgumentParser(description="Compare Huber convergence settings without touching production")
    ap.add_argument("--dataset", required=True)
    ap.add_argument("--output", default="experiments/central_skill_d1_w1/results/huber_400_sensitivity.csv")
    args = ap.parse_args()
    warnings.filterwarnings("ignore", category=ConvergenceWarning)
    low = evaluate(Path(args.dataset), 150, 1e-5).rename(columns={"skill": "skill_iter150"})
    high = evaluate(Path(args.dataset), 400, 1e-6).rename(columns={"skill": "skill_iter400"})
    merged = low.merge(high, on=["horizon", "split"])
    merged["abs_diff"] = (merged["skill_iter150"] - merged["skill_iter400"]).abs()
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    merged.to_csv(args.output, index=False)
    print(merged.to_string(index=False))


if __name__ == "__main__":
    main()