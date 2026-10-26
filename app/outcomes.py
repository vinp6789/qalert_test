"""Outcome resolver: labels every emitted (and suppressed) entry signal with
its realized result using stored candles. Fully automatic.

Resolution rules (long):
- SL touched before TP1  -> result SL,  realized_r = -1
- TP1 then SL/BE         -> TP1_THEN_BE, realized_r = +0.5 (1/3 off at 1.5R, rest BE)
- TP1 then TP2           -> TP2, realized_r = +2.5  (1/3*1.5 + 2/3*3)
- neither within expiry  -> EXPIRED, realized_r = mark-to-market R at expiry
Intrabar ambiguity (both SL and TP1 inside one candle) resolves pessimistically
to SL - the backtest must not be flattered.
"""
from __future__ import annotations
import datetime as dt


def resolve_pending(conn, cfg: dict, now: dt.datetime | None = None) -> int:
    now = now or dt.datetime.now(dt.timezone.utc)
    rows = conn.execute("""
        SELECT s.id, s.symbol, s.entry_high, s.stop, s.tp1, s.tp2, s.created_at,
               s.status
        FROM signals s LEFT JOIN outcomes o ON o.signal_id = s.id
        WHERE o.signal_id IS NULL AND s.setup LIKE '%%_LONG'
          AND s.stop IS NOT NULL""").fetchall()
    resolved = 0
    for sid, symbol, entry, stop, tp1, tp2, created, status in rows:
        entry, stop, tp1, tp2 = map(float, (entry, stop, tp1, tp2))
        candles = conn.execute("""
            SELECT high, low, close FROM candles
            WHERE symbol=%s AND tf='4h' AND open_time > %s
            ORDER BY open_time""", (symbol, created)).fetchall()
        result = None
        r = 0.0
        risk = entry - stop
        if risk <= 0:
            continue
        mfe = mae = 0.0
        tp1_hit = False
        for high, low, close in candles:
            high, low, close = float(high), float(low), float(close)
            mfe = max(mfe, (high - entry) / risk)
            mae = min(mae, (low - entry) / risk)
            if not tp1_hit:
                if low <= stop:                       # pessimistic on ambiguity
                    result, r = "SL", -1.0
                    break
                if high >= tp1:
                    tp1_hit = True
                    if low <= stop:                   # same candle: pessimistic
                        result, r = "SL", -1.0
                        break
            else:
                if high >= tp2:
                    result, r = "TP2", 0.5 + 2.0      # 1/3*1.5R + 2/3*3R = 2.5
                    break
                if low <= entry:                      # stop moved to BE after TP1
                    result, r = "TP1_THEN_BE", 0.5
                    break
        if result is None:
            age = now - created
            if age > dt.timedelta(days=cfg["outcome_expiry_days"]):
                last = float(candles[-1][2]) if candles else entry
                result = "EXPIRED"
                r = 0.5 + (2 / 3) * (last - entry) / risk if tp1_hit \
                    else (last - entry) / risk
            else:
                continue  # still live
        conn.execute("""
            INSERT INTO outcomes (signal_id, result, realized_r, mfe_r, mae_r,
                                  hypothetical)
            VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
            (sid, result, round(r, 3), round(mfe, 3), round(mae, 3),
             status != "EMITTED"))
        resolved += 1
    conn.commit()
    return resolved


def performance_report(conn) -> dict:
    rows = conn.execute("""
        SELECT s.setup, s.regime, s.status,
               count(*), avg(CASE WHEN o.realized_r > 0 THEN 1.0 ELSE 0 END),
               avg(o.realized_r)
        FROM outcomes o JOIN signals s ON s.id=o.signal_id
        GROUP BY 1,2,3 ORDER BY 1,2,3""").fetchall()
    return {"by_setup_regime_status": [
        {"setup": a, "regime": b, "status": c, "n": d,
         "win_rate": round(float(e), 3), "avg_r": round(float(f), 3)}
        for a, b, c, d, e, f in rows]}
