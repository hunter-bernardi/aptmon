"""One monitoring pass: scrape every building, diff against history, emit events, notify."""
from __future__ import annotations

import logging

from .db import DB
from .fetch import Fetcher
from .models import Unit, utcnow
from .notify import Notifier
from .scrapers import scrape_building

log = logging.getLogger("aptmon.monitor")


def _money(n):
    return "?" if n is None else ("-" if n < 0 else "") + f"${abs(n):,.0f}"


def apply_results(db: DB, cfg: dict, run_id: int, key: str, units: list[Unit], scraper: str,
                  errors: list[str], ts: str) -> list[int]:
    """Persist one building's scrape and return ids of events created."""
    bcfg = cfg["buildings"][key]
    name = bcfg.get("name", key)
    wanted = [float(b) for b in cfg.get("bedrooms", [2])]
    ncfg = cfg["notify"]
    event_ids: list[int] = []

    evidence = len(units)                               # listings of any size (proves the page parsed)
    tracked = [u for u in units if u.price and u.beds in wanted]
    # beds unknown but everything else fine -> keep (better a false positive than a silent miss)
    # (studios/1BRs are < 900 sf in all four buildings, so small unknowns are dropped)
    tracked += [u for u in units if u.price and u.beds is None and (u.sqft is None or u.sqft >= 900)
                and not any(t.unit == u.unit for t in tracked)]
    ok = evidence > 0

    prev = db.last_status(key)
    db.record_status(run_id, key, ok, len(tracked), evidence, scraper, "; ".join(errors) or None, ts)

    if not ok:
        # Don't delist anything on a failed scrape. Alert once per outage, not every run.
        if ncfg.get("on_scraper_broken") and (prev is None or prev["ok"]):
            event_ids.append(db.add_event(ts, None, key, None, "broken", None, None,
                                          f"{name}: scraper returned nothing ({'; '.join(errors)[:300]})"))
        return event_ids
    if prev is not None and not prev["ok"]:
        event_ids.append(db.add_event(ts, None, key, None, "recovered", None, None, f"{name}: scraper recovered"))

    seen_keys = set()
    for u in tracked:
        # Related: a listing first stored under its listing id, now resolved to a residence #
        lid = (u.extra or {}).get("listing_id")
        if lid and not u.unit.startswith("L") and db.get_unit(f"{key}:L{lid}"):
            db.rename_key(f"{key}:L{lid}", u.key(), u.unit)

        k = u.key()
        seen_keys.add(k)
        existing = db.get_unit(k)
        last = db.last_price(k)
        db.upsert_unit(u, ts)
        db.add_price(k, ts, u.price, run_id)
        label = f"{name} #{u.unit}"
        if existing is None:
            if ncfg.get("on_new_unit"):
                event_ids.append(db.add_event(ts, k, key, u.unit, "new", None, u.price,
                                              f"New 2BR listed: {label} at {_money(u.price)}"
                                              + (f" ({u.sqft:,} sf)" if u.sqft else "")))
            else:
                db.add_event(ts, k, key, u.unit, "new", None, u.price, f"New: {label} at {_money(u.price)}")
            continue
        if existing["status"] == "off_market":
            eid = db.add_event(ts, k, key, u.unit, "relisted", last, u.price,
                               f"Back on market: {label} at {_money(u.price)} (was {_money(last)})")
            if ncfg.get("on_relisted"):
                event_ids.append(eid)
        if last is None or u.price == last:
            continue
        diff = u.price - last
        pct = diff / last
        if diff < 0:
            big_enough = -diff >= ncfg.get("min_drop_dollars", 1) and -pct * 100 >= ncfg.get("min_drop_pct", 0)
            msg = (f"Price drop: {label} {_money(last)} -> {_money(u.price)} "
                   f"({_money(diff)}, {pct:+.1%})" + (f" · {u.plan}" if u.plan else ""))
            eid = db.add_event(ts, k, key, u.unit, "drop", last, u.price, msg)
            if ncfg.get("on_drop") and big_enough:
                event_ids.append(eid)
        else:
            msg = f"Price increase: {label} {_money(last)} -> {_money(u.price)} (+{_money(diff)}, {pct:+.1%})"
            eid = db.add_event(ts, k, key, u.unit, "increase", last, u.price, msg)
            if ncfg.get("on_increase"):
                event_ids.append(eid)

    for k in db.active_keys(key) - seen_keys:
        db.mark_off_market(k, ts)
        u = db.get_unit(k)
        eid = db.add_event(ts, k, key, u["unit"], "off_market", db.last_price(k), None,
                           f"Off market (leased or pulled): {name} #{u['unit']}")
        if ncfg.get("on_off_market"):
            event_ids.append(eid)
    return event_ids


def run(cfg: dict, only: list[str] | None = None, debug: bool = False, notify: bool = True) -> dict:
    db = DB(cfg["db_path"])
    ts = utcnow()
    run_id = db.start_run(ts)
    fetcher = Fetcher(cfg, debug=debug)
    summary: dict = {}
    to_notify: list[int] = []
    wanted = [float(b) for b in cfg.get("bedrooms", [2])]
    try:
        for key, bcfg in cfg["buildings"].items():
            if only and key not in only:
                continue
            if bcfg.get("enabled") is False:
                continue
            log.info("scraping %s", key)
            units, scraper, errors = scrape_building(
                key, bcfg, fetcher, wanted_beds=wanted, known_units=db.listing_map(key))
            to_notify += apply_results(db, cfg, run_id, key, units, scraper, errors, ts)
            st = db.last_status(key)
            summary[key] = {"ok": bool(st["ok"]), "tracked": st["n_units"], "seen": st["n_seen"],
                            "scraper": scraper, "errors": errors}
    finally:
        fetcher.close()
        db.finish_run(run_id, utcnow(), summary)

    if notify and to_notify:
        events = [db.one("SELECT * FROM events WHERE id=?", i) for i in to_notify]
        sent = Notifier(cfg).send_events(events)
        db.mark_notified([e["id"] for e in events if sent])
    summary["_events"] = len(to_notify)
    return summary
