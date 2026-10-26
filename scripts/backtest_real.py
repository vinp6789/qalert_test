"""Walk-forward backtest on REAL stored candles (after download_history.py).
This is the edge measurement that gates go-live."""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import numpy as np
from app import db
from app.backtest import run_backtest, summarize
from app.config import load_active_config

with db.connect() as conn:
    _, cfg = load_active_config(conn)
    all_frames = {}
    for symbol in cfg["watchlist"]:
        frames = {}
        for tf in ("1d", "4h"):
            rows = conn.execute("""SELECT open, high, low, close, volume
                FROM candles WHERE symbol=%s AND tf=%s ORDER BY open_time""",
                (symbol, tf)).fetchall()
            if len(rows) < 250:
                break
            arr = np.array(rows, dtype=float)
            frames[tf] = {"open": arr[:, 0], "high": arr[:, 1], "low": arr[:, 2],
                          "close": arr[:, 3], "volume": arr[:, 4]}
        if len(frames) == 2:
            all_frames[symbol] = frames
    if "BTC" not in all_frames:
        sys.exit("Need BTC history first: python scripts/download_history.py")
    print(f"backtesting {len(all_frames)} symbols")
    results = run_backtest(all_frames, "BTC", cfg, step=6)
    by = {}
    for r in results:
        by.setdefault(r["setup"], []).append(r)
    for k, v in sorted(by.items()):
        print(f"{k}: {summarize(v)}")
    print("OVERALL:", summarize(results))
    print("\nGo-live gate: overall avg_r must remain > 0 after a 30-40% haircut,"
          "\nand per-setup n should be >= 30 before trusting its stats.")
