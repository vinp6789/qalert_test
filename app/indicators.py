"""Technical indicators implemented on numpy arrays. Pure functions, no state.
All are standard textbook definitions (Wilder smoothing where applicable) so
results match TradingView defaults closely enough for signal logic.
"""
from __future__ import annotations
import numpy as np


def ema(x: np.ndarray, period: int) -> np.ndarray:
    alpha = 2.0 / (period + 1)
    out = np.empty_like(x, dtype=float)
    out[0] = x[0]
    for i in range(1, len(x)):
        out[i] = alpha * x[i] + (1 - alpha) * out[i - 1]
    return out


def _wilder(x: np.ndarray, period: int) -> np.ndarray:
    out = np.empty_like(x, dtype=float)
    out[:period] = np.nan
    out[period - 1] = np.nanmean(x[:period])
    for i in range(period, len(x)):
        out[i] = (out[i - 1] * (period - 1) + x[i]) / period
    return out


def rsi(close: np.ndarray, period: int = 14) -> np.ndarray:
    delta = np.diff(close, prepend=close[0])
    gain = np.where(delta > 0, delta, 0.0)
    loss = np.where(delta < 0, -delta, 0.0)
    ag, al = _wilder(gain, period), _wilder(loss, period)
    rs = np.divide(ag, al, out=np.full_like(ag, np.inf), where=al != 0)
    return 100 - 100 / (1 + rs)


def atr(high: np.ndarray, low: np.ndarray, close: np.ndarray,
        period: int = 14) -> np.ndarray:
    prev_close = np.roll(close, 1)
    prev_close[0] = close[0]
    tr = np.maximum(high - low,
                    np.maximum(np.abs(high - prev_close), np.abs(low - prev_close)))
    return _wilder(tr, period)


def adx(high: np.ndarray, low: np.ndarray, close: np.ndarray,
        period: int = 14) -> np.ndarray:
    up = np.diff(high, prepend=high[0])
    dn = -np.diff(low, prepend=low[0])
    plus_dm = np.where((up > dn) & (up > 0), up, 0.0)
    minus_dm = np.where((dn > up) & (dn > 0), dn, 0.0)
    tr = atr(high, low, close, period)
    with np.errstate(invalid="ignore", divide="ignore"):
        plus_di = 100 * _wilder(plus_dm, period) / tr
        minus_di = 100 * _wilder(minus_dm, period) / tr
        dx = 100 * np.abs(plus_di - minus_di) / (plus_di + minus_di)
    return _wilder(np.nan_to_num(dx), period)


def macd_hist(close: np.ndarray, fast: int = 12, slow: int = 26,
              signal: int = 9) -> np.ndarray:
    line = ema(close, fast) - ema(close, slow)
    return line - ema(line, signal)


def obv(close: np.ndarray, volume: np.ndarray) -> np.ndarray:
    direction = np.sign(np.diff(close, prepend=close[0]))
    return np.cumsum(direction * volume)


def realized_vol(close: np.ndarray, window: int = 20) -> float:
    """Annualized realized vol of the last `window` returns (daily assumed)."""
    rets = np.diff(np.log(close[-(window + 1):]))
    return float(np.std(rets) * np.sqrt(365))


def swing_levels(high: np.ndarray, low: np.ndarray, lookback: int = 60,
                 pivot: int = 3) -> tuple[list[float], list[float]]:
    """Pivot-based swing highs (resistance) and lows (support) in lookback window."""
    res, sup = [], []
    n = len(high)
    start = max(pivot, n - lookback)
    for i in range(start, n - pivot):
        if high[i] == max(high[i - pivot:i + pivot + 1]):
            res.append(float(high[i]))
        if low[i] == min(low[i - pivot:i + pivot + 1]):
            sup.append(float(low[i]))
    return sorted(set(res)), sorted(set(sup))
