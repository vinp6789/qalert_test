"""Risk engine: every entry gets a complete trade plan or is rejected.
Stops are structure-based, ATR-sanity-checked. Rejection reasons are explicit.
"""
from __future__ import annotations


class RiskRejection(Exception):
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


def build_plan(price: float, atr: float, nearest_sup: float | None,
               cfg: dict) -> dict:
    if atr is None or atr <= 0:
        raise RiskRejection("ATR unavailable")
    if nearest_sup is None or nearest_sup >= price:
        raise RiskRejection("no valid support below price for structure stop")
    stop = nearest_sup * 0.995              # just below structure
    dist = price - stop
    if dist > cfg["max_stop_atr"] * atr:
        raise RiskRejection(
            f"stop {dist / atr:.1f}x ATR away (max {cfg['max_stop_atr']}) - poor geometry")
    if dist < cfg["min_stop_atr"] * atr:
        raise RiskRejection(
            f"stop {dist / atr:.1f}x ATR away (min {cfg['min_stop_atr']}) - too tight, noise will stop it")
    r = dist
    tp1 = price + 1.5 * r
    tp2 = price + 3.0 * r
    risk_usd = cfg["account_usd"] * cfg["risk_pct"] / 100.0
    units = risk_usd / dist
    return {
        "entry_low": round(price * 0.995, 8), "entry_high": round(price * 1.005, 8),
        "stop": round(stop, 8), "tp1": round(tp1, 8), "tp2": round(tp2, 8),
        "risk_usd": round(risk_usd, 2), "units": round(units, 6),
        "position_usd": round(units * price, 2),
        "stop_pct": round(dist / price * 100, 2), "atr": round(atr, 8),
    }
