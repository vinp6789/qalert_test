"""Exchange market-data clients. Both return identical normalized shapes.

Normalized candle: dict(open_time: int ms, open, high, low, close, volume: float)
All endpoints used are public (no API key).
"""
from __future__ import annotations
import httpx

TF_MAP_BINANCE = {"1h": "1h", "4h": "4h", "1d": "1d"}
TF_MAP_BYBIT = {"1h": "60", "4h": "240", "1d": "D"}
TF_MS = {"1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000}


class ProviderError(Exception):
    pass


class BinanceClient:
    name = "binance"
    base = "https://api.binance.com"

    def __init__(self, client: httpx.Client | None = None):
        self.http = client or httpx.Client(timeout=10)

    def klines(self, symbol: str, quote: str, tf: str, limit: int = 300) -> list[dict]:
        pair = f"{symbol}{quote}"
        r = self.http.get(f"{self.base}/api/v3/klines",
                          params={"symbol": pair, "interval": TF_MAP_BINANCE[tf],
                                  "limit": limit})
        if r.status_code != 200:
            raise ProviderError(f"binance klines {pair} {tf}: HTTP {r.status_code}")
        out = []
        for k in r.json():
            out.append({"open_time": int(k[0]), "open": float(k[1]),
                        "high": float(k[2]), "low": float(k[3]),
                        "close": float(k[4]), "volume": float(k[5])})
        return out

    def funding_rate(self, symbol: str) -> float | None:
        """Latest perp funding rate; None if no perp exists for symbol."""
        r = self.http.get("https://fapi.binance.com/fapi/v1/premiumIndex",
                          params={"symbol": f"{symbol}USDT"})
        if r.status_code != 200:
            return None
        return float(r.json().get("lastFundingRate", 0))

    def open_interest(self, symbol: str) -> float | None:
        r = self.http.get("https://fapi.binance.com/fapi/v1/openInterest",
                          params={"symbol": f"{symbol}USDT"})
        if r.status_code != 200:
            return None
        return float(r.json().get("openInterest", 0))


class BybitClient:
    name = "bybit"
    base = "https://api.bybit.com"

    def __init__(self, client: httpx.Client | None = None):
        self.http = client or httpx.Client(timeout=10)

    def klines(self, symbol: str, quote: str, tf: str, limit: int = 300) -> list[dict]:
        pair = f"{symbol}{quote}"
        r = self.http.get(f"{self.base}/v5/market/kline",
                          params={"category": "spot", "symbol": pair,
                                  "interval": TF_MAP_BYBIT[tf], "limit": limit})
        body = r.json() if r.status_code == 200 else {}
        if r.status_code != 200 or body.get("retCode") != 0:
            raise ProviderError(f"bybit klines {pair} {tf}: HTTP {r.status_code} "
                                f"ret={body.get('retCode')}")
        rows = body["result"]["list"]  # bybit returns newest-first
        out = []
        for k in reversed(rows):
            out.append({"open_time": int(k[0]), "open": float(k[1]),
                        "high": float(k[2]), "low": float(k[3]),
                        "close": float(k[4]), "volume": float(k[5])})
        return out

    def funding_rate(self, symbol: str) -> float | None:
        r = self.http.get(f"{self.base}/v5/market/tickers",
                          params={"category": "linear", "symbol": f"{symbol}USDT"})
        body = r.json() if r.status_code == 200 else {}
        try:
            return float(body["result"]["list"][0]["fundingRate"])
        except (KeyError, IndexError, TypeError, ValueError):
            return None

    def open_interest(self, symbol: str) -> float | None:
        r = self.http.get(f"{self.base}/v5/market/tickers",
                          params={"category": "linear", "symbol": f"{symbol}USDT"})
        body = r.json() if r.status_code == 200 else {}
        try:
            return float(body["result"]["list"][0]["openInterest"])
        except (KeyError, IndexError, TypeError, ValueError):
            return None
