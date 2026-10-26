"""Pipeline validation on synthetic data: verifies the walk-forward harness
end-to-end (setup detection, gating, risk geometry, outcome simulation).
NOTE: synthetic random walks measure MECHANICS, not edge. Edge measurement
requires real klines (scripts/download_history.py, run on deployment box).
"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from tests.conftest import make_candles, frames
from app.backtest import run_backtest, summarize
from app.config import DEFAULT_CONFIG

symbols = ["BTC"] + [f"ALT{i}" for i in range(6)]
all_frames = {}
for i, s in enumerate(symbols):
    drift = 0.0035 if i % 2 == 0 else -0.001   # mix of up/down trending tokens
    all_frames[s] = {
        "1d": frames(make_candles(n=500, drift=drift, vol=0.022, seed=100 + i)),
        "4h": frames(make_candles(n=1500, drift=drift / 6, vol=0.011,
                                  seed=200 + i, tf_ms=14_400_000)),
    }

results = run_backtest(all_frames, "BTC", DEFAULT_CONFIG, step=6)
print("total candidate signals:", len(results))
by_setup = {}
for r in results:
    by_setup.setdefault(r["setup"], []).append(r)
for k, v in by_setup.items():
    print(f"  {k}: {summarize(v)}")
print("overall:", summarize(results))

# invariants
assert len(results) > 0, "harness produced zero signals on trending synthetic data"
for r in results:
    p = r["plan"]
    assert p["stop"] < p["entry_low"] < p["tp1"] < p["tp2"], "plan ordering broken"
    assert r["regime"] in ("RISK_ON", "NEUTRAL"), f"gate leak: {r['regime']}"
print("INVARIANTS OK")
