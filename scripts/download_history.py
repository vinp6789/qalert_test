"""Download real historical klines into Postgres for backtesting.
Run on the deployment box (needs internet access to Binance):
    python scripts/download_history.py 2024-01-01
Paginates 1000 candles per request within rate limits.
"""
import sys, pathlib, time, datetime as dt
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import httpx
from app import db
from app.config import load_active_config
from app.data.provider import store_candles

TF_MS = {"1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000}

start = dt.datetime.fromisoformat(sys.argv[1] if len(sys.argv) > 1 else "2024-01-01")
start_ms = int(start.replace(tzinfo=dt.timezone.utc).timestamp() * 1000)

with db.connect() as conn:
    db.migrate(conn)
    _, cfg = load_active_config(conn)
    http = httpx.Client(timeout=15)
    for symbol in cfg["watchlist"]:
        for tf in cfg["timeframes"]:
            cursor, total = start_ms, 0
            while True:
                r = http.get("https://api.binance.com/api/v3/klines",
                             params={"symbol": f"{symbol}{cfg['quote']}",
                                     "interval": tf, "startTime": cursor,
                                     "limit": 1000})
                if r.status_code != 200:
                    print(f"  {symbol} {tf}: HTTP {r.status_code}, skipping")
                    break
                rows = r.json()
                if not rows:
                    break
                candles = [{"open_time": int(k[0]), "open": float(k[1]),
                            "high": float(k[2]), "low": float(k[3]),
                            "close": float(k[4]), "volume": float(k[5])}
                           for k in rows]
                store_candles(conn, symbol, tf, candles, "binance")
                total += len(candles)
                cursor = candles[-1]["open_time"] + TF_MS[tf]
                if len(rows) < 1000:
                    break
                time.sleep(0.15)  # stay far under rate limits
            print(f"{symbol} {tf}: {total} candles")
