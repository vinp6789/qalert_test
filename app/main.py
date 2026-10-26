"""Entrypoints: FastAPI service (POST /run, /health, /report) + CLI."""
from __future__ import annotations
import sys
from fastapi import FastAPI
from . import db, engine, outcomes, scoring, alerts
from .config import load_active_config, settings

api = FastAPI(title="qalert")


@api.post("/run")
def trigger_run():
    with db.connect() as conn:
        db.migrate(conn)
        return engine.run(conn, budget_s=settings.run_budget_seconds)


@api.post("/resolve")
def trigger_resolve():
    with db.connect() as conn:
        _, cfg = load_active_config(conn)
        n = outcomes.resolve_pending(conn, cfg)
        bins = scoring.rebuild_calibration(conn)
        return {"resolved": n, "calibration_bins": bins}


@api.get("/health")
def health():
    with db.connect() as conn:
        run = conn.execute("""SELECT run_id, status, summary FROM runs
                              ORDER BY started_at DESC LIMIT 1""").fetchone()
        prov = conn.execute("""SELECT provider, fail_count, down_until
                               FROM provider_health""").fetchall()
        return {"last_run": run, "providers": prov}


@api.get("/report")
def report():
    with db.connect() as conn:
        return outcomes.performance_report(conn)


@api.post("/heartbeat")
def heartbeat():
    with db.connect() as conn:
        run = conn.execute("""SELECT run_id, status, summary FROM runs
                              ORDER BY started_at DESC LIMIT 1""").fetchone()
        msg = (f"💓 System healthy. Last run {run[0]} status={run[1]} "
               f"scored={run[2].get('scored') if run and run[2] else '?'}"
               if run else "💓 System up. No runs yet.")
        alerts.queue(conn, "HEARTBEAT", msg)
        alerts.deliver_pending(conn)
        return {"ok": True}


def cli():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    with db.connect() as conn:
        db.migrate(conn)
        if cmd == "run":
            print(engine.run(conn))
        elif cmd == "resolve":
            _, cfg = load_active_config(conn)
            print({"resolved": outcomes.resolve_pending(conn, cfg),
                   "bins": scoring.rebuild_calibration(conn)})
        elif cmd == "report":
            print(outcomes.performance_report(conn))
        elif cmd == "migrate":
            print({"applied": db.migrate(conn)})


if __name__ == "__main__":
    cli()
