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


def evaluate_m3_performance_and_stability(reference_m3: dict, current_m3: dict, thresholds: dict) -> dict:
    pinball_delta = float(current_m3.get("pinball_loss_m3", 0.0)) - float(reference_m3.get("pinball_loss_m3", 0.0))
    top1_drop = float(reference_m3.get("top1_accuracy_m3", 0.0)) - float(current_m3.get("top1_accuracy_m3", 0.0))
    top3_drop = float(reference_m3.get("top3_accuracy_m3", 0.0)) - float(current_m3.get("top3_accuracy_m3", 0.0))
    week_distance_delta = float(current_m3.get("week_distance_m3", 0.0)) - float(reference_m3.get("week_distance_m3", 0.0))
    champion_flip_rate_delta = float(current_m3.get("champion_flip_rate_m3", 0.0)) - float(reference_m3.get("champion_flip_rate_m3", 0.0))

    checks = {
        "pinball_loss_m3": pinball_delta,
        "top1_accuracy_m3_drop": top1_drop,
        "top3_accuracy_m3_drop": top3_drop,
        "week_distance_m3_delta": week_distance_delta,
        "champion_flip_rate_m3_delta": champion_flip_rate_delta,
    }

    state = "GREEN"
    if (
        pinball_delta >= float(thresholds["pinball_loss_m3_fail"])
        or top1_drop >= float(thresholds["top1_accuracy_m3_drop_fail"])
        or top3_drop >= float(thresholds["top3_accuracy_m3_drop_fail"])
        or week_distance_delta >= float(thresholds["week_distance_m3_fail"])
        or champion_flip_rate_delta >= float(thresholds["champion_flip_rate_m3_fail"])
    ):
        state = "RED"
    elif (
        pinball_delta >= float(thresholds["pinball_loss_m3_warn"])
        or top1_drop >= float(thresholds["top1_accuracy_m3_drop_warn"])
        or top3_drop >= float(thresholds["top3_accuracy_m3_drop_warn"])
        or week_distance_delta >= float(thresholds["week_distance_m3_warn"])
        or champion_flip_rate_delta >= float(thresholds["champion_flip_rate_m3_warn"])
    ):
        state = "YELLOW"

    target_lights = {
        "value_pinball_floor_m3": (
            "RED" if pinball_delta >= float(thresholds["pinball_loss_m3_fail"]) else "YELLOW" if pinball_delta >= float(thresholds["pinball_loss_m3_warn"]) else "GREEN"
        ),
        "timing_floor_week_m3_top1": (
            "RED" if top1_drop >= float(thresholds["top1_accuracy_m3_drop_fail"]) else "YELLOW" if top1_drop >= float(thresholds["top1_accuracy_m3_drop_warn"]) else "GREEN"
        ),
        "timing_floor_week_m3_top3": (
            "RED" if top3_drop >= float(thresholds["top3_accuracy_m3_drop_fail"]) else "YELLOW" if top3_drop >= float(thresholds["top3_accuracy_m3_drop_warn"]) else "GREEN"
        ),
        "timing_floor_week_m3_distance": (
            "RED" if week_distance_delta >= float(thresholds["week_distance_m3_fail"]) else "YELLOW" if week_distance_delta >= float(thresholds["week_distance_m3_warn"]) else "GREEN"
        ),
        "stability_champion_m3": (
            "RED" if champion_flip_rate_delta >= float(thresholds["champion_flip_rate_m3_fail"]) else "YELLOW" if champion_flip_rate_delta >= float(thresholds["champion_flip_rate_m3_warn"]) else "GREEN"
        ),
    }

    return {
        "state": state,
        "checks": checks,
        "target_traffic_lights": target_lights,
    }


def _expected_impact_if_not_retrained(traffic_light: str, m3_only_degraded: bool) -> str:
    if traffic_light == "RED" and m3_only_degraded:
        return "Alto impacto en horizonte largo: peor timing de suelo trimestral, mayor error de downside estructural y sesgo en sizing/priorización táctica de m3."
    if traffic_light == "RED":
        return "Alto impacto: deterioro sistémico de señales y riesgo de degradación operativa amplia si no se reentrena."
    if traffic_light == "YELLOW" and m3_only_degraded:
        return "Impacto moderado concentrado en m3: pérdida gradual de precisión en floor_m3/floor_week_m3 y menor calidad del contexto de riesgo largo."
    if traffic_light == "YELLOW":
        return "Impacto moderado: degradación parcial; mantener monitorización estrecha hasta la próxima ventana."
    return "Impacto bajo: estabilidad suficiente para diferir retraining sin coste material esperado."


def build_retraining_decision(components: dict, retraining_cfg: dict | None = None) -> dict:
    retraining_cfg = retraining_cfg or {}
    states = [v.get("state", "GREEN") for v in components.values()]
    traffic_light = _worst_state(states)

    m3_component_names = {
        "m3_value_timing_drift",
        "m3_data_quality",
        "m3_performance_stability",
    }
    m3_states = [components[k].get("state", "GREEN") for k in components if k in m3_component_names]
    non_m3_states = [components[k].get("state", "GREEN") for k in components if k not in m3_component_names]

    m3_agg_state = _worst_state(m3_states)
    non_m3_agg_state = _worst_state(non_m3_states)
    m3_only_degraded = _state_rank(m3_agg_state) >= _state_rank("YELLOW") and _state_rank(non_m3_agg_state) == 0

    if traffic_light == "RED":
        recommendation = "RETRAIN_NOW"
    elif traffic_light == "YELLOW":
        recommendation = "RETRAIN_SOON"
    else:
        recommendation = "SKIP_RETRAIN"

    m3_retraining_decision = "SKIP_M3_RETRAIN"
    if _state_rank(m3_agg_state) == 2:
        m3_retraining_decision = "RETRAIN_M3_NOW"
    elif _state_rank(m3_agg_state) == 1:
        m3_retraining_decision = "RETRAIN_M3_SOON"

    package_decision = "FULL_PACKAGE"
    if m3_only_degraded:
        package_decision = "M3_ONLY"

    executive = (
        f"Semáforo agregado={traffic_light} (m3={m3_agg_state}, core={non_m3_agg_state}). "
        f"Recomendación global={recommendation}; paquete quincenal recomendado={package_decision}; decisión m3={m3_retraining_decision}."
    )

    technical_lines = []
    for name, payload in components.items():
        details = {k: v for k, v in payload.items() if k != "state"}
        technical_lines.append(f"- {name}: state={payload.get('state')} details={details}")

    impact = _expected_impact_if_not_retrained(traffic_light=traffic_light, m3_only_degraded=m3_only_degraded)

    return {
        "traffic_light": traffic_light,
        "recommendation": recommendation,
        "m3_traffic_light": m3_agg_state,
        "non_m3_traffic_light": non_m3_agg_state,
        "m3_only_degraded": m3_only_degraded,
        "retraining_package_decision": package_decision,
        "m3_retraining_decision": m3_retraining_decision,
        "executive_explanation": executive,
        "technical_explanation": "\n".join(technical_lines),
        "impact_if_not_retrained": impact,
        "components": components,
    }
