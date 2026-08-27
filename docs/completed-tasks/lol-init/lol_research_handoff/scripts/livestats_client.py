#!/usr/bin/env python3
"""lolesports livestats: discover live games and poll window/details.

Window/details need no API key. getLive / getSchedule / getLeagues need the
public lolesports.com site key (also in unofficial OpenAPI docs).
"""
import json, urllib.request
from datetime import datetime, timedelta, timezone

UA = "LolResearcher/1.0 (historical research)"
LOL_KEY = "0TvQnueqKa5mxJntVWt0w4LpLfEkrV1Ta8rQBb9Z"
ESPORTS = "https://esports-api.lolesports.com/persisted/gw"
FEED = "https://feed.lolesports.com/livestats/v1"


def get(url, headers=None):
    req = urllib.request.Request(
        url,
        headers=headers or {"User-Agent": UA, "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def esports(path):
    return get(
        f"{ESPORTS}/{path}",
        {
            "User-Agent": UA,
            "Accept": "application/json",
            "x-api-key": LOL_KEY,
        },
    )


def get_live(hl="en-US"):
    return esports(f"getLive?hl={hl}")


def get_leagues(hl="en-US"):
    return esports(f"getLeagues?hl={hl}")


def get_schedule(league_id, hl="en-US"):
    return esports(f"getSchedule?hl={hl}&leagueId={league_id}")


def get_event_details(match_id, hl="en-US"):
    return esports(f"getEventDetails?hl={hl}&id={match_id}")


def aligned_starting_time(now=None, back_s=30):
    """startingTime must be 10s-aligned. Window end must be >= 20s old."""
    now = now or datetime.now(timezone.utc)
    t = now.replace(microsecond=0) - timedelta(seconds=back_s)
    t = t.replace(second=t.second - (t.second % 10))
    return t.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def window(game_id, starting_time=None):
    url = f"{FEED}/window/{game_id}"
    if starting_time:
        url += f"?startingTime={starting_time}"
    return get(url)


def details(game_id, starting_time, participant_ids=None):
    url = f"{FEED}/details/{game_id}?startingTime={starting_time}"
    if participant_ids:
        url += f"&participantIds={participant_ids}"
    return get(url)


def latest_window(game_id):
    """Newest allowed slice (~20-30s behind wall clock)."""
    return window(game_id, aligned_starting_time())


if __name__ == "__main__":
    live = get_live()
    events = ((live.get("data") or {}).get("schedule") or {}).get("events") or []
    print("live events", len(events))
    for ev in events:
        match = ev.get("match") or {}
        league = (ev.get("league") or {}).get("name")
        teams = " vs ".join(
            (t.get("code") or t.get("name") or "?") for t in (match.get("teams") or [])
        )
        print(league, ev.get("id"), ev.get("state"), teams)
        for g in match.get("games") or []:
            print(" ", g.get("number"), g.get("id"), g.get("state"))
            if g.get("state") == "inProgress":
                w = latest_window(g["id"])
                fr = (w.get("frames") or [None])[-1]
                if fr:
                    print("   last", fr.get("rfc460Timestamp"),
                          "K", fr["blueTeam"]["totalKills"], fr["redTeam"]["totalKills"],
                          "G", fr["blueTeam"]["totalGold"], fr["redTeam"]["totalGold"])
