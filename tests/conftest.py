import sys, pathlib, datetime as dt
import numpy as np
import pytest
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from app import db  # noqa: E402

TEST_DB = "postgresql://postgres:postgres@localhost:5432/qalert_test"


@pytest.fixture()
def conn():
    import psycopg
    admin = psycopg.connect("postgresql://postgres:postgres@localhost:5432/postgres",
                            autocommit=True)
    admin.execute("DROP DATABASE IF EXISTS qalert_test")
    admin.execute("CREATE DATABASE qalert_test")
    admin.close()
    c = db.connect(TEST_DB)
    db.migrate(c)
    yield c
    c.close()


def make_candles(n=300, start_price=100.0, drift=0.001, vol=0.02,
                 seed=1, tf_ms=86_400_000, end_time=None):
    """Geometric random walk OHLCV, newest last, fresh by default."""
    rng = np.random.default_rng(seed)
    end_time = end_time or int(dt.datetime.now(dt.timezone.utc).timestamp() * 1000)
    rets = rng.normal(drift, vol, n)
    closes = start_price * np.exp(np.cumsum(rets))
    out = []
    for i in range(n):
        c = closes[i]
        o = closes[i - 1] if i else start_price
        hi = max(o, c) * (1 + abs(rng.normal(0, vol / 2)))
        lo = min(o, c) * (1 - abs(rng.normal(0, vol / 2)))
        v = float(abs(rng.normal(1000, 300)))
        t = end_time - (n - 1 - i) * tf_ms
        out.append({"open_time": t, "open": float(o), "high": float(hi),
                    "low": float(lo), "close": float(c), "volume": v})
    return out


def frames(candles):
    return {k: np.array([c[k] for c in candles]) for k in
            ("open", "high", "low", "close", "volume")}
