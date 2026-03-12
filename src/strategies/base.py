from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class StrategyDecision:
    strategy_id: str
    symbol: str
    side: str
    score: float
    qty: int
    horizon: str
    entry_reason: str
    exit_reason: str
    stop_price: float
    take_profit_price: float
    expected_return: float
    expected_range: float
    timing_alignment: float
    m3_context: dict[str, Any] | None = None
    priority_adjustment: int = 0
    blocked: bool = False
    blocked_reason: str = ""


def _safe_float(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def expected_cost_bps(global_cfg: dict) -> float:
    return _safe_float(global_cfg["costs"]["commission_bps"]) + _safe_float(global_cfg["costs"]["slippage_bps"])


def range_is_tradeable(row: dict, global_cfg: dict) -> bool:
    min_range_multiple = _safe_float(global_cfg["guards"]["min_range_vs_cost_multiple"])
    range_pct = _safe_float(row.get("expected_range_d1")) / max(_safe_float(row.get("close"), 1.0), 1e-9)
    cost_pct = expected_cost_bps(global_cfg) / 10000.0
    return range_pct >= (cost_pct * min_range_multiple)


def near_ceiling_for_long(row: dict, global_cfg: dict) -> bool:
    close = _safe_float(row.get("close"))
    ceiling = _safe_float(row.get("ceiling_d1"))
    if close <= 0 or ceiling <= 0:
        return True
    threshold = _safe_float(global_cfg["guards"]["no_long_if_within_ceiling_pct"])
    return ((ceiling - close) / close) <= threshold


def near_floor_for_short(row: dict, global_cfg: dict) -> bool:
    close = _safe_float(row.get("close"))
    floor = _safe_float(row.get("floor_d1"))
    if close <= 0 or floor <= 0:
        return True
    threshold = _safe_float(global_cfg["guards"]["no_short_if_within_floor_pct"])
    return ((close - floor) / close) <= threshold


def timing_alignment_score(row: dict, session: str, strategy_cfg: dict) -> float:
    preferred = set(str(x) for x in str(strategy_cfg.get("preferred_d1_buckets", "")).split(",") if str(x))
    bucket_floor = str(row.get("floor_time_bucket_d1") or "")
    bucket_ceiling = str(row.get("ceiling_time_bucket_d1") or "")
    session_bonus = _safe_float(strategy_cfg.get("timing_match_bonus", 0.0))
    base = _safe_float(strategy_cfg.get("timing_base_score", 0.5))

    score = base
    if session in preferred and (bucket_floor == session or bucket_ceiling == session):
        score += session_bonus
    if bucket_floor in preferred or bucket_ceiling in preferred:
        score += session_bonus * 0.5
    return max(0.0, min(1.0, score))


def position_size_from_risk(row: dict, strategy_cfg: dict, global_cfg: dict) -> int:
    nav = _safe_float(global_cfg["portfolio"]["nav_usd"])
    risk_pct = _safe_float(strategy_cfg["position_sizing"]["risk_budget_pct_nav"])
    max_notional = _safe_float(strategy_cfg["position_sizing"]["max_notional_usd"])
    close = max(_safe_float(row.get("close"), 1.0), 1e-9)
    budget = min(nav * risk_pct, max_notional)
    qty = int(max(0.0, budget / close))
    return max(0, qty)


def adjusted_position_size_from_risk(row: dict, strategy_cfg: dict, global_cfg: dict, size_multiplier: float) -> int:
    base_qty = position_size_from_risk(row, strategy_cfg, global_cfg)
    return int(max(0, base_qty * max(size_multiplier, 0.0)))


def m3_context_for_decision(row: dict, side: str, global_cfg: dict, strategy_cfg: dict) -> tuple[bool, str, dict[str, Any]]:
    cfg = global_cfg.get("m3_context", {})
    if not cfg.get("enabled", True):
        return True, "OK", {"enabled": False}

    close = _safe_float(row.get("close"), 0.0)
    floor_m3 = _safe_float(row.get("floor_m3"), 0.0)
    week = int(_safe_float(row.get("floor_week_m3"), 0.0))
    confidence = _safe_float(row.get("floor_week_m3_confidence"), 0.0)
    rr = _safe_float(row.get("reward_risk_ratio"), 0.0)

    near_weeks = int(cfg.get("near_weeks", 2))
    imminent_weeks = int(cfg.get("imminent_weeks", 1))
    far_weeks = int(cfg.get("far_weeks", 6))
    default_rr = _safe_float(cfg.get("default_min_reward_risk_ratio", 0.0))
    rr_when_near = _safe_float(cfg.get("min_reward_risk_ratio_when_near", default_rr))
    rr_when_far = _safe_float(cfg.get("min_reward_risk_ratio_when_far", default_rr))
    near_size_multiplier = _safe_float(cfg.get("size_multiplier_when_near", 1.0), 1.0)
    imminent_size_multiplier = _safe_float(cfg.get("size_multiplier_when_imminent", near_size_multiplier), near_size_multiplier)
    far_size_multiplier = _safe_float(cfg.get("size_multiplier_when_far", 1.0), 1.0)
    priority_penalty_near = int(_safe_float(cfg.get("priority_penalty_when_near", 0), 0.0))
    priority_boost_far = int(_safe_float(cfg.get("priority_boost_when_far", 0), 0.0))
    long_block_dist = _safe_float(cfg.get("tactical_long_block_if_above_floor_m3_pct", 1.0), 1.0)
    long_block_min_week = int(_safe_float(cfg.get("tactical_long_block_min_week", 1), 1.0))

    above_floor_pct = (close - floor_m3) / max(close, 1e-9) if close > 0 and floor_m3 > 0 else 0.0
    is_imminent = week > 0 and week <= imminent_weeks
    is_near = week > 0 and week <= near_weeks
    is_far_or_passed = week <= 0 or week >= far_weeks

    size_multiplier = 1.0
    priority_adjustment = 0
    rr_min = default_rr

    if is_near:
        size_multiplier = near_size_multiplier
        priority_adjustment += priority_penalty_near
        rr_min = max(rr_min, rr_when_near)
    if is_imminent:
        size_multiplier = min(size_multiplier, imminent_size_multiplier)
    if is_far_or_passed:
        size_multiplier = max(size_multiplier, far_size_multiplier)
        priority_adjustment -= priority_boost_far
        rr_min = max(rr_when_far, 0.0)

    contradictory_horizons: list[str] = []
    d1 = _safe_float(row.get("expected_return_d1"))
    w1 = _safe_float(row.get("expected_return_w1"))
    q1 = _safe_float(row.get("expected_return_q1"))
    m3_ret = _safe_float(row.get("expected_return_m3"))
    if side == "BUY" and m3_ret < 0:
        if d1 > 0:
            contradictory_horizons.append("d1")
        if w1 > 0:
            contradictory_horizons.append("w1")
        if q1 > 0:
            contradictory_horizons.append("q1")
    if side == "SELL" and m3_ret > 0:
        if d1 < 0:
            contradictory_horizons.append("d1")
        if w1 < 0:
            contradictory_horizons.append("w1")
        if q1 < 0:
            contradictory_horizons.append("q1")

    if rr < rr_min:
        return False, f"m3 risk filter: reward/risk {rr:.2f} below required {rr_min:.2f}", {
            "enabled": True,
            "floor_m3": floor_m3,
            "floor_week_m3": week,
            "floor_week_m3_confidence": confidence,
            "near_term_floor_week": is_near,
            "size_multiplier": size_multiplier,
            "priority_adjustment": priority_adjustment,
            "reward_risk_ratio": rr,
            "required_reward_risk": rr_min,
            "above_floor_m3_pct": above_floor_pct,
            "contradicts_horizons": contradictory_horizons,
        }

    if side == "BUY" and week >= long_block_min_week and week <= near_weeks and above_floor_pct >= long_block_dist:
        return False, "m3 context blocks tactical long: quarterly floor likely ahead while price remains well above floor_m3", {
            "enabled": True,
            "floor_m3": floor_m3,
            "floor_week_m3": week,
            "floor_week_m3_confidence": confidence,
            "near_term_floor_week": is_near,
            "size_multiplier": size_multiplier,
            "priority_adjustment": priority_adjustment,
            "reward_risk_ratio": rr,
            "required_reward_risk": rr_min,
            "above_floor_m3_pct": above_floor_pct,
            "contradicts_horizons": contradictory_horizons,
        }

    return True, "OK", {
        "enabled": True,
        "floor_m3": floor_m3,
        "floor_week_m3": week,
        "floor_week_m3_confidence": confidence,
        "near_term_floor_week": is_near,
        "size_multiplier": size_multiplier,
        "priority_adjustment": priority_adjustment,
        "reward_risk_ratio": rr,
        "required_reward_risk": rr_min,
        "above_floor_m3_pct": above_floor_pct,
        "contradicts_horizons": contradictory_horizons,
    }


def liquidity_ok(row: dict, strategy_cfg: dict) -> bool:
    adv = _safe_float(row.get("avg_dollar_volume", row.get("dollar_volume", 0.0)))
    min_adv = _safe_float(strategy_cfg["liquidity"]["min_avg_dollar_volume"])
    return adv >= min_adv


def common_entry_guards(row: dict, side: str, global_cfg: dict, strategy_cfg: dict) -> tuple[bool, str]:
    if not range_is_tradeable(row, global_cfg):
        return False, "Expected range too narrow versus costs"
    if side == "BUY" and near_ceiling_for_long(row, global_cfg):
        return False, "Long blocked: too close to expected ceiling"
    if side == "SELL" and near_floor_for_short(row, global_cfg):
        return False, "Short blocked: too close to expected floor"
    if not liquidity_ok(row, strategy_cfg):
        return False, "Liquidity below minimum"
    return True, "OK"


def build_order_payload(decision: StrategyDecision, strategy_cfg: dict, global_cfg: dict) -> dict:
    return {
        "strategy_id": decision.strategy_id,
        "symbol": decision.symbol,
        "side": decision.side,
        "qty": decision.qty,
        "score": round(decision.score, 6),
        "horizon": decision.horizon,
        "stop_price": round(decision.stop_price, 4),
        "take_profit_price": round(decision.take_profit_price, 4),
        "entry_reason": decision.entry_reason,
        "exit_reason": decision.exit_reason,
        "expected_return": round(decision.expected_return, 6),
        "expected_range": round(decision.expected_range, 6),
        "timing_alignment": round(decision.timing_alignment, 6),
        "cost_assumption_bps": expected_cost_bps(global_cfg),
        "cooldown_cycles": int(strategy_cfg["cooldown_cycles"]),
        "max_rotation_per_cycle": int(strategy_cfg["max_rotation_per_cycle"]),
        "m3_context": decision.m3_context or {},
        "priority_adjustment": int(decision.priority_adjustment),
    }
