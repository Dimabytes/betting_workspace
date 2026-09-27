"""Replay GRID archives through the real GridFrameReducer and test side-row rules on emitted ticks.

Run from the esports-trader root: PYTHONPATH=src uv run python <this file>

A: each side needs exactly 5 rows (Oddin-style), no portrait filter.
B: a row counts only with a portrait (iconUrl); each side needs exactly 5.
C: portrait filter only when a side has >5 rows; each side needs exactly 5.

A tick "drops" under a rule when its table fails the rule. Window by snapshot second:
pre < -60, model -60..540, late > 540. Shape = (radiant rows, radiant portraits,
dire rows, dire portraits). After fix 10 (rule C in the reducer) rule C must drop 0.
"""

import gzip
import json
from collections import Counter, defaultdict
from pathlib import Path

import trader.grid_feed as gf
import trader.grid_widgets as gw
from trader.game_profile import GAME_PROFILES

portrait: dict[int, bool] = {}
orig_player = gw._player_from_row


def player_with_portrait(row):
    player = orig_player(row)
    if player is not None:
        portrait[id(player)] = bool(row["entity"].get("iconUrl"))
    return player


gw._player_from_row = player_with_portrait

captured = []
orig_live = gf._live_snapshot


def live_capture(*args):
    snap = orig_live(*args)
    board, table, sides = args[0], args[1], args[2]
    captured.append((snap.second, table, sides))
    return snap


gf._live_snapshot = live_capture


def side_ok(players, rule):
    if rule == "B" or (rule == "C" and len(players) > 5):
        players = [p for p in players if portrait[id(p)]]
    return len(players) == 5


report = defaultdict(Counter)
per_archive = defaultdict(lambda: defaultdict(Counter))
for d in sorted(Path("data/trader").glob("grid-*")):
    meta_path, archive_path = d / "match.json", d / "grid_state.jsonl.gz"
    if not meta_path.exists() or not archive_path.exists():
        continue
    meta = json.loads(meta_path.read_text())
    game = meta.get("game") or "dota"
    market = meta["market"]
    reducer = gf.GridFrameReducer(
        meta["map_number"], market["outcome_0_name"], market["outcome_1_name"], GAME_PROFILES[game]
    )
    captured.clear()
    portrait.clear()
    with gzip.open(archive_path, "rt") as f:
        records = [json.loads(line) for line in f]
    try:
        list(gf.replay_grid_records(records, reducer))
    except (gf.GridOrientationError, ValueError) as exc:
        report[game][("replay_failed", d.name, type(exc).__name__)] += 1
        continue
    report[game]["archives"] += 1
    for second, table, sides in captured:
        window = "pre" if second < -60 else ("model" if second <= 540 else "late")
        radiant = [p for p in table.players if p.team_id == sides.radiant_id]
        dire = [p for p in table.players if p.team_id == sides.dire_id]
        shape = (
            len(radiant),
            sum(portrait[id(p)] for p in radiant),
            len(dire),
            sum(portrait[id(p)] for p in dire),
        )
        report[game][("ticks", window)] += 1
        for rule in "ABC":
            if not (side_ok(radiant, rule) and side_ok(dire, rule)):
                report[game][(rule, window)] += 1
                per_archive[game][d.name][(rule, window)] += 1
                per_archive[game][d.name][("shape", rule, shape)] += 1

for game, counts in report.items():
    print(f"== {game}")
    for key in sorted(counts, key=str):
        print("  ", key, counts[key])
    for rule in "ABC":
        in_model = {a: s for a, s in per_archive[game].items() if s[(rule, "model")] > 0}
        anywhere = {a: s for a, s in per_archive[game].items() if any(k[0] == rule for k in s)}
        print(f"  rule {rule}: archives with a dropped tick in model window {len(in_model)}, anywhere {len(anywhere)}")
        for a, s in list(in_model.items())[:12]:
            shapes = {k[2]: v for k, v in s.items() if k[0] == "shape" and k[1] == rule}
            print("     ", a, "model", s[(rule, "model")], "pre", s[(rule, "pre")], "late", s[(rule, "late")], shapes)
    shapes = Counter()
    degenerate = {}
    for a, s in per_archive[game].items():
        ticks = 0
        for k, v in s.items():
            if k[0] == "shape" and k[1] == "C":
                shapes[k[2]] += v
                if k[2][0] == 0 or k[2][2] == 0:
                    ticks += v
        if ticks:
            degenerate[a] = ticks
    print("  rule C drop shapes", shapes.most_common(12))
    print("  archives with degenerate ticks", len(degenerate), "ticks", sum(degenerate.values()))
