-- 001_init.sql : core schema
CREATE TABLE IF NOT EXISTS schema_version (
  version INT PRIMARY KEY,
  applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS config (
  version SERIAL PRIMARY KEY,
  data JSONB NOT NULL,
  active BOOLEAN NOT NULL DEFAULT FALSE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS one_active_config ON config (active) WHERE active;

CREATE TABLE IF NOT EXISTS runs (
  run_id TEXT PRIMARY KEY,             -- e.g. '4H-2026-07-02T08:00:00Z'
  started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  finished_at TIMESTAMPTZ,
  status TEXT NOT NULL DEFAULT 'RUNNING',  -- RUNNING|OK|FAILED
  config_version INT REFERENCES config(version),
  summary JSONB
);

CREATE TABLE IF NOT EXISTS candles (
  symbol TEXT NOT NULL,
  tf TEXT NOT NULL,                    -- '1h','4h','1d'
  open_time TIMESTAMPTZ NOT NULL,
  open NUMERIC NOT NULL, high NUMERIC NOT NULL,
  low NUMERIC NOT NULL, close NUMERIC NOT NULL,
  volume NUMERIC NOT NULL,
  source TEXT NOT NULL,
  PRIMARY KEY (symbol, tf, open_time)
);
CREATE INDEX IF NOT EXISTS candles_lookup ON candles (symbol, tf, open_time DESC);

CREATE TABLE IF NOT EXISTS provider_health (
  provider TEXT PRIMARY KEY,
  fail_count INT NOT NULL DEFAULT 0,
  down_until TIMESTAMPTZ,
  last_error TEXT,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS regimes (
  run_id TEXT PRIMARY KEY REFERENCES runs(run_id),
  state TEXT NOT NULL,                 -- RISK_ON|NEUTRAL|CHOP|RISK_OFF
  degraded BOOLEAN NOT NULL DEFAULT FALSE,
  evidence JSONB NOT NULL
);

CREATE TABLE IF NOT EXISTS signals (
  id BIGSERIAL PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES runs(run_id),
  symbol TEXT NOT NULL,
  setup TEXT NOT NULL,                 -- TREND_PULLBACK_LONG|BREAKOUT_LONG|EXIT_WARNING|...
  direction TEXT NOT NULL,             -- LONG|SHORT|FLAT
  tier TEXT,                           -- A|B|WATCH
  status TEXT NOT NULL,                -- EMITTED|SUPPRESSED_REGIME|SUPPRESSED_DATA_GAP|SUPPRESSED_COOLDOWN|SUPPRESSED_RISK_GEOMETRY|SUPPRESSED_CORRELATION
  raw_score NUMERIC,
  factors JSONB NOT NULL,
  regime TEXT NOT NULL,
  calibration JSONB,                   -- {win_rate, n, avg_r} or {uncalibrated: true, n}
  entry_low NUMERIC, entry_high NUMERIC,
  stop NUMERIC, tp1 NUMERIC, tp2 NUMERIC,
  atr NUMERIC,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS signals_dedup ON signals (symbol, setup, direction, created_at DESC);

CREATE TABLE IF NOT EXISTS alerts (
  id BIGSERIAL PRIMARY KEY,
  signal_id BIGINT REFERENCES signals(id),
  priority INT NOT NULL,               -- lower = more urgent
  kind TEXT NOT NULL,                  -- ENTRY|EXIT_WARNING|STOP_HIT|TP_HIT|RISK|SYSTEM|HEARTBEAT
  message TEXT NOT NULL,
  sent_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS positions (
  id BIGSERIAL PRIMARY KEY,
  symbol TEXT NOT NULL,
  direction TEXT NOT NULL,
  entry NUMERIC NOT NULL,
  stop NUMERIC NOT NULL,
  tp1 NUMERIC NOT NULL,
  tp2 NUMERIC NOT NULL,
  tp1_taken BOOLEAN NOT NULL DEFAULT FALSE,
  status TEXT NOT NULL DEFAULT 'OPEN', -- OPEN|CLOSED
  source_signal_id BIGINT REFERENCES signals(id),
  opened_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  closed_at TIMESTAMPTZ,
  close_reason TEXT
);

CREATE TABLE IF NOT EXISTS outcomes (
  signal_id BIGINT PRIMARY KEY REFERENCES signals(id),
  resolved_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  result TEXT NOT NULL,                -- TP2|TP1_THEN_BE|SL|EXPIRED
  realized_r NUMERIC NOT NULL,
  mfe_r NUMERIC, mae_r NUMERIC,
  hypothetical BOOLEAN NOT NULL DEFAULT FALSE  -- true for suppressed signals
);

CREATE TABLE IF NOT EXISTS calibration (
  score_bin INT PRIMARY KEY,           -- 0..9 deciles of raw_score 0-100
  n INT NOT NULL,
  win_rate NUMERIC NOT NULL,
  avg_r NUMERIC NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO schema_version (version) VALUES (1) ON CONFLICT DO NOTHING;
