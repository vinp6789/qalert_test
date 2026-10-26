"""Portfolio-level correlation guard: 21 alts are mostly one trade in a crash.
If >= limit open positions correlate > threshold with the candidate, the new
entry is downgraded one tier and flagged in the alert.
"""
from __future__ import annotations
import numpy as np


def _returns(conn, symbol: str, days: int) -> np.ndarray | None:
    rows = conn.execute("""SELECT close FROM candles WHERE symbol=%s AND tf='1d'
                           ORDER BY open_time DESC LIMIT %s""",
                        (symbol, days + 1)).fetchall()
    if len(rows) < days // 2:
        return None
    closes = np.array([float(r[0]) for r in reversed(rows)])
    return np.diff(np.log(closes))


def correlation_flag(conn, candidate: str, cfg: dict) -> tuple[bool, list[str]]:
    """Returns (over_limit, correlated_symbols)."""
    open_syms = [r[0] for r in conn.execute(
        "SELECT DISTINCT symbol FROM positions WHERE status='OPEN'").fetchall()
        if r[0] != candidate]
    if len(open_syms) < cfg["correlated_open_limit"]:
        return False, []
    cand = _returns(conn, candidate, cfg["correlation_window_days"])
    if cand is None:
        return False, []   # can't compute -> don't block, but also don't pretend
    correlated = []
    for s in open_syms:
        other = _returns(conn, s, cfg["correlation_window_days"])
        if other is None:
            continue
        n = min(len(cand), len(other))
        if n < 30:
            continue
        c = float(np.corrcoef(cand[-n:], other[-n:])[0, 1])
        if c > cfg["correlation_threshold"]:
            correlated.append(s)
    return len(correlated) >= cfg["correlated_open_limit"], correlated
