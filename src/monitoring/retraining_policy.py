from __future__ import annotations


def _state_rank(state: str) -> int:
    return {"GREEN": 0, "YELLOW": 1, "RED": 2}.get(state, 0)


def _worst_state(states: list[str]) -> str:
    return max(states, key=_state_rank) if states else "GREEN"


def evaluate_performance_deterioration(reference_perf: dict, current_perf: dict, thresholds: dict) -> dict:
    pinball_delta = float(current_perf.get("pinball_loss", 0.0)) - float(reference_perf.get("pinball_loss", 0.0))
    breach_delta = abs(float(current_perf.get("breach_rate", 0.0)) - float(reference_perf.get("breach_rate", 0.0)))

    state = "GREEN"
    if pinball_delta >= float(thresholds["pinball_loss_fail"]) or breach_delta >= float(thresholds["breach_rate_fail"]):
        state = "RED"
    elif pinball_delta >= float(thresholds["pinball_loss_warn"]) or breach_delta >= float(thresholds["breach_rate_warn"]):
        state = "YELLOW"

    return {
        "state": state,
        "pinball_loss_delta": pinball_delta,
        "breach_rate_delta": breach_delta,
    }


def evaluate_timing_deterioration(reference_timing: dict, current_timing: dict, thresholds: dict) -> dict:
    # accuracy drops; others increase with worse performance.
    acc_drop = float(reference_timing.get("accuracy", 0.0)) - float(current_timing.get("accuracy", 0.0))
    log_loss_delta = float(current_timing.get("log_loss", 0.0)) - float(reference_timing.get("log_loss", 0.0))
    brier_delta = float(current_timing.get("brier_score", 0.0)) - float(reference_timing.get("brier_score", 0.0))
    timing_distance_delta = float(current_timing.get("timing_distance", 0.0)) - float(reference_timing.get("timing_distance", 0.0))

    state = "GREEN"
    if (
        acc_drop >= float(thresholds["accuracy_drop_fail"])
        or log_loss_delta >= float(thresholds["log_loss_fail"])
        or brier_delta >= float(thresholds["brier_fail"])
        or timing_distance_delta >= float(thresholds["timing_distance_fail"])
    ):
        state = "RED"
    elif (
        acc_drop >= float(thresholds["accuracy_drop_warn"])
        or log_loss_delta >= float(thresholds["log_loss_warn"])
        or brier_delta >= float(thresholds["brier_warn"])
        or timing_distance_delta >= float(thresholds["timing_distance_warn"])
    ):
        state = "YELLOW"

    return {
        "state": state,
        "accuracy_drop": acc_drop,
        "log_loss_delta": log_loss_delta,
        "brier_delta": brier_delta,
        "timing_distance_delta": timing_distance_delta,
    }


def evaluate_paper_trading_deterioration(reference_paper: dict, current_paper: dict, thresholds: dict) -> dict:
    ret_drop = float(reference_paper.get("strategy_return", 0.0)) - float(current_paper.get("strategy_return", 0.0))
    dd_increase = float(current_paper.get("max_drawdown", 0.0)) - float(reference_paper.get("max_drawdown", 0.0))
    sharpe_drop = float(reference_paper.get("sharpe", 0.0)) - float(current_paper.get("sharpe", 0.0))

    state = "GREEN"
    if (
        ret_drop >= float(thresholds["return_drop_fail"])
        or dd_increase >= float(thresholds["drawdown_increase_fail"])
        or sharpe_drop >= float(thresholds["sharpe_drop_fail"])
    ):
        state = "RED"
    elif (
        ret_drop >= float(thresholds["return_drop_warn"])
        or dd_increase >= float(thresholds["drawdown_increase_warn"])
        or sharpe_drop >= float(thresholds["sharpe_drop_warn"])
    ):
        state = "YELLOW"

    return {
        "state": state,
        "return_drop": ret_drop,
        "drawdown_increase": dd_increase,
        "sharpe_drop": sharpe_drop,
    }


def build_retraining_decision(components: dict) -> dict:
    states = [v.get("state", "GREEN") for v in components.values()]
    traffic_light = _worst_state(states)

    if traffic_light == "RED":
        recommendation = "RETRAIN_NOW"
    elif traffic_light == "YELLOW":
        recommendation = "RETRAIN_SOON"
    else:
        recommendation = "SKIP_RETRAIN"

    executive = (
        f"Semáforo {traffic_light}. Recomendación: {recommendation}. "
        "Evaluación quincenal completada sobre drift de datos/targets, calibración,"
        " performance de cuantiles, timing y paper trading."
    )

    technical_lines = []
    for name, payload in components.items():
        details = {k: v for k, v in payload.items() if k != "state"}
        technical_lines.append(f"- {name}: state={payload.get('state')} details={details}")

    return {
        "traffic_light": traffic_light,
        "recommendation": recommendation,
        "executive_explanation": executive,
        "technical_explanation": "\n".join(technical_lines),
        "components": components,
    }
