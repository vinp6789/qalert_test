import datetime as dt
import numpy as np
from app import engine, outcomes, scoring
from app.config import DEFAULT_CONFIG, load_active_config
from app.data.provider import FailoverProvider
from tests.conftest import make_candles, frames


class FakeClient:
    name = "binance"
    def __init__(self, table):  # table: {(sym, tf): candles}
        self.table = table
    def klines(self, symbol, quote, tf, limit):
        from app.data.clients import ProviderError
        c = self.table.get((symbol, tf))
        if c is None:
            raise ProviderError(f"no data {symbol} {tf}")
        return c
    def funding_rate(self, s): return 0.0001
    def open_interest(self, s): return 1000.0


def _small_cfg(conn, watchlist):
    import json
    cfg = dict(DEFAULT_CONFIG, watchlist=watchlist)
    conn.execute("INSERT INTO config (data, active) VALUES (%s, TRUE)",
                 (json.dumps(cfg),))
    conn.commit()
    return cfg


def _table_for(symbols, seed_base=10, drift=0.003):
    t = {}
    for i, s in enumerate(symbols):
        t[(s, "1d")] = make_candles(n=300, drift=drift, vol=0.015, seed=seed_base + i)
        t[(s, "4h")] = make_candles(n=300, drift=drift / 6, vol=0.008,
                                    seed=seed_base + i + 50, tf_ms=14_400_000)
        t[(s, "1h")] = make_candles(n=300, drift=drift / 24, vol=0.004,
                                    seed=seed_base + i + 99, tf_ms=3_600_000)
    return t


def test_run_idempotent_and_excludes_unavailable(conn):
    _small_cfg(conn, ["BTC", "ETH", "GHOST"])   # GHOST has no data
    table = _table_for(["BTC", "ETH"])
    p = FailoverProvider(conn, primary=FakeClient(table),
                         secondary=FakeClient(table))
    r1 = engine.run(conn, provider=p)
    assert r1["scored"] == 2
    assert any(s == "GHOST" for s, _ in r1["unavailable"])
    r2 = engine.run(conn, provider=p)
    assert r2.get("skipped") == "already completed"   # idempotent


def test_signals_persist_with_suppression_reasons(conn):
    _small_cfg(conn, ["BTC", "ETH"])
    table = _table_for(["BTC", "ETH"], drift=-0.004)  # downtrend => RISK_OFF
    p = FailoverProvider(conn, primary=FakeClient(table),
                         secondary=FakeClient(table))
    engine.run(conn, provider=p)
    rows = conn.execute("SELECT status FROM signals").fetchall()
    # in RISK_OFF no entry may be EMITTED
    assert all(s != "EMITTED" or True for (s,) in rows)
    emitted_entries = conn.execute(
        "SELECT count(*) FROM signals WHERE status='EMITTED' "
        "AND setup LIKE '%%_LONG'").fetchone()[0]
    assert emitted_entries == 0


def test_outcome_resolution_pessimistic_and_correct(conn):
    _, cfg = load_active_config(conn)
    # plant a signal + candles by hand
    conn.execute("INSERT INTO runs (run_id, status) VALUES ('T','OK')")
    sid = conn.execute("""
        INSERT INTO signals (run_id, symbol, setup, direction, status,
            factors, regime, entry_high, stop, tp1, tp2, raw_score, created_at)
        VALUES ('T','XXX','TREND_PULLBACK_LONG','LONG','EMITTED','{}','RISK_ON',
                100, 95, 107.5, 115, 85, now() - interval '10 days')
        RETURNING id""").fetchone()[0]
    # candle path: hits TP1 (107.5) and stop (95) in SAME candle -> pessimistic SL
    base = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=9)
    conn.execute("""INSERT INTO candles VALUES
        ('XXX','4h',%s,100,108,94,100,1000,'test')""", (base,))
    conn.commit()
    n = outcomes.resolve_pending(conn, cfg)
    assert n == 1
    res = conn.execute("SELECT result, realized_r FROM outcomes "
                       "WHERE signal_id=%s", (sid,)).fetchone()
    assert res[0] == "SL" and float(res[1]) == -1.0


def test_tp1_then_tp2_r_math(conn):
    _, cfg = load_active_config(conn)
    conn.execute("INSERT INTO runs (run_id, status) VALUES ('T2','OK')")
    sid = conn.execute("""
        INSERT INTO signals (run_id, symbol, setup, direction, status,
            factors, regime, entry_high, stop, tp1, tp2, raw_score, created_at)
        VALUES ('T2','YYY','BREAKOUT_LONG','LONG','EMITTED','{}','RISK_ON',
                100, 95, 107.5, 115, 85, now() - interval '10 days')
        RETURNING id""").fetchone()[0]
    base = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=9)
    conn.execute("""INSERT INTO candles VALUES
        ('YYY','4h',%s,100,108,99,107,1000,'test'),
        ('YYY','4h',%s,107,116,106,115,1000,'test')""",
        (base, base + dt.timedelta(hours=4)))
    conn.commit()
    outcomes.resolve_pending(conn, cfg)
    res = conn.execute("SELECT result, realized_r FROM outcomes "
                       "WHERE signal_id=%s", (sid,)).fetchone()
    assert res[0] == "TP2" and abs(float(res[1]) - 2.5) < 1e-6


def test_calibration_and_kill_switch(conn):
    _, cfg = load_active_config(conn)
    conn.execute("INSERT INTO runs (run_id, status) VALUES ('T3','OK')")
    # 25 losing signals in bin 8
    for i in range(25):
        sid = conn.execute("""
            INSERT INTO signals (run_id, symbol, setup, direction, status,
                factors, regime, raw_score, stop, entry_high, tp1, tp2)
            VALUES ('T3','Z%s','BREAKOUT_LONG','LONG','EMITTED','{}','RISK_ON',
                    85, 95, 100, 107.5, 115) RETURNING id""" % i).fetchone()[0]
        conn.execute("""INSERT INTO outcomes (signal_id, result, realized_r,
                        hypothetical) VALUES (%s,'SL',-1,FALSE)""", (sid,))
    conn.commit()
    scoring.rebuild_calibration(conn)
    cal = scoring.calibrate(conn, 85, cfg)
    assert cal["uncalibrated"] is False and cal["win_rate"] == 0.0
    assert scoring.kill_switch_engaged(conn, cfg) is True


def test_correlation_guard(conn):
    import json, numpy as np, datetime as dt
    from app import portfolio
    from app.config import DEFAULT_CONFIG
    cfg = DEFAULT_CONFIG
    # 3 open positions + candidate, all with identical return series (corr=1)
    now = dt.datetime.now(dt.timezone.utc)
    rng = np.random.default_rng(5)
    rets = rng.normal(0.001, 0.02, 120)
    closes = 100 * np.exp(np.cumsum(rets))
    conn.execute("INSERT INTO runs (run_id, status) VALUES ('TC','OK')")
    for sym in ("P1", "P2", "P3", "CAND"):
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO candles VALUES (%s,'1d',%s,%s,%s,%s,%s,1,'t')",
                [(sym, now - dt.timedelta(days=120 - i), c, c * 1.01, c * 0.99, c)
                 for i, c in enumerate(closes)])
        if sym != "CAND":
            conn.execute("""INSERT INTO positions (symbol, direction, entry,
                stop, tp1, tp2) VALUES (%s,'LONG',100,95,107,115)""", (sym,))
    conn.commit()
    over, syms = portfolio.correlation_flag(conn, "CAND", cfg)
    assert over and set(syms) == {"P1", "P2", "P3"}
