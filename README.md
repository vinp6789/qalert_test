# qalert — self-improving crypto alert engine

Production alert system for a configurable watchlist: entry, exit, TP, SL, and
risk alerts with regime gating, empirically calibrated confidence, automatic
outcome labeling, and a self-protective kill switch.

## Architecture

```
cron / n8n schedule ──POST /run──▶ Python engine (FastAPI)
                                     │ 1 data layer   Binance→Bybit failover,
                                     │                circuit breakers, staleness
                                     │                & sanity gates
                                     │ 2 regime       RISK_ON/NEUTRAL/CHOP/RISK_OFF
                                     │                gates which setups may fire;
                                     │                degraded data only tightens
                                     │ 3 factors      6 orthogonal families,
                                     │                None ≠ 0 (no silent defaults)
                                     │ 4 setups       TREND_PULLBACK_LONG,
                                     │                BREAKOUT_LONG, EXIT_WARNING
                                     │ 5 scoring      renormalized weighted score;
                                     │                confidence = empirical win
                                     │                rate of score bin, or
                                     │                "UNCALIBRATED (n=…)"
                                     │ 6 risk         structure stop, ATR-sanity,
                                     │                1.5R/3R TPs, sizing; bad
                                     │                geometry ⇒ rejected
                                     │ 7 alerts       dedup/cooldown, priority
                                     │                (STOP>RISK>EXIT>TP>ENTRY),
                                     │                durable-then-deliver
                                     ▼
                                  Postgres (single source of truth:
                                  candles, runs, signals, outcomes,
                                  calibration, provider_health, positions)
```

The exact same `engine.evaluate_token()` runs live and in the backtester —
no live/backtest divergence by construction.

## Key invariants (enforced by code + tests)

- **No silent defaults.** Missing data → factor `None` → confluence rule may
  suppress with `SUPPRESSED_DATA_GAP`. Stale/malformed candles → token excluded
  with reason in run summary. Never scored on bad data.
- **Idempotent runs.** `run_id` = 4h candle boundary; re-trigger is a no-op.
- **Regime gates entries.** No new longs in CHOP/RISK_OFF; NEUTRAL admits
  A-tier only. Suppressed signals are logged and outcome-labeled
  (hypothetical) so the gates themselves are measured.
- **Pessimistic ambiguity.** Candle touching both TP and SL resolves as SL,
  in live outcome labeling and in backtest alike.
- **Kill switch.** Rolling 20-signal win rate < 30% ⇒ entries demoted to
  WATCH automatically.
- **Confidence is earned.** Calibration bins need n ≥ 20 real resolved
  outcomes before a win-rate is displayed.

## Deploy (Railway)

1. `railway up` this repo (Dockerfile included) next to your existing
   Postgres; set env vars from `.env.example`.
2. Schedule `POST /run` every 4h (Railway cron, or n8n Schedule→HTTP node),
   `POST /resolve` daily, `POST /heartbeat` daily 08:00 IST.
3. Telegram: set `QALERT_TELEGRAM_BOT_TOKEN` + `QALERT_TELEGRAM_CHAT_ID`.
   Without them, alerts are durably stored in the `alerts` table (dev mode).

## Validation before real capital

```bash
python scripts/download_history.py 2024-01-01   # real Binance klines → DB
python scripts/backtest_real.py                 # walk-forward, same code path
```
Gate: overall avg R must stay positive after a 30–40% haircut, per-setup
n ≥ 30. Then 4–6 weeks shadow mode (alerts on, capital off), comparing live
outcome labels to backtest distribution before acting on signals.

## Operations

- `GET /health` — last run, provider circuit-breaker state
- `GET /report` — win rate / avg R by setup × regime × status
- `POST /resolve` — label outcomes + rebuild calibration bins
- Config lives in the `config` table (versioned); every signal records the
  config version that produced it. Edit watchlist/weights without redeploying.
- Monthly: review `/report`; adjust weights at most ±5pp with n ≥ 30.

## Test suite

`pytest tests/` — 23 tests covering indicator math, failover + circuit
breakers, staleness/sanity gates, regime conservatism under degraded data,
score renormalization, risk-geometry rejection, run idempotency,
suppression persistence, pessimistic outcome resolution, R-math, calibration,
kill switch. `scripts/validate_backtest.py` checks pipeline invariants
end-to-end on synthetic data.

## Known limits / next iterations

- Regime dominance/funding inputs currently use exchange funding only;
  CoinGecko dominance feed is a planned `002` addition (degradation rule
  already handles its absence conservatively).
- Portfolio correlation guard is live: with ≥3 open positions correlated
  >0.8 (90d daily returns) to a candidate, A-tier downgrades to B and B-tier
  is suppressed with `SUPPRESSED_CORRELATION`.
- `/track SYMBOL` Telegram command needs a small webhook handler (or n8n
  Telegram trigger writing to `positions`).
