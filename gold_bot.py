#!/usr/bin/env python3
"""Posts the daily WARDOGS gold bar rate to a Discord channel via a webhook.

Sources (the newest date wins; Metaforge is preferred on a tie):
  1. https://metaforge.app/api/wardogs/market   (JSON, daily points)
  2. https://wardogshub.gg/gold-market/         (fallback, regex on the page)

The rate turns over at midnight UTC, so run once a day shortly after 00:00 UTC.
Standard library only. Set DISCORD_WEBHOOK_URL in the environment.
"""
import json
import os
import re
import sys
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path

METAFORGE_URL = "https://metaforge.app/api/wardogs/market"
HUB_URL = "https://wardogshub.gg/gold-market/"
STATE = Path(os.environ.get("GOLD_STATE_FILE", Path(__file__).with_name("last_rate.json")))
UA = "wardogs-gold-discord-bot/1.1 (personal use; a few requests per day)"
STALE_DAYS = 2  # alert once if the newest rate is this many days behind today (UTC)

HUB_PATTERN = re.compile(r"gold bar costs \$([\d,]+) on (\d{1,2} \w+ \d{4})")


def http(url, data=None, headers=None):
    req = urllib.request.Request(url, data=data, headers={"User-Agent": UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read().decode("utf-8", "replace")


def ms_to_date(ms):
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).date()


def from_metaforge():
    data = json.loads(http(METAFORGE_URL))
    pts = data["points"]
    last = pts[-1]
    prev = (ms_to_date(pts[-2]["t"]), int(pts[-2]["price"])) if len(pts) > 1 else None
    prices = [p["price"] for p in pts[-30:]]
    st = data.get("stats", {})
    ch = st.get("changes", {})
    stats = {
        "7d": ch.get("7d"),
        "30d": ch.get("30d"),
        "vs_avg": last["price"] / (sum(prices) / len(prices)) - 1,  # same as Metaforge's "vs 30-day average"
        "low": st.get("min"),
        "high": st.get("max"),
    }
    return {"day": ms_to_date(last["t"]), "price": int(last["price"]), "prev": prev, "stats": stats, "source": "Metaforge"}


def from_hub():
    m = HUB_PATTERN.search(http(HUB_URL))
    if not m:
        raise ValueError("rate sentence not found on wardogshub.gg")
    return {
        "day": datetime.strptime(m.group(2), "%d %B %Y").date(),
        "price": int(m.group(1).replace(",", "")),
        "prev": None,
        "stats": None,
        "source": "WARDOGS Hub",
    }


def parse_day(s):
    for parse in (date.fromisoformat, lambda x: datetime.strptime(x, "%d %B %Y").date()):
        try:
            return parse(s)
        except (TypeError, ValueError):
            pass
    return None


def load_state():
    try:
        return json.loads(STATE.read_text())
    except (OSError, ValueError):
        return {}


def fmt_day(d):
    return f"{d.day} {d:%b %Y}"


def money(n):
    return f"{'+' if n >= 0 else '-'}${abs(n):,}"


GREEN, RED, GREY = 0x2ECC71, 0xE74C3C, 0x95A5A6  # green = cheaper gold (good for buyers)


def pct(x):
    return "n/a" if x is None else f"{x:+.1%}"


def build_embed(best, state):
    prev = best["prev"]
    if prev is None and parse_day(state.get("day")):
        prev = (parse_day(state["day"]), state["price"])
    desc, color = f"**${best['price']:,}**", GREY
    if prev and prev[1]:
        diff = best["price"] - prev[1]
        desc += f"\n{money(diff)} ({diff / prev[1]:+.1%}) vs {fmt_day(prev[0])}"
        color = GREEN if diff < 0 else RED if diff > 0 else GREY
    embed = {
        "title": f"\U0001F947 Gold bar, {fmt_day(best['day'])}",
        "description": desc,
        "color": color,
        "timestamp": datetime(best["day"].year, best["day"].month, best["day"].day, tzinfo=timezone.utc).isoformat(),
        "footer": {"text": f"Source: {best['source']}"},
    }
    st = best["stats"]
    if st:
        fields = [
            ("7 days", pct(st["7d"])),
            ("30 days", pct(st["30d"])),
            ("vs 30-day avg", pct(st["vs_avg"])),
        ]
        if st["low"]:
            fields.append(("All-time low", f"${st['low']:,}"))
        if st["high"]:
            fields.append(("All-time high", f"${st['high']:,}"))
        embed["fields"] = [{"name": n, "value": v, "inline": True} for n, v in fields]
    return embed


def pick_best(fetchers):
    results = []
    for fn in fetchers:
        try:
            results.append(fn())
        except Exception as e:  # one source failing is fine
            print(f"{fn.__name__} failed: {e}", file=sys.stderr)
    if not results:
        sys.exit("Both sources failed - nothing to post.")
    return max(results, key=lambda r: r["day"])  # first wins ties = Metaforge


def post(webhook, text=None, embed=None):
    payload = {"content": text} if text else {"embeds": [embed]}
    http(webhook, json.dumps(payload).encode(), {"Content-Type": "application/json"})


def main():
    webhook = os.environ["DISCORD_WEBHOOK_URL"]
    best = pick_best([from_metaforge, from_hub])
    state = load_state()
    today = datetime.now(timezone.utc).date()

    if parse_day(state.get("day")) != best["day"]:
        post(webhook, embed=build_embed(best, state))
        STATE.write_text(json.dumps({"day": best["day"].isoformat(), "price": best["price"], "stale_for": None}))
    elif (today - best["day"]).days >= STALE_DAYS and state.get("stale_for") != best["day"].isoformat():
        post(webhook, f"⚠️ No new gold rate since {fmt_day(best['day'])} "
                      f"(${best['price']:,}). Both sources look stale - may be a game-side outage.")
        state["stale_for"] = best["day"].isoformat()
        STATE.write_text(json.dumps(state))


if __name__ == "__main__":
    main()
