#!/usr/bin/env python3
"""Uptime monitor: check URLs on an interval and email on status transitions."""

from __future__ import annotations

import argparse
import json
import os
import smtplib
import ssl
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
URLS_PATH = ROOT / "urls.json"
STATE_PATH = ROOT / "state.json"

SMTP_ENV_KEYS = (
    "SMTP_HOST",
    "SMTP_PORT",
    "SMTP_USER",
    "SMTP_PASSWORD",
    "ALERT_FROM",
    "ALERT_TO",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_config() -> dict[str, Any]:
    with URLS_PATH.open(encoding="utf-8") as f:
        config = json.load(f)
    urls = config.get("urls")
    if not isinstance(urls, list) or not urls:
        raise SystemExit("urls.json must contain a non-empty 'urls' array")
    config.setdefault("intervalSeconds", 60)
    config.setdefault("timeoutSeconds", 45)
    return config


def load_state() -> dict[str, Any]:
    if not STATE_PATH.exists():
        return {}
    with STATE_PATH.open(encoding="utf-8") as f:
        data = json.load(f)
    return data if isinstance(data, dict) else {}


def save_state(state: dict[str, Any]) -> None:
    with STATE_PATH.open("w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)
        f.write("\n")


def check_url(url: str, timeout: float) -> dict[str, Any]:
    started = time.monotonic()
    req = urllib.request.Request(
        url,
        method="GET",
        headers={"User-Agent": "UptimeRobot/1.0"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status = getattr(resp, "status", None) or resp.getcode()
            elapsed_ms = int((time.monotonic() - started) * 1000)
            up = 200 <= int(status) < 400
            return {
                "up": up,
                "status": int(status),
                "error": None,
                "responseMs": elapsed_ms,
                "checkedAt": utc_now(),
            }
    except urllib.error.HTTPError as exc:
        elapsed_ms = int((time.monotonic() - started) * 1000)
        status = int(exc.code)
        return {
            "up": 200 <= status < 400,
            "status": status,
            "error": str(exc.reason),
            "responseMs": elapsed_ms,
            "checkedAt": utc_now(),
        }
    except Exception as exc:  # noqa: BLE001 - any failure means down
        elapsed_ms = int((time.monotonic() - started) * 1000)
        return {
            "up": False,
            "status": None,
            "error": f"{type(exc).__name__}: {exc}",
            "responseMs": elapsed_ms,
            "checkedAt": utc_now(),
        }


def smtp_config() -> dict[str, str] | None:
    values = {key: os.environ.get(key, "").strip() for key in SMTP_ENV_KEYS}
    if all(values.values()):
        return values
    return None


def format_alert(url: str, result: dict[str, Any], recovered: bool) -> tuple[str, str]:
    if recovered:
        subject = f"[UP] {url}"
        status_line = "Site recovered"
    else:
        subject = f"[DOWN] {url}"
        status_line = "Site is down"

    status = result.get("status")
    error = result.get("error")
    body_lines = [
        status_line,
        f"URL: {url}",
        f"Checked at (UTC): {result.get('checkedAt')}",
        f"HTTP status: {status if status is not None else 'n/a'}",
        f"Response time: {result.get('responseMs')} ms",
    ]
    if error:
        body_lines.append(f"Error: {error}")
    return subject, "\n".join(body_lines) + "\n"


def send_alert(url: str, result: dict[str, Any], recovered: bool) -> None:
    subject, body = format_alert(url, result, recovered)
    cfg = smtp_config()
    if cfg is None:
        print("=== ALERT (SMTP not configured; printing instead) ===")
        print(f"Subject: {subject}")
        print(body)
        print("=== END ALERT ===")
        return

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = cfg["ALERT_FROM"]
    msg["To"] = cfg["ALERT_TO"]
    msg.set_content(body)

    context = ssl.create_default_context()
    port = int(cfg["SMTP_PORT"])
    with smtplib.SMTP(cfg["SMTP_HOST"], port, timeout=30) as server:
        server.ehlo()
        server.starttls(context=context)
        server.ehlo()
        server.login(cfg["SMTP_USER"], cfg["SMTP_PASSWORD"])
        server.send_message(msg)
    print(f"Sent alert: {subject}")


def should_alert(previous: dict[str, Any] | None, current: dict[str, Any]) -> bool | None:
    """Return False for down alert, True for recovery, None for no alert.

    The bool is passed as recovered= to send_alert.
    """
    if previous is None:
        # First sight: alert only if already down
        return False if not current["up"] else None
    prev_up = bool(previous.get("up"))
    curr_up = bool(current["up"])
    if prev_up and not curr_up:
        return False  # down alert
    if not prev_up and curr_up:
        return True  # recovery
    return None


def run_once(config: dict[str, Any], state: dict[str, Any]) -> bool:
    """Check all URLs. Returns True if up/status state changed enough to persist."""
    timeout = float(config["timeoutSeconds"])
    changed = False
    for url in config["urls"]:
        result = check_url(url, timeout)
        previous = state.get(url)
        alert = should_alert(previous, result)
        status_label = "UP" if result["up"] else "DOWN"
        detail = result["status"] if result["status"] is not None else result["error"]
        print(f"[{result['checkedAt']}] {status_label} {url} ({detail}, {result['responseMs']} ms)")
        if alert is not None:
            send_alert(url, result, recovered=alert)
        new_entry = {
            "up": result["up"],
            "status": result["status"],
            "checkedAt": result["checkedAt"],
        }
        if previous is None or bool(previous.get("up")) != result["up"] or previous.get("status") != result["status"]:
            state[url] = new_entry
            changed = True
        else:
            state[url] = new_entry
    return changed


def main() -> int:
    parser = argparse.ArgumentParser(description="Uptime monitor")
    parser.add_argument(
        "--once",
        action="store_true",
        help="Check all URLs once and exit",
    )
    args = parser.parse_args()

    config = load_config()
    state = load_state()
    if smtp_config() is None:
        print("SMTP not configured; alerts will be printed to the log")
    else:
        print("SMTP configured; alerts will be emailed")

    if args.once:
        changed = run_once(config, state)
        if changed:
            save_state(state)
        return 0

    run_for = os.environ.get("RUN_FOR_SECONDS", "").strip()
    if not run_for:
        raise SystemExit(
            "Set RUN_FOR_SECONDS for a timed loop, or pass --once for a single check"
        )

    budget = float(run_for)
    interval = float(config["intervalSeconds"])
    deadline = time.monotonic() + budget
    print(f"Monitoring for {budget:.0f}s every {interval:.0f}s")

    while True:
        loop_start = time.monotonic()
        if loop_start >= deadline:
            break
        changed = run_once(config, state)
        if changed:
            save_state(state)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        sleep_for = min(interval, remaining)
        # Account for time spent checking
        elapsed = time.monotonic() - loop_start
        sleep_for = max(0.0, min(sleep_for, interval - elapsed, remaining))
        if sleep_for > 0:
            time.sleep(sleep_for)

    print("Run budget exhausted; exiting cleanly")
    return 0


if __name__ == "__main__":
    sys.exit(main())
