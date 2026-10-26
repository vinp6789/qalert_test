"""Walk-forward backtest harness. Replays stored candles through the SAME
evaluate_token() used live. No lookahead: at step t only candles [0..t] are
visible. Outcomes resolved with the same pessimistic-ambiguity rules.
"""
from __future__ import annotations
import numpy as np
from . import engine, regime as rg
from .outcomes import resolve_pending  # noqa: F401 (re-export for scripts)


def _slice(frames: dict, end: int) -> dict:
    return {k: v[:end] for k, v in frames.items()}


def run_backtest(all_frames: dict[str, dict], btc_symbol: str, cfg: dict,
                 step: int = 6, min_history: int = 220) -> list[dict]:
    """all_frames: {symbol: {'1d': frames, '4h': frames}} aligned arrays.
    Steps through 1d history; 4h sliced proportionally (4 bars per day... uses
    ratio of lengths to stay robust to gaps).
    Returns signal records with simulated outcomes."""
    btc = all_frames[btc_symbol]
    n_days = len(btc["1d"]["close"])
    results = []
    for t in range(min_history, n_days - 1, max(step // 6, 1)):
        btc_view = {"1d": _slice(btc["1d"], t),
                    "4h": _slice(btc["4h"], _h4_end(btc, t))}
        regime_res = rg.classify(btc_view["1d"], None, None, None)
        for symbol, frames in all_frames.items():
            if len(frames["1d"]["close"]) < t:
                continue
            view = {"1d": _slice(frames["1d"], t),
                    "4h": _slice(frames["4h"], _h4_end(frames, t))}
            rec = engine.evaluate_token(symbol, view, btc_view,
                                        {"funding_rate": None}, regime_res, cfg)
            if rec.get("setup") and rec["setup"].endswith("_LONG") \
               and rec["status"] in ("EMITTED", "WATCH_ONLY") and "plan" in rec:
                rec["t"] = t
                rec["outcome"] = _simulate(frames["1d"], t, rec["plan"],
                                           cfg["outcome_expiry_days"])
                results.append(rec)
    return results


def _h4_end(frames: dict, t_days: int) -> int:
    ratio = len(frames["4h"]["close"]) / max(len(frames["1d"]["close"]), 1)
    return max(60, int(t_days * ratio))


def _simulate(d1: dict, t: int, plan: dict, expiry_days: int) -> dict:
    entry, stop, tp1, tp2 = (plan["entry_high"], plan["stop"],
                             plan["tp1"], plan["tp2"])
    risk = entry - stop
    tp1_hit = False
    for i in range(t, min(t + expiry_days, len(d1["close"]))):
        hi, lo = d1["high"][i], d1["low"][i]
        if not tp1_hit:
            if lo <= stop:
                return {"result": "SL", "r": -1.0}
            if hi >= tp1:
                tp1_hit = True
                if lo <= stop:
                    return {"result": "SL", "r": -1.0}
        else:
            if hi >= tp2:
                return {"result": "TP2", "r": 2.5}
            if lo <= entry:
                return {"result": "TP1_THEN_BE", "r": 0.5}
    last = d1["close"][min(t + expiry_days, len(d1["close"]) - 1)]
    r = 0.5 + (2 / 3) * (last - entry) / risk if tp1_hit else (last - entry) / risk
    return {"result": "EXPIRED", "r": round(float(r), 3)}


def summarize(results: list[dict]) -> dict:
    if not results:
        return {"n": 0}
    rs = np.array([x["outcome"]["r"] for x in results])
    wins = rs > 0
    gross_win = rs[wins].sum() if wins.any() else 0.0
    gross_loss = -rs[~wins].sum() if (~wins).any() else 0.0
    return {"n": len(rs), "win_rate": round(float(wins.mean()), 3),
            "avg_r": round(float(rs.mean()), 3),
            "profit_factor": round(float(gross_win / gross_loss), 2)
            if gross_loss > 0 else None,
            "worst_streak": _worst_streak(rs)}


def _worst_streak(rs) -> int:
    worst = cur = 0
    for r in rs:
        cur = cur + 1 if r < 0 else 0
        worst = max(worst, cur)
    return worst
