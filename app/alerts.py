"""Alert layer: dedup/cooldown, priority hierarchy, rendering, delivery.
Telegram delivery is best-effort; every alert is durably stored first.
"""
from __future__ import annotations
import httpx
from .config import settings

PRIORITY = {"STOP_HIT": 0, "RISK": 1, "EXIT_WARNING": 2, "TP_HIT": 3,
            "ENTRY": 4, "SYSTEM": 5, "HEARTBEAT": 6}


def in_cooldown(conn, symbol: str, setup: str, direction: str,
                hours: int, atr: float | None, new_stop: float | None) -> bool:
    row = conn.execute("""
        SELECT tier, stop FROM signals
        WHERE symbol=%s AND setup=%s AND direction=%s AND status='EMITTED'
          AND created_at > now() - make_interval(hours => %s)
        ORDER BY created_at DESC LIMIT 1""",
        (symbol, setup, direction, hours)).fetchone()
    if not row:
        return False
    # allow re-alert if invalidation moved materially (> 1 ATR)
    if atr and new_stop and row[1] is not None:
        if abs(float(row[1]) - new_stop) > atr:
            return False
    return True


def render_entry(symbol: str, setup: str, tier_: str, regime: dict,
                 factors: dict, cal: dict, plan: dict) -> str:
    f = " | ".join(f"{k[:5].title()} {'+' if v >= 0 else ''}{v:.0f}"
                   if v is not None else f"{k[:5].title()} N/A"
                   for k, v in factors.items())
    if cal.get("uncalibrated"):
        conf = f"UNCALIBRATED (n={cal['n']})"
    else:
        conf = f"{cal['win_rate']:.0%} win rate (n={cal['n']}, avg {cal['avg_r']:.1f}R)"
    return (
        f"{'🟢' if tier_ == 'A' else '🔵'} {tier_}-TIER LONG — {symbol} ({setup})\n"
        f"Regime: {regime['state']}{' [degraded inputs]' if regime.get('degraded') else ''}\n"
        f"Confluence: {f}\n"
        f"Calibrated: {conf}\n"
        f"Entry: {plan['entry_low']}–{plan['entry_high']} | "
        f"SL: {plan['stop']} (−{plan['stop_pct']}%)\n"
        f"TP1: {plan['tp1']} (1.5R, take ⅓, stop→BE) | TP2: {plan['tp2']} (3R)\n"
        f"Size @{plan['risk_usd']:.0f} USD risk: {plan['units']} {symbol} "
        f"(~${plan['position_usd']})\n"
        f"Invalid if: daily close < {plan['stop']}\n"
        f"/track {symbol} to monitor")


def queue(conn, kind: str, message: str, signal_id: int | None = None) -> int:
    row = conn.execute("""
        INSERT INTO alerts (signal_id, priority, kind, message)
        VALUES (%s,%s,%s,%s) RETURNING id""",
        (signal_id, PRIORITY[kind], kind, message)).fetchone()
    conn.commit()
    return row[0]


def deliver_pending(conn) -> int:
    """Send unsent alerts, highest priority first. No token => mark sent
    (DB is the delivery channel in dev)."""
    rows = conn.execute("""
        SELECT id, message FROM alerts WHERE sent_at IS NULL
        ORDER BY priority, id""").fetchall()
    sent = 0
    for aid, msg in rows:
        ok = _send_telegram(msg) if settings.telegram_bot_token else True
        if ok:
            conn.execute("UPDATE alerts SET sent_at=now() WHERE id=%s", (aid,))
            sent += 1
    conn.commit()
    return sent


def _send_telegram(msg: str) -> bool:
    try:
        r = httpx.post(
            f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage",
            json={"chat_id": settings.telegram_chat_id, "text": msg},
            timeout=10)
        return r.status_code == 200
    except Exception:  # noqa: BLE001
        return False
