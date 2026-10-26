"""Run orchestration: idempotent, sequential, budget-bounded.
The same evaluate_token() is used by live runs and the backtester - one code
path, no live/backtest divergence.
"""
from __future__ import annotations
import datetime as dt
import time
import numpy as np
from . import factors as fac, setups, scoring, risk, regime as rg, alerts, portfolio
from .config import load_active_config
from .data.provider import FailoverProvider, DataUnavailable, store_candles


def frames_from_candles(candles: list[dict]) -> dict:
    return {k: np.array([c[k] for c in candles], dtype=float)
            for k in ("open", "high", "low", "close", "volume")}


def evaluate_token(symbol: str, token_frames: dict, btc_frames: dict,
                   deriv: dict, regime_res: dict, cfg: dict,
                   killed: bool = False) -> dict:
    """Pure decision logic for one token. Returns a signal record (dict).
    Used identically by live engine and backtester."""
    fscores, struct_ev = fac.compute_all(token_frames, btc_frames, deriv)
    setup = setups.detect(token_frames, fscores, struct_ev)
    base = {"symbol": symbol, "factors": fscores, "regime": regime_res["state"],
            "struct_ev": struct_ev}
    if setup is None:
        return {**base, "setup": None, "status": "NO_SETUP"}
    base["setup"] = setup
    base["direction"] = "LONG" if setup.endswith("_LONG") else "FLAT"

    ok, why = setups.mandatory_ok(setup, fscores)
    if not ok:
        return {**base, "status": "SUPPRESSED_DATA_GAP", "reason": why}

    score, why = scoring.raw_score(fscores, cfg["weights"], cfg["min_factor_families"])
    if score is None:
        return {**base, "status": "SUPPRESSED_DATA_GAP", "reason": why}
    base["raw_score"] = score
    t = scoring.tier(score, cfg)

    if setup.endswith("_LONG"):
        if not rg.allows(regime_res["state"], setup):
            return {**base, "tier": t, "status": "SUPPRESSED_REGIME",
                    "reason": f"{setup} not allowed in {regime_res['state']}"}
        # NEUTRAL regime: A-grade only
        if regime_res["state"] == "NEUTRAL" and t != "A":
            return {**base, "tier": t, "status": "SUPPRESSED_REGIME",
                    "reason": "NEUTRAL regime admits A-tier only"}
        try:
            plan = risk.build_plan(float(token_frames["1d"]["close"][-1]),
                                   struct_ev.get("atr"),
                                   struct_ev.get("nearest_sup"), cfg)
        except risk.RiskRejection as e:
            return {**base, "tier": t, "status": "SUPPRESSED_RISK_GEOMETRY",
                    "reason": e.reason}
        base["plan"] = plan
        if killed:
            return {**base, "tier": "WATCH", "status": "SUPPRESSED_KILL_SWITCH",
                    "reason": "rolling win rate below kill-switch threshold"}
        if t == "WATCH":
            return {**base, "tier": t, "status": "WATCH_ONLY"}
    return {**base, "tier": t if setup.endswith("_LONG") else None,
            "status": "EMITTED"}


def persist_signal(conn, run_id: str, rec: dict) -> int | None:
    if rec.get("setup") is None:
        return None
    plan = rec.get("plan", {})
    row = conn.execute("""
        INSERT INTO signals (run_id, symbol, setup, direction, tier, status,
            raw_score, factors, regime, calibration,
            entry_low, entry_high, stop, tp1, tp2, atr)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        RETURNING id""",
        (run_id, rec["symbol"], rec["setup"], rec.get("direction", "FLAT"),
         rec.get("tier"), rec["status"], rec.get("raw_score"),
         _json(conn, rec["factors"]), rec["regime"],
         _json(conn, rec.get("calibration")),
         plan.get("entry_low"), plan.get("entry_high"), plan.get("stop"),
         plan.get("tp1"), plan.get("tp2"),
         rec.get("struct_ev", {}).get("atr"))).fetchone()
    conn.commit()
    return row[0]


def _json(conn, obj):
    from psycopg.types.json import Json
    return Json(obj) if obj is not None else None


def run(conn, provider: FailoverProvider | None = None,
        now: dt.datetime | None = None, budget_s: int = 240) -> dict:
    """One full engine cycle. Idempotent on run_id."""
    now = now or dt.datetime.now(dt.timezone.utc)
    cfg_version, cfg = load_active_config(conn)
    # run_id anchored to the current 4h candle boundary
    anchor = now.replace(minute=0, second=0, microsecond=0)
    anchor = anchor.replace(hour=(anchor.hour // 4) * 4)
    run_id = f"4H-{anchor.isoformat()}"
    exists = conn.execute("SELECT status FROM runs WHERE run_id=%s", (run_id,)).fetchone()
    if exists and exists[0] == "OK":
        return {"run_id": run_id, "skipped": "already completed"}
    conn.execute("""INSERT INTO runs (run_id, config_version) VALUES (%s,%s)
                    ON CONFLICT (run_id) DO UPDATE SET started_at=now(),
                    status='RUNNING'""", (run_id, cfg_version))
    conn.commit()

    provider = provider or FailoverProvider(conn)
    t0 = time.monotonic()
    unavailable, scored, emitted = [], [], []

    # 1. BTC first (regime + relative strength depend on it)
    btc_frames = {}
    try:
        for tf in cfg["timeframes"]:
            candles, src = provider.fetch_candles("BTC", cfg["quote"], tf,
                                                  cfg["candle_limit"],
                                                  cfg["staleness_factor"])
            store_candles(conn, "BTC", tf, candles, src)
            btc_frames[tf] = frames_from_candles(candles)
    except DataUnavailable as e:
        btc_frames = {}
        unavailable.append(("BTC", e.reason))

    agg_funding = provider.fetch_derivatives("BTC").get("funding_rate")
    regime_res = rg.classify(btc_frames.get("1d"), None, None, agg_funding)
    conn.execute("""INSERT INTO regimes (run_id, state, degraded, evidence)
                    VALUES (%s,%s,%s,%s) ON CONFLICT (run_id) DO UPDATE
                    SET state=EXCLUDED.state, degraded=EXCLUDED.degraded,
                        evidence=EXCLUDED.evidence""",
                 (run_id, regime_res["state"], regime_res["degraded"],
                  _json(conn, regime_res["evidence"])))
    conn.commit()

    killed = scoring.kill_switch_engaged(conn, cfg)

    # 2. Per-token, sequential
    for symbol in cfg["watchlist"]:
        if symbol == "BTC":
            token_frames = btc_frames
        else:
            if time.monotonic() - t0 > budget_s:
                unavailable.append((symbol, "run budget exhausted"))
                continue
            token_frames = {}
            try:
                for tf in cfg["timeframes"]:
                    candles, src = provider.fetch_candles(
                        symbol, cfg["quote"], tf, cfg["candle_limit"],
                        cfg["staleness_factor"])
                    store_candles(conn, symbol, tf, candles, src)
                    token_frames[tf] = frames_from_candles(candles)
            except DataUnavailable as e:
                unavailable.append((symbol, e.reason))
                continue
        if not token_frames:
            unavailable.append((symbol, "no frames"))
            continue
        deriv = provider.fetch_derivatives(symbol)
        rec = evaluate_token(symbol, token_frames, btc_frames, deriv,
                             regime_res, cfg, killed)
        scored.append(symbol)
        if rec["status"] == "EMITTED" and rec["setup"].endswith("_LONG"):
            over, corr_syms = portfolio.correlation_flag(conn, symbol, cfg)
            if over:
                if rec["tier"] == "A":
                    rec["tier"] = "B"
                elif rec["tier"] == "B":
                    rec["status"] = "SUPPRESSED_CORRELATION"
                    rec["reason"] = f"correlated open positions: {corr_syms}"
                    persist_signal(conn, run_id, rec)
                    continue
                rec["corr_flag"] = corr_syms
            rec["calibration"] = scoring.calibrate(conn, rec["raw_score"], cfg)
            if alerts.in_cooldown(conn, symbol, rec["setup"], rec["direction"],
                                  cfg["cooldown_hours"],
                                  rec.get("struct_ev", {}).get("atr"),
                                  rec.get("plan", {}).get("stop")):
                rec["status"] = "SUPPRESSED_COOLDOWN"
                persist_signal(conn, run_id, rec)
                continue
            sid = persist_signal(conn, run_id, rec)
            msg = alerts.render_entry(symbol, rec["setup"], rec["tier"],
                                      regime_res, rec["factors"],
                                      rec["calibration"], rec["plan"])
            alerts.queue(conn, "ENTRY", msg, sid)
            emitted.append(symbol)
        else:
            persist_signal(conn, run_id, rec)

    # 3. tracked position monitoring (STOP_HIT / TP_HIT / EXIT_WARNING)
    _monitor_positions(conn, cfg, provider)

    alerts.deliver_pending(conn)
    summary = {"regime": regime_res["state"], "scored": len(scored),
               "unavailable": unavailable, "emitted": emitted,
               "kill_switch": killed,
               "elapsed_s": round(time.monotonic() - t0, 1)}
    conn.execute("""UPDATE runs SET finished_at=now(), status='OK', summary=%s
                    WHERE run_id=%s""", (_json(conn, summary), run_id))
    conn.commit()
    return {"run_id": run_id, **summary}


def _monitor_positions(conn, cfg: dict, provider) -> None:
    rows = conn.execute("""SELECT id, symbol, direction, entry, stop, tp1, tp2,
                           tp1_taken FROM positions WHERE status='OPEN'""").fetchall()
    for pid, symbol, direction, entry, stop, tp1, tp2, tp1_taken in rows:
        row = conn.execute("""SELECT close FROM candles WHERE symbol=%s AND tf='1h'
                              ORDER BY open_time DESC LIMIT 1""", (symbol,)).fetchone()
        if not row:
            continue
        price = float(row[0])
        entry, stop, tp1, tp2 = map(float, (entry, stop, tp1, tp2))
        if price <= stop:
            alerts.queue(conn, "STOP_HIT",
                         f"🛑 STOP HIT — {symbol} @ {price} (stop {stop}). Close position.")
            conn.execute("""UPDATE positions SET status='CLOSED', closed_at=now(),
                            close_reason='STOP' WHERE id=%s""", (pid,))
        elif price >= tp2:
            alerts.queue(conn, "TP_HIT",
                         f"🎯 TP2 HIT — {symbol} @ {price}. Close remainder or trail structure.")
            conn.execute("""UPDATE positions SET status='CLOSED', closed_at=now(),
                            close_reason='TP2' WHERE id=%s""", (pid,))
        elif price >= tp1 and not tp1_taken:
            alerts.queue(conn, "TP_HIT",
                         f"🎯 TP1 HIT — {symbol} @ {price}. Take ⅓, move stop to {entry} (BE).")
            conn.execute("""UPDATE positions SET tp1_taken=TRUE, stop=entry
                            WHERE id=%s""", (pid,))
    conn.commit()
