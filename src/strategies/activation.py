from __future__ import annotations

from dataclasses import asdict, dataclass


VALID_MODES = {"backtest", "paper", "live"}


@dataclass(frozen=True)
class ActivationDecision:
    strategy_id: str
    mode: str
    allowed: bool
    reason: str
    readiness: str
    canonical_serving_enabled: bool
    promotion_eligible: bool

    def to_dict(self) -> dict:
        return asdict(self)


def _is_true(mapping: dict, key: str) -> bool:
    """Fail closed: only the literal boolean True enables an activation gate."""
    return mapping.get(key) is True


def strategy_activation_decision(config: dict, strategy_id: str, mode: str) -> ActivationDecision:
    normalized_mode = str(mode or "").strip().lower()
    if normalized_mode not in VALID_MODES:
        raise ValueError(f"Unsupported strategy mode: {mode!r}; expected one of {sorted(VALID_MODES)}")

    strategies = config.get("strategies")
    if not isinstance(strategies, dict) or strategy_id not in strategies:
        return ActivationDecision(
            strategy_id=strategy_id,
            mode=normalized_mode,
            allowed=False,
            reason="strategy_not_registered",
            readiness="missing",
            canonical_serving_enabled=False,
            promotion_eligible=False,
        )

    strategy_cfg = strategies[strategy_id]
    if not isinstance(strategy_cfg, dict):
        return ActivationDecision(
            strategy_id=strategy_id,
            mode=normalized_mode,
            allowed=False,
            reason="invalid_strategy_config",
            readiness="invalid",
            canonical_serving_enabled=False,
            promotion_eligible=False,
        )

    readiness = str(strategy_cfg.get("readiness") or "unspecified")
    canonical_serving_enabled = _is_true(strategy_cfg, "canonical_serving_enabled")
    promotion_eligible = _is_true(strategy_cfg, "promotion_eligible")

    if normalized_mode == "backtest":
        allowed = _is_true(strategy_cfg, "research_enabled")
        reason = "research_enabled" if allowed else "research_disabled"
        return ActivationDecision(
            strategy_id=strategy_id,
            mode=normalized_mode,
            allowed=allowed,
            reason=reason,
            readiness=readiness,
            canonical_serving_enabled=canonical_serving_enabled,
            promotion_eligible=promotion_eligible,
        )

    activation_cfg = config.get("activation")
    if not isinstance(activation_cfg, dict):
        activation_cfg = {}

    if normalized_mode == "paper":
        # Paper is intentionally independent of canonical serving. A challenger may
        # earn prospective evidence in a simulated account before it is promoted.
        if not _is_true(activation_cfg, "paper_execution_enabled"):
            reason = "global_paper_disabled"
            allowed = False
        elif not _is_true(strategy_cfg, "paper_enabled"):
            reason = "strategy_paper_disabled"
            allowed = False
        else:
            reason = "paper_enabled"
            allowed = True
        return ActivationDecision(
            strategy_id=strategy_id,
            mode=normalized_mode,
            allowed=allowed,
            reason=reason,
            readiness=readiness,
            canonical_serving_enabled=canonical_serving_enabled,
            promotion_eligible=promotion_eligible,
        )

    if not _is_true(activation_cfg, "live_execution_enabled"):
        reason = "global_live_disabled"
        allowed = False
    elif not _is_true(strategy_cfg, "live_enabled"):
        reason = "strategy_live_disabled"
        allowed = False
    elif activation_cfg.get("require_canonical_serving", True) is not False and not canonical_serving_enabled:
        reason = "canonical_serving_disabled"
        allowed = False
    else:
        reason = "live_enabled"
        allowed = True

    return ActivationDecision(
        strategy_id=strategy_id,
        mode=normalized_mode,
        allowed=allowed,
        reason=reason,
        readiness=readiness,
        canonical_serving_enabled=canonical_serving_enabled,
        promotion_eligible=promotion_eligible,
    )


def activation_snapshot(config: dict, mode: str) -> dict[str, dict]:
    strategies = config.get("strategies", {})
    if not isinstance(strategies, dict):
        return {}
    return {
        strategy_id: strategy_activation_decision(config, strategy_id, mode).to_dict()
        for strategy_id in strategies
    }
