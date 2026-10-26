import numpy as np
import pytest
from app import regime as rg, scoring, risk
from tests.conftest import make_candles, frames


def test_regime_missing_btc_is_risk_off():
    res = rg.classify(None, None, None, None)
    assert res["state"] == "RISK_OFF" and res["degraded"]


def test_regime_strong_uptrend_risk_on():
    up = frames(make_candles(n=300, drift=0.004, vol=0.012, seed=7))
    res = rg.classify(up, 55.0, -1.0, 0.0001)
    assert res["state"] in ("RISK_ON", "NEUTRAL")  # never RISK_OFF in clean uptrend
    assert res["evidence"]["btc_above_200ema"]


def test_regime_downtrend_risk_off():
    dn = frames(make_candles(n=300, drift=-0.004, vol=0.015, seed=3))
    res = rg.classify(dn, 60.0, 1.0, 0.0)
    assert res["state"] == "RISK_OFF"


def test_degraded_inputs_never_loosen():
    up = frames(make_candles(n=300, drift=0.004, vol=0.012, seed=7))
    full = rg.classify(up, 55.0, -1.0, 0.0001)
    degraded = rg.classify(up, None, None, None)
    order = rg.CONSERVATISM
    assert order[degraded["state"]] >= order[full["state"]]


def test_score_renormalizes_over_available():
    w = {"a": 0.5, "b": 0.5}
    s, _ = scoring.raw_score({"a": 100, "b": None}, w, min_families=1)
    assert s == 100.0  # renormalized, not diluted by missing family
    s2, why = scoring.raw_score({"a": 100, "b": None}, w, min_families=2)
    assert s2 is None and "available" in why


def test_risk_rejects_far_stop():
    cfg = {"max_stop_atr": 2.5, "min_stop_atr": 0.5,
           "account_usd": 10000, "risk_pct": 1.0}
    with pytest.raises(risk.RiskRejection):
        risk.build_plan(price=100, atr=2.0, nearest_sup=90, cfg=cfg)  # 5x ATR


def test_risk_plan_math():
    cfg = {"max_stop_atr": 2.5, "min_stop_atr": 0.5,
           "account_usd": 10000, "risk_pct": 1.0}
    plan = risk.build_plan(price=100, atr=3.0, nearest_sup=96, cfg=cfg)
    dist = 100 - plan["stop"]
    assert abs(plan["tp1"] - (100 + 1.5 * dist)) < 1e-6
    assert abs(plan["units"] * dist - 100.0) < 0.5  # $100 risk on $10k @1%
