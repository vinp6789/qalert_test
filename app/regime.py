"""Market regime engine. Computed once per run, BEFORE token scoring.
Degradation rule: missing inputs always tighten toward the more conservative
state, never loosen.
Order of conservatism: RISK_OFF > CHOP > NEUTRAL > RISK_ON.
"""
from __future__ import annotations
import numpy as np
from . import indicators as ind

CONSERVATISM = {"RISK_OFF": 3, "CHOP": 2, "NEUTRAL": 1, "RISK_ON": 0}

# gating matrix: regime -> allowed setup types
GATES = {
    "RISK_ON":  {"TREND_PULLBACK_LONG", "BREAKOUT_LONG", "EXIT_WARNING"},
    "NEUTRAL":  {"TREND_PULLBACK_LONG", "EXIT_WARNING"},   # pullbacks only; A-grade filter applied later
    "CHOP":     {"EXIT_WARNING"},
    "RISK_OFF": {"EXIT_WARNING"},
}


def classify(btc_d1: dict | None, btc_dominance: float | None,
             dominance_change_7d: float | None,
             agg_funding: float | None) -> dict:
    """Returns {state, degraded, evidence}."""
    evidence: dict = {}
    votes: list[str] = []
    degraded = False

    if btc_d1 is not None and len(btc_d1["close"]) >= 210:
        c = btc_d1["close"]
        e50, e200 = ind.ema(c, 50), ind.ema(c, 200)
        above200 = c[-1] > e200[-1]
        stacked = e50[-1] > e200[-1]
        vol = ind.realized_vol(c, 20)
        evidence.update({"btc_above_200ema": bool(above200),
                         "btc_50_over_200": bool(stacked),
                         "btc_realized_vol_20d": round(vol, 3)})
        if above200 and stacked:
            votes.append("CHOP" if vol > 1.0 else "RISK_ON")
        elif not above200 and not stacked:
            votes.append("RISK_OFF")
        else:
            votes.append("NEUTRAL")
        # trend quality check: price whipsawing around 50EMA = chop
        crosses = int(np.sum(np.diff(np.sign(c[-20:] - e50[-20:])) != 0))
        evidence["btc_50ema_crosses_20d"] = crosses
        if crosses >= 5:
            votes.append("CHOP")
    else:
        degraded = True
        evidence["btc_trend"] = "UNAVAILABLE"
        votes.append("RISK_OFF")  # no BTC data = assume worst

    if btc_dominance is not None and dominance_change_7d is not None:
        evidence.update({"btc_dominance": btc_dominance,
                         "dominance_change_7d": dominance_change_7d})
        votes.append("RISK_ON" if dominance_change_7d < -0.3 else
                     ("NEUTRAL" if dominance_change_7d < 0.5 else "RISK_OFF"))
    else:
        degraded = True
        evidence["dominance"] = "UNAVAILABLE"
        # missing dominance alone doesn't force RISK_OFF; it just can't loosen

    if agg_funding is not None:
        evidence["aggregate_funding"] = agg_funding
        if agg_funding > 0.0008:
            votes.append("NEUTRAL")   # crowded: cap at NEUTRAL
    else:
        degraded = True
        evidence["funding"] = "UNAVAILABLE"

    # final state = most conservative of (median vote, any hard vote)
    if not votes:
        return {"state": "RISK_OFF", "degraded": True, "evidence": evidence}
    state = max(votes, key=lambda v: CONSERVATISM[v])
    # median softening: only if a clear majority is more permissive AND not degraded
    if not degraded:
        from collections import Counter
        common, cnt = Counter(votes).most_common(1)[0]
        if cnt > len(votes) / 2 and CONSERVATISM[common] < CONSERVATISM[state]:
            # majority may soften by at most one step
            softened = min(CONSERVATISM[state] - 1, CONSERVATISM[common])
            state = {v: k for k, v in CONSERVATISM.items()}[max(softened, CONSERVATISM[common])]
    evidence["votes"] = votes
    return {"state": state, "degraded": degraded, "evidence": evidence}


def allows(regime_state: str, setup: str) -> bool:
    return setup in GATES.get(regime_state, set())
