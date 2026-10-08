"""Compare live LoL prices: Bovada live map, Pinnacle, Polymarket.

Both books publish live odds with no API key. Pinnacle uses the public arcadia
guest key that pinnacle.com itself ships; Bovada's coupon endpoint is open.
Polymarket comes from gamma bestBid/bestAsk, so `pm` is a real book midpoint.

`bov` is Bovada's in-play moneyline on the map being played. `pin` is the
Pinnacle series line, printed only while they keep it open. `pm_map` is the
Polymarket market named by ARGS map number, `pm_series` is Match Winner.

Invocation:
  make run F=scripts/watch_lol_book_odds.py ARGS="lol-al-ig1-2026-09-12 Anyone 2"
"""

import json
import sys
import time
from typing import cast

import httpx

PIN = "https://guest.api.arcadia.pinnacle.com/0.1"
PIN_HEADERS = {
    "X-API-Key": "CmX2KcMrXuFmNg6YFbmTxE0y9CIrOi0R",
    "Referer": "https://www.pinnacle.com/",
    "User-Agent": "Mozilla/5.0",
}
BOVADA = (
    "https://www.bovada.lv/services/sports/event/coupon/events/A/description"
    "/esports/league-of-legends?marketFilterId=def&liveOnly=true&lang=en"
)
GAMMA = "https://gamma-api.polymarket.com/events"
ESPORTS_SPORT_ID = 12
POLL_SECONDS = 5.0


def american_to_prob(price: float) -> float:
    """Implied probability with vig still in it."""
    return 100 / (price + 100) if price > 0 else -price / (-price + 100)


def devig(home: float, away: float) -> float:
    """Home probability with the book's margin removed proportionally."""
    return home / (home + away)


def pinnacle_series(client: httpx.Client, home_hint: str) -> float | None:
    """Devigged home probability on the Pinnacle series moneyline, if open."""
    matchups = client.get(f"{PIN}/sports/{ESPORTS_SPORT_ID}/matchups", params={"brandId": 0}).json()
    for matchup in matchups:
        parent = matchup.get("parent") or matchup
        names = [p.get("name", "") for p in parent.get("participants", [])]
        if len(names) != 2 or home_hint.lower() not in names[0].lower():
            continue
        markets = client.get(f"{PIN}/matchups/{matchup['id']}/markets/related/straight").json()
        for raw_market in markets:
            if not isinstance(raw_market, dict):
                continue
            market = cast(dict[str, object], raw_market)
            if market.get("key") != "s;0;m" or market.get("isAlternate"):
                continue
            entries = cast(list[dict[str, object]], market.get("prices", []))
            prices = {
                str(entry["designation"]): float(cast(float, entry["price"]))
                for entry in entries
                if "designation" in entry and "price" in entry
            }
            if "home" not in prices or "away" not in prices:
                continue
            return devig(american_to_prob(prices["home"]), american_to_prob(prices["away"]))
    return None


def bovada_live_map(client: httpx.Client, home_hint: str) -> float | None:
    """Devigged home probability on Bovada's in-play map moneyline."""
    for group in client.get(BOVADA).json():
        for event in group.get("events", []):
            if home_hint.lower() not in event["description"].lower():
                continue
            for display in event.get("displayGroups", []):
                for market in display.get("markets", []):
                    if market["description"] != "Moneyline":
                        continue
                    outcomes = market["outcomes"]
                    if len(outcomes) != 2:
                        continue
                    probs = [american_to_prob(float(o["price"]["american"])) for o in outcomes]
                    return devig(*probs)
    return None


def polymarket(
    client: httpx.Client, slug: str, titles: tuple[str, ...], home_hint: str
) -> dict[str, float | None]:
    """Midpoint of bestBid/bestAsk for each named market, oriented to home."""
    found: dict[str, float | None] = dict.fromkeys(titles)
    for event in client.get(GAMMA, params={"slug": slug}).json():
        for market in event.get("markets", []):
            title = market.get("groupItemTitle")
            if title not in found:
                continue
            bid, ask = market.get("bestBid"), market.get("bestAsk")
            if bid is None or ask is None:
                continue
            first_is_home = home_hint.lower() in json.loads(market["outcomes"])[0].lower()
            mid = (float(bid) + float(ask)) / 2
            found[title] = mid if first_is_home else 1 - mid
    return found


def main() -> None:
    slug = sys.argv[1] if len(sys.argv) > 1 else "lol-al-ig1-2026-09-12"
    home_hint = sys.argv[2] if len(sys.argv) > 2 else "Anyone"
    map_number = sys.argv[3] if len(sys.argv) > 3 else "2"
    map_title = f"Game {map_number} Winner"
    titles = (map_title, "Match Winner")
    print(f"home={home_hint} map={map_title} (probabilities are for {home_hint})", flush=True)
    with httpx.Client(timeout=15.0, headers=PIN_HEADERS) as client:
        while True:
            cells = [time.strftime("%H:%M:%S")]
            for label, fetch in (
                ("bov", lambda: {"map": bovada_live_map(client, home_hint)}),
                ("pin", lambda: {"series": pinnacle_series(client, home_hint)}),
                ("pm", lambda: polymarket(client, slug, titles, home_hint)),
            ):
                try:
                    got = fetch()
                except Exception as exc:
                    cells.append(f"{label}=err({type(exc).__name__})")
                    continue
                for key, value in got.items():
                    cells.append(
                        f"{label}.{key}={value:.3f}" if value is not None else f"{label}.{key}=-"
                    )
            print("  ".join(cells), flush=True)
            time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
