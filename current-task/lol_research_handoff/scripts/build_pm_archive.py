#!/usr/bin/env python3
"""Build Polymarket LoL archive: one JSON per map + CSV index + zip."""
import csv, json, os, re, time, zipfile, urllib.parse, urllib.request, unicodedata
from collections import defaultdict
from datetime import datetime

OUT = "/workspace/pm_lol_archive"
JSON_DIR = os.path.join(OUT, "json")
CACHE_DIR = "/workspace/tl_cache"
os.makedirs(JSON_DIR, exist_ok=True)
os.makedirs(CACHE_DIR, exist_ok=True)
UA = "LolResearcher/1.0 (historical research; polymarket+leaguepedia)"

STOP = re.compile(
    r"\b(esports|esport|e-sports|e sports|gaming|team|club|university|legend|legends)\b",
    re.I,
)
ALIASES = {
    "nongshim red force": "nongshim redforce",
    "nongshim redforce": "nongshim redforce",
    "the ruddy sack": "ruddy",
    "ruddy corporation": "ruddy",
    "otf": "only the family",
    "only the family": "only the family",
    "kabum ilha das lendas": "kabum",
    "orbit anonymo": "anonymo",
    "anonymo": "anonymo",
    "devils one": "devilsone",
    "devilsone": "devilsone",
    "big": "berlin international",
    "berlin international": "berlin international",
    "leo": "lund organization",
    "lund organization": "lund organization",
}


def http_json(url, retries=5, sleep=2.0):
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=90) as r:
                raw = r.read()
            if raw[:1] in (b"{", b"["):
                return json.loads(raw)
            last = raw[:240]
        except Exception as e:
            last = str(e)
        time.sleep(sleep * (i + 1))
    raise RuntimeError(f"failed {url}: {last}")


def strip_acc(s):
    if not s:
        return ""
    trans = str.maketrans({"ø": "o", "Ø": "O", "æ": "ae", "Æ": "AE", "å": "a", "Å": "A", "ß": "ss"})
    s = s.translate(trans)
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c) and unicodedata.category(c)[0] != "C")
    return s


def norm(name):
    if not name:
        return ""
    s = strip_acc(name).lower()
    s = s.replace("&", " and ")
    s = re.sub(r"\(.*?\)", " ", s)
    s = STOP.sub(" ", s)
    s = re.sub(r"[^a-z0-9]+", " ", s)
    s = " ".join(s.split())
    return ALIASES.get(s, s)


def tokens(name):
    t = set(norm(name).split())
    t -= {"the", "of", "and", "vs", "a", "e"}
    return t


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


def team_score(a, b):
    ta, tb = tokens(a), tokens(b)
    na, nb = norm(a), norm(b)
    if not ta or not tb:
        return 0
    if na == nb or ta == tb:
        return 1.0
    inter = ta & tb
    if inter:
        sig = {"academy", "challengers", "youth", "blue", "bee", "fenix"}
        if (ta & sig) != (tb & sig):
            return 0.35 * (len(inter) / max(len(ta), len(tb)))
        return len(inter) / max(len(ta), len(tb))
    if na and nb and (na in nb or nb in na) and min(len(na), len(nb)) >= 3:
        return 0.8
    for x in ta:
        for y in tb:
            if len(x) >= 4 and (x in y or y in x):
                return 0.6
    return 0


def pair_score(a, b, t1, t2):
    return max(team_score(a, t1) + team_score(b, t2), team_score(a, t2) + team_score(b, t1))


def extract_minute_payload(tl):
    if not isinstance(tl, dict):
        return None
    info = tl.get("info", tl)
    interval = info.get("frameInterval") or 60000
    frames = info.get("frames") or []
    minutes, kills = [], []
    for fr in frames:
        ts = fr.get("timestamp") or 0
        minute = int(round(ts / 60000))
        players = []
        pf = fr.get("participantFrames") or {}
        for k, rec in pf.items():
            if not isinstance(rec, dict):
                continue
            pid = rec.get("participantId")
            if pid is None and str(k).isdigit():
                pid = int(k)
            players.append({
                "participantId": pid,
                "totalGold": rec.get("totalGold"),
                "currentGold": rec.get("currentGold"),
                "xp": rec.get("xp"),
                "level": rec.get("level"),
                "minionsKilled": rec.get("minionsKilled"),
                "jungleMinionsKilled": rec.get("jungleMinionsKilled"),
            })
        players.sort(key=lambda p: (p["participantId"] is None, p["participantId"] if isinstance(p["participantId"], int) else 99))
        minutes.append({"minute": minute, "timestamp_ms": ts, "players": players})
        for ev in fr.get("events") or []:
            if ev.get("type") == "CHAMPION_KILL":
                kills.append({
                    "timestamp_ms": ev.get("timestamp"),
                    "killerId": ev.get("killerId"),
                    "victimId": ev.get("victimId"),
                    "assists": ev.get("assistingParticipantIds") or [],
                })
    return {
        "frame_interval_ms": interval,
        "n_frames": len(frames),
        "minutes": minutes,
        "kills": kills,
    }


def mw_page(title):
    params = {
        "action": "query", "titles": title, "prop": "revisions",
        "rvslots": "main", "rvprop": "content", "format": "json",
    }
    url = "https://lol.fandom.com/api.php?" + urllib.parse.urlencode(params)
    data = http_json(url, sleep=2.0)
    pages = data.get("query", {}).get("pages", {})
    for p in pages.values():
        if "missing" in p:
            return None
        revs = p.get("revisions") or []
        if not revs:
            return None
        rev = revs[0]
        return rev.get("slots", {}).get("main", {}).get("*") or rev.get("*")
    return None


def fetch_timeline(rpgid):
    if not rpgid:
        return None, None
    safe = rpgid.replace("/", "_")
    cache = os.path.join(CACHE_DIR, safe + ".json")
    if os.path.exists(cache):
        try:
            obj = json.load(open(cache))
            return obj.get("timeline"), obj.get("meta")
        except Exception:
            pass
    if "_" in rpgid:
        plat, gid = rpgid.split("_", 1)
        spaced = f"{plat} {gid}"
    else:
        spaced = rpgid
    payload, meta = None, None
    for ver in (5, 4):
        for ident in (spaced, rpgid.replace("_", " ")):
            title = f"V{ver} data:{ident}/Timeline"
            try:
                content = mw_page(title)
            except Exception as e:
                print("   tl err", title, e)
                content = None
            time.sleep(1.3)
            if not content:
                continue
            try:
                tl = json.loads(content)
            except Exception:
                continue
            payload = extract_minute_payload(tl)
            if payload and payload["n_frames"]:
                meta = {"wiki_title": title, "riot_version": ver, "riot_platform_game_id": rpgid}
                break
        if payload:
            break
    json.dump({"timeline": payload, "meta": meta}, open(cache, "w"))
    return payload, meta


def maps_for_event(event):
    title = event.get("title") or ""
    m = re.search(r"\(BO(\d)\)", title, re.I)
    bo = int(m.group(1)) if m else 3
    child = {}
    moneyline = None
    for mk in event.get("markets") or []:
        gt = mk.get("groupItemTitle") or ""
        typ = mk.get("sportsMarketType")
        gm = re.match(r"Game\s+(\d+)\s+Winner", gt, re.I)
        if gm:
            child[int(gm.group(1))] = mk
        elif typ == "moneyline" or gt == "Match Winner":
            moneyline = mk
    maps = []
    for n in range(1, bo + 1):
        mk = child.get(n)
        if mk:
            maps.append({
                "map_number": n,
                "condition_id": mk.get("conditionId") or "",
                "market_title": mk.get("groupItemTitle") or "",
                "polymarket_game_id": mk.get("gameId"),
                "condition_source": "game_winner",
            })
        elif n == bo and moneyline:
            maps.append({
                "map_number": n,
                "condition_id": moneyline.get("conditionId") or "",
                "market_title": moneyline.get("groupItemTitle") or "Match Winner",
                "polymarket_game_id": moneyline.get("gameId"),
                "condition_source": "series_moneyline",
            })
        else:
            maps.append({
                "map_number": n,
                "condition_id": "",
                "market_title": "",
                "polymarket_game_id": None,
                "condition_source": "none",
            })
    return maps


def game_num(g):
    gm = g.get("Gamename") or ""
    m = re.search(r"(\d+)", gm)
    if m:
        return int(m.group(1))
    gid = g.get("GameId") or ""
    m = re.search(r"_(\d+)$", gid)
    return int(m.group(1)) if m else 99


def event_dt(event):
    for mk in event.get("markets") or []:
        dt = parse_iso(mk.get("gameStartTime"))
        if dt:
            return dt
    return parse_iso(event.get("endDate")) or parse_iso(event.get("startDate"))

def best_match(event, ms_rows, by_mid_sg):
    parsed = parse_title(event["title"])
    if not parsed:
        return None, []
    a, b, bo, league = parsed
    dt = event_dt(event)
    window = 60 * 60 * 60  # ±60h
    cands = []
    # Prefer grouping scoreboard GameIds without the last _N
    for mid, games in by_mid_sg.items():
        g0 = games[0]
        gd = parse_iso(g0.get("DateTime_UTC") or g0.get("DateTime UTC"))
        if dt and gd and abs((dt - gd).total_seconds()) > window:
            continue
        sc = pair_score(a, b, g0.get("Team1") or "", g0.get("Team2") or "")
        if sc >= 1.15:
            row = {
                "MatchId": mid,
                "Team1": g0.get("Team1"),
                "Team2": g0.get("Team2"),
                "DateTime_UTC": g0.get("DateTime_UTC") or g0.get("DateTime UTC"),
                "OverviewPage": g0.get("OverviewPage"),
                "BestOf": None,
                "Winner": None,
            }
            cands.append((sc, 0 if gd and dt else 1, abs((dt-gd).total_seconds()) if dt and gd else 10**12, row, games))
    # Also match upcoming series from MatchSchedule (no games yet)
    for row in ms_rows:
        gd = parse_iso(row.get("DateTime_UTC"))
        if dt and gd and abs((dt - gd).total_seconds()) > window:
            continue
        sc = pair_score(a, b, row.get("Team1") or "", row.get("Team2") or "")
        if sc >= 1.15:
            games = by_mid_sg.get(row.get("MatchId"), [])
            cands.append((sc, 1 if not games else 0, abs((dt-gd).total_seconds()) if dt and gd else 10**12, row, games))
    if not cands:
        return None, []
    # highest score, prefer series that already have games, then closest date
    best = max(cands, key=lambda x: (x[0], -x[1], -x[2] if False else 0, -x[2]))
    # actually closest date among top score: sort by score desc, has_games, closeness
    best = sorted(cands, key=lambda x: (-x[0], x[1], x[2]))[0]
    row, games = best[3], best[4]
    games = sorted(games, key=game_num)
    seen, ordered = set(), []
    for g in games:
        rid = g.get("RiotPlatformGameId")
        if rid and rid not in seen:
            seen.add(rid)
            ordered.append(g)
    return row, ordered


def main():
    events = json.load(open("/workspace/pm_lol_100_full.json"))
    sg = json.load(open("/workspace/lp_sg_aug.json"))
    ms = []
    if os.path.exists("/workspace/lp_matchschedule_aug.json"):
        ms = json.load(open("/workspace/lp_matchschedule_aug.json"))
    # normalize scoreboard keys (space vs underscore)
    for g in sg:
        if "DateTime UTC" in g and "DateTime_UTC" not in g:
            g["DateTime_UTC"] = g.get("DateTime UTC")
        if not g.get("MatchId") and g.get("GameId"):
            g["MatchId"] = re.sub(r"_\d+$", "", g["GameId"])
    by_mid_sg = defaultdict(list)
    for g in sg:
        key = g.get("MatchId") or re.sub(r"_\d+$", "", g.get("GameId") or "")
        by_mid_sg[key].append(g)
    print("events", len(events), "sg", len(sg), "ms", len(ms), "series", len(by_mid_sg))

    rows = []
    stats = {"with_timeline": 0, "no_match": 0, "no_timeline": 0, "map_not_played": 0}
    for i, ev in enumerate(events, 1):
        slug = ev["slug"]
        url = ev["url"]
        print(f"[{i}/100] {slug}", flush=True)
        maps = maps_for_event(ev)
        ms_row, lp_maps = best_match(ev, ms, by_mid_sg)
        if not ms_row:
            stats["no_match"] += 1
            print("  NO MATCH", ev.get("title"))
        else:
            print("  match", ms_row.get("MatchId"), "games", len(lp_maps))
        lp_by_n = {}
        for g in lp_maps:
            lp_by_n[game_num(g)] = g
        for mp in maps:
            n = mp["map_number"]
            fname = f"{slug}_map{n}.json"
            fpath = os.path.join(JSON_DIR, fname)
            payload = {
                "polymarket_url": url,
                "polymarket_event_id": ev.get("id"),
                "slug": slug,
                "title": ev.get("title"),
                "map_number": n,
                "condition_id": mp["condition_id"],
                "condition_source": mp["condition_source"],
                "market_title": mp["market_title"],
                "closed": ev.get("closed"),
                "startDate": ev.get("startDate"),
                "endDate": ev.get("endDate"),
                "eventMetadata": ev.get("eventMetadata") or {},
                "timeline": None,
                "timeline_meta": None,
                "note": None,
            }
            if ms_row:
                payload["leaguepedia_match"] = {
                    "MatchId": ms_row.get("MatchId"),
                    "Team1": ms_row.get("Team1"),
                    "Team2": ms_row.get("Team2"),
                    "DateTime_UTC": ms_row.get("DateTime_UTC"),
                    "OverviewPage": ms_row.get("OverviewPage"),
                    "BestOf": ms_row.get("BestOf"),
                    "Winner": ms_row.get("Winner"),
                }
            g = lp_by_n.get(n)
            rpgid = None
            if g:
                rpgid = g.get("RiotPlatformGameId")
                payload["leaguepedia_game"] = {
                    "RiotPlatformGameId": rpgid,
                    "Team1": g.get("Team1"),
                    "Team2": g.get("Team2"),
                    "WinTeam": g.get("WinTeam"),
                    "Blue": g.get("Team1"),
                    "Red": g.get("Team2"),
                    "DateTime_UTC": g.get("DateTime_UTC"),
                    "OverviewPage": g.get("OverviewPage"),
                    "MatchId": g.get("MatchId"),
                    "GameId": g.get("GameId"),
                    "Gamename": g.get("Gamename"),
                    "Gamelength": g.get("Gamelength"),
                }
            if rpgid:
                tl, meta = fetch_timeline(rpgid)
                payload["timeline"] = tl
                payload["timeline_meta"] = meta
                if tl:
                    stats["with_timeline"] += 1
                    print(f"   map{n} timeline frames={tl.get('n_frames')}")
                else:
                    stats["no_timeline"] += 1
                    payload["note"] = "leaguepedia match found but timeline missing"
                    print(f"   map{n} NO TIMELINE {rpgid}")
            elif g:
                stats["no_timeline"] += 1
                payload["note"] = "leaguepedia match found but timeline missing"
            elif ms_row:
                stats["map_not_played"] += 1
                payload["note"] = "leaguepedia match found but this map not played / not listed"
            else:
                payload["note"] = "no leaguepedia match for this map"
            with open(fpath, "w") as f:
                json.dump(payload, f, ensure_ascii=False)
            rows.append({
                "polymarket_url": url,
                "map_number": n,
                "condition_id": mp["condition_id"],
                "condition_source": mp["condition_source"],
                "json_file": fname,
                "title": ev.get("title"),
                "has_timeline": bool(payload["timeline"]),
                "n_minutes": (payload["timeline"] or {}).get("n_frames") or 0,
            })
        if i % 5 == 0:
            json.dump({"rows": len(rows), **stats}, open("/workspace/pm_archive_stats.json", "w"))
            print("stats", stats, flush=True)

    csv_path = os.path.join(OUT, "index.csv")
    with open(csv_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["polymarket_url", "map_number", "condition_id",
                                          "condition_source", "json_file", "title",
                                          "has_timeline", "n_minutes"])
        w.writeheader()
        w.writerows(rows)

    zip_path = "/workspace/polymarket_lol_last100_maps.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(csv_path, "index.csv")
        for fn in sorted(os.listdir(JSON_DIR)):
            if fn.endswith(".json"):
                z.write(os.path.join(JSON_DIR, fn), f"json/{fn}")
    print("WROTE", zip_path, "rows", len(rows), "stats", stats)
    json.dump({"zip": zip_path, "rows": len(rows), **stats},
              open("/workspace/pm_archive_stats.json", "w"))


if __name__ == "__main__":
    main()
