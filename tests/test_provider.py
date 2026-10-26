import datetime as dt
import pytest
from app.data.provider import FailoverProvider, DataUnavailable
from app.data.clients import ProviderError
from tests.conftest import make_candles


class FakeClient:
    def __init__(self, name, candles=None, fail=False):
        self.name, self.candles, self.fail = name, candles, fail
        self.calls = 0

    def klines(self, *a, **k):
        self.calls += 1
        if self.fail:
            raise ProviderError(f"{self.name} down")
        return self.candles

    def funding_rate(self, s): return 0.0001
    def open_interest(self, s): return 1000.0


def test_failover_to_secondary(conn):
    good = make_candles()
    p = FailoverProvider(conn, primary=FakeClient("binance", fail=True),
                         secondary=FakeClient("bybit", candles=good))
    candles, src = p.fetch_candles("BTC", "USDT", "1d", 300, retries=1)
    assert src == "bybit" and len(candles) == 300


def test_stale_data_rejected(conn):
    old_end = int((dt.datetime.now(dt.timezone.utc)
                   - dt.timedelta(days=3)).timestamp() * 1000)
    stale = make_candles(end_time=old_end)
    p = FailoverProvider(conn, primary=FakeClient("binance", candles=stale),
                         secondary=FakeClient("bybit", candles=stale))
    with pytest.raises(DataUnavailable) as e:
        p.fetch_candles("BTC", "USDT", "1d", 300, retries=1)
    assert "stale" in str(e.value)


def test_circuit_breaker_opens_after_threshold(conn):
    bad = FakeClient("binance", fail=True)
    good = make_candles()
    p = FailoverProvider(conn, primary=bad,
                         secondary=FakeClient("bybit", candles=good))
    p.fetch_candles("BTC", "USDT", "1d", 300, retries=3)  # 3 failures -> breaker
    calls_before = bad.calls
    p.fetch_candles("ETH", "USDT", "1d", 300, retries=3)
    assert bad.calls == calls_before  # primary skipped: circuit open


def test_malformed_ohlc_rejected(conn):
    bad = make_candles()
    bad[-1]["low"] = bad[-1]["high"] + 1  # impossible
    p = FailoverProvider(conn, primary=FakeClient("binance", candles=bad),
                         secondary=FakeClient("bybit", fail=True))
    with pytest.raises(DataUnavailable):
        p.fetch_candles("BTC", "USDT", "1d", 300, retries=1)
