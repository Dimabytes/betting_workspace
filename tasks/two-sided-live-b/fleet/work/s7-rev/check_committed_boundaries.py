import ast
import asyncio
import copy
import logging
import re
import subprocess
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from itertools import product
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Literal


REPO = "/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader"
BASE = "f605a6b9"
HEAD = "629657b0"


def read_source(commit, path):
    return subprocess.check_output(
        ["git", "show", f"{commit}:{path}"], cwd=REPO, text=True
    )


def load_nodes(commit, path, requested, namespace):
    tree = ast.parse(read_source(commit, path))
    nodes = [node for node in tree.body if getattr(node, "name", None) in requested]
    assert len(nodes) == len(requested)
    module = ast.Module(body=nodes, type_ignores=[])
    exec(compile(module, f"{commit}:{path}", "exec"), namespace)


class TradingDisabled(Exception):
    pass


@dataclass(frozen=True)
class ClipTier:
    clip_usdc: float
    names: tuple[str, ...]


@dataclass(frozen=True)
class ClipTable:
    default_usdc: float
    tiers: tuple[ClipTier, ...]


class GameProfile:
    pass


class FreshSidecar:
    pass


namespace = {
    "Mapping": Mapping,
    "ClipTier": ClipTier,
    "ClipTable": ClipTable,
    "GameProfile": GameProfile,
    "FreshSidecar": FreshSidecar,
    "DotaStrategy": Literal["follow300", "two_sided"],
    "ExecutionMode": Literal["paper", "live"],
    "TradingDisabled": TradingDisabled,
    "cast": lambda annotation, value: value,
    "LEAGUE_SUFFIX_RE": re.compile(r"\(BO\s*\d+\)\s*-\s*(.+)$", re.IGNORECASE),
    "logger": logging.getLogger("s7-review-isolated"),
}
namespace["logger"].addHandler(logging.NullHandler())
load_nodes(
    HEAD,
    "src/shared/utils/lol_leagues.py",
    {"parse_event_league", "_clean_league"},
    namespace,
)
load_nodes(HEAD, "src/trader/discovery.py", {"league_matches"}, namespace)
load_nodes(HEAD, "src/trader/wallet_host.py", {"select_title_whitelist"}, namespace)
load_nodes(
    HEAD,
    "src/trader/trading_mode.py",
    {"read_dota_strategy", "require_two_sided_start"},
    namespace,
)


def compile_filter(commit, names, class_name, space):
    tree = ast.parse(read_source(commit, "src/trader/discovery.py"))
    original = next(node for node in tree.body if getattr(node, "name", None) == "MarketDiscovery")
    methods = [copy.deepcopy(node) for node in original.body if getattr(node, "name", None) in names]
    assert len(methods) == len(names)
    cls = ast.ClassDef(name=class_name, bases=[], keywords=[], body=methods, decorator_list=[])
    module = ast.fix_missing_locations(ast.Module(body=[cls], type_ignores=[]))
    exec(compile(module, f"{commit}:discovery-filter", "exec"), space)


base_space = dict(namespace)
load_nodes(BASE, "src/trader/discovery.py", {"is_league_blacklisted"}, base_space)
compile_filter(BASE, {"_drop_blacklisted"}, "BaseFilter", base_space)
compile_filter(HEAD, {"_title_skip_reason", "_drop_skipped_titles"}, "HeadFilter", namespace)

dota = SimpleNamespace(game="dota", primary=SimpleNamespace(profile_name="dota-map"), uses_steam=True)
lol = SimpleNamespace(game="lol", primary=SimpleNamespace(profile_name="lol-map"), uses_steam=False)
parser = namespace["read_dota_strategy"]
for raw, expected in [(None, "follow300"), ("", "follow300"), ("  ", "follow300"),
                      ("two_sided", "two_sided"), (" two_sided ", "two_sided")]:
    namespace["env_value"] = lambda name, raw=raw: raw
    assert parser() == expected
for raw in ["follow300", "two-sided", "TWO_SIDED", "1"]:
    namespace["env_value"] = lambda name, raw=raw: raw
    try:
        parser()
    except TradingDisabled:
        continue
    raise AssertionError(raw)
print("Strategy parser: 5 accepted/default and 4 rejected cases passed")

require = namespace["require_two_sided_start"]
startup_cases = 0
for mode, games, signature, creds in product(
    ("live", "paper"), ((dota,), (lol,), (dota, lol)), (0, 1, 2, 3), (True, False)
):
    accepted = mode == "live" and games == (dota,) and signature == 3 and creds
    try:
        require(mode=mode, games=games, signature_type=signature, has_builder_creds=creds)
    except TradingDisabled:
        assert not accepted
    else:
        assert accepted
    startup_cases += 1
print(f"Two-sided startup predicate: {startup_cases} combinations passed")

select = namespace["select_title_whitelist"]
assert select("follow300", {}, dota) == ()
assert select("follow300", {}, lol) == ()
try:
    select("two_sided", {"dota-map": ClipTable(5.0, ())}, dota)
except TradingDisabled:
    pass
else:
    raise AssertionError("Empty two-sided whitelist accepted")
print("Follow300 empty whitelist and two-sided empty-whitelist refusal passed")

titles = [
    None,
    "Winline showmatch",
    "Dota 2: Winline Team vs Team B (BO3) - EPL",
    "Dota 2: A vs B (BO3) - Winline Star Series",
    "Dota 2: A vs B (BO3) - BetBoom Streamers Battle Playoffs",
    "Dota 2: A vs B (BO3) - BLAST Slam IV Group Stage",
    "Dota 2: A vs B (BO3) - PARI Universe",
]
for blacklist in ((), ("Streamers", "Winline")):
    old = base_space["BaseFilter"]()
    new = namespace["HeadFilter"]()
    old._title_blacklist = blacklist
    new._title_blacklist = blacklist
    new._title_whitelist = ()
    old._title_skips = set()
    new._title_skips = set()
    rows = tuple(SimpleNamespace(event_title=title, condition_id=str(index)) for index, title in enumerate(titles))
    for _ in range(2):
        previous = old._drop_blacklisted(rows) if blacklist else rows
        current = new._drop_skipped_titles(rows)
        assert previous == current
        assert old._title_skips == new._title_skips
print("Follow300 filter parity: 7 titles, 2 blacklist policies, 2 cycles passed")

new = namespace["HeadFilter"]()
new._title_blacklist = ("Streamers", "Winline")
new._title_whitelist = ("BLAST Slam",)
new._title_skips = set()
blast = SimpleNamespace(condition_id="blast", event_title="Dota 2: A vs B (BO3) - BLAST Slam IV Group Stage")
other = SimpleNamespace(condition_id="epl", event_title="Dota 2: A vs B (BO3) - EPL World Series")
team = SimpleNamespace(condition_id="team", event_title="Dota 2: BLAST Slam vs B (BO3) - PARI Universe")
missing = SimpleNamespace(condition_id="missing", event_title=None)
assert new._drop_skipped_titles((blast, other, team, missing)) == (blast,)
print("BLAST-only whitelist: BLAST kept; EPL, team-name spoof, missing title dropped")

template = tomllib.loads(read_source(HEAD, "config/trading.toml"))["clips"]["dota"]
table = ClipTable(template["default"], tuple(ClipTier(tier["clip"], tuple(tier["names"])) for tier in template["tiers"]))
names = select("two_sided", {"dota-map": table}, dota)
new._title_whitelist = names
assert other in new._drop_skipped_titles((blast, other))
print(f"BLAST-only constraint counterexample: current template names={names!r}; EPL retained")
nonblast = ClipTable(5.0, (ClipTier(20.0, ("PARI Universe",)),))
assert select("two_sided", {"dota-map": nonblast}, dota) == ("PARI Universe",)
print("Non-BLAST-only clip table is also accepted by two-sided whitelist selection")


def load_callable(commit, path, requested, space, owner=None):
    tree = ast.parse(read_source(commit, path))
    body = tree.body
    if owner:
        body = next(node for node in body if getattr(node, "name", None) == owner).body
    node = copy.deepcopy(next(node for node in body if getattr(node, "name", None) == requested))
    for arg in [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]:
        arg.annotation = None
    node.returns = None
    module = ast.fix_missing_locations(ast.Module(body=[node], type_ignores=[]))
    exec(compile(module, f"{commit}:{path}:{requested}", "exec"), space)


host_space = dict(namespace)
host_space["Any"] = Any
host_space["polymaker_engine"] = SimpleNamespace(
    ExecutionGateway=object(), construct_quotes=object(), reconcile=object()
)
host_space["PaperGateway"] = object()
host_space["require_live_wallet"] = lambda: None
host_space["read_template"] = lambda games: object()
host_space["wallet_db_path"] = lambda mode: Path("/unused/s7-review/wallet.db")
lock_events = []
host_space["acquire_file_lock"] = lambda path: SimpleNamespace(close=lambda: lock_events.append("closed"))
host_space["patch_engine_classes"] = lambda: object()
host_space["restore_engine_classes"] = lambda restore: None
host_space["CollateralCache"] = lambda: object()
config_events = []
host_space["materialize_wallet_config_dir"] = lambda *args: SimpleNamespace(
    config_dir="unused", cleanup=lambda: config_events.append("closed")
)
engine_events = []


class ReachedEngine(Exception):
    pass


def record_engine(cfg, paper):
    engine_events.append(paper)
    raise ReachedEngine()


host_space["Engine"] = record_engine
host_space["logger"] = namespace["logger"]
load_callable(HEAD, "src/trader/host_resources.py", "open_wallet_host", host_space)
startup_wiring_cases = [
    ("paper", (dota,), 3, True, "--mode live"),
    ("live", (dota, lol), 3, True, "only dota assigned"),
    ("live", (lol,), 3, True, "only dota assigned"),
    ("live", (dota,), 0, True, "signature_type 3"),
    ("live", (dota,), 1, True, "signature_type 3"),
    ("live", (dota,), 2, True, "signature_type 3"),
    ("live", (dota,), 3, False, "POLY_BUILDER_KEY"),
]
for mode, games, signature, creds, message in startup_wiring_cases:
    cfg = SimpleNamespace(engine=SimpleNamespace(journal=True), wallet=SimpleNamespace(signature_type=signature),
                          secrets=SimpleNamespace(has_builder_creds=creds, browser_address="fake-funder"))
    host_space["Config"] = SimpleNamespace(load=lambda *args, **kwargs: cfg)
    engine_events.clear()
    lock_events.clear()
    config_events.clear()
    try:
        host_space["open_wallet_host"](None, {}, "commit", mode, games, "two_sided")
    except TradingDisabled as exc:
        assert message in str(exc)
    else:
        raise AssertionError(message)
    assert engine_events == []
    assert lock_events == ["closed"]
    assert config_events == ["closed"]
print("Committed open_wallet_host wiring: 7 refusals precede Engine; lock/config cleanup passed")

cfg = SimpleNamespace(engine=SimpleNamespace(journal=True), wallet=SimpleNamespace(signature_type=1),
                      secrets=SimpleNamespace(browser_address="fake-funder"))
host_space["Config"] = SimpleNamespace(load=lambda *args, **kwargs: cfg)
try:
    host_space["open_wallet_host"](None, {}, "commit", "live", (dota,), "follow300")
except ReachedEngine:
    pass
else:
    raise AssertionError("Follow300 did not reach Engine")
assert engine_events == [False]
print("Follow300 reaches Engine without two-sided signature/builder validation")

run_space = dict(namespace)
run_space["DisirCatalog"] = lambda *args, **kwargs: SimpleNamespace(open_matches=lambda: ())
run_space["require_brand_token"] = lambda: "fake-brand"
run_space["datetime"] = SimpleNamespace(now=lambda zone: None)
run_space["UTC"] = object()
run_space["asyncio"] = asyncio
run_space["probe_grid_scoreboard_cycle"] = lambda: None
run_space["select_title_blacklist"] = lambda mode, game: ("Streamers", "Winline")
run_space["MarketDiscovery"] = lambda *args: object()
load_callable(HEAD, "src/trader/wallet_host.py", "run", run_space, "WalletHost")
run_events = []


async def record_start():
    run_events.append("start")


empty_host = SimpleNamespace(_games=(dota,), _archive_by_game={"dota": Path("/unused")},
                             _strategy="two_sided", _mode="live", steam_client=None,
                             clip_tables={"dota-map": ClipTable(5.0, ())},
                             engine=SimpleNamespace(start=record_start))
try:
    asyncio.run(run_space["run"](empty_host))
except TradingDisabled as exc:
    assert "[clips.dota].tiers" in str(exc)
else:
    raise AssertionError("Empty whitelist did not refuse host.run")
assert run_events == []
print("Committed WalletHost.run wiring: empty whitelist refusal precedes engine.start")

worker_space = dict(namespace)
worker_space["asyncio"] = asyncio
worker_space["replace"] = lambda handoff, **kwargs: handoff
worker_space["strategy_profile_name"] = lambda **kwargs: "dota-map"
worker_events = []


class FollowWorker:
    def __init__(self, *args):
        worker_events.append(("follow300", args))

    async def run(self):
        worker_events.append("run")


class TwoWorker:
    def __init__(self, *args):
        worker_events.append(("two_sided", args))

    async def run(self):
        worker_events.append("run")


worker_space["MatchWorker"] = FollowWorker
worker_space["TwoSidedWorker"] = TwoWorker
feed = SimpleNamespace(source="GRID", stale_seconds=10.0)


async def choose_feed(handoff, map_ended):
    return SimpleNamespace(feed=feed, handoff=handoff)


worker_space["select_feed"] = choose_feed
load_callable(HEAD, "src/trader/wallet_host.py", "_pick_and_run", worker_space, "WalletHost")
for strategy in ("follow300", "two_sided"):
    worker_events.clear()
    model = object()
    host = SimpleNamespace(_strategy=strategy, _mode="live", _models={"dota-map": model}, _map_ended=None)
    handoff = SimpleNamespace(game="dota", oddin_match_id=None)
    record = SimpleNamespace(handoff=handoff, announced=True, record_only=False)
    asyncio.run(worker_space["_pick_and_run"](host, record))
    assert worker_events == [(strategy, (host, handoff, model, "live", feed, 10.0)), "run"]
print("Committed worker dispatcher: Follow300 and two-sided select intended workers with same six arguments")
