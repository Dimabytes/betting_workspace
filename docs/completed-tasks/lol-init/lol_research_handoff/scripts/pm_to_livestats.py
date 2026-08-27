#!/usr/bin/env python3
"""Match a Polymarket LoL event to lolesports livestats gameId.

No shared id. Use league + teams + date, then map N -> games[n].id.
Team scoring is the same idea as build_pm_archive.py (normalize + token overlap).
"""
import json, re, unicodedata, urllib.parse, urllib.request
from datetime import datetime, timezone

UA = "LolResearcher/1.0 (historical research)"
LOL_KEY = "0TvQnueqKa5mxJntVWt0w4LpLfEkrV1Ta8rQBb9Z"
ESPORTS = "https://esports-api.lolesports.com/persisted/gw"
GAMMA = "https://gamma-api.polymarket.com"

# Polymarket eventMetadata.league -> getLeagues slug
LEAGUE_SLUG = {
    "lec": "lec",
    "lck": "lck",
    "lpl": "lpl",
    "lcs": "lcs",
    "lcp": "lcp",
    "cblol": "cblol-brazil",
    "lck challengers league": "lck_challengers_league",
    "north american challengers league": "nacl",
    "nacl": "nacl",
    "circuito desafiante": "cd",
    "lfl": "lfl",
    "la ligue française": "lfl",
    "nlc": "nlc",
    "tcl": "turkiye-sampiyonluk-ligi",
    "prime league 1st division": "primeleague",
    "prime league": "primeleague",
    "lplol": "liga_portuguesa",
    "liga portuguesa": "liga_portuguesa",
    "les": "les",
    "lit": "lit",
    "rift legends": "rift_legends",
    "road of legends": "roadoflegends",
    "hitpoint masters": "hitpoint_masters",
    "ebl": "esports_balkan_league",
    "esports balkan league": "esports_balkan_league",
    "arabian league": "arabian_league",
    "lrs": "south_regional_league",
    "lrn": "north_regional_league",
    "kespa cup": "kespa_cup",
    "emea masters": "emea_masters",
}

# not on getLeagues (no livestats): Equal eSports Cup, Nexus League, ...

STOP = re.compile(
    r"\b(esports|esport|e-sports|e sports|gaming|team|club|university|legend|legends)\b",
    re.I,
)


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
        {"User-Agent": UA, "Accept": "application/json", "x-api-key": LOL_KEY},
    )


def strip_acc(s):
    if not s:
        return ""
    trans = str.maketrans({"ø": "o", "Ø": "O", "æ": "ae", "Æ": "AE", "å": "a", "Å": "A"})
    s = s.translate(trans)
    s = unicodedata.normalize("NFKD", s)
    return "".join(c for c in s if not unicodedata.combining(c))


def norm(name):
    s = strip_acc(name).lower()
    s = re.sub(r"\(.*?\)", " ", s)
    s = STOP.sub(" ", s)
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return " ".join(s.split())


def tokens(name):
    return set(norm(name).split()) - {"the", "of", "and", "vs", "a", "e"}


def team_score(a, b):
    ta, tb = tokens(a), tokens(b)
    na, nb = norm(a), norm(b)
    if not ta or not tb:
        return 0
    if na == nb or ta == tb:
        return 1.0
    inter = ta & tb
    if inter:
        return len(inter) / max(len(ta), len(tb))
    if na and nb and (na in nb or nb in na) and min(len(na), len(nb)) >= 3:
        return 0.8
    return 0


def pair_score(a, b, t1, t2):
    return max(team_score(a, t1) + team_score(b, t2), team_score(a, t2) + team_score(b, t1))


def parse_title(title):
    title = strip_acc(title)
    m = re.match(r"^LoL:\s*(.+?)\s+vs\.?\s+(.+?)\s+\((BO\d)\)\s*-\s*(.+)$", title, re.I)
    if not m:
        m = re.match(r"^LoL:\s*(.+?)\s+vs\.?\s+(.+?)\s+\((BO\d)\)", title, re.I)
        if not m:
            return None
        return m.group(1).strip(), m.group(2).strip(), m.group(3).upper(), ""
    return m.group(1).strip(), m.group(2).strip(), m.group(3).upper(), m.group(4).strip()


def parse_iso(s):
    if not s:
        return None
    s = str(s).replace("Z", "+00:00")
    if " " in s and "T" not in s:
        s = s.replace(" ", "T") + "+00:00"
    try:
        return datetime.fromisoformat(s)
    except Exception:
        return None


def leagues_by_slug():
    rows = ((esports("getLeagues?hl=en-US").get("data") or {}).get("leagues") or [])
    return {r.get("slug"): r for r in rows}


def pm_event(slug):
    return get(f"{GAMMA}/events/slug/{slug}")


def live_events():
    return ((esports("getLive?hl=en-US").get("data") or {}).get("schedule") or {}).get("events") or []


def schedule_events(league_id):
    return ((esports(f"getSchedule?hl=en-US&leagueId={league_id}").get("data") or {}).get("schedule") or {}).get("events") or []


def match_event(pm, league_catalog=None):
    parsed = parse_title(pm.get("title") or "")
    if not parsed:
        return None
    a, b, bo, _ = parsed
    meta = pm.get("eventMetadata") or {}
    slug = LEAGUE_SLUG.get((meta.get("league") or "").strip().lower())
    dt = parse_iso(pm.get("startDate") or pm.get("endDate"))

    # 1) live first
    cands = []
    for ev in live_events():
        teams = (ev.get("match") or {}).get("teams") or []
        if len(teams) < 2:
            continue
        sc = pair_score(a, b, teams[0].get("name") or teams[0].get("code") or "",
                        teams[1].get("name") or teams[1].get("code") or "")
        if sc >= 1.15:
            cands.append((sc, 0, ev))
    if cands:
        return sorted(cands, key=lambda x: -x[0])[0][2]

    # 2) schedule of mapped league
    if slug:
        catalog = league_catalog or leagues_by_slug()
        lg = catalog.get(slug)
        if lg:
            for ev in schedule_events(lg["id"]):
                teams = (ev.get("match") or {}).get("teams") or []
                if len(teams) < 2:
                    continue
                sc = pair_score(a, b, teams[0].get("name") or "", teams[1].get("name") or "")
                if sc < 1.15:
                    continue
                evdt = parse_iso(ev.get("startTime") or ev.get("blockName"))
                delta = abs((dt - evdt).total_seconds()) if dt and evdt else 10**12
                cands.append((sc, delta, ev))
    if not cands:
        return None
    return sorted(cands, key=lambda x: (-x[0], x[1]))[0][2]


def game_id_for_map(lol_event, map_number):
    games = (lol_event.get("match") or {}).get("games") or []
    for g in games:
        if int(g.get("number") or 0) == int(map_number):
            return g.get("id"), g.get("state")
    return None, None


if __name__ == "__main__":
    import sys
    slug = sys.argv[1] if len(sys.argv) > 1 else "lol-th-gx-2026-08-23"
    map_n = int(sys.argv[2]) if len(sys.argv) > 2 else 2
    pm = pm_event(slug)
    print("pm", pm.get("title"), pm.get("score"), (pm.get("eventMetadata") or {}).get("league"))
    ev = match_event(pm)
    if not ev:
        print("NO LIVESTATS MATCH")
        raise SystemExit(1)
    gid, state = game_id_for_map(ev, map_n)
    print("lol event", ev.get("id"), (ev.get("league") or {}).get("name"), ev.get("state"))
    print("map", map_n, "gameId", gid, "state", state)
