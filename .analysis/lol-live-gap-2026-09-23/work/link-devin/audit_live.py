"""Audit every live LoL map under data/trader/grid-*.

Per map:
- match.json: map_number, teams.radiant/dire, market fields, final winner/pnl.
- grid_state.jsonl: last scoreboard -> games[map-1] teams (id, BLUE/RED, won);
  series_table nicks per team_id for roster-side sanity.
- session.jsonl: fills, last non-null market_p_radiant.

Checks: teams.radiant == BLUE team name; teams.dire == RED team name;
yes_is_radiant consistent with outcome_0_name vs radiant name; market_slug
gameN == map_number; GRID winner vs last market_p_radiant and final.winner;
nick prefixes under team ids vs claimed side names.
"""

import json
import re
import sys
from collections import defaultdict
from pathlib import Path

E = Path("/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader")
TRADER = E / "data/trader"
sys.path.insert(0, str(E / "src"))
from shared.utils.team_names import normalize_team_name  # noqa: E402
from shared.constants.lol import LOL_TEAM_ALIASES  # noqa: E402
from lol.lolesports_match import score_lol_team_name  # noqa: E402

GAME_RE = re.compile(r"-game(\d+)\b")
TAG_RE = re.compile(r"^([A-Za-z0-9]{2,7})\s")


def norm(s):
    return normalize_team_name(s or "")


def parse_boards(path):
    """Return (last scoreboard payload dict, list of table payloads)."""
    import gzip
    last_board = None
    tables = []
    opener = open
    if not path.exists() and path.with_suffix(path.suffix + ".gz").exists():
        path = path.with_suffix(path.suffix + ".gz")
        opener = gzip.open
    try:
        with opener(path, "rt") as fh:
            for line in fh:
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                frame_raw = rec.get("frame")
                if not frame_raw:
                    continue
                try:
                    frame = json.loads(frame_raw)
                except json.JSONDecodeError:
                    continue
                service = str(frame.get("service", ""))
                items = frame.get("data") or []
                if not items:
                    continue
                data = items[0].get("data")
                if not data:
                    continue
                if "series_scoreboard" in service:
                    try:
                        last_board = json.loads(data)
                    except json.JSONDecodeError:
                        pass
                elif "series_table" in service:
                    try:
                        tables.append(json.loads(data))
                    except json.JSONDecodeError:
                        pass
    except FileNotFoundError:
        return None, []
    return last_board, tables


def pinned_game(board, map_number):
    games = board.get("games") or []
    idx = map_number - 1
    if idx < 0 or idx >= len(games):
        return None
    return games[idx]


def session_stats(path):
    import gzip
    fills = []
    last_priced = None
    session_end = None
    opener = open
    if not path.exists() and path.with_suffix(path.suffix + ".gz").exists():
        path = path.with_suffix(path.suffix + ".gz")
        opener = gzip.open
    try:
        with opener(path, "rt") as fh:
            for line in fh:
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                kind = rec.get("kind")
                if kind == "fill":
                    fills.append(rec)
                elif kind == "signal":
                    if rec.get("market_p_radiant") is not None:
                        last_priced = rec
                elif kind == "session_end":
                    session_end = rec
    except FileNotFoundError:
        pass
    return fills, last_priced, session_end


def table_roster(tables):
    """team_id -> set of player nicks from the last series_table payload."""
    if not tables:
        return {}
    last = tables[-1]
    roster = defaultdict(set)
    # walk stateGroups -> states -> entityGroups -> rows
    for sg in last.get("stateGroups") or []:
        for st in sg.get("states") or []:
            for eg in st.get("entityGroups") or []:
                if eg.get("name") != "Player":
                    continue
                for ent in eg.get("entities") or []:
                    tid = str(ent.get("teamId") or ent.get("teamID") or "")
                    nick = ent.get("name") or ent.get("nickname") or ""
                    if tid and nick:
                        roster[tid].add(str(nick))
    return roster


results = []
for d in sorted(TRADER.glob("grid-*")):
    mj = d / "match.json"
    if not mj.exists():
        continue
    try:
        meta = json.loads(mj.read_text())
    except Exception:
        continue
    if meta.get("game") != "lol":
        continue
    market = meta.get("market") or {}
    teams = meta.get("teams") or {}
    final = meta.get("final") or {}
    map_number = meta.get("map_number")
    board, tables = parse_boards(d / "grid_state.jsonl")
    fills, last_priced, session_end = session_stats(d / "session.jsonl")

    row = {
        "dir": d.name,
        "map_number": map_number,
        "radiant": teams.get("radiant"),
        "dire": teams.get("dire"),
        "yes_is_radiant": market.get("yes_is_radiant"),
        "outcome_0": market.get("outcome_0_name"),
        "outcome_1": market.get("outcome_1_name"),
        "slug": market.get("market_slug"),
        "event_slug": market.get("event_slug"),
        "condition_id": market.get("condition_id"),
        "grid_series_id": market.get("grid_series_id"),
        "tournament": meta.get("tournament"),
        "winner": final.get("winner"),
        "pnl": (final.get("pnl") or {}).get("realized_pnl_usdc"),
        "fills": len(fills),
        "joined_at": meta.get("joined_at_utc"),
        "issues": [],
    }

    # slug gameN vs map_number
    m = GAME_RE.search(row["slug"] or "")
    row["slug_game"] = int(m.group(1)) if m else None
    if row["slug_game"] is not None and row["slug_game"] != map_number:
        row["issues"].append(f"slug game{row['slug_game']} != map_number {map_number}")

    # series id matches dir name
    if str(row["grid_series_id"]) not in d.name:
        row["issues"].append(f"grid_series_id {row['grid_series_id']} not in dir name")

    if board is None:
        row["issues"].append("no scoreboard frames")
        results.append(row)
        continue

    game = pinned_game(board, map_number)
    if game is None:
        row["issues"].append(f"games[{map_number-1}] missing ({len(board.get('games') or [])} games)")
        results.append(row)
        continue

    gteams = game.get("teams") or []
    by_side = {}
    for t in gteams:
        side = ((t.get("infoText") or {}).get("text") or "").upper()
        by_side.setdefault(side, []).append(t)
    series_names = {t["id"]: t.get("name") for t in (board.get("series", {}).get("teams") or [])}
    row["game_status"] = game.get("status")
    row["grid_blue"] = [series_names.get(t["id"], t["id"]) for t in by_side.get("BLUE", [])]
    row["grid_red"] = [series_names.get(t["id"], t["id"]) for t in by_side.get("RED", [])]
    row["grid_blue_id"] = [t["id"] for t in by_side.get("BLUE", [])]
    row["grid_red_id"] = [t["id"] for t in by_side.get("RED", [])]
    winners = [series_names.get(t["id"], t["id"]) for t in gteams if t.get("won")]
    row["grid_winner"] = winners
    row["active_game_number"] = board.get("activeGameIndex", -9) + 1
    row["series_format"] = board.get("series", {}).get("format")

    # teams.radiant == BLUE name
    blue_names = row["grid_blue"]
    red_names = row["grid_red"]
    if len(blue_names) != 1 or len(red_names) != 1:
        row["issues"].append(f"pinned game sides not 1v1: blue={blue_names} red={red_names}")
    else:
        if score_lol_team_name(row["radiant"], blue_names[0], None) < 0.72:
            row["issues"].append(f"teams.radiant {row['radiant']!r} != GRID BLUE {blue_names[0]!r}")
        if score_lol_team_name(row["dire"], red_names[0], None) < 0.72:
            row["issues"].append(f"teams.dire {row['dire']!r} != GRID RED {red_names[0]!r}")
        # orientation: outcome_0 should name-match radiant iff yes_is_radiant
        s0r = score_lol_team_name(row["outcome_0"], row["radiant"], None)
        s0d = score_lol_team_name(row["outcome_0"], row["dire"], None)
        implied = s0r >= s0d
        if implied != row["yes_is_radiant"]:
            row["issues"].append(
                f"yes_is_radiant={row['yes_is_radiant']} but outcome_0 {row['outcome_0']!r} "
                f"scores radiant={s0r:.2f} dire={s0d:.2f}")
        # GRID winner vs final.winner + last mid
        if winners:
            w = winners[0]
            radiant_won = score_lol_team_name(w, row["radiant"], None) >= 0.72
            dire_won = score_lol_team_name(w, row["dire"], None) >= 0.72
            if not radiant_won and not dire_won:
                row["issues"].append(f"GRID winner {w!r} matches neither side")
            else:
                row["grid_radiant_won"] = radiant_won
                if row["winner"] in ("radiant", "dire"):
                    if (row["winner"] == "radiant") != radiant_won:
                        row["issues"].append(f"final.winner={row['winner']} but GRID winner={w!r}")
                if last_priced is not None:
                    mid = last_priced["market_p_radiant"]
                    row["last_mid"] = mid
                    if radiant_won and mid < 0.3:
                        row["issues"].append(f"GRID says radiant won but last mid={mid}")
                    if not radiant_won and mid > 0.7:
                        row["issues"].append(f"GRID says dire won but last mid={mid}")
        elif game.get("status") == "finished" and not winners:
            row["issues"].append("game finished but no won flag")

    # match-winner markets (no gameN): pinned map must be the format's last map
    if row["slug_game"] is None:
        fmt = row.get("series_format") or ""
        bo = int(fmt.split("-")[-1]) if fmt.startswith("best-of-") else None
        if bo is not None and map_number != bo:
            row["issues"].append(f"match-winner pinned to map {map_number}, format {fmt}")
        if bo is None:
            row["issues"].append(f"match-winner pinned, unknown format {fmt!r}")

    # roster check: nicks under each team id should carry that team's tag
    roster = table_roster(tables)
    row["roster"] = {tid: sorted(n)[:6] for tid, n in roster.items()}
    results.append(row)

# ---- report ----
n = len(results)
with_fills = [r for r in results if r["fills"] > 0]
print(f"lol live maps: {n}, with fills: {len(with_fills)}")
bad = [r for r in results if r["issues"]]
print(f"maps with issues: {len(bad)}")
for r in bad:
    print(f"\n{r['dir']} map={r['map_number']} slug={r['slug']} fills={r['fills']}")
    print(f"  teams: radiant={r['radiant']!r} dire={r['dire']!r} yes_is_radiant={r['yes_is_radiant']}")
    print(f"  outcomes: 0={r['outcome_0']!r} 1={r['outcome_1']!r}")
    print(f"  grid: blue={r.get('grid_blue')} red={r.get('grid_red')} winner={r.get('grid_winner')} "
          f"status={r.get('game_status')} fmt={r.get('series_format')} active_n={r.get('active_game_number')}")
    print(f"  final.winner={r['winner']} last_mid={r.get('last_mid')} pnl={r['pnl']}")
    for i in r["issues"]:
        print(f"  !! {i}")
    print(f"  roster={r.get('roster')}")

# roster side check across all maps
print("\n== roster/team-tag check ==")
for r in results:
    roster = r.get("roster") or {}
    blue_ids = r.get("grid_blue_id") or []
    red_ids = r.get("grid_red_id") or []
    if not blue_ids or not red_ids:
        continue
    blue_nicks = roster.get(blue_ids[0], set())
    red_nicks = roster.get(red_ids[0], set())
    if not blue_nicks and not red_nicks:
        continue
    bt = [TAG_RE.match(n).group(1).upper() for n in blue_nicks if TAG_RE.match(n)]
    rt = [TAG_RE.match(n).group(1).upper() for n in red_nicks if TAG_RE.match(n)]
    if bt and rt and set(bt) == set(rt):
        print(f"  {r['dir']}: same tags on both sides? blue={bt} red={rt}")

Path("/tmp/live_audit_rows.json").write_text(json.dumps(results, indent=1, default=str))
print("\nwrote /tmp/live_audit_rows.json")
