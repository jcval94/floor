from __future__ import annotations

import argparse
import csv
import json
import math
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.impute import SimpleImputer
from sklearn.linear_model import HuberRegressor, Ridge
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

SEED = 102
RNG = np.random.default_rng(SEED)
HORIZON_SESSIONS = {"d1": 1, "w1": 5}
N_FOLDS = 5
VALID_SESSIONS = 126
BOOTSTRAP_REPS = 2000
BLOCK_LENGTH = {"d1": 5, "w1": 10}
BOUNDARY_GUARD = 0.02

CORE_FEATURES = (
    "atr_norm",
    "rolling_vol_5",
    "rolling_vol_20",
    "rolling_vol_60",
    "downside_vol_20",
    "parkinson_vol_20",
    "gap_open_to_prev_close",
    "relative_volume_20",
    "dist_to_low_20",
    "dist_to_high_20",
    "sma_slope_5_20",
    "beta_20",
    "rel_strength_20",
    "momentum_10",
    "momentum_20",
    "vol_regime_score",
    "recent_drawdown_20",
    "range_width_5",
    "range_width_20",
    "range_width_60",
    "price_position_in_range_20",
    "trend_context_m3",
    "slope_4w",
    "slope_8w",
    "slope_13w",
    "drawdown_13w",
    "range_compression_20_60",
    "rel_strength_4w",
    "rel_strength_8w",
    "rel_strength_13w",
    "dist_to_low_3m",
    "dist_to_low_6m",
    "dist_to_low_12m",
    "vol_persistence_20_60",
    "range_amp_daily_5",
    "range_amp_daily_13",
    "rsi_14",
    "bollinger_width_20",
    "vwap_distance",
    "month_sin",
    "month_cos",
)

ROBUST_RANGE_FEATURES = (
    "ret_lag_1", "ret_lag_2", "ret_lag_5", "ret_lag_10",
    "rolling_vol_5", "rolling_vol_20", "rolling_vol_60", "downside_vol_20",
    "atr_norm", "parkinson_vol_20", "gap_open_to_prev_close", "relative_volume_20",
    "dist_to_low_20", "dist_to_high_20", "sma_slope_5_20", "beta_20",
    "rel_strength_20", "momentum_10", "momentum_20", "vol_regime_score",
    "recent_drawdown_20", "intraday_range_5", "range_width_5", "range_width_20",
    "range_width_60", "price_position_in_range_20", "trend_context_m3", "slope_4w",
    "slope_8w", "slope_13w", "drawdown_13w", "range_compression_20_60",
    "rel_strength_4w", "rel_strength_8w", "rel_strength_13w", "dist_to_low_3m",
    "dist_to_low_6m", "dist_to_low_12m", "vol_persistence_20_60",
    "range_amp_daily_5", "range_amp_daily_13", "rsi_14", "bollinger_width_20",
    "vwap_distance", "open_to_close", "high_to_close", "low_to_close",
    "month_sin", "month_cos",
)

ANCHOR_FEATURES = (
    "atr_norm", "trend_context_m3", "drawdown_13w", "dist_to_low_3m",
    "ai_horizon_alignment", "rel_strength_20",
)

HGB_PARAMS = dict(
    loss="absolute_error", learning_rate=0.05, max_iter=80, max_leaf_nodes=7,
    min_samples_leaf=60, l2_regularization=0.1, early_stopping=False,
    random_state=SEED,
)

FAMILY_ORDER = (
    "atr_only",
    "adaptive_atr",
    "current_model",
    "residual_ridge",
    "residual_huber",
    "residual_boosted",
    "ratio_ridge",
    "spread_asymmetry",
    "atr_model_shrinkage",
    "conservative_ensemble",
)

FAMILY_LABELS = {
    "atr_only": "ATR-only",
    "adaptive_atr": "Adaptive ATR",
    "current_model": "Current robust_range_v3",
    "residual_ridge": "ATR + residual Ridge",
    "residual_huber": "ATR + residual Huber",
    "residual_boosted": "ATR + residual HGB",
    "ratio_ridge": "floor/ATR + ceiling/ATR Ridge",
    "spread_asymmetry": "Spread + asymmetry Ridge",
    "atr_model_shrinkage": "ATR/current shrinkage",
    "conservative_ensemble": "Conservative ensemble",
}

COMPLEXITY = {
    "atr_only": "very_low",
    "adaptive_atr": "very_low",
    "current_model": "medium",
    "residual_ridge": "low",
    "residual_huber": "low_medium",
    "residual_boosted": "medium",
    "ratio_ridge": "low",
    "spread_asymmetry": "low",
    "atr_model_shrinkage": "medium",
    "conservative_ensemble": "medium_high",
}


def _num(v: Any, default: float = np.nan) -> float:
    try:
        if v is None:
            return default
        x = float(v)
        return x if math.isfinite(x) else default
    except (TypeError, ValueError):
        return default


def load_dataset(path: Path) -> tuple[pd.DataFrame, dict[str, Any]]:
    with path.open("r", encoding="utf-8") as fh:
        payload = json.load(fh)
    rows = payload["rows"]
    needed = {
        "timestamp", "symbol", "close", "split", "vol_regime", "target_end_date_d1",
        "target_end_date_w1", "horizon_complete_d1", "horizon_complete_w1",
        "floor_d1", "ceiling_d1", "floor_w1", "ceiling_w1", "atr_14",
        "open", "high", "low",
    } | set(CORE_FEATURES) | set(ROBUST_RANGE_FEATURES) | {"ai_horizon_alignment"}
    records = [{k: row.get(k) for k in needed} for row in rows]
    df = pd.DataFrame.from_records(records)
    df["date"] = pd.to_datetime(df["timestamp"], utc=True).dt.normalize()
    df["target_end_date_d1"] = pd.to_datetime(df["target_end_date_d1"], errors="coerce", utc=True).dt.normalize()
    df["target_end_date_w1"] = pd.to_datetime(df["target_end_date_w1"], errors="coerce", utc=True).dt.normalize()
    df["atr_norm"] = pd.to_numeric(df["atr_14"], errors="coerce") / pd.to_numeric(df["close"], errors="coerce")
    if "ai_horizon_alignment" not in df.columns:
        df["ai_horizon_alignment"] = 0.0
    df["ai_horizon_alignment"] = pd.to_numeric(df["ai_horizon_alignment"], errors="coerce").fillna(0.0)
    # Ensure derived values exist even if an older ABT omits them.
    close = pd.to_numeric(df["close"], errors="coerce")
    for name, source in (("open_to_close", "open"), ("high_to_close", "high"), ("low_to_close", "low")):
        if name not in df.columns or df[name].isna().all():
            df[name] = pd.to_numeric(df[source], errors="coerce") / close - 1.0
    month = df["date"].dt.month.astype(float)
    if "month_sin" not in df.columns or df["month_sin"].isna().all():
        df["month_sin"] = np.sin(2.0 * np.pi * month / 12.0)
    if "month_cos" not in df.columns or df["month_cos"].isna().all():
        df["month_cos"] = np.cos(2.0 * np.pi * month / 12.0)
    return df, payload


def prepare_horizon(df: pd.DataFrame, horizon: str) -> pd.DataFrame:
    out = df[df[f"horizon_complete_{horizon}"].fillna(False).astype(bool)].copy()
    out["close"] = pd.to_numeric(out["close"], errors="coerce")
    out["actual_floor"] = pd.to_numeric(out[f"floor_{horizon}"], errors="coerce")
    out["actual_ceiling"] = pd.to_numeric(out[f"ceiling_{horizon}"], errors="coerce")
    out = out[(out["close"] > 0) & out["actual_floor"].notna() & out["actual_ceiling"].notna() & out["atr_norm"].notna()]
    out["floor_delta"] = ((out["close"] - out["actual_floor"]) / out["close"]).clip(0.0001, 0.6)
    out["ceiling_delta"] = ((out["actual_ceiling"] - out["close"]) / out["close"]).clip(0.0001, 0.6)
    out["actual_width_delta"] = out["floor_delta"] + out["ceiling_delta"]
    out["trend_sign"] = np.where(pd.to_numeric(out["trend_context_m3"], errors="coerce").fillna(0.0) >= 0.0, "up", "down")
    out["vol_regime"] = out["vol_regime"].fillna("UNKNOWN").astype(str)
    out["regime_key"] = out["vol_regime"] + ":" + out["trend_sign"]
    out["period"] = out["date"].dt.tz_localize(None).dt.to_period("Q").astype(str)
    return out.sort_values(["date", "symbol"]).reset_index(drop=True)


def distinct_dates(frame: pd.DataFrame) -> list[pd.Timestamp]:
    return sorted(frame["date"].dropna().unique())


def _purged_train(frame: pd.DataFrame, valid_start: pd.Timestamp, horizon: str, embargo_sessions: int) -> pd.DataFrame:
    dates = [d for d in distinct_dates(frame) if d < valid_start]
    if not dates:
        return frame.iloc[0:0].copy()
    if embargo_sessions > 0 and len(dates) > embargo_sessions:
        cutoff = dates[-embargo_sessions - 1]
    else:
        cutoff = dates[-1]
    target_col = f"target_end_date_{horizon}"
    return frame[(frame["date"] <= cutoff) & (frame[target_col] < valid_start)].copy()


def make_outer_folds(pretest: pd.DataFrame, horizon: str) -> list[dict[str, Any]]:
    dates = distinct_dates(pretest)
    required = N_FOLDS * VALID_SESSIONS + 252
    if len(dates) < required:
        raise ValueError(f"Need at least {required} pretest sessions, found {len(dates)} for {horizon}")
    validation_dates = dates[-N_FOLDS * VALID_SESSIONS:]
    folds = []
    for idx in range(N_FOLDS):
        block = validation_dates[idx * VALID_SESSIONS:(idx + 1) * VALID_SESSIONS]
        valid_start, valid_end = block[0], block[-1]
        train = _purged_train(pretest[pretest["date"] < valid_start], valid_start, horizon, HORIZON_SESSIONS[horizon])
        valid = pretest[pretest["date"].isin(block)].copy()
        folds.append({"fold": idx + 1, "train": train, "valid": valid, "valid_start": valid_start, "valid_end": valid_end})
    return folds


def inner_split(train: pd.DataFrame, horizon: str, tune_fraction: float = 0.20) -> tuple[pd.DataFrame, pd.DataFrame]:
    dates = distinct_dates(train)
    cut = max(20, int(len(dates) * (1.0 - tune_fraction)))
    cut = min(cut, len(dates) - 20)
    tune_dates = dates[cut:]
    tune_start = tune_dates[0]
    core = _purged_train(train[train["date"] < tune_start], tune_start, horizon, HORIZON_SESSIONS[horizon])
    tune = train[train["date"].isin(tune_dates)].copy()
    return core, tune


def feature_matrix(frame: pd.DataFrame, names: tuple[str, ...]) -> np.ndarray:
    cols = []
    for name in names:
        if name == "atr_norm":
            s = frame["atr_norm"]
        elif name in frame.columns:
            s = frame[name]
        else:
            s = pd.Series(np.nan, index=frame.index)
        cols.append(pd.to_numeric(s, errors="coerce").to_numpy(dtype=float))
    return np.column_stack(cols)


def clip_delta(x: np.ndarray) -> np.ndarray:
    return np.clip(np.asarray(x, dtype=float), 0.0001, 0.6)


def atr_fit(train: pd.DataFrame) -> dict[str, float]:
    atr = np.maximum(train["atr_norm"].to_numpy(float), 1e-8)
    return {
        "floor_k": float(np.median(train["floor_delta"].to_numpy(float) / atr)),
        "ceiling_k": float(np.median(train["ceiling_delta"].to_numpy(float) / atr)),
    }


def atr_predict(model: dict[str, float], frame: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    atr = frame["atr_norm"].to_numpy(float)
    return clip_delta(model["floor_k"] * atr), clip_delta(model["ceiling_k"] * atr)


def median_fit(train: pd.DataFrame) -> dict[str, float]:
    return {"floor": float(train["floor_delta"].median()), "ceiling": float(train["ceiling_delta"].median())}


def median_predict(model: dict[str, float], frame: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    return np.full(len(frame), model["floor"]), np.full(len(frame), model["ceiling"])


def adaptive_atr_fit(train: pd.DataFrame, tau: float = 250.0) -> dict[str, Any]:
    global_model = atr_fit(train)
    table: dict[str, dict[str, float]] = {}
    for key, group in train.groupby("regime_key", sort=True):
        if len(group) < 40:
            continue
        local = atr_fit(group)
        w = len(group) / (len(group) + tau)
        table[str(key)] = {
            "floor_k": w * local["floor_k"] + (1.0 - w) * global_model["floor_k"],
            "ceiling_k": w * local["ceiling_k"] + (1.0 - w) * global_model["ceiling_k"],
            "rows": int(len(group)),
            "shrinkage_weight": float(w),
        }
    return {"global": global_model, "table": table, "tau": tau}


def adaptive_atr_predict(model: dict[str, Any], frame: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    f = np.empty(len(frame), dtype=float)
    c = np.empty(len(frame), dtype=float)
    atr = frame["atr_norm"].to_numpy(float)
    keys = frame["regime_key"].astype(str).to_numpy()
    for i, key in enumerate(keys):
        cell = model["table"].get(key, model["global"])
        f[i] = cell["floor_k"] * atr[i]
        c[i] = cell["ceiling_k"] * atr[i]
    return clip_delta(f), clip_delta(c)


def fit_stumps(train: pd.DataFrame, target: np.ndarray, rounds: int = 6, lr: float = 0.45) -> dict[str, Any]:
    X = feature_matrix(train, ANCHOR_FEATURES)
    X = np.nan_to_num(X, nan=0.0, posinf=0.0, neginf=0.0)
    pred = np.full(len(train), float(np.mean(target)))
    stumps = []
    for _ in range(rounds):
        residual = target - pred
        best = None
        best_error = math.inf
        for j, name in enumerate(ANCHOR_FEATURES):
            values = X[:, j]
            threshold = float(np.median(values))
            mask = values <= threshold
            if mask.all() or (~mask).all():
                continue
            left = float(np.mean(residual[mask]))
            right = float(np.mean(residual[~mask]))
            fitted = np.where(mask, left, right)
            error = float(np.sum((residual - fitted) ** 2))
            if error < best_error:
                best_error = error
                best = (j, name, threshold, left, right)
        if best is None:
            break
        j, name, threshold, left, right = best
        stumps.append({"j": j, "name": name, "threshold": threshold, "left": left, "right": right})
        pred += lr * np.where(X[:, j] <= threshold, left, right)
    return {"base": float(np.mean(target)), "stumps": stumps, "lr": lr}


def predict_stumps(model: dict[str, Any], frame: pd.DataFrame) -> np.ndarray:
    X = np.nan_to_num(feature_matrix(frame, ANCHOR_FEATURES), nan=0.0, posinf=0.0, neginf=0.0)
    pred = np.full(len(frame), model["base"], dtype=float)
    for stump in model["stumps"]:
        j = int(stump["j"])
        pred += model["lr"] * np.where(X[:, j] <= stump["threshold"], stump["left"], stump["right"])
    return clip_delta(pred)


def current_fit(train: pd.DataFrame) -> dict[str, Any]:
    floor_y = train["floor_delta"].to_numpy(float)
    ceil_y = train["ceiling_delta"].to_numpy(float)
    floor_anchor = fit_stumps(train, floor_y)
    ceil_anchor = fit_stumps(train, ceil_y)
    atr = atr_fit(train)
    X = np.nan_to_num(feature_matrix(train, ROBUST_RANGE_FEATURES), nan=0.0, posinf=0.0, neginf=0.0)
    ceil_hgb = HistGradientBoostingRegressor(**HGB_PARAMS).fit(X, ceil_y)
    return {"floor_anchor": floor_anchor, "ceil_anchor": ceil_anchor, "floor_k": atr["floor_k"], "ceil_hgb": ceil_hgb}


def current_predict(model: dict[str, Any], frame: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    floor_anchor = predict_stumps(model["floor_anchor"], frame)
    ceil_anchor = predict_stumps(model["ceil_anchor"], frame)
    atr = frame["atr_norm"].to_numpy(float)
    floor_challenger = clip_delta(model["floor_k"] * atr)
    X = np.nan_to_num(feature_matrix(frame, ROBUST_RANGE_FEATURES), nan=0.0, posinf=0.0, neginf=0.0)
    ceil_challenger = clip_delta(model["ceil_hgb"].predict(X))
    return clip_delta(0.8 * floor_anchor + 0.2 * floor_challenger), clip_delta(0.8 * ceil_anchor + 0.2 * ceil_challenger)


def make_ridge(alpha: float = 1.0) -> Pipeline:
    return Pipeline([
        ("imputer", SimpleImputer(strategy="median")),
        ("scaler", StandardScaler()),
        ("model", Ridge(alpha=alpha)),
    ])


def make_huber() -> Pipeline:
    return Pipeline([
        ("imputer", SimpleImputer(strategy="median")),
        ("scaler", StandardScaler()),
        ("model", HuberRegressor(epsilon=1.35, alpha=1e-4, max_iter=400, tol=1e-6)),
    ])


def residual_fit(train: pd.DataFrame, kind: str) -> dict[str, Any]:
    base = atr_fit(train)
    bf, bc = atr_predict(base, train)
    X = feature_matrix(train, CORE_FEATURES)
    yf = train["floor_delta"].to_numpy(float) - bf
    yc = train["ceiling_delta"].to_numpy(float) - bc
    if kind == "ridge":
        fm, cm = make_ridge(1.0), make_ridge(1.0)
        fm.fit(X, yf); cm.fit(X, yc)
    elif kind == "huber":
        fm, cm = make_huber(), make_huber()
        fm.fit(X, yf); cm.fit(X, yc)
    elif kind == "boosted":
        X0 = np.nan_to_num(X, nan=0.0, posinf=0.0, neginf=0.0)
        params = dict(HGB_PARAMS)
        params.update(max_iter=60, min_samples_leaf=80, max_leaf_nodes=7)
        fm = HistGradientBoostingRegressor(**params).fit(X0, yf)
        cm = HistGradientBoostingRegressor(**params).fit(X0, yc)
    else:
        raise ValueError(kind)
    return {"base": base, "floor_model": fm, "ceiling_model": cm, "kind": kind}


def residual_predict(model: dict[str, Any], frame: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    bf, bc = atr_predict(model["base"], frame)
    X = feature_matrix(frame, CORE_FEATURES)
    if model["kind"] == "boosted":
        X = np.nan_to_num(X, nan=0.0, posinf=0.0, neginf=0.0)
    return clip_delta(bf + model["floor_model"].predict(X)), clip_delta(bc + model["ceiling_model"].predict(X))


def ratio_fit(train: pd.DataFrame) -> dict[str, Any]:
    atr = np.maximum(train["atr_norm"].to_numpy(float), 1e-6)
    X = feature_matrix(train, CORE_FEATURES)
    yf = np.log(np.maximum(train["floor_delta"].to_numpy(float) / atr, 1e-4))
    yc = np.log(np.maximum(train["ceiling_delta"].to_numpy(float) / atr, 1e-4))
    fm, cm = make_ridge(1.0), make_ridge(1.0)
    fm.fit(X, yf); cm.fit(X, yc)
    return {"floor_model": fm, "ceiling_model": cm}


def ratio_predict(model: dict[str, Any], frame: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    X = feature_matrix(frame, CORE_FEATURES)
    atr = np.maximum(frame["atr_norm"].to_numpy(float), 1e-6)
    return clip_delta(np.exp(model["floor_model"].predict(X)) * atr), clip_delta(np.exp(model["ceiling_model"].predict(X)) * atr)


def spread_asym_fit(train: pd.DataFrame) -> dict[str, Any]:
    atr = np.maximum(train["atr_norm"].to_numpy(float), 1e-6)
    width = train["actual_width_delta"].to_numpy(float)
    asym = (train["ceiling_delta"].to_numpy(float) - train["floor_delta"].to_numpy(float)) / np.maximum(width, 1e-6)
    X = feature_matrix(train, CORE_FEATURES)
    wm, am = make_ridge(1.0), make_ridge(1.0)
    wm.fit(X, np.log(np.maximum(width / atr, 1e-4)))
    am.fit(X, np.clip(asym, -0.95, 0.95))
    return {"width_model": wm, "asym_model": am}


def spread_asym_predict(model: dict[str, Any], frame: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    X = feature_matrix(frame, CORE_FEATURES)
    atr = np.maximum(frame["atr_norm"].to_numpy(float), 1e-6)
    width = np.exp(model["width_model"].predict(X)) * atr
    asym = np.clip(model["asym_model"].predict(X), -0.95, 0.95)
    floor = width * (1.0 - asym) / 2.0
    ceiling = width * (1.0 + asym) / 2.0
    return clip_delta(floor), clip_delta(ceiling)


def prediction_metrics(frame: pd.DataFrame, pf: np.ndarray, pc: np.ndarray) -> dict[str, float]:
    af = frame["floor_delta"].to_numpy(float)
    ac = frame["ceiling_delta"].to_numpy(float)
    close = frame["close"].to_numpy(float)
    ef = np.abs(pf - af)
    ec = np.abs(pc - ac)
    es = np.abs((pf + pc) - (af + ac))
    return {
        "mae_floor": float(np.mean(ef * close)),
        "mae_ceiling": float(np.mean(ec * close)),
        "mae_spread": float(np.mean(es * close)),
        "mae_floor_pct": float(np.mean(ef)),
        "mae_ceiling_pct": float(np.mean(ec)),
        "mae_spread_pct": float(np.mean(es)),
        "central_interval_coverage": float(np.mean((af <= pf) & (ac <= pc))),
    }


def per_row_losses(frame: pd.DataFrame, pf: np.ndarray, pc: np.ndarray) -> pd.DataFrame:
    af = frame["floor_delta"].to_numpy(float)
    ac = frame["ceiling_delta"].to_numpy(float)
    out = frame[["date", "symbol", "vol_regime", "regime_key", "period", "close"]].copy()
    out["floor_loss_pct"] = np.abs(pf - af)
    out["ceiling_loss_pct"] = np.abs(pc - ac)
    out["spread_loss_pct"] = np.abs((pf + pc) - (af + ac))
    out["pred_floor_delta"] = pf
    out["pred_ceiling_delta"] = pc
    return out


def skill(loss: float, base_loss: float) -> float:
    return float(1.0 - loss / base_loss) if base_loss > 0 else 0.0


def objective_with_guard(frame: pd.DataFrame, pred: tuple[np.ndarray, np.ndarray], atr_pred: tuple[np.ndarray, np.ndarray]) -> tuple[float, float, float, float]:
    m = prediction_metrics(frame, *pred)
    a = prediction_metrics(frame, *atr_pred)
    floor_reg = m["mae_floor_pct"] / max(a["mae_floor_pct"], 1e-12) - 1.0
    ceil_reg = m["mae_ceiling_pct"] / max(a["mae_ceiling_pct"], 1e-12) - 1.0
    penalty = max(0.0, floor_reg - BOUNDARY_GUARD) + max(0.0, ceil_reg - BOUNDARY_GUARD)
    return (m["mae_spread_pct"] + 2.0 * penalty, m["mae_spread_pct"], floor_reg, ceil_reg)


def choose_lambda(core: pd.DataFrame, tune: pd.DataFrame) -> float:
    atr_m = atr_fit(core); cur_m = current_fit(core)
    atr_p = atr_predict(atr_m, tune); cur_p = current_predict(cur_m, tune)
    candidates = []
    for lam in np.linspace(0.0, 1.0, 11):
        pred = ((1-lam)*atr_p[0] + lam*cur_p[0], (1-lam)*atr_p[1] + lam*cur_p[1])
        candidates.append((objective_with_guard(tune, pred, atr_p), float(lam)))
    candidates.sort(key=lambda x: x[0])
    return candidates[0][1]


ENSEMBLE_WEIGHTS = (
    (1.00, 0.00, 0.00, 0.00),
    (0.50, 0.50, 0.00, 0.00),
    (0.50, 0.00, 0.50, 0.00),
    (0.50, 0.00, 0.00, 0.50),
    (0.50, 0.25, 0.00, 0.25),
    (0.50, 0.00, 0.25, 0.25),
    (0.40, 0.20, 0.20, 0.20),
    (0.25, 0.25, 0.25, 0.25),
)


def fit_base_pool(train: pd.DataFrame) -> dict[str, Any]:
    return {
        "atr": atr_fit(train),
        "ridge": residual_fit(train, "ridge"),
        "huber": residual_fit(train, "huber"),
        "boosted": residual_fit(train, "boosted"),
    }


def pool_predict(pool: dict[str, Any], frame: pd.DataFrame) -> dict[str, tuple[np.ndarray, np.ndarray]]:
    return {
        "atr": atr_predict(pool["atr"], frame),
        "ridge": residual_predict(pool["ridge"], frame),
        "huber": residual_predict(pool["huber"], frame),
        "boosted": residual_predict(pool["boosted"], frame),
    }


def combine_pool(preds: dict[str, tuple[np.ndarray, np.ndarray]], weights: tuple[float, float, float, float]) -> tuple[np.ndarray, np.ndarray]:
    names = ("atr", "ridge", "huber", "boosted")
    pf = sum(w * preds[n][0] for w, n in zip(weights, names))
    pc = sum(w * preds[n][1] for w, n in zip(weights, names))
    return clip_delta(pf), clip_delta(pc)


def choose_ensemble_weights(core: pd.DataFrame, tune: pd.DataFrame) -> tuple[float, float, float, float]:
    pool = fit_base_pool(core)
    preds = pool_predict(pool, tune)
    atr_pred = preds["atr"]
    scored = [(objective_with_guard(tune, combine_pool(preds, w), atr_pred), w) for w in ENSEMBLE_WEIGHTS]
    scored.sort(key=lambda x: x[0])
    return scored[0][1]


def fit_family(name: str, train: pd.DataFrame, horizon: str) -> tuple[Any, dict[str, Any]]:
    meta: dict[str, Any] = {}
    if name == "atr_only":
        return atr_fit(train), meta
    if name == "adaptive_atr":
        return adaptive_atr_fit(train), meta
    if name == "current_model":
        return current_fit(train), meta
    if name == "residual_ridge":
        return residual_fit(train, "ridge"), meta
    if name == "residual_huber":
        return residual_fit(train, "huber"), meta
    if name == "residual_boosted":
        return residual_fit(train, "boosted"), meta
    if name == "ratio_ridge":
        return ratio_fit(train), meta
    if name == "spread_asymmetry":
        return spread_asym_fit(train), meta
    if name == "atr_model_shrinkage":
        core, tune = inner_split(train, horizon)
        lam = choose_lambda(core, tune)
        meta["lambda"] = lam
        return {"atr": atr_fit(train), "current": current_fit(train), "lambda": lam}, meta
    if name == "conservative_ensemble":
        core, tune = inner_split(train, horizon)
        weights = choose_ensemble_weights(core, tune)
        meta["weights"] = list(weights)
        return {"pool": fit_base_pool(train), "weights": weights}, meta
    raise ValueError(name)


def predict_family(name: str, model: Any, frame: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    if name == "atr_only": return atr_predict(model, frame)
    if name == "adaptive_atr": return adaptive_atr_predict(model, frame)
    if name == "current_model": return current_predict(model, frame)
    if name.startswith("residual_"): return residual_predict(model, frame)
    if name == "ratio_ridge": return ratio_predict(model, frame)
    if name == "spread_asymmetry": return spread_asym_predict(model, frame)
    if name == "atr_model_shrinkage":
        a = atr_predict(model["atr"], frame); c = current_predict(model["current"], frame); lam = model["lambda"]
        return clip_delta((1-lam)*a[0] + lam*c[0]), clip_delta((1-lam)*a[1] + lam*c[1])
    if name == "conservative_ensemble":
        return combine_pool(pool_predict(model["pool"], frame), model["weights"])
    raise ValueError(name)


def moving_block_bootstrap_skill(loss_df: pd.DataFrame, candidate: str, reps: int, block_len: int) -> dict[str, float]:
    tmp = loss_df[loss_df["family"].isin([candidate, "atr_only"])].copy()
    pivot = tmp.groupby(["date", "family"], observed=True)["spread_loss_pct"].mean().unstack("family").dropna()
    if candidate == "atr_only":
        return {"estimate": 0.0, "ci_low": 0.0, "ci_high": 0.0, "prob_gt_zero": 0.0, "dates": int(len(pivot)), "block_length": block_len}
    arr_c = pivot[candidate].to_numpy(float); arr_a = pivot["atr_only"].to_numpy(float)
    estimate = skill(float(arr_c.mean()), float(arr_a.mean()))
    n = len(pivot)
    if n < block_len * 2:
        return {"estimate": estimate, "ci_low": float("nan"), "ci_high": float("nan"), "prob_gt_zero": float("nan"), "dates": n, "block_length": block_len}
    starts = np.arange(0, n - block_len + 1)
    sims = np.empty(reps, dtype=float)
    for b in range(reps):
        idxs = []
        while len(idxs) < n:
            s = int(RNG.choice(starts))
            idxs.extend(range(s, s + block_len))
        idx = np.asarray(idxs[:n])
        sims[b] = skill(float(arr_c[idx].mean()), float(arr_a[idx].mean()))
    return {
        "estimate": estimate,
        "ci_low": float(np.quantile(sims, 0.025)),
        "ci_high": float(np.quantile(sims, 0.975)),
        "prob_gt_zero": float(np.mean(sims > 0.0)),
        "dates": n,
        "block_length": block_len,
    }


def aggregate_slice(losses: pd.DataFrame, by: str, horizon: str) -> pd.DataFrame:
    rows = []
    base = losses[losses["family"] == "atr_only"]
    for family in FAMILY_ORDER:
        fam = losses[losses["family"] == family]
        for key, group in fam.groupby(by, observed=True):
            b = base[base[by] == key]
            if len(group) < 5 or len(b) < 5:
                continue
            rows.append({
                "horizon": horizon, "family": family, by: str(key), "rows": int(len(group)),
                "mae_floor_pct": float(group["floor_loss_pct"].mean()),
                "mae_ceiling_pct": float(group["ceiling_loss_pct"].mean()),
                "mae_spread_pct": float(group["spread_loss_pct"].mean()),
                "skill_vs_atr": skill(float(group["spread_loss_pct"].mean()), float(b["spread_loss_pct"].mean())),
            })
    return pd.DataFrame(rows)


def run_horizon(frame: pd.DataFrame, horizon: str, outdir: Path, *, write_row_losses: bool = False) -> dict[str, Any]:
    print(f"[{horizon}] preparing folds", flush=True)
    pretest = frame[frame["split"].isin(["train", "validation"])].copy()
    test = frame[frame["split"] == "test"].copy()
    test_start = test["date"].min()
    target_col = f"target_end_date_{horizon}"
    pretest = pretest[pretest[target_col] < test_start].copy()
    folds = make_outer_folds(pretest, horizon)

    fold_rows = []
    loss_parts = []
    compute: dict[str, float] = {f: 0.0 for f in FAMILY_ORDER}
    hyper_meta: dict[str, list[Any]] = {f: [] for f in FAMILY_ORDER}

    for fold in folds:
        print(f"[{horizon}] fold {fold['fold']} {fold['valid_start'].date()}..{fold['valid_end'].date()} train={len(fold['train'])} valid={len(fold['valid'])}", flush=True)
        train, valid = fold["train"], fold["valid"]
        global_model = median_fit(train); global_pred = median_predict(global_model, valid)
        global_metrics = prediction_metrics(valid, *global_pred)
        fold_predictions: dict[str, tuple[np.ndarray, np.ndarray]] = {}
        for family in FAMILY_ORDER:
            t0 = time.perf_counter()
            model, meta = fit_family(family, train, horizon)
            pred = predict_family(family, model, valid)
            elapsed = time.perf_counter() - t0
            compute[family] += elapsed
            hyper_meta[family].append(meta)
            fold_predictions[family] = pred
            m = prediction_metrics(valid, *pred)
            fold_rows.append({
                "horizon": horizon, "fold": fold["fold"], "family": family,
                "valid_start": str(fold["valid_start"].date()), "valid_end": str(fold["valid_end"].date()),
                "train_rows": int(len(train)), "valid_rows": int(len(valid)), **m,
                "global_median_mae_spread_pct": global_metrics["mae_spread_pct"],
                "fit_predict_seconds": elapsed,
                "hyper_meta": json.dumps(meta, sort_keys=True),
            })
        atr_m = next(x for x in fold_rows if x["horizon"] == horizon and x["fold"] == fold["fold"] and x["family"] == "atr_only")
        for row in fold_rows:
            if row["horizon"] == horizon and row["fold"] == fold["fold"]:
                row["skill_vs_atr"] = skill(row["mae_spread_pct"], atr_m["mae_spread_pct"])
                row["skill_vs_global_median"] = skill(row["mae_spread_pct"], global_metrics["mae_spread_pct"])
                row["floor_regression_vs_atr"] = row["mae_floor_pct"] / atr_m["mae_floor_pct"] - 1.0
                row["ceiling_regression_vs_atr"] = row["mae_ceiling_pct"] / atr_m["mae_ceiling_pct"] - 1.0
                row["qualified_win_vs_atr"] = bool(row["skill_vs_atr"] > 0 and row["floor_regression_vs_atr"] <= BOUNDARY_GUARD and row["ceiling_regression_vs_atr"] <= BOUNDARY_GUARD)
        for family, pred in fold_predictions.items():
            part = per_row_losses(valid, *pred)
            part["horizon"] = horizon; part["fold"] = fold["fold"]; part["family"] = family
            loss_parts.append(part)

    fold_df = pd.DataFrame(fold_rows)
    losses = pd.concat(loss_parts, ignore_index=True)

    # Final locked test: tune only on pretest, then refit on all leakage-safe pretest.
    print(f"[{horizon}] locked test evaluation rows={len(test)}", flush=True)
    final_rows = []
    final_loss_parts = []
    final_global = median_fit(pretest); final_global_pred = median_predict(final_global, test)
    final_global_metrics = prediction_metrics(test, *final_global_pred)
    final_meta = {}
    for family in FAMILY_ORDER:
        t0 = time.perf_counter()
        model, meta = fit_family(family, pretest, horizon)
        pred = predict_family(family, model, test)
        elapsed = time.perf_counter() - t0
        compute[family] += elapsed
        m = prediction_metrics(test, *pred)
        final_meta[family] = meta
        final_rows.append({"horizon": horizon, "family": family, "rows": int(len(test)), **m, "fit_predict_seconds": elapsed, "hyper_meta": json.dumps(meta, sort_keys=True)})
        part = per_row_losses(test, *pred); part["horizon"] = horizon; part["family"] = family
        final_loss_parts.append(part)
    test_df = pd.DataFrame(final_rows)
    atr_test = test_df[test_df["family"] == "atr_only"].iloc[0]
    for i in test_df.index:
        test_df.loc[i, "skill_vs_atr"] = skill(test_df.loc[i, "mae_spread_pct"], atr_test["mae_spread_pct"])
        test_df.loc[i, "skill_vs_global_median"] = skill(test_df.loc[i, "mae_spread_pct"], final_global_metrics["mae_spread_pct"])
        test_df.loc[i, "floor_regression_vs_atr"] = test_df.loc[i, "mae_floor_pct"] / atr_test["mae_floor_pct"] - 1.0
        test_df.loc[i, "ceiling_regression_vs_atr"] = test_df.loc[i, "mae_ceiling_pct"] / atr_test["mae_ceiling_pct"] - 1.0
        test_df.loc[i, "qualified_win_vs_atr"] = bool(test_df.loc[i, "skill_vs_atr"] > 0 and test_df.loc[i, "floor_regression_vs_atr"] <= BOUNDARY_GUARD and test_df.loc[i, "ceiling_regression_vs_atr"] <= BOUNDARY_GUARD)
    test_losses = pd.concat(final_loss_parts, ignore_index=True)

    summaries = []
    bootstrap = {}
    recent_folds = sorted(fold_df["fold"].unique())[-2:]
    for family in FAMILY_ORDER:
        fd = fold_df[fold_df["family"] == family].sort_values("fold")
        fam_loss = losses[losses["family"] == family]
        atr_loss = losses[losses["family"] == "atr_only"]
        global_mean = float(fd["global_median_mae_spread_pct"].mean())
        pooled = {
            "mae_floor": float((fam_loss["floor_loss_pct"] * fam_loss["close"]).mean()),
            "mae_ceiling": float((fam_loss["ceiling_loss_pct"] * fam_loss["close"]).mean()),
            "mae_spread": float((fam_loss["spread_loss_pct"] * fam_loss["close"]).mean()),
            "mae_floor_pct": float(fam_loss["floor_loss_pct"].mean()),
            "mae_ceiling_pct": float(fam_loss["ceiling_loss_pct"].mean()),
            "mae_spread_pct": float(fam_loss["spread_loss_pct"].mean()),
        }
        atr_spread = float(atr_loss["spread_loss_pct"].mean())
        recent = fd[fd["fold"].isin(recent_folds)]
        bt = moving_block_bootstrap_skill(losses, family, BOOTSTRAP_REPS, BLOCK_LENGTH[horizon])
        bootstrap[f"{horizon}:{family}"] = bt
        test_row = test_df[test_df["family"] == family].iloc[0]
        summaries.append({
            "horizon": horizon, "family": family, "label": FAMILY_LABELS[family],
            **pooled,
            "skill_vs_atr": skill(pooled["mae_spread_pct"], atr_spread),
            "skill_vs_global_median": skill(pooled["mae_spread_pct"], global_mean),
            "folds_won": int((fd["skill_vs_atr"] > 0).sum()),
            "fold_win_pct": float((fd["skill_vs_atr"] > 0).mean()),
            "qualified_folds_won": int(fd["qualified_win_vs_atr"].sum()),
            "qualified_fold_win_pct": float(fd["qualified_win_vs_atr"].mean()),
            "fold_skill_min": float(fd["skill_vs_atr"].min()),
            "fold_skill_median": float(fd["skill_vs_atr"].median()),
            "fold_skill_max": float(fd["skill_vs_atr"].max()),
            "recent_2fold_skill_mean": float(recent["skill_vs_atr"].mean()),
            "bootstrap_ci_low": bt["ci_low"], "bootstrap_ci_high": bt["ci_high"],
            "bootstrap_prob_skill_gt_zero": bt["prob_gt_zero"],
            "test_mae_floor_pct": float(test_row["mae_floor_pct"]),
            "test_mae_ceiling_pct": float(test_row["mae_ceiling_pct"]),
            "test_mae_spread_pct": float(test_row["mae_spread_pct"]),
            "test_skill_vs_atr": float(test_row["skill_vs_atr"]),
            "test_skill_vs_global_median": float(test_row["skill_vs_global_median"]),
            "test_floor_regression_vs_atr": float(test_row["floor_regression_vs_atr"]),
            "test_ceiling_regression_vs_atr": float(test_row["ceiling_regression_vs_atr"]),
            "compute_seconds": float(compute[family]), "complexity": COMPLEXITY[family],
        })

    summary_df = pd.DataFrame(summaries)
    ticker_df = aggregate_slice(losses, "symbol", horizon)
    regime_df = aggregate_slice(losses, "vol_regime", horizon)
    period_df = aggregate_slice(losses, "period", horizon)

    test_ticker_df = aggregate_slice(test_losses, "symbol", horizon)
    test_regime_df = aggregate_slice(test_losses, "vol_regime", horizon)
    test_period_df = aggregate_slice(test_losses, "period", horizon)

    compact_outputs = (("fold_results", fold_df), ("test_results", test_df), ("ticker_results", ticker_df), ("regime_results", regime_df), ("period_results", period_df), ("test_ticker_results", test_ticker_df), ("test_regime_results", test_regime_df), ("test_period_results", test_period_df), ("summary", summary_df))
    for name, d in compact_outputs:
        d.to_csv(outdir / f"{horizon}_{name}.csv", index=False)
    if write_row_losses:
        losses.to_csv(outdir / f"{horizon}_oos_losses.csv", index=False)
        test_losses.to_csv(outdir / f"{horizon}_test_losses.csv", index=False)

    return {"summary": summary_df, "fold": fold_df, "losses": losses, "test": test_df, "ticker": ticker_df, "regime": regime_df, "period": period_df, "test_ticker": test_ticker_df, "test_regime": test_regime_df, "test_period": test_period_df, "bootstrap": bootstrap, "final_meta": final_meta,
            "dates": {"pretest_start": str(pretest["date"].min().date()), "pretest_end": str(pretest["date"].max().date()), "test_start": str(test["date"].min().date()), "test_end": str(test["date"].max().date())}}


def candidate_status(row: pd.Series) -> str:
    """Pre-test-only evidence label; locked test never affects selection/status."""
    if row["family"] == "atr_only":
        return "baseline"
    broad = (
        row["skill_vs_atr"] > 0
        and row["qualified_folds_won"] >= 4
        and row["bootstrap_ci_low"] > 0
    )
    if broad:
        return "promising"
    if row["skill_vs_atr"] > 0 and row["folds_won"] >= 3:
        return "mixed"
    return "not_supported"


def select_next_stage(summary: pd.DataFrame) -> list[str]:
    """Select cross-horizon candidates using pre-test evidence only."""
    complexity_penalty = {
        "adaptive_atr": 0.000,
        "residual_ridge": 0.010,
        "residual_huber": 0.035,
        "residual_boosted": 0.010,
        "ratio_ridge": 0.010,
        "spread_asymmetry": 0.002,
        "atr_model_shrinkage": 0.010,
        "conservative_ensemble": 0.050,
        "current_model": 0.015,
    }
    candidates: list[tuple[float, str]] = []
    for family in FAMILY_ORDER:
        if family == "atr_only":
            continue
        rows = summary[summary["family"] == family]
        if set(rows["horizon"]) != {"d1", "w1"}:
            continue
        if not all(
            (float(r.skill_vs_atr) > 0)
            and (int(r.qualified_folds_won) >= 4)
            and (float(r.bootstrap_ci_low) > 0)
            for r in rows.itertuples()
        ):
            continue
        mean_skill = float(rows["skill_vs_atr"].mean())
        recent = float(rows["recent_2fold_skill_mean"].mean())
        score = mean_skill + 0.25 * recent - complexity_penalty.get(family, 0.02)
        candidates.append((score, family))
    candidates.sort(reverse=True)
    return [family for _, family in candidates[:3]]


def markdown_table(df: pd.DataFrame, cols: list[str]) -> str:
    show = df[cols].copy()
    for c in show.columns:
        if pd.api.types.is_float_dtype(show[c]):
            show[c] = show[c].map(lambda x: f"{x:.4f}")
    return show.to_markdown(index=False)


def build_report(results: dict[str, Any], outdir: Path, source_meta: dict[str, Any]) -> None:
    summary = pd.concat([results[h]["summary"] for h in ("d1", "w1")], ignore_index=True)
    summary["status"] = summary.apply(candidate_status, axis=1)
    summary.to_csv(outdir / "summary_all.csv", index=False)
    combined_fold = pd.concat([results[h]["fold"] for h in ("d1", "w1")], ignore_index=True)
    combined_fold.to_csv(outdir / "fold_results_all.csv", index=False)
    combined_ticker = pd.concat([results[h]["ticker"] for h in ("d1", "w1")], ignore_index=True)
    combined_ticker.to_csv(outdir / "ticker_results_all.csv", index=False)
    combined_regime = pd.concat([results[h]["regime"] for h in ("d1", "w1")], ignore_index=True)
    combined_regime.to_csv(outdir / "regime_results_all.csv", index=False)
    combined_period = pd.concat([results[h]["period"] for h in ("d1", "w1")], ignore_index=True)
    combined_period.to_csv(outdir / "period_results_all.csv", index=False)
    combined_test = pd.concat([results[h]["test"] for h in ("d1", "w1")], ignore_index=True)
    combined_test.to_csv(outdir / "test_results_all.csv", index=False)

    bootstrap = {}
    for h in ("d1", "w1"): bootstrap.update(results[h]["bootstrap"])
    (outdir / "bootstrap.json").write_text(json.dumps(bootstrap, indent=2, sort_keys=True), encoding="utf-8")

    # Cross-horizon next-stage selection is deliberately pre-test only.
    recommendations = select_next_stage(summary)

    metadata = {
        "generated_at_utc": pd.Timestamp.utcnow().isoformat(),
        "seed": SEED,
        "source": source_meta,
        "protocol": {
            "horizons": ["d1", "w1"], "outer_folds": N_FOLDS, "validation_sessions_per_fold": VALID_SESSIONS,
            "embargo_sessions": HORIZON_SESSIONS, "purge": "target_end_date < validation_start",
            "test_selection": "never; final locked test evaluated after configurations frozen",
            "bootstrap": {"method": "moving block bootstrap on date-level mean spread loss", "reps": BOOTSTRAP_REPS, "block_length": BLOCK_LENGTH},
            "boundary_guard": BOUNDARY_GUARD,
        },
        "features": list(CORE_FEATURES),
        "families": list(FAMILY_ORDER),
        "recommendations": recommendations,
        "recommendation_selection": {
            "uses_locked_test": False,
            "rule": "positive pooled skill in D1/W1 + >=4 qualified folds each + bootstrap lower bound > 0 + small complexity penalty",
        },
        "date_ranges": {h: results[h]["dates"] for h in ("d1", "w1")},
    }
    (outdir / "metadata.json").write_text(json.dumps(metadata, indent=2, sort_keys=True), encoding="utf-8")

    lines = [
        "# D1/W1 central-skill experiment results",
        "",
        "This report is experimental only. It does not alter production champions, risk geometry, conformal calibration, or strategy alpha.",
        "",
        "## Protocol",
        "",
        f"- Source ABT: `{source_meta.get('artifact_name')}` from workflow run `{source_meta.get('workflow_run_id')}` / head `{source_meta.get('head_sha')}`.",
        f"- Five expanding OOS folds, each {VALID_SESSIONS} market sessions.",
        "- Training is purged using each horizon's `target_end_date`; an additional horizon-length embargo is applied before every validation block.",
        "- The original test split is never used to select a family or tune shrinkage/ensemble weights. It is evaluated once after each configuration is frozen from pre-test data.",
        "- ATR-only is refit on every training fold and scored on exactly the same rows as every candidate.",
        f"- 95% uncertainty uses {BOOTSTRAP_REPS} moving-block bootstrap resamples of date-level mean spread loss (D1 block={BLOCK_LENGTH['d1']}, W1 block={BLOCK_LENGTH['w1']}).",
        "- A qualified fold win requires positive spread skill and neither floor nor ceiling MAE to regress more than 2% versus ATR.",
        "",
        "## Comparative summary",
        "",
    ]
    cols = ["horizon", "label", "skill_vs_atr", "folds_won", "qualified_folds_won", "fold_skill_median", "bootstrap_ci_low", "bootstrap_ci_high", "test_skill_vs_atr", "test_floor_regression_vs_atr", "test_ceiling_regression_vs_atr", "compute_seconds", "status"]
    lines.append(markdown_table(summary, cols))
    lines += ["", "## Recommended next-stage candidates (selected without test)", ""]
    for fam in recommendations:
        rows = summary[summary.family == fam]
        d1 = rows[rows.horizon == "d1"].iloc[0]
        w1 = rows[rows.horizon == "w1"].iloc[0]
        lines.append(
            f"- **{FAMILY_LABELS[fam]}** — D1 pre-test {d1.skill_vs_atr:+.2%} "
            f"({int(d1.qualified_folds_won)}/5 qualified), W1 pre-test {w1.skill_vs_atr:+.2%} "
            f"({int(w1.qualified_folds_won)}/5 qualified). Locked-test diagnostics only: "
            f"D1 {d1.test_skill_vs_atr:+.2%}, W1 {w1.test_skill_vs_atr:+.2%}."
        )
    lines.append("")
    lines += [
        "## Interpretation guardrails", "",
        "- Central interval coverage is diagnostic only and is not used as evidence of central predictive skill.",
        "- The report does not use conformal/risk-width expansion and does not touch directional strategy metrics.",
        "- Candidate status and next-stage selection use pre-test evidence only. The locked test is confirmatory and never changes a recommendation.",
        "- Exact ticker/regime/period decompositions are in the companion CSV files.",
    ]
    (outdir / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--source-meta", default="{}", help="JSON metadata for provenance")
    ap.add_argument("--write-row-losses", action="store_true", help="Persist large row-level OOS/test loss files")
    args = ap.parse_args()
    outdir = Path(args.output); outdir.mkdir(parents=True, exist_ok=True)
    source_meta = json.loads(args.source_meta)
    t0 = time.perf_counter()
    df, payload = load_dataset(Path(args.dataset))
    print(f"loaded rows={len(df)} split_policy={payload.get('split_policy')}", flush=True)
    results = {}
    for horizon in ("d1", "w1"):
        results[horizon] = run_horizon(prepare_horizon(df, horizon), horizon, outdir, write_row_losses=args.write_row_losses)
    build_report(results, outdir, source_meta)
    print(f"done seconds={time.perf_counter()-t0:.1f} output={outdir}", flush=True)


if __name__ == "__main__":
    main()