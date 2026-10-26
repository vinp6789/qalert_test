"""Configuration: environment settings + versioned runtime config stored in DB."""
from __future__ import annotations
import json
from pydantic_settings import BaseSettings

DEFAULT_CONFIG: dict = {
    "watchlist": ["BTC", "ETH", "SOL", "AAVE", "LINK", "JUP", "RENDER", "ONDO",
                  "TAO", "ZEC", "PENDLE", "NEAR", "ORDI", "STX", "XMR", "AKT",
                  "HYPE", "VIRTUAL", "FLUID", "NOCK", "FACY"],
    "quote": "USDT",
    "timeframes": ["1d", "4h", "1h"],
    "candle_limit": 300,
    "risk_pct": 1.0,
    "account_usd": 10000.0,
    "cooldown_hours": 24,
    "max_stop_atr": 2.5,
    "min_stop_atr": 0.5,
    "staleness_factor": 1.5,
    "weights": {"trend": 0.30, "volume": 0.20, "structure": 0.15,
                "momentum": 0.15, "rel_strength": 0.10, "derivatives": 0.10},
    "tier_a_score": 80, "tier_b_score": 60,
    "min_factor_families": 4,
    "correlation_window_days": 90, "correlation_threshold": 0.8,
    "correlated_open_limit": 3,
    "kill_switch": {"window": 20, "min_win_rate": 0.30},
    "calibration_min_n": 20,
    "outcome_expiry_days": 21,
}


class Settings(BaseSettings):
    database_url: str = "postgresql://postgres:postgres@localhost:5432/qalert"
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    run_budget_seconds: int = 240
    model_config = {"env_prefix": "QALERT_"}


settings = Settings()


def load_active_config(conn) -> tuple[int, dict]:
    """Return (version, config). Seeds DEFAULT_CONFIG on first run."""
    row = conn.execute(
        "SELECT version, data FROM config WHERE active LIMIT 1").fetchone()
    if row:
        return row[0], row[1]
    row = conn.execute(
        "INSERT INTO config (data, active) VALUES (%s, TRUE) RETURNING version",
        (json.dumps(DEFAULT_CONFIG),)).fetchone()
    conn.commit()
    return row[0], DEFAULT_CONFIG
