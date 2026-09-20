"""Notification channels: console, macOS banner, ntfy (phone push), email, Slack/Discord webhook."""
from __future__ import annotations

import logging
import os
import platform
import shutil
import smtplib
import subprocess
from email.message import EmailMessage

import requests

log = logging.getLogger("aptmon.notify")

TITLES = {"drop": "📉 Price drop", "increase": "📈 Price increase", "new": "🆕 New listing",
          "off_market": "🚪 Off market", "broken": "⚠️ Scraper broken",
          "recovered": "✅ Scraper recovered", "relisted": "↩️ Relisted"}


class Notifier:
    def __init__(self, cfg: dict):
        self.ch = cfg["notify"]["channels"]
        self.url = cfg.get("dashboard_url") or "http://127.0.0.1:8050"

    def send_events(self, events: list[dict]) -> bool:
        if not events:
            return True
        drops = [e for e in events if e["kind"] == "drop"]
        if len(events) == 1:
            title = TITLES.get(events[0]["kind"], "Apartment monitor")
        else:
            title = f"{len(drops)} price drop(s)" if drops else f"{len(events)} apartment alerts"
        body = "\n".join(e["message"] for e in events)
        return self.send(title, body)

    def send(self, title: str, body: str) -> bool:
        ok = False
        for name, fn in (("console", self._console), ("macos", self._macos), ("ntfy", self._ntfy),
                         ("email", self._email), ("webhook", self._webhook)):
            c = self.ch.get(name, {})
            if not c.get("enabled"):
                continue
            try:
                fn(c, title, body, self.url)
                ok = True
            except Exception as e:
                log.warning("notify via %s failed: %s", name, e)
        return ok

    @staticmethod
    def _console(c, title, body, url=None):
        print(f"\n=== {title} ===\n{body}\n")

    @staticmethod
    def _macos(c, title, body, url=None):
        if platform.system() != "Darwin":
            return
        esc = lambda s: s.replace("\\", "\\\\").replace('"', '\\"')
        if shutil.which("terminal-notifier"):
            subprocess.run(["terminal-notifier", "-title", title, "-message", body[:500], "-sound", "default",
                            "-open", url], check=False, timeout=10)
        else:
            subprocess.run(["osascript", "-e",
                            f'display notification "{esc(body[:500])}" with title "{esc(title)}" sound name "Glass"'],
                           check=False, timeout=10)

    @staticmethod
    def _ntfy(c, title, body, url=None):
        if not c.get("topic"):
            raise ValueError("ntfy.topic is empty")
        # ntfy headers must be latin-1; the emoji goes in Tags instead
        clean = title.encode("ascii", "ignore").decode().strip()
        headers = {"Title": clean or "Apartment monitor", "Tags": "house,chart_with_downwards_trend",
                   "Priority": "high" if "drop" in title.lower() else "default"}
        if url and url.startswith("http") and "127.0.0.1" not in url:
            headers["Click"] = url                       # tap the notification -> open the dashboard
        if c.get("token"):
            headers["Authorization"] = f"Bearer {c['token']}"
        r = requests.post(f"{c.get('server', 'https://ntfy.sh').rstrip('/')}/{c['topic']}",
                          data=body.encode(), timeout=15, headers=headers)
        r.raise_for_status()

    @staticmethod
    def _email(c, title, body, url=None):
        pw = os.environ.get(c.get("password_env", "APTMON_SMTP_PASSWORD"), "")
        msg = EmailMessage()
        msg["Subject"], msg["From"], msg["To"] = title, c.get("from") or c["username"], c["to"]
        msg.set_content(body)
        with smtplib.SMTP(c["smtp_host"], int(c.get("smtp_port", 587)), timeout=20) as s:
            s.starttls()
            if c.get("username"):
                s.login(c["username"], pw)
            s.send_message(msg)

    @staticmethod
    def _webhook(c, title, body, url=None):
        url = c["url"]
        payload = {"content": f"**{title}**\n{body}"} if "discord" in url else {"text": f"*{title}*\n{body}"}
        requests.post(url, json=payload, timeout=15).raise_for_status()
