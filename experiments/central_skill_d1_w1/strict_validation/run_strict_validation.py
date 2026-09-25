from __future__ import annotations

import argparse
import importlib.util
import json
import math
import time
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor

SEED = 260925
RNG = np.random.default_rng(SEED)
_BOOT_INDEX_CACHE: dict[tuple[int,int,int], np.ndarray] = {}
BOOT_REPS = 5000
BLOCK_LENGTH = {"d1": 5, "w1": 10}
HAC_LAG = {"d1": 5, "w1": 10}
WINDOW_SESSIONS = 20
BOUNDARY_GUARD = 0.02

MAIN_CANDIDATES = (
    "spread_asymmetry",
    "residual_boosted",
    "atr_model_shrinkage",
    "ensemble_top3_equal",
)
AUXILIARY_CANDIDATES = ("ensemble_spread_shrink_equal",)

ABLATIONS = (
    "direct_hgb_no_atr",
    "atr_only",
    "residual_boosted",
    "adaptive_residual_boosted",
    "ensemble_top3_equal",
)

LABELS = {
    "atr_only": "ATR-only",
    "incumbent": "Champion actual",
    "spread_asymmetry": "Spread + asymmetry Ridge",
    "residual_boosted": "ATR + residual HGB",
    "atr_model_shrinkage": "ATR/current shrinkage",
    "direct_hgb_no_atr": "HGB directo sin ATR anchor",
    "adaptive_residual_boosted": "ATR adaptativo + residual HGB",
    "ensemble_spread_shrink_equal": "Ensemble spread+shrinkage 50/50",
    "ensemble_top3_equal": "Ensemble top-3 1/3 cada uno",
}


def load_phase1_module(path: Path):
    spec = importlib.util.spec_from_file_location("phase1", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot import {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def raw_numeric(frame: pd.DataFrame, name: str) -> np.ndarray:
    if name == "atr_14":
        return frame["atr_norm"].to_numpy(float)
    if name in frame.columns:
        return pd.to_numeric(frame[name], errors="coerce").fillna(0.0).to_numpy(float)
    return np.zeros(len(frame), dtype=float)


def predict_exported_hgb(params: dict[str, Any], frame: pd.DataFrame) -> np.ndarray:
    names = [str(x) for x in params["features"]]
    X = np.column_stack([raw_numeric(frame, n) for n in names])
    pred = np.full(len(frame), float(params["baseline"]), dtype=float)
    for tree in params["trees"]:
        for i in range(len(frame)):
            node_idx = 0
            while True:
                is_leaf, feature_idx, threshold, left, right, value = tree[node_idx]
                if bool(is_leaf):
                    pred[i] += float(value)
                    break
                node_idx = int(left) if X[i, int(feature_idx)] <= float(threshold) else int(right)
    return np.clip(pred, 0.0001, 0.7)


def predict_anchor_stumps(params: dict[str, Any], frame: pd.DataFrame) -> np.ndarray:
    pred = np.full(len(frame), float(params["base"]), dtype=float)
    lr = float(params["lr"])
    for stump in params.get("stumps", []):
        vals = raw_numeric(frame, str(stump["feature"]))
        leaf = np.where(vals <= float(stump["threshold"]), float(stump["left"]), float(stump["right"]))
        pred += lr * leaf
    return np.clip(pred, 0.0001, 0.7)


def predict_artifact_head(params: dict[str, Any], frame: pd.DataFrame) -> np.ndarray:
    kind = str(params.get("kind") or "")
    if kind == "anchored_blend":
        w = float(params["challenger_weight"])
        anchor = predict_anchor_stumps(params["anchor"], frame)
        challenger = predict_artifact_head(params["challenger"], frame)
        return np.clip((1.0 - w) * anchor + w * challenger, 0.0001, 0.7)
    if kind == "atr_median":
        return np.clip(float(params["multiplier"]) * frame["atr_norm"].to_numpy(float), 0.0001, 0.7)
    if kind == "hist_gradient_boosting":
        return predict_exported_hgb(params, frame)
    raise ValueError(f"Unsupported incumbent head kind={kind}")


def predict_incumbent(artifact: dict[str, Any], frame: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    p = artifact["params"]
    return predict_artifact_head(p["floor"], frame), predict_artifact_head(p["ceiling"], frame)


def fit_direct_hgb(phase1, train: pd.DataFrame) -> dict[str, Any]:
    X = np.nan_to_num(phase1.feature_matrix(train, phase1.CORE_FEATURES), nan=0.0, posinf=0.0, neginf=0.0)
    params = dict(phase1.HGB_PARAMS)
    params.update(max_iter=60, min_samples_leaf=80, max_leaf_nodes=7)
    fm = HistGradientBoostingRegressor(**params).fit(X, train["floor_delta"].to_numpy(float))
    cm = HistGradientBoostingRegressor(**params).fit(X, train["ceiling_delta"].to_numpy(float))
    return {"floor_model": fm, "ceiling_model": cm}


def predict_direct_hgb(phase1, model: dict[str, Any], frame: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    X = np.nan_to_num(phase1.feature_matrix(frame, phase1.CORE_FEATURES), nan=0.0, posinf=0.0, neginf=0.0)
    return phase1.clip_delta(model["floor_model"].predict(X)), phase1.clip_delta(model["ceiling_model"].predict(X))


def fit_adaptive_residual(phase1, train: pd.DataFrame) -> dict[str, Any]:
    base = phase1.adaptive_atr_fit(train)
    bf, bc = phase1.adaptive_atr_predict(base, train)
    X = np.nan_to_num(phase1.feature_matrix(train, phase1.CORE_FEATURES), nan=0.0, posinf=0.0, neginf=0.0)
    params = dict(phase1.HGB_PARAMS)
    params.update(max_iter=60, min_samples_leaf=80, max_leaf_nodes=7)
    fm = HistGradientBoostingRegressor(**params).fit(X, train["floor_delta"].to_numpy(float) - bf)
    cm = HistGradientBoostingRegressor(**params).fit(X, train["ceiling_delta"].to_numpy(float) - bc)
    return {"base": base, "floor_model": fm, "ceiling_model": cm}


def predict_adaptive_residual(phase1, model: dict[str, Any], frame: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    bf, bc = phase1.adaptive_atr_predict(model["base"], frame)
    X = np.nan_to_num(phase1.feature_matrix(frame, phase1.CORE_FEATURES), nan=0.0, posinf=0.0, neginf=0.0)
    return phase1.clip_delta(bf + model["floor_model"].predict(X)), phase1.clip_delta(bc + model["ceiling_model"].predict(X))


def make_losses(frame: pd.DataFrame, pf: np.ndarray, pc: np.ndarray, name: str) -> pd.DataFrame:
    af = frame["floor_delta"].to_numpy(float)
    ac = frame["ceiling_delta"].to_numpy(float)
    out = frame[["date", "symbol", "vol_regime", "period", "close"]].copy()
    out["family"] = name
    out["floor_loss_pct"] = np.abs(pf - af)
    out["ceiling_loss_pct"] = np.abs(pc - ac)
    out["spread_loss_pct"] = np.abs((pf + pc) - (af + ac))
    out["floor_loss"] = out["floor_loss_pct"] * out["close"]
    out["ceiling_loss"] = out["ceiling_loss_pct"] * out["close"]
    out["spread_loss"] = out["spread_loss_pct"] * out["close"]
    return out


def skill(candidate: float, baseline: float) -> float:
    return 1.0 - candidate / baseline if baseline > 0 else float("nan")


def aggregate_metrics(losses: pd.DataFrame, name: str, atr: pd.DataFrame, incumbent: pd.DataFrame) -> dict[str, Any]:
    c = losses[losses.family == name]
    a = atr
    i = incumbent
    out: dict[str, Any] = {"family": name, "label": LABELS[name], "rows": int(len(c))}
    for stem in ("floor", "ceiling", "spread"):
        pct = f"{stem}_loss_pct"
        raw = f"{stem}_loss"
        out[f"mae_{stem}_pct"] = float(c[pct].mean())
        out[f"mae_{stem}"] = float(c[raw].mean())
        out[f"skill_{stem}_vs_atr"] = skill(float(c[pct].mean()), float(a[pct].mean()))
        out[f"skill_{stem}_vs_incumbent"] = skill(float(c[pct].mean()), float(i[pct].mean()))
    out["floor_regression_vs_atr"] = -out["skill_floor_vs_atr"]
    out["ceiling_regression_vs_atr"] = -out["skill_ceiling_vs_atr"]
    return out


def bootstrap_skill(date_losses: pd.DataFrame, candidate: str, loss_col: str, block_len: int, reps: int = BOOT_REPS) -> dict[str, float]:
    piv = date_losses.pivot(index="date", columns="family", values=loss_col).dropna(subset=["atr_only", candidate])
    a = piv["atr_only"].to_numpy(float)
    c = piv[candidate].to_numpy(float)
    n = len(piv)
    est = skill(float(c.mean()), float(a.mean()))
    if n < 2 * block_len:
        return {"estimate": est, "ci_low": float("nan"), "ci_high": float("nan"), "prob_gt_zero": float("nan")}
    key = (n, block_len, reps)
    idx = _BOOT_INDEX_CACHE.get(key)
    if idx is None:
        n_blocks = int(math.ceil(n / block_len))
        starts = RNG.integers(0, n - block_len + 1, size=(reps, n_blocks))
        offsets = np.arange(block_len, dtype=int)
        idx = (starts[:, :, None] + offsets[None, None, :]).reshape(reps, -1)[:, :n]
        _BOOT_INDEX_CACHE[key] = idx
    cmean = c[idx].mean(axis=1)
    amean = a[idx].mean(axis=1)
    sims = 1.0 - cmean / amean
    return {
        "estimate": est,
        "ci_low": float(np.quantile(sims, 0.025)),
        "ci_high": float(np.quantile(sims, 0.975)),
        "prob_gt_zero": float(np.mean(sims > 0.0)),
    }


def hac_mean_test(date_losses: pd.DataFrame, candidate: str, loss_col: str, lag: int) -> dict[str, float]:
    piv = date_losses.pivot(index="date", columns="family", values=loss_col).dropna(subset=["atr_only", candidate])
    # Positive x means candidate has lower loss than ATR.
    x = (piv["atr_only"] - piv[candidate]).to_numpy(float)
    n = len(x)
    mu = float(x.mean())
    z = x - mu
    gamma0 = float(np.dot(z, z) / n)
    lrv = gamma0
    max_lag = min(lag, n - 1)
    for k in range(1, max_lag + 1):
        gamma = float(np.dot(z[k:], z[:-k]) / n)
        w = 1.0 - k / (max_lag + 1.0)
        lrv += 2.0 * w * gamma
    se = math.sqrt(max(lrv, 0.0) / n) if n else float("nan")
    t = mu / se if se > 0 else (math.inf if mu > 0 else -math.inf if mu < 0 else 0.0)
    # one-sided H1: candidate loss < ATR loss => mu > 0
    p_one = 0.5 * math.erfc(t / math.sqrt(2.0)) if math.isfinite(t) else (0.0 if t > 0 else 1.0)
    return {"mean_loss_improvement": mu, "hac_se": se, "hac_t": float(t), "hac_p_one_sided": float(p_one), "lag": int(max_lag)}


def fixed_windows(frame: pd.DataFrame, all_losses: pd.DataFrame, candidate: str, horizon: str) -> pd.DataFrame:
    dates = sorted(frame["date"].unique())
    blocks = [dates[i:i + WINDOW_SESSIONS] for i in range(0, len(dates), WINDOW_SESSIONS)]
    rows = []
    for idx, ds in enumerate(blocks, 1):
        if len(ds) < 10:
            continue
        sub = all_losses[all_losses["date"].isin(ds) & all_losses.family.isin(["atr_only", candidate])]
        g = sub.groupby("family")["spread_loss_pct"].mean()
        if "atr_only" not in g or candidate not in g:
            continue
        rows.append({
            "horizon": horizon,
            "family": candidate,
            "window": idx,
            "start": str(pd.Timestamp(ds[0]).date()),
            "end": str(pd.Timestamp(ds[-1]).date()),
            "sessions": len(ds),
            "skill_spread_vs_atr": skill(float(g[candidate]), float(g["atr_only"])),
        })
    return pd.DataFrame(rows)


def slice_skill(all_losses: pd.DataFrame, candidate: str, by: str, horizon: str) -> pd.DataFrame:
    sub = all_losses[all_losses.family.isin(["atr_only", candidate])]
    g = sub.groupby([by, "family"], observed=True)[["floor_loss_pct", "ceiling_loss_pct", "spread_loss_pct"]].mean().reset_index()
    rows = []
    for key, part in g.groupby(by, observed=True):
        p = part.set_index("family")
        if "atr_only" not in p.index or candidate not in p.index:
            continue
        rows.append({
            "horizon": horizon, "family": candidate, by: key,
            "skill_floor_vs_atr": skill(float(p.loc[candidate, "floor_loss_pct"]), float(p.loc["atr_only", "floor_loss_pct"])),
            "skill_ceiling_vs_atr": skill(float(p.loc[candidate, "ceiling_loss_pct"]), float(p.loc["atr_only", "ceiling_loss_pct"])),
            "skill_spread_vs_atr": skill(float(p.loc[candidate, "spread_loss_pct"]), float(p.loc["atr_only", "spread_loss_pct"])),
        })
    return pd.DataFrame(rows)


def drawdown_metrics(date_losses: pd.DataFrame, candidate: str) -> dict[str, float]:
    piv = date_losses.pivot(index="date", columns="family", values="spread_loss_pct").dropna(subset=["atr_only", candidate])
    excess = (piv["atr_only"] - piv[candidate]).to_numpy(float)
    cum = np.cumsum(excess)
    running_peak = np.maximum.accumulate(np.r_[0.0, cum])[:-1]
    dd = running_peak - cum
    max_dd = float(dd.max()) if len(dd) else 0.0
    total_atr = float(piv["atr_only"].sum())
    worst_idx = int(np.argmax(dd)) if len(dd) else 0
    # worst rolling 20-session skill
    win_skills = []
    if len(piv) >= WINDOW_SESSIONS:
        for s in range(0, len(piv) - WINDOW_SESSIONS + 1):
            aa = piv["atr_only"].iloc[s:s+WINDOW_SESSIONS].mean()
            cc = piv[candidate].iloc[s:s+WINDOW_SESSIONS].mean()
            win_skills.append(skill(float(cc), float(aa)))
    return {
        "max_cumulative_drawdown_abs": max_dd,
        "max_cumulative_drawdown_pct_total_atr_loss": max_dd / total_atr if total_atr > 0 else float("nan"),
        "worst_rolling_20_session_skill": float(min(win_skills)) if win_skills else float("nan"),
        "ending_cumulative_excess_loss_reduction": float(cum[-1]) if len(cum) else 0.0,
        "drawdown_end_date": str(pd.Timestamp(piv.index[worst_idx]).date()) if len(piv) else "",
    }


def classify(row: pd.Series) -> str:
    if row["family"] in {"atr_only", "incumbent"}:
        return "control"
    positive = row["skill_spread_vs_atr"] > 0
    boundary_ok = row["floor_regression_vs_atr"] <= BOUNDARY_GUARD and row["ceiling_regression_vs_atr"] <= BOUNDARY_GUARD
    broad = row["ticker_positive_pct"] >= 0.60 and row["regime_positive_pct"] >= (2/3)
    temporal = row["atr_superior_period_pct"] <= 0.40
    stat = row["bootstrap_prob_gt_zero"] >= 0.90 and row["bootstrap_ci_low"] > 0
    if positive and boundary_ok and broad and temporal and stat:
        return "merece promoción"
    if positive and boundary_ok and row["atr_superior_period_pct"] <= 0.50:
        return "prometedor pero necesita más observaciones"
    return "no mejora ATR de forma robusta"


def run_horizon(phase1, frame: pd.DataFrame, horizon: str, incumbent_artifact: dict[str, Any], outdir: Path) -> dict[str, Any]:
    # Development = all train + validation rows whose complete target is known
    # strictly before the held-out test begins. Once train+validation are merged
    # for the final challenger fit, their old internal boundary is no longer an
    # information boundary; only the final test cutoff matters.
    dev = frame[frame["split"].isin(["train", "validation"])].copy()
    test = frame[frame["split"] == "test"].copy()
    test_start = test["date"].min()
    target_col = f"target_end_date_{horizon}"
    dev = dev[dev[target_col] < test_start].copy()
    if dev.empty or test.empty:
        raise RuntimeError(f"Empty dev/test for {horizon}")

    predictions: dict[str, tuple[np.ndarray, np.ndarray]] = {}
    timings: dict[str, float] = {}
    meta: dict[str, Any] = {}

    t0 = time.perf_counter(); atr_model = phase1.atr_fit(dev); predictions["atr_only"] = phase1.atr_predict(atr_model, test); timings["atr_only"] = time.perf_counter()-t0
    t0 = time.perf_counter(); predictions["incumbent"] = predict_incumbent(incumbent_artifact, test); timings["incumbent"] = time.perf_counter()-t0

    t0 = time.perf_counter(); spread_model = phase1.spread_asym_fit(dev); predictions["spread_asymmetry"] = phase1.spread_asym_predict(spread_model, test); timings["spread_asymmetry"] = time.perf_counter()-t0
    t0 = time.perf_counter(); residual_model = phase1.residual_fit(dev, "boosted"); predictions["residual_boosted"] = phase1.residual_predict(residual_model, test); timings["residual_boosted"] = time.perf_counter()-t0
    t0 = time.perf_counter(); shrink_model, shrink_meta = phase1.fit_family("atr_model_shrinkage", dev, horizon); predictions["atr_model_shrinkage"] = phase1.predict_family("atr_model_shrinkage", shrink_model, test); timings["atr_model_shrinkage"] = time.perf_counter()-t0; meta["atr_model_shrinkage"] = shrink_meta

    t0 = time.perf_counter(); direct_model = fit_direct_hgb(phase1, dev); predictions["direct_hgb_no_atr"] = predict_direct_hgb(phase1, direct_model, test); timings["direct_hgb_no_atr"] = time.perf_counter()-t0
    t0 = time.perf_counter(); adapt_resid_model = fit_adaptive_residual(phase1, dev); predictions["adaptive_residual_boosted"] = predict_adaptive_residual(phase1, adapt_resid_model, test); timings["adaptive_residual_boosted"] = time.perf_counter()-t0

    predictions["ensemble_spread_shrink_equal"] = (
        phase1.clip_delta((predictions["spread_asymmetry"][0] + predictions["atr_model_shrinkage"][0]) / 2.0),
        phase1.clip_delta((predictions["spread_asymmetry"][1] + predictions["atr_model_shrinkage"][1]) / 2.0),
    )
    timings["ensemble_spread_shrink_equal"] = timings["spread_asymmetry"] + timings["atr_model_shrinkage"]
    predictions["ensemble_top3_equal"] = (
        phase1.clip_delta((predictions["spread_asymmetry"][0] + predictions["residual_boosted"][0] + predictions["atr_model_shrinkage"][0]) / 3.0),
        phase1.clip_delta((predictions["spread_asymmetry"][1] + predictions["residual_boosted"][1] + predictions["atr_model_shrinkage"][1]) / 3.0),
    )
    timings["ensemble_top3_equal"] = timings["spread_asymmetry"] + timings["residual_boosted"] + timings["atr_model_shrinkage"]

    names = ["atr_only", "incumbent", "spread_asymmetry", "residual_boosted", "atr_model_shrinkage", "direct_hgb_no_atr", "adaptive_residual_boosted", "ensemble_spread_shrink_equal", "ensemble_top3_equal"]
    all_losses = pd.concat([make_losses(test, *predictions[n], n) for n in names], ignore_index=True)
    date_losses = all_losses.groupby(["date", "family"], observed=True)[["floor_loss_pct", "ceiling_loss_pct", "spread_loss_pct"]].mean().reset_index()
    atr_losses = all_losses[all_losses.family == "atr_only"]
    inc_losses = all_losses[all_losses.family == "incumbent"]

    summaries = []
    ticker_parts=[]; regime_parts=[]; window_parts=[]
    for name in names:
        s = aggregate_metrics(all_losses, name, atr_losses, inc_losses)
        s["horizon"] = horizon
        s["fit_predict_seconds"] = timings[name]
        b_spread = bootstrap_skill(date_losses, name, "spread_loss_pct", BLOCK_LENGTH[horizon])
        b_floor = bootstrap_skill(date_losses, name, "floor_loss_pct", BLOCK_LENGTH[horizon])
        b_ceil = bootstrap_skill(date_losses, name, "ceiling_loss_pct", BLOCK_LENGTH[horizon])
        s.update({
            "bootstrap_ci_low": b_spread["ci_low"], "bootstrap_ci_high": b_spread["ci_high"], "bootstrap_prob_gt_zero": b_spread["prob_gt_zero"],
            "floor_boot_ci_low": b_floor["ci_low"], "floor_boot_ci_high": b_floor["ci_high"],
            "ceiling_boot_ci_low": b_ceil["ci_low"], "ceiling_boot_ci_high": b_ceil["ci_high"],
        })
        h = hac_mean_test(date_losses, name, "spread_loss_pct", HAC_LAG[horizon]) if name != "atr_only" else {"mean_loss_improvement":0.0,"hac_se":0.0,"hac_t":0.0,"hac_p_one_sided":1.0,"lag":HAC_LAG[horizon]}
        s.update(h)
        dd = drawdown_metrics(date_losses, name)
        s.update(dd)
        tw = slice_skill(all_losses, name, "symbol", horizon)
        rg = slice_skill(all_losses, name, "vol_regime", horizon)
        wi = fixed_windows(test, all_losses, name, horizon)
        s["ticker_positive_pct"] = float((tw.skill_spread_vs_atr > 0).mean()) if len(tw) else float("nan")
        s["ticker_median_skill"] = float(tw.skill_spread_vs_atr.median()) if len(tw) else float("nan")
        s["ticker_min_skill"] = float(tw.skill_spread_vs_atr.min()) if len(tw) else float("nan")
        s["regime_positive_pct"] = float((rg.skill_spread_vs_atr > 0).mean()) if len(rg) else float("nan")
        s["regime_min_skill"] = float(rg.skill_spread_vs_atr.min()) if len(rg) else float("nan")
        s["periods"] = int(len(wi))
        s["atr_superior_period_pct"] = float((wi.skill_spread_vs_atr < 0).mean()) if len(wi) else float("nan")
        s["period_median_skill"] = float(wi.skill_spread_vs_atr.median()) if len(wi) else float("nan")
        s["period_min_skill"] = float(wi.skill_spread_vs_atr.min()) if len(wi) else float("nan")
        summaries.append(s)
        ticker_parts.append(tw); regime_parts.append(rg); window_parts.append(wi)

    summary = pd.DataFrame(summaries)
    summary["classification"] = summary.apply(classify, axis=1)
    ticker = pd.concat(ticker_parts, ignore_index=True)
    regime = pd.concat(regime_parts, ignore_index=True)
    windows = pd.concat(window_parts, ignore_index=True)

    # Per-window incumbent comparisons for main challengers.
    for cand in MAIN_CANDIDATES:
        pass

    summary.to_csv(outdir / f"{horizon}_strict_summary.csv", index=False)
    ticker.to_csv(outdir / f"{horizon}_ticker.csv", index=False)
    regime.to_csv(outdir / f"{horizon}_regime.csv", index=False)
    windows.to_csv(outdir / f"{horizon}_windows.csv", index=False)
    date_losses.to_csv(outdir / f"{horizon}_date_losses.csv", index=False)

    return {
        "summary": summary,
        "ticker": ticker,
        "regime": regime,
        "windows": windows,
        "date_losses": date_losses,
        "meta": meta,
        "dates": {"dev_start": str(dev.date.min().date()), "dev_end": str(dev.date.max().date()), "test_start": str(test.date.min().date()), "test_end": str(test.date.max().date()), "test_sessions": int(test.date.nunique()), "dev_rows": int(len(dev)), "test_rows": int(len(test))},
    }


def build_report(results: dict[str, Any], outdir: Path, provenance: dict[str, Any]) -> None:
    all_summary = pd.concat([results[h]["summary"] for h in ("d1","w1")], ignore_index=True)
    all_summary.to_csv(outdir / "strict_summary_all.csv", index=False)
    pd.concat([results[h]["ticker"] for h in ("d1","w1")], ignore_index=True).to_csv(outdir / "strict_ticker_all.csv", index=False)
    pd.concat([results[h]["regime"] for h in ("d1","w1")], ignore_index=True).to_csv(outdir / "strict_regime_all.csv", index=False)
    pd.concat([results[h]["windows"] for h in ("d1","w1")], ignore_index=True).to_csv(outdir / "strict_windows_all.csv", index=False)

    # Cross-horizon classification. A positive but statistically/breadth-uncertain
    # result is "promising" rather than rejected; outright rejection is reserved
    # for non-positive skill, material boundary regression, or temporal fragility.
    class_rows=[]
    for fam in MAIN_CANDIDATES:
        r=all_summary[all_summary.family==fam].copy()
        positive=bool((r.skill_spread_vs_atr > 0).all())
        boundary_ok=bool(((r.floor_regression_vs_atr <= BOUNDARY_GUARD) & (r.ceiling_regression_vs_atr <= BOUNDARY_GUARD)).all())
        temporal_ok=bool((r.atr_superior_period_pct <= 0.50).all())
        promotion_stats=bool((r.bootstrap_ci_low > 0).all() and (r.bootstrap_prob_gt_zero >= 0.90).all())
        broad=bool((r.ticker_positive_pct >= 0.60).all() and (r.regime_positive_pct >= (2/3)).all())
        if positive and boundary_ok and temporal_ok and promotion_stats and broad:
            c="merece promoción"
        elif positive and boundary_ok and temporal_ok:
            c="prometedor pero necesita más observaciones"
        else:
            c="no mejora ATR de forma robusta"
        class_rows.append({"family":fam,"label":LABELS[fam],"classification":c})
    classification=pd.DataFrame(class_rows)
    classification.to_csv(outdir / "classification.csv", index=False)

    # Ablation deltas by horizon.
    abl=[]
    for h in ("d1","w1"):
        s=results[h]["summary"].set_index("family")
        for fam in ABLATIONS:
            row=s.loc[fam]
            abl.append({"horizon":h,"family":fam,"label":LABELS[fam],"skill_spread_vs_atr":row.skill_spread_vs_atr,"skill_floor_vs_atr":row.skill_floor_vs_atr,"skill_ceiling_vs_atr":row.skill_ceiling_vs_atr,"bootstrap_ci_low":row.bootstrap_ci_low,"bootstrap_ci_high":row.bootstrap_ci_high,"atr_superior_period_pct":row.atr_superior_period_pct})
    pd.DataFrame(abl).to_csv(outdir / "ablation.csv", index=False)

    metadata={
        "generated_at_utc": pd.Timestamp.utcnow().isoformat(),
        "seed": SEED,
        "provenance": provenance,
        "protocol": {
            "selection_frozen_from_phase1": True,
            "final_set": "original source split=test only",
            "candidate_fit_data": "source train + validation only; no test rows",
            "no_final_hyperparameter_tuning": True,
            "main_candidates": list(MAIN_CANDIDATES),
            "ensemble_weights": {"top3_equal":{"spread_asymmetry":1/3,"residual_boosted":1/3,"atr_model_shrinkage":1/3},"spread_shrink_equal":{"spread_asymmetry":0.5,"atr_model_shrinkage":0.5}},
            "ablation": list(ABLATIONS),
            "block_bootstrap_reps": BOOT_REPS,
            "block_length": BLOCK_LENGTH,
            "hac_lag": HAC_LAG,
            "fixed_window_sessions": WINDOW_SESSIONS,
            "boundary_guard": BOUNDARY_GUARD,
        },
        "date_ranges": {h:results[h]["dates"] for h in ("d1","w1")},
        "model_meta": {h:results[h]["meta"] for h in ("d1","w1")},
    }
    (outdir/"metadata.json").write_text(json.dumps(metadata,indent=2,sort_keys=True),encoding="utf-8")

    def pct(x): return f"{x:+.2%}" if pd.notna(x) else "n/a"
    lines=[
        "# Strict promotion validation — D1/W1 central skill",
        "",
        "This is a promotion-stage validation only. Production champions are unchanged.",
        "",
        "## Protocol",
        "",
        "- Candidate families were frozen from the previous experimental phase before this run.",
        "- Final evaluation uses only the original held-out `test` split. Candidate models are fit on `train + validation`; no test label is used for fitting, lambda selection, feature selection or ensemble weighting.",
        "- The incumbent is the exact serialized production champion artifact, not a refitted proxy.",
        "- ATR-only is refit on all pre-test data, making the baseline as strong as possible at the final cutoff.",
        "- Equal-weight ensemble is fixed at 1/3 each across Spread+Asymmetry, ATR+Residual HGB and ATR/Incumbent Shrinkage; weights are not optimized on test.",
        f"- Uncertainty: {BOOT_REPS:,} moving-block bootstrap resamples over date-level losses; HAC/Newey-West on the ATR-minus-candidate daily loss differential.",
        f"- Stability windows: non-overlapping {WINDOW_SESSIONS}-session final-test blocks. A final remainder shorter than 10 sessions is omitted.",
        "- Boundary guard: neither floor nor ceiling normalized MAE may regress by more than 2% versus ATR.",
        "",
        "## Final OOS comparison",
        "",
        "|H|Model|Spread skill vs ATR|Floor skill|Ceiling skill|Skill vs incumbent|Bootstrap 95%|P(skill>0)|HAC p (1-side)|Tickers +|Regimes +|ATR wins periods|Worst 20d skill|Max perf DD / ATR loss|Class|",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for h in ("d1","w1"):
        s=results[h]["summary"]
        order=["atr_only","incumbent","spread_asymmetry","residual_boosted","atr_model_shrinkage","ensemble_spread_shrink_equal","ensemble_top3_equal"]
        for fam in order:
            r=s[s.family==fam].iloc[0]
            lines.append(
                f"|{h.upper()}|{LABELS[fam]}|{pct(r.skill_spread_vs_atr)}|{pct(r.skill_floor_vs_atr)}|{pct(r.skill_ceiling_vs_atr)}|{pct(r.skill_spread_vs_incumbent)}|[{pct(r.bootstrap_ci_low)}, {pct(r.bootstrap_ci_high)}]|{r.bootstrap_prob_gt_zero:.1%}|{r.hac_p_one_sided:.4f}|{r.ticker_positive_pct:.0%}|{r.regime_positive_pct:.0%}|{r.atr_superior_period_pct:.0%}|{pct(r.worst_rolling_20_session_skill)}|{pct(r.max_cumulative_drawdown_pct_total_atr_loss)}|{r.classification}|"
            )
    lines += ["", "## Ablation — where the skill comes from", "", "|H|Layer|Spread skill vs ATR|Floor skill|Ceiling skill|Bootstrap 95%|ATR wins periods|", "|---|---|---:|---:|---:|---:|---:|"]
    ab=pd.read_csv(outdir/"ablation.csv")
    for r in ab.itertuples():
        lines.append(f"|{r.horizon.upper()}|{r.label}|{pct(r.skill_spread_vs_atr)}|{pct(r.skill_floor_vs_atr)}|{pct(r.skill_ceiling_vs_atr)}|[{pct(r.bootstrap_ci_low)}, {pct(r.bootstrap_ci_high)}]|{r.atr_superior_period_pct:.0%}|")
    lines += ["", "## Cross-horizon classification", ""]
    for r in classification.itertuples():
        lines.append(f"- **{r.label}: {r.classification}.**")
    lines += ["", "## Interpretation", "", "See the companion CSV files for exact ticker, volatility-regime and 20-session-window decompositions. The p-value is supporting evidence only; breadth, temporal recurrence, boundary behavior and drawdown are considered jointly."]
    (outdir/"REPORT.md").write_text("\n".join(lines)+"\n",encoding="utf-8")


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--dataset",required=True)
    ap.add_argument("--phase1-runner",required=True)
    ap.add_argument("--d1-champion",required=True)
    ap.add_argument("--w1-champion",required=True)
    ap.add_argument("--output",required=True)
    ap.add_argument("--provenance",default="{}")
    args=ap.parse_args()
    outdir=Path(args.output); outdir.mkdir(parents=True,exist_ok=True)
    phase1=load_phase1_module(Path(args.phase1_runner))
    df,payload=phase1.load_dataset(Path(args.dataset))
    champions={"d1":json.load(open(args.d1_champion,encoding="utf-8")),"w1":json.load(open(args.w1_champion,encoding="utf-8"))}
    results={}
    for h in ("d1","w1"):
        frame=phase1.prepare_horizon(df,h)
        # prepare_horizon retained merged eligibility fields.
        print(f"running {h} rows={len(frame)}",flush=True)
        results[h]=run_horizon(phase1,frame,h,champions[h],outdir)
    provenance=json.loads(args.provenance)
    provenance["source_split_policy"]=payload.get("split_policy")
    build_report(results,outdir,provenance)
    print("done",flush=True)

if __name__=="__main__":
    main()