from __future__ import annotations

import math

from strategies.base import StrategyDecision


def _f(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def platform_fee_bps_per_side(cfg: dict) -> float:
    return _f(cfg.get("costs", {}).get("platform_fee_bps_per_side"), 0.0)


def round_trip_cost_bps(cfg: dict) -> float:
    costs = cfg.get("costs", {})
    broker = _f(costs.get("broker_commission_bps"), _f(costs.get("commission_bps"), 0.0))
    slippage = _f(costs.get("slippage_bps"), 0.0)
    return 2.0 * (broker + slippage + platform_fee_bps_per_side(cfg))


def _net(gross_pct: float, cfg: dict) -> float:
    return gross_pct - round_trip_cost_bps(cfg) / 10000.0


def _geometry(row: dict, horizon: str) -> dict[str, float]:
    close = _f(row.get("close"))
    floor = _f(row.get(f"floor_{horizon}"))
    ceiling = _f(row.get(f"ceiling_{horizon}"))
    if close <= 0 or floor <= 0 or ceiling <= floor:
        return {"close": close, "floor": floor, "ceiling": ceiling, "up": 0.0, "down": 0.0, "long_rr": 0.0, "short_rr": 0.0}
    up = max(0.0, ceiling - close) / close
    down = max(0.0, close - floor) / close
    return {
        "close": close,
        "floor": floor,
        "ceiling": ceiling,
        "up": up,
        "down": down,
        "long_rr": up / max(down, 1e-9),
        "short_rr": down / max(up, 1e-9),
    }


def _liquid(row: dict, strategy_cfg: dict) -> bool:
    adv = _f(row.get("avg_dollar_volume", row.get("dollar_volume", 0.0)))
    return adv >= _f(strategy_cfg.get("liquidity", {}).get("min_avg_dollar_volume"), 0.0)


def _size(row: dict, strategy_cfg: dict, global_cfg: dict, stop: float, multiplier: float = 1.0) -> int:
    close = _f(row.get("close"))
    if close <= 0 or stop <= 0:
        return 0
    nav = _f(global_cfg.get("portfolio", {}).get("nav_usd"))
    risk_pct = _f(strategy_cfg.get("position_sizing", {}).get("risk_budget_pct_nav"))
    max_notional = _f(strategy_cfg.get("position_sizing", {}).get("max_notional_usd"))
    risk_budget = nav * risk_pct * max(multiplier, 0.0)
    friction = close * round_trip_cost_bps(global_cfg) / 10000.0
    risk_per_share = abs(close - stop) + friction
    if risk_per_share <= 0:
        return 0
    by_risk = int(risk_budget / risk_per_share)
    by_notional = int(max_notional / close) if max_notional > 0 else by_risk
    return max(0, min(by_risk, by_notional))


def _m3(row: dict, action: str, cfg: dict) -> tuple[bool, float, int, dict]:
    m3 = cfg.get("m3_context", {})
    if not m3.get("enabled", True):
        return True, 1.0, 0, {"enabled": False}
    close = _f(row.get("close"))
    floor = _f(row.get("floor_m3"))
    week = int(_f(row.get("floor_week_m3")))
    conf = _f(row.get("floor_week_m3_confidence"))
    min_conf = _f(m3.get("min_timing_confidence"), 0.55)
    reliable = week > 0 and conf >= min_conf
    near = reliable and week <= int(m3.get("near_weeks", 2))
    imminent = reliable and week <= int(m3.get("imminent_weeks", 1))
    far = reliable and week >= int(m3.get("far_weeks", 6))
    above_floor = (close - floor) / max(close, 1e-9) if close > 0 and floor > 0 else 0.0

    multiplier = 1.0
    priority = 0
    if action == "BUY" and near:
        multiplier = _f(m3.get("size_multiplier_when_near_buy"), 0.65)
        priority += int(_f(m3.get("priority_penalty_when_near_buy"), 1))
        if imminent:
            multiplier = min(multiplier, _f(m3.get("size_multiplier_when_imminent_buy"), 0.50))
    elif action == "SELL" and near:
        multiplier = _f(m3.get("size_multiplier_when_near_sell"), 1.10)
        priority -= int(_f(m3.get("priority_boost_when_near_sell"), 1))
    elif far:
        multiplier = _f(m3.get("size_multiplier_when_far"), 1.05)

    ctx = {
        "enabled": True,
        "floor_m3": floor,
        "floor_week_m3": week,
        "floor_week_m3_confidence": conf,
        "timing_reliable": reliable,
        "near_term_floor_week": near,
        "size_multiplier": multiplier,
        "priority_adjustment": priority,
        "above_floor_m3_pct": above_floor,
    }
    if (
        action == "BUY"
        and near
        and week >= int(m3.get("tactical_long_block_min_week", 1))
        and above_floor >= _f(m3.get("tactical_long_block_if_above_floor_m3_pct"), 1.0)
    ):
        return False, multiplier, priority, ctx
    return True, multiplier, priority, ctx


def _hold(strategy_id: str, row: dict, horizon: str, reason: str, score: float = 0.0) -> StrategyDecision:
    return StrategyDecision(
        strategy_id=strategy_id,
        symbol=str(row["symbol"]),
        side="HOLD",
        score=score,
        qty=0,
        horizon=horizon,
        entry_reason=reason,
        exit_reason="No trade",
        stop_price=0.0,
        take_profit_price=0.0,
        expected_return=0.0,
        expected_range=0.0,
        timing_alignment=0.5,
    )


def generate_breakout_floor_orders(rows: list[dict], global_cfg: dict, strategy_cfg: dict, session: str) -> list[StrategyDecision]:
    del session
    e = strategy_cfg.get("entry", {})
    min_trend = _f(e.get("min_abs_trend_score"), 0.01)
    min_rr = _f(e.get("min_reward_risk"), 1.25)
    min_net = _f(e.get("min_net_edge_pct"), 0.003)
    wm = _f(e.get("momentum_weight"), 0.65)
    wr = _f(e.get("relative_strength_weight"), 0.35)
    buffer = _f(strategy_cfg.get("risk", {}).get("stop_buffer_pct"), 0.0)
    out = []
    for row in rows:
        g = _geometry(row, "d1")
        trend = wm * _f(row.get("momentum_20")) + wr * _f(row.get("rel_strength_20"))
        action = "HOLD"
        gross = 0.0
        rr = 0.0
        if trend >= min_trend and g["long_rr"] >= min_rr and _net(g["up"], global_cfg) >= min_net:
            action, gross, rr = "BUY", g["up"], g["long_rr"]
        elif trend <= -min_trend and g["short_rr"] >= min_rr and _net(g["down"], global_cfg) >= min_net:
            action, gross, rr = "SELL", g["down"], g["short_rr"]
        if action == "HOLD" or not _liquid(row, strategy_cfg):
            out.append(_hold("breakout_protected_by_floor", row, "d1", f"HOLD: trend/range does not clear cost-adjusted gate (trend={trend:.4f})"))
            continue
        ok, mult, prio, ctx = _m3(row, action, global_cfg)
        if not ok:
            h = _hold("breakout_protected_by_floor", row, "d1", "HOLD: reliable M3 floor timing blocks tactical BUY")
            h.m3_context = ctx
            out.append(h)
            continue
        stop = g["floor"] * (1 - buffer) if action == "BUY" else g["ceiling"] * (1 + buffer)
        take = g["ceiling"] if action == "BUY" else g["floor"]
        qty = _size(row, strategy_cfg, global_cfg, stop, mult)
        if qty <= 0:
            out.append(_hold("breakout_protected_by_floor", row, "d1", "HOLD: zero risk-sized quantity"))
            continue
        score = max(0.0, _net(gross, global_cfg)) * min(rr, 3.0) * max(0.0, min(1.0, _f(row.get("confidence_score"), 0.5)))
        out.append(StrategyDecision(
            strategy_id="breakout_protected_by_floor", symbol=str(row["symbol"]), side=action,
            score=score, qty=qty, horizon="d1",
            entry_reason=f"{action}: trend={trend:.4f}, rr={rr:.2f}, net_edge={_net(gross, global_cfg):.2%} after {round_trip_cost_bps(global_cfg):.0f} bps round-trip",
            exit_reason="D1 floor/ceiling or one-session timeout", stop_price=stop, take_profit_price=take,
            expected_return=0.0, expected_range=max(0.0, g["ceiling"]-g["floor"]), timing_alignment=0.5,
            m3_context=ctx, priority_adjustment=prio,
        ))
    return out


def generate_mean_reversion_orders(rows: list[dict], global_cfg: dict, strategy_cfg: dict, session: str) -> list[StrategyDecision]:
    del session
    e = strategy_cfg.get("entry", {})
    near = _f(e.get("near_anchor_pct"), 0.02)
    min_rr = _f(e.get("min_reward_risk"), 1.30)
    min_net = _f(e.get("min_net_edge_pct"), 0.004)
    min_recovery = _f(e.get("min_recovery_momentum"), -0.005)
    max_fading = _f(e.get("max_fading_momentum"), 0.005)
    buffer = _f(strategy_cfg.get("risk", {}).get("stop_buffer_pct"), 0.0)
    out=[]
    for row in rows:
        g=_geometry(row,"w1"); close=g["close"]; mom=_f(row.get("momentum_20"))
        if close<=0 or not _liquid(row,strategy_cfg):
            out.append(_hold("mean_reversion_floor_w1",row,"w1","HOLD: invalid geometry/liquidity")); continue
        df=max(0.0,close-g["floor"])/close
        dc=max(0.0,g["ceiling"]-close)/close
        candidates=[]
        if df<=near and mom>=min_recovery and g["long_rr"]>=min_rr and _net(g["up"],global_cfg)>=min_net:
            candidates.append(("BUY",_net(g["up"],global_cfg)*min(g["long_rr"],3.0),g["up"],g["long_rr"]))
        if dc<=near and mom<=max_fading and g["short_rr"]>=min_rr and _net(g["down"],global_cfg)>=min_net:
            candidates.append(("SELL",_net(g["down"],global_cfg)*min(g["short_rr"],3.0),g["down"],g["short_rr"]))
        if not candidates:
            out.append(_hold("mean_reversion_floor_w1",row,"w1",f"HOLD: no W1 anchor reversal (floor_dist={df:.2%}, ceiling_dist={dc:.2%})")); continue
        action,score,gross,rr=max(candidates,key=lambda x:x[1])
        ok,mult,prio,ctx=_m3(row,action,global_cfg)
        if not ok:
            h=_hold("mean_reversion_floor_w1",row,"w1","HOLD: reliable M3 floor timing blocks tactical BUY"); h.m3_context=ctx; out.append(h); continue
        stop=g["floor"]*(1-buffer) if action=="BUY" else g["ceiling"]*(1+buffer)
        take=g["ceiling"] if action=="BUY" else g["floor"]
        qty=_size(row,strategy_cfg,global_cfg,stop,mult)
        if qty<=0:
            out.append(_hold("mean_reversion_floor_w1",row,"w1","HOLD: zero risk-sized quantity")); continue
        out.append(StrategyDecision(
            strategy_id="mean_reversion_floor_w1",symbol=str(row["symbol"]),side=action,score=score,qty=qty,horizon="w1",
            entry_reason=f"{action}: W1 anchor reversal, rr={rr:.2f}, net_edge={_net(gross,global_cfg):.2%}",
            exit_reason="Opposite W1 anchor or five-session timeout",stop_price=stop,take_profit_price=take,
            expected_return=0.0,expected_range=max(0.0,g["ceiling"]-g["floor"]),timing_alignment=0.5,
            m3_context=ctx,priority_adjustment=prio,
        ))
    return out


def generate_cross_horizon_orders(rows: list[dict], global_cfg: dict, strategy_cfg: dict, session: str) -> list[StrategyDecision]:
    del session
    e=strategy_cfg.get("entry",{})
    min_ratio=_f(e.get("min_asymmetry_ratio"),1.35)
    min_trend=_f(e.get("min_abs_trend_score"),0.005)
    min_net=_f(e.get("min_net_edge_pct"),0.004)
    weights={"d1":_f(e.get("d1_weight"),0.2),"w1":_f(e.get("w1_weight"),0.3),"q1":_f(e.get("q1_weight"),0.5)}
    total=sum(weights.values()) or 1.0; weights={k:v/total for k,v in weights.items()}
    wm=_f(e.get("momentum_weight"),0.6); wr=_f(e.get("relative_strength_weight"),0.4)
    buffer=_f(strategy_cfg.get("risk",{}).get("stop_buffer_pct"),0.0)
    out=[]
    for row in rows:
        gs={h:_geometry(row,h) for h in weights}
        if any(g["close"]<=0 or g["floor"]<=0 or g["ceiling"]<=0 for g in gs.values()) or not _liquid(row,strategy_cfg):
            out.append(_hold("cross_horizon_asymmetry",row,"q1","HOLD: incomplete cross-horizon geometry/liquidity")); continue
        up=sum(weights[h]*gs[h]["up"] for h in weights); down=sum(weights[h]*gs[h]["down"] for h in weights)
        lr=up/max(down,1e-9); sr=down/max(up,1e-9)
        trend=wm*_f(row.get("momentum_20"))+wr*_f(row.get("rel_strength_20"))
        action="HOLD"; gross=0.0; rr=0.0
        if lr>=min_ratio and trend>=min_trend and _net(up,global_cfg)>=min_net:
            action,gross,rr="BUY",up,lr
        elif sr>=min_ratio and trend<=-min_trend and _net(down,global_cfg)>=min_net:
            action,gross,rr="SELL",down,sr
        if action=="HOLD":
            out.append(_hold("cross_horizon_asymmetry",row,"q1",f"HOLD: asymmetry/trend inconclusive (long={lr:.2f}, short={sr:.2f}, trend={trend:.4f})")); continue
        ok,mult,prio,ctx=_m3(row,action,global_cfg)
        if not ok:
            h=_hold("cross_horizon_asymmetry",row,"q1","HOLD: reliable M3 floor timing blocks tactical BUY"); h.m3_context=ctx; out.append(h); continue
        q=gs["q1"]; stop=q["floor"]*(1-buffer) if action=="BUY" else q["ceiling"]*(1+buffer); take=q["ceiling"] if action=="BUY" else q["floor"]
        qty=_size(row,strategy_cfg,global_cfg,stop,mult)
        if qty<=0:
            out.append(_hold("cross_horizon_asymmetry",row,"q1","HOLD: zero risk-sized quantity")); continue
        score=max(0.0,_net(gross,global_cfg))*min(rr,3.0)*max(0.0,min(1.0,_f(row.get("confidence_score"),0.5)))
        out.append(StrategyDecision(
            strategy_id="cross_horizon_asymmetry",symbol=str(row["symbol"]),side=action,score=score,qty=qty,horizon="q1",
            entry_reason=f"{action}: cross-horizon asymmetry={rr:.2f}, trend={trend:.4f}, net_edge={_net(gross,global_cfg):.2%}",
            exit_reason="Q1 anchor or ten-session timeout",stop_price=stop,take_profit_price=take,
            expected_return=0.0,expected_range=max(0.0,q["ceiling"]-q["floor"]),timing_alignment=0.5,
            m3_context=ctx,priority_adjustment=prio,
        ))
    return out


def generate_weekly_opportunity_orders(rows: list[dict], global_cfg: dict, strategy_cfg: dict, session: str) -> list[StrategyDecision]:
    del session
    field=str(strategy_cfg.get("model_score_field") or "weekly_opportunity_score")
    e=strategy_cfg.get("entry",{})
    buy_frac=max(0.0,min(1.0,_f(e.get("buy_top_fraction"),0.2)))
    sell_frac=max(0.0,min(1.0,_f(e.get("sell_bottom_fraction"),0.2)))
    min_buy=_f(e.get("min_buy_score"),0.0); max_sell=_f(e.get("max_sell_score"),0.0)
    min_rr=_f(e.get("min_reward_risk"),1.2); min_net=_f(e.get("min_net_edge_pct"),0.004)
    buffer=_f(strategy_cfg.get("risk",{}).get("stop_buffer_pct"),0.0)
    scored=[]
    for row in rows:
        try: score=float(row.get(field))
        except (TypeError,ValueError): continue
        if math.isfinite(score): scored.append((row,score))
    if not scored: return []
    desc=sorted(scored,key=lambda x:x[1],reverse=True)
    nb=max(1,math.ceil(len(desc)*buy_frac)) if buy_frac>0 else 0
    ns=max(1,math.ceil(len(desc)*sell_frac)) if sell_frac>0 else 0
    buys={str(r["symbol"]) for r,s in desc[:nb] if s>min_buy}
    sells={str(r["symbol"]) for r,s in desc[-ns:] if s<max_sell}
    out=[]
    for row,model_score in scored:
        g=_geometry(row,"q1"); action="HOLD"; gross=0.0; rr=0.0
        if str(row["symbol"]) in buys and g["long_rr"]>=min_rr and _net(g["up"],global_cfg)>=min_net:
            action,gross,rr="BUY",g["up"],g["long_rr"]
        elif str(row["symbol"]) in sells and g["short_rr"]>=min_rr and _net(g["down"],global_cfg)>=min_net:
            action,gross,rr="SELL",g["down"],g["short_rr"]
        if action=="HOLD" or not _liquid(row,strategy_cfg):
            out.append(_hold("weekly_opportunity_ridge",row,"q1",f"HOLD: weekly score={model_score:.4f} is not a cost-valid tail opportunity")); continue
        stop=g["floor"]*(1-buffer) if action=="BUY" else g["ceiling"]*(1+buffer); take=g["ceiling"] if action=="BUY" else g["floor"]
        qty=_size(row,strategy_cfg,global_cfg,stop)
        if qty<=0:
            out.append(_hold("weekly_opportunity_ridge",row,"q1","HOLD: zero risk-sized quantity")); continue
        scale=min(abs(model_score),3.0)/3.0; score=max(0.0,_net(gross,global_cfg))*min(rr,3.0)*scale
        out.append(StrategyDecision(
            strategy_id="weekly_opportunity_ridge",symbol=str(row["symbol"]),side=action,score=score,qty=qty,horizon="q1",
            entry_reason=f"{action}: weekly Ridge tail score={model_score:.4f}, rr={rr:.2f}, net_edge={_net(gross,global_cfg):.2%}",
            exit_reason="Q1 anchor or ten-session timeout",stop_price=stop,take_profit_price=take,
            expected_return=0.0,expected_range=max(0.0,g["ceiling"]-g["floor"]),timing_alignment=0.5,
        ))
    return out
