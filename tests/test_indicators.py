import numpy as np
from app import indicators as ind


def test_ema_converges_to_constant():
    x = np.full(100, 50.0)
    assert abs(ind.ema(x, 20)[-1] - 50.0) < 1e-9


def test_ema_known_sequence():
    # EMA(3) of [1..5], alpha=0.5: 1, 1.5, 2.25, 3.125, 4.0625
    out = ind.ema(np.array([1, 2, 3, 4, 5], dtype=float), 3)
    assert np.allclose(out, [1, 1.5, 2.25, 3.125, 4.0625])


def test_rsi_bounds_and_direction():
    up = np.cumsum(np.ones(50)) + 100
    down = 200 - np.cumsum(np.ones(50))
    assert ind.rsi(up)[-1] > 90
    assert ind.rsi(down)[-1] < 10


def test_atr_positive_and_scale():
    h = np.array([12.0] * 50); l = np.array([10.0] * 50); c = np.array([11.0] * 50)
    a = ind.atr(h, l, c)[-1]
    assert abs(a - 2.0) < 0.01  # constant TR of 2


def test_swing_levels_finds_pivot():
    low = np.full(80, 10.0); high = np.full(80, 11.0)
    high[40] = 15.0  # isolated pivot high
    low[50] = 8.0
    res, sup = ind.swing_levels(high, low, lookback=80)
    assert 15.0 in res and 8.0 in sup


def test_obv_direction():
    c = np.array([1, 2, 3, 4, 5], dtype=float)
    v = np.ones(5) * 10
    assert ind.obv(c, v)[-1] == 40  # first bar sign(0)=0
