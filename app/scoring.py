"""Conviction scoring + empirical calibration.

Raw score: weighted sum of available factor families, renormalized over the
weights of AVAILABLE families only (missing families shrink confidence via the
confluence rule, they are never imputed as zero). Mapped to 0-100.

Calibration: displayed confidence = realized win rate of the score's decile
bin, from the outcomes table. Bins with n < calibration_min_n report
uncalibrated=True. The raw score is never shown as a probability.
"""
from __future__ import annotations


def raw_score(factors: dict, weights: dict, min_families: int) -> tuple[float | None, str]:
    avail = {k: v for k, v in factors.items() if v is not None}
    if len(avail) < min_families:
        return None, f"only {len(avail)}/{len(factors)} factor families available"
    wsum = sum(weights[k] for k in avail)
    if wsum <= 0:
        return None, "zero weight coverage"
    s = sum(weights[k] * avail[k] for k in avail) / wsum   # in [-100, 100]
    return round((s + 100) / 2, 1), ""                      # -> [0, 100]


def tier(score: float, cfg: dict) -> str:
    if score >= cfg["tier_a_score"]:
        return "A"
    if score >= cfg["tier_b_score"]:
        return "B"
    return "WATCH"


def calibrate(conn, score: float, cfg: dict) -> dict:
    b = min(int(score // 10), 9)
    row = conn.execute(
        "SELECT n, win_rate, avg_r FROM calibration WHERE score_bin=%s", (b,)
    ).fetchone()
    if not row or row[0] < cfg["calibration_min_n"]:
        return {"uncalibrated": True, "n": row[0] if row else 0, "bin": b}
    return {"uncalibrated": False, "n": row[0], "win_rate": float(row[1]),
            "avg_r": float(row[2]), "bin": b}


def rebuild_calibration(conn) -> int:
    """Recompute bin stats from resolved REAL (non-hypothetical) entry outcomes."""
    rows = conn.execute("""
        SELECT least(floor(s.raw_score/10)::int, 9) AS bin,
               count(*) AS n,
               avg(CASE WHEN o.realized_r > 0 THEN 1.0 ELSE 0.0 END) AS wr,
               avg(o.realized_r) AS ar
        FROM outcomes o JOIN signals s ON s.id = o.signal_id
        WHERE o.hypothetical = FALSE AND s.setup LIKE '%%_LONG'
          AND s.raw_score IS NOT NULL
        GROUP BY 1""").fetchall()
    with conn.cursor() as cur:
        for b, n, wr, ar in rows:
            cur.execute("""
                INSERT INTO calibration (score_bin, n, win_rate, avg_r, updated_at)
                VALUES (%s,%s,%s,%s,now())
                ON CONFLICT (score_bin) DO UPDATE
                  SET n=EXCLUDED.n, win_rate=EXCLUDED.win_rate,
                      avg_r=EXCLUDED.avg_r, updated_at=now()""", (b, n, wr, ar))
    conn.commit()
    return len(rows)


def kill_switch_engaged(conn, cfg: dict) -> bool:
    """True if rolling win rate over last `window` resolved real entries is
    below threshold. System then demotes all entries to WATCH."""
    ks = cfg["kill_switch"]
    rows = conn.execute("""
        SELECT o.realized_r FROM outcomes o
        JOIN signals s ON s.id = o.signal_id
        WHERE o.hypothetical = FALSE AND s.setup LIKE '%%_LONG'
        ORDER BY o.resolved_at DESC LIMIT %s""", (ks["window"],)).fetchall()
    if len(rows) < ks["window"]:
        return False
    wr = sum(1 for (r,) in rows if r > 0) / len(rows)
    return wr < ks["min_win_rate"]
