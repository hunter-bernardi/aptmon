"""Config loading. Everything has a sane default so `aptmon scrape` works with no config file."""
from __future__ import annotations

import copy
import os
from pathlib import Path

import yaml

HOME = Path(os.environ.get("APTMON_HOME", Path.home() / ".aptmon"))

DEFAULTS: dict = {
    "db_path": str(HOME / "aptmon.db"),
    "debug_dir": str(HOME / "debug"),
    "bedrooms": [2],                 # which bedroom counts to track
    "timezone": "America/Chicago",   # "7 AM" and "today" are evaluated in this zone
    "run_at_hour": 7,                # daily check time (local)
    "dashboard_url": "",             # link opened when you tap a push alert
    "headless": True,
    "request_timeout": 30,
    "trend": {
        "window_days": 30,           # slope is fit over this lookback
        "flat_band_per_week": 10,    # |slope| below this ($/wk) counts as flat
    },
    "notify": {
        "on_drop": True,
        "on_increase": False,
        "on_new_unit": False,
        "on_off_market": True,       # ping when a tracked unit disappears (leased or pulled)
        "on_relisted": True,         # ping when an off-market unit comes back
        "on_scraper_broken": True,
        "min_drop_dollars": 1,       # ignore sub-$1 noise
        "min_drop_pct": 0.0,
        "channels": {
            "console": {"enabled": True},
            "macos": {"enabled": True},          # native notification banner (macOS only)
            "ntfy": {"enabled": False, "topic": "", "server": "https://ntfy.sh"},
            "email": {
                "enabled": False, "smtp_host": "smtp.gmail.com", "smtp_port": 587,
                "username": "", "password_env": "APTMON_SMTP_PASSWORD",
                "from": "", "to": "",
            },
            "webhook": {"enabled": False, "url": ""},   # Slack/Discord incoming webhook
        },
    },
    "buildings": {
        "wolf_point_east": {
            "name": "Wolf Point East",
            "neighborhood": "River North",
            "scraper": "sightmap",
            "sightmap_embed": "https://sightmap.com/embed/y8pxdmnjw19",
            "fallback": "entrata_floorplans",
            "site": "https://www.wolfpointeast.com",
        },
        "foundry": {
            "name": "The Foundry",
            "neighborhood": "Lincoln Park / Goose Island",
            "scraper": "sightmap",
            "sightmap_embed": "https://sightmap.com/embed/8xvrozk5vjk",
            "fallback": "generic_json",
            "fallback_urls": [
                "https://www.livefoundrychicago.com/floor-plans",
                "https://9126415.onlineleasing.realpage.com/",
            ],
            "site": "https://www.livefoundrychicago.com",
        },
        "lincoln_common": {
            "name": "Lincoln Common",
            "neighborhood": "Lincoln Park",
            "scraper": "rentcafe",
            "site": "https://www.lincolncommonapartments.com",
            "floorplans_path": "/floorplans",
        },
        "the_row": {
            "name": "The Row",
            "neighborhood": "Fulton Market",
            "scraper": "related",
            "site": "https://www.relatedrentals.com",
            "building_path": "/apartment-rentals/chicago/west-loop/the-row-fulton-market",
            "search_url": "https://www.relatedrentals.com/search?city=36&property=47365991",
            "slug": "the-row-fulton-market",
        },
    },
}


def _merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def load(path: str | None = None) -> dict:
    candidates = [path] if path else [os.environ.get("APTMON_CONFIG"), "config.yaml", str(HOME / "config.yaml")]
    user: dict = {}
    for c in candidates:
        if c and Path(c).expanduser().exists():
            user = yaml.safe_load(Path(c).expanduser().read_text()) or {}
            user["_config_path"] = str(Path(c).expanduser().resolve())
            break
    cfg = _merge(DEFAULTS, user)
    _env_overrides(cfg)
    for k in ("db_path", "debug_dir"):
        cfg[k] = str(Path(cfg[k]).expanduser())
    Path(cfg["db_path"]).parent.mkdir(parents=True, exist_ok=True)
    return cfg


def _env_overrides(cfg: dict) -> None:
    """Cloud runs (GitHub Actions) configure secrets through env vars, never through committed files."""
    e = os.environ
    ch = cfg["notify"]["channels"]
    if e.get("APTMON_NTFY_TOPIC"):
        ch["ntfy"].update(enabled=True, topic=e["APTMON_NTFY_TOPIC"])
        if e.get("APTMON_NTFY_SERVER"):
            ch["ntfy"]["server"] = e["APTMON_NTFY_SERVER"]
        if e.get("APTMON_NTFY_TOKEN"):
            ch["ntfy"]["token"] = e["APTMON_NTFY_TOKEN"]
    if e.get("APTMON_WEBHOOK_URL"):
        ch["webhook"].update(enabled=True, url=e["APTMON_WEBHOOK_URL"])
    if e.get("APTMON_DASHBOARD_URL"):
        cfg["dashboard_url"] = e["APTMON_DASHBOARD_URL"]
    if e.get("APTMON_DB_PATH"):
        cfg["db_path"] = e["APTMON_DB_PATH"]
    if e.get("APTMON_TZ"):
        cfg["timezone"] = e["APTMON_TZ"]
    if e.get("APTMON_RUN_AT_HOUR"):
        cfg["run_at_hour"] = int(e["APTMON_RUN_AT_HOUR"])
    if e.get("CI"):
        ch["macos"]["enabled"] = False
