"""Setup detection. Explicit, named, mutually exclusive per token per run.
A setup is only a *candidate*; regime gates, confluence rules, risk geometry
and cooldowns can still suppress it downstream.
"""
from __future__ import annotations
import numpy as np
from . import indicators as ind

# mandatory factor families per setup (must be available AND agree in sign)
MANDATORY = {
    "TREND_PULLBACK_LONG": {"trend": 1, "structure": 0},   # trend must be positive
    "BREAKOUT_LONG": {"trend": 1, "volume": 1},            # volume must confirm
}


def detect(token_frames: dict, factors: dict, struct_ev: dict) -> str | None:
    d1, h4 = token_frames.get("1d"), token_frames.get("4h")
    if d1 is None or h4 is None:
        return None
    c1, c4 = d1["close"], h4["close"]
    if len(c1) < 210 or len(c4) < 60:
        return None

    e20 = ind.ema(c1, 20)
    e50 = ind.ema(c1, 50)
    e200 = ind.ema(c1, 200)
    price = c1[-1]
    uptrend = price > e200[-1] and e20[-1] > e50[-1]

    # --- BREAKOUT_LONG: close above recent swing resistance on volume ---
    res, _ = ind.swing_levels(d1["high"], d1["low"], lookback=60)
    prior_res = [r for r in res if r < price * 1.001 and r > price * 0.90]
    vma = np.mean(d1["volume"][-21:-1])
    vol_confirm = vma > 0 and d1["volume"][-1] > 1.3 * vma
    broke_out = bool(prior_res) and c1[-2] < max(prior_res) <= price
    if uptrend and broke_out and vol_confirm:
        return "BREAKOUT_LONG"

    # --- TREND_PULLBACK_LONG: uptrend, price pulled into 20/50 EMA zone, holding ---
    zone_hi, zone_lo = max(e20[-1], e50[-1]), min(e20[-1], e50[-1])
    a = ind.atr(d1["high"], d1["low"], c1)[-1]
    in_zone = (zone_lo - 0.5 * a) <= price <= (zone_hi + 0.5 * a)
    pulled_back = price < max(c1[-15:]) * 0.97          # actually retraced
    holding = c4[-1] > c4[-2] or c4[-1] > ind.ema(c4, 20)[-1]  # not knife-catching
    if uptrend and in_zone and pulled_back and holding:
        return "TREND_PULLBACK_LONG"

    # --- EXIT_WARNING: held-trend deterioration (evaluated for any token;
    #     alert layer only emits it for tracked positions) ---
    broke_50 = price < e50[-1] and c1[-2] >= e50[-2]
    heavy_dist = (factors.get("volume") is not None and factors["volume"] < -50)
    if broke_50 or heavy_dist:
        return "EXIT_WARNING"
    return None


def mandatory_ok(setup: str, factors: dict) -> tuple[bool, str]:
    for fam, min_sign in MANDATORY.get(setup, {}).items():
        v = factors.get(fam)
        if v is None:
            return False, f"mandatory factor '{fam}' UNAVAILABLE"
        if min_sign > 0 and v <= 0:
            return False, f"mandatory factor '{fam}' not confirming ({v:.0f})"
    return True, ""
