"""Six orthogonal factor families. Each returns a score in [-100, 100] or None
(UNAVAILABLE). None is never coerced to 0 - missing evidence is not neutral
evidence. Scores are deterministic functions of candle arrays.
"""
from __future__ import annotations
import numpy as np
from . import indicators as ind


def _clip(x: float) -> float:
    return float(np.clip(x, -100, 100))


def trend_structure(d1: dict, h4: dict) -> float | None:
    """EMA stack + slope + ADX on 1D, confirmed on 4H."""
    if d1 is None or h4 is None:
        return None
    c = d1["close"]
    if len(c) < 210:
        return None
    e20, e50, e200 = ind.ema(c, 20), ind.ema(c, 50), ind.ema(c, 200)
    score = 0.0
    score += 25 if c[-1] > e200[-1] else -25
    score += 20 if e20[-1] > e50[-1] > e200[-1] else (
        -20 if e20[-1] < e50[-1] < e200[-1] else 0)
    slope = (e50[-1] - e50[-6]) / e50[-6] if e50[-6] else 0
    score += _clip(slope * 2000) * 0.25  # ~2.5%/5d slope saturates
    adx_now = ind.adx(d1["high"], d1["low"], c)[-1]
    score += 15 if adx_now > 25 else (-10 if adx_now < 15 else 0)
    c4 = h4["close"]
    e50_4 = ind.ema(c4, 50)
    score += 15 if c4[-1] > e50_4[-1] else -15
    return _clip(score)


def momentum_quality(h4: dict) -> float | None:
    if h4 is None or len(h4["close"]) < 60:
        return None
    c = h4["close"]
    r = ind.rsi(c)
    # RSI *regime*: bull ranges hold 40-80, bear ranges 20-60
    recent = r[-20:]
    score = 0.0
    score += 30 if np.nanmin(recent) > 38 else (-30 if np.nanmax(recent) < 62 else 0)
    hist = ind.macd_hist(c)
    score += 25 if hist[-1] > hist[-3] else -25
    score += 15 if hist[-1] > 0 else -15
    # blow-off penalty: RSI > 80 is late, not strong, for NEW entries
    if r[-1] > 80:
        score -= 25
    return _clip(score)


def volume_confirmation(d1: dict, h4: dict) -> float | None:
    if d1 is None or len(d1["volume"]) < 30:
        return None
    v, c = d1["volume"], d1["close"]
    vma = np.mean(v[-21:-1])
    if vma == 0:
        return None
    rel = v[-1] / vma
    up_day = c[-1] > c[-2]
    score = 0.0
    if up_day:
        score += _clip((rel - 1) * 60)       # above-avg volume on up move
    else:
        score -= _clip((rel - 1) * 60)       # heavy volume on down move = distribution
    o = ind.obv(c, v)
    score += 30 if o[-1] > np.mean(o[-15:-1]) else -30
    return _clip(score)


def relative_strength(token_d1: dict, btc_d1: dict) -> float | None:
    if token_d1 is None or btc_d1 is None:
        return None
    n = min(len(token_d1["close"]), len(btc_d1["close"]))
    if n < 60:
        return None
    ratio = token_d1["close"][-n:] / btc_d1["close"][-n:]
    e20 = ind.ema(ratio, 20)
    score = 40 if ratio[-1] > e20[-1] else -40
    chg = (ratio[-1] - ratio[-15]) / ratio[-15]
    score += _clip(chg * 400)  # 25% 2-week outperformance saturates
    return _clip(score)


def derivatives_posture(deriv: dict, funding_history: list[float] | None = None
                        ) -> float | None:
    fr = deriv.get("funding_rate") if deriv else None
    if fr is None:
        return None
    score = 0.0
    # extreme positive funding = crowded longs = penalty for new longs
    if fr > 0.0008:      # >0.08% per 8h, top-decile crowding historically
        score -= 60
    elif fr > 0.0003:
        score -= 20
    elif fr < -0.0003:   # shorts paying = squeeze fuel
        score += 30
    else:
        score += 10      # neutral funding is mildly healthy
    return _clip(score)


def structure_levels(d1: dict) -> tuple[float | None, dict]:
    """Score + evidence: distance to nearest overhead resistance / support."""
    if d1 is None or len(d1["close"]) < 80:
        return None, {}
    price = d1["close"][-1]
    a = ind.atr(d1["high"], d1["low"], d1["close"])[-1]
    res, sup = ind.swing_levels(d1["high"], d1["low"])
    overhead = [r for r in res if r > price]
    below = [s for s in sup if s < price]
    nearest_res = min(overhead) if overhead else None
    nearest_sup = max(below) if below else None
    score = 0.0
    if nearest_res is not None and a > 0:
        room = (nearest_res - price) / a
        score += _clip((room - 1.5) * 30)   # <1.5 ATR to resistance penalized
    else:
        score += 40                          # blue sky
    if nearest_sup is not None and a > 0:
        dist = (price - nearest_sup) / a
        score += 20 if dist < 2.0 else 0     # support nearby anchors the stop
    return _clip(score), {"nearest_res": nearest_res, "nearest_sup": nearest_sup,
                          "atr": float(a)}


def compute_all(token_frames: dict, btc_frames: dict, deriv: dict
                ) -> tuple[dict, dict]:
    """Returns ({family: score|None}, structure_evidence)."""
    d1, h4 = token_frames.get("1d"), token_frames.get("4h")
    struct_score, struct_ev = structure_levels(d1)
    factors = {
        "trend": trend_structure(d1, h4),
        "momentum": momentum_quality(h4),
        "volume": volume_confirmation(d1, h4),
        "rel_strength": relative_strength(d1, btc_frames.get("1d")),
        "derivatives": derivatives_posture(deriv),
        "structure": struct_score,
    }
    return factors, struct_ev
