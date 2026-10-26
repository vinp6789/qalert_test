"""Failover data provider with per-provider circuit breakers (state in Postgres,
so breaker state survives restarts) plus candle validation & staleness gates.

Contract: fetch_candles() either returns validated fresh candles or raises
DataUnavailable with a reason. It NEVER returns stale/suspect data.
"""
from __future__ import annotations
import datetime as dt
import time
from .clients import BinanceClient, BybitClient, ProviderError, TF_MS

BREAKER_THRESHOLD = 3
BREAKER_COOLDOWN_MIN = 15
MAX_SINGLE_CANDLE_MOVE = 0.40  # >40% single-candle move => corroborate or reject


class DataUnavailable(Exception):
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


class FailoverProvider:
    def __init__(self, conn, primary=None, secondary=None, now=None):
        self.conn = conn
        self.primary = primary or BinanceClient()
        self.secondary = secondary or BybitClient()
        self.now = now or (lambda: dt.datetime.now(dt.timezone.utc))

    # ---- circuit breaker (persisted) ----
    def _is_down(self, name: str) -> bool:
        row = self.conn.execute(
            "SELECT down_until FROM provider_health WHERE provider=%s", (name,)
        ).fetchone()
        return bool(row and row[0] and row[0] > self.now())

    def _record_failure(self, name: str, err: str) -> None:
        row = self.conn.execute("""
            INSERT INTO provider_health (provider, fail_count, last_error, updated_at)
            VALUES (%s, 1, %s, now())
            ON CONFLICT (provider) DO UPDATE
              SET fail_count = provider_health.fail_count + 1,
                  last_error = EXCLUDED.last_error, updated_at = now()
            RETURNING fail_count""", (name, err)).fetchone()
        if row[0] >= BREAKER_THRESHOLD:
            self.conn.execute(
                "UPDATE provider_health SET down_until = now() + make_interval(mins => %s),"
                " fail_count = 0 WHERE provider = %s",
                (BREAKER_COOLDOWN_MIN, name))
        self.conn.commit()

    def _record_success(self, name: str) -> None:
        self.conn.execute("""
            INSERT INTO provider_health (provider, fail_count, updated_at)
            VALUES (%s, 0, now())
            ON CONFLICT (provider) DO UPDATE
              SET fail_count = 0, down_until = NULL, updated_at = now()""", (name,))
        self.conn.commit()

    # ---- validation ----
    @staticmethod
    def _validate(candles: list[dict], tf: str, staleness_factor: float,
                  now_ms: int) -> str | None:
        """Return reason string if invalid, else None."""
        if not candles or len(candles) < 60:
            return f"insufficient candles ({len(candles) if candles else 0})"
        last = candles[-1]
        # freshness: last candle open must be within staleness_factor * tf of now
        if now_ms - last["open_time"] > TF_MS[tf] * staleness_factor:
            age_h = (now_ms - last["open_time"]) / 3_600_000
            return f"stale data: last candle {age_h:.1f}h old for tf={tf}"
        for c in candles[-30:]:
            if not (c["low"] <= c["open"] <= c["high"]
                    and c["low"] <= c["close"] <= c["high"]) or c["low"] <= 0:
                return "malformed OHLC"
        for prev, cur in zip(candles[-30:-1], candles[-29:]):
            if prev["close"] > 0:
                move = abs(cur["close"] - prev["close"]) / prev["close"]
                if move > MAX_SINGLE_CANDLE_MOVE:
                    return f"suspect tick: {move:.0%} single-candle move"
        return None

    # ---- public API ----
    def fetch_candles(self, symbol: str, quote: str, tf: str, limit: int,
                      staleness_factor: float = 1.5,
                      retries: int = 3) -> tuple[list[dict], str]:
        """Returns (candles, source). Raises DataUnavailable otherwise."""
        now_ms = int(self.now().timestamp() * 1000)
        reasons = []
        for client in (self.primary, self.secondary):
            if self._is_down(client.name):
                reasons.append(f"{client.name}: circuit open")
                continue
            for attempt in range(retries):
                try:
                    candles = client.klines(symbol, quote, tf, limit)
                    bad = self._validate(candles, tf, staleness_factor, now_ms)
                    if bad:
                        raise ProviderError(f"{client.name}: {bad}")
                    self._record_success(client.name)
                    return candles, client.name
                except Exception as e:  # noqa: BLE001 - any failure = provider failure
                    reasons.append(str(e))
                    self._record_failure(client.name, str(e))
                    if attempt < retries - 1:
                        time.sleep(min(4 ** attempt, 8))
        raise DataUnavailable("; ".join(reasons[-4:]) or "all providers failed")

    def fetch_derivatives(self, symbol: str) -> dict:
        """Best-effort funding/OI. Missing values are None (factor -> UNAVAILABLE)."""
        for client in (self.primary, self.secondary):
            if self._is_down(client.name):
                continue
            try:
                fr = client.funding_rate(symbol)
                oi = client.open_interest(symbol)
                if fr is not None or oi is not None:
                    return {"funding_rate": fr, "open_interest": oi,
                            "source": client.name}
            except Exception:  # noqa: BLE001
                continue
        return {"funding_rate": None, "open_interest": None, "source": None}


def store_candles(conn, symbol: str, tf: str, candles: list[dict], source: str) -> None:
    with conn.cursor() as cur:
        cur.executemany("""
            INSERT INTO candles (symbol, tf, open_time, open, high, low, close, volume, source)
            VALUES (%s,%s,to_timestamp(%s/1000.0),%s,%s,%s,%s,%s,%s)
            ON CONFLICT (symbol, tf, open_time) DO UPDATE
              SET open=EXCLUDED.open, high=EXCLUDED.high, low=EXCLUDED.low,
                  close=EXCLUDED.close, volume=EXCLUDED.volume, source=EXCLUDED.source""",
            [(symbol, tf, c["open_time"], c["open"], c["high"], c["low"],
              c["close"], c["volume"], source) for c in candles])
    conn.commit()
