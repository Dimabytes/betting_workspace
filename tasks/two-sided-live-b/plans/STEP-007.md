# STEP-007: Host and daemon: `DOTA_STRATEGY`, start checks, worker choice, BLAST Slam whitelist

Task: `W/tasks/two-sided-live-b/feature.json`, step STEP-007.
Code repo: `E = /Users/dimabytes/work/polymarket/dota_2_bot/esports-trader`, branch `main`.
Base: STEP-001..STEP-004 (`7b1b6990`), STEP-005 `da5ce546` (committed: `ctf_merge.install_adapter_merge`, `pair_merge.PairMerger`), then STEP-006 (`plans/STEP-006.md`: `trader.two_sided_worker.TwoSidedWorker`).
No Figma, no UI.

Read `W/AGENTS.md` and `E/AGENTS.md` first. Rules that matter here:
- Function names are verbs. Arguments are required (the existing defaulted kwargs of test fixtures are the 1% exception).
- No `dict[str, Any]` for our own data. No anonymous multi-field tuples.
- No branches for inputs that cannot occur.
- **New code gets no comments and no docstrings** (review config). `# pyright:` file pragmas are allowed. New test functions get no docstrings either.
- Service A (Follow300, compose service `live`) must behave exactly as before when `DOTA_STRATEGY` is unset (FR-7). `../poly-maker` is frozen.
- No network in tests. Never use the `*_B` keys. Do not touch `compose.yaml` or `config/**` (that is STEP-008).

## Precondition

Start only after STEP-006 is committed. In E:

```bash
git status --short   # only the two untracked data/backtests/dota_maker/validation_*ts-regress-* dirs
grep -n "class TwoSidedWorker" src/trader/two_sided_worker.py
grep -n "def install_adapter_merge\|def _skip_fork_merge" src/trader/ctf_merge.py
grep -n "async def sweep" src/trader/pair_merge.py
```

All greps must hit. `TwoSidedWorker.__init__` must take the same positional arguments as `MatchWorker.__init__`: `(host, discovered, model, mode, feed, feed_timeout_seconds, exit_timeout_seconds=None)`. If STEP-006 changed that, adapt only the `_pick_and_run` call, never the worker.

`make lint` runs pre-commit, which stashes unstaged files and type-checks the whole project, so a dirty tree mixes another step's errors into this one.

## Goal

1. `trading_mode.py`: `DotaStrategy`, `read_dota_strategy()`, `require_two_sided_start(...)`.
2. `discovery.py`: `league_matches(title, names)` replaces `is_league_blacklisted`. `MarketDiscovery` takes a required `title_whitelist` next to `title_blacklist`. A non-empty whitelist keeps only matching titles and logs `discovery skip reason=title_whitelist`.
3. `wallet_host.py`: `WalletHost` takes a required `strategy`. `_pick_and_run` builds `TwoSidedWorker` for `two_sided`, `MatchWorker` for `follow300`, with the same arguments. `_install_runtime_seams` calls `install_adapter_merge` only for `two_sided`. `run()` passes the whitelist (`[clips.dota].tiers` names for `two_sided`, `()` for `follow300`).
4. `host_resources.py`: parse the strategy once, run the start check, log `strategy=… signature_type=… funder=…`, pass `strategy` to `WalletHost`.
5. Tests (fakes, no network).

## Decisions (made without the owner, with reasons)

1. **The parser is `read_dota_strategy()`, not `dota_strategy()`.** E/AGENTS.md: "Function names should be action verbs." It is the same function the feature names.
2. **Only `two_sided` is accepted, after `strip()`.** Unset, empty or whitespace gives `follow300`. Every other value raises `TradingDisabled`, including the literal `follow300`. The feature says "иначе TradingDisabled". A is the service without the variable, so there is one way to say "A". `strip()` matches `assigned_games`. Case is exact, as in `assigned_games`.
3. **The strategy is read once, in `run_wallet_daemon`, right after `reject_legacy_env()` and before the idle branch.** A bad value fails even when the process would idle. `run_wallet_daemon`'s signature does not change, so `orchestrator.py` and its tests stay untouched.
4. **The start check is one pure function, `require_two_sided_start(*, mode, games, signature_type, has_builder_creds)`.** `open_wallet_host` calls it right after `Config.load` and the journal check, before `Engine(...)` and before the models load.
   - It needs `cfg.wallet.signature_type` and `cfg.secrets.has_builder_creds`, and `cfg` exists only there.
   - A refused start builds no Engine, loads no model and touches no venue. The existing `except BaseException` in `open_wallet_host` closes the lock and the temp config dir.
   - It takes primitives, not `Config`, so tests never build `Secrets()`. `Secrets()` reads `E/.env`, which holds A's `PK`.
5. **An idle process (no game assigned) stays idle under `two_sided`.** There is no refusal: idle opens nothing and trades nothing, and A's idle path stays byte-identical.
6. **The whitelist is built per profile in `WalletHost.run()` by `select_title_whitelist(strategy, clip_tables, profile)` in `wallet_host.py`, next to `select_title_blacklist`.**
   - `follow300` returns `()` without touching `clip_tables`. `two_sided` returns the tier names of `clip_tables[profile.primary.profile_name]`, which is `[clips.dota]` for Dota (`"dota-map"`).
   - `WalletHost` therefore gets only `strategy`, not a second whitelist argument.
7. **An empty whitelist under `two_sided` is refused** (`TradingDisabled`, raised in `run()` before `engine.start()`). This is my addition: an empty whitelist means "pass everything", and B would then trade every Dota tournament at the default clip (Non-Goal: no tournament besides BLAST Slam). `run_wallet_daemon`'s `finally` closes the host. The Engine never connected.
8. **`league_matches` folds case with `casefold()`, as `clip_rules.choose_clip` does.** A title that passes the whitelist then always hits its clip tier. A's blacklist needles (`Streamers`, `Winline`) are ASCII, where `casefold() == lower()`, so A is unchanged.
9. **One filter pass, `_drop_skipped_titles`, replaces `_drop_blacklisted`.** The blacklist is checked first, then the whitelist. There is one log-dedup set, as before. The `if self._title_blacklist:` guard goes: with both lists empty the pass drops nothing. A's skip log text stays byte-identical (`discovery skip reason=title_blacklist cid=%s title=%r`).
10. **`WalletHost.__init__` takes `strategy: DotaStrategy` as its last positional parameter.** The other 19 parameters are positional too (house style). `self._strategy` is set before `self._install_runtime_seams()` runs.
11. **The host log line is unconditional:** `trader wallet: strategy=%s signature_type=%d funder=%s`, with funder = `cfg.secrets.browser_address`. That is exactly what the fork gateway uses as funder (`sec.browser_address or address`), and `require_live_wallet` already requires it in live. A gets the same info line with `strategy=follow300`. It is a log line, not a behavior change.
12. **B still loads the Dota models.** `_load_strategy_models(games)` is unchanged, and `_pick_and_run` passes `self._models[profile_name]` to `TwoSidedWorker`. `MatchStart` provenance and the journal pin read `model.model_reference`. `TwoSidedWorker` never calls `predict_fair` (STEP-006).
13. **Host wiring tests go to a new file, `tests/test_trader_two_sided_host.py`.** `tests/test_trader_wallet_host.py` is already 2981 lines. The new file imports that file's `_bare_host`, `_record_for`, `_run_engine` and `_patch_run_to_discovery`. That is the existing precedent: `test_trader_wallet_host.py` imports from `test_trader_unsettled_buy_activation.py`.
14. **The seam test uses a real paper `Engine` and the real `WalletHost.__init__`.** That is the production paper path, so every other seam installs as it does in service `paper`. Asserting "the fork method stays" needs the real class method. `Secrets(_env_file=None)` keeps it off `E/.env`.

## Order of edits (paths relative to E)

### 1. `src/trader/trading_mode.py`

After `ExecutionMode = Literal["paper", "live"]` add:

```python
DotaStrategy = Literal["follow300", "two_sided"]
```

Append after `require_live_wallet`:

```python
def read_dota_strategy() -> DotaStrategy:
    raw = env_value("DOTA_STRATEGY")
    if raw is None or not raw.strip():
        return "follow300"
    token = raw.strip()
    if token == "two_sided":
        return "two_sided"
    raise TradingDisabled(f"DOTA_STRATEGY {token!r} is not two_sided; leave it unset for follow300")


def require_two_sided_start(
    *,
    mode: ExecutionMode,
    games: tuple[GameProfile, ...],
    signature_type: int,
    has_builder_creds: bool,
) -> None:
    if mode != "live":
        raise TradingDisabled(f"DOTA_STRATEGY=two_sided requires --mode live, got {mode}")
    assigned = ",".join(profile.game for profile in games)
    if assigned != "dota":
        raise TradingDisabled(
            f"DOTA_STRATEGY=two_sided requires only dota assigned, got {assigned!r}"
        )
    if signature_type != 3:
        raise TradingDisabled(
            "DOTA_STRATEGY=two_sided requires wallet.signature_type 3 (deposit wallet), "
            f"got {signature_type}"
        )
    if not has_builder_creds:
        raise TradingDisabled(
            "DOTA_STRATEGY=two_sided requires POLY_BUILDER_KEY, POLY_BUILDER_SECRET "
            "and POLY_BUILDER_PASSPHRASE for relayer merges"
        )
```

The module docstring stays as it is.

### 2. `src/trader/discovery.py`

Replace `is_league_blacklisted` (lines 70-79, docstring included) with:

```python
def league_matches(title: str | None, names: tuple[str, ...]) -> bool:
    league = parse_event_league(None, title)
    if league is None:
        return False
    folded = league.casefold()
    return any(name.casefold() in folded for name in names)
```

`MarketDiscovery.__init__`: add `title_whitelist: tuple[str, ...],` right after `title_blacklist: tuple[str, ...],`, and `self._title_whitelist = title_whitelist` right after `self._title_blacklist = title_blacklist`.

`discover()`: replace

```python
        eligible = tuple(sidecar for sidecar in scan.sidecars if sidecar.is_tradeable())
        if self._title_blacklist:
            eligible = self._drop_blacklisted(eligible)
```

with

```python
        tradeable = tuple(sidecar for sidecar in scan.sidecars if sidecar.is_tradeable())
        eligible = self._drop_skipped_titles(tradeable)
```

The rest of `discover()` is unchanged.

Replace `_drop_blacklisted` (docstring included) with:

```python
    def _title_skip_reason(self, title: str | None) -> str | None:
        if league_matches(title, self._title_blacklist):
            return "title_blacklist"
        if self._title_whitelist and not league_matches(title, self._title_whitelist):
            return "title_whitelist"
        return None

    def _drop_skipped_titles(
        self, sidecars: tuple[FreshSidecar, ...]
    ) -> tuple[FreshSidecar, ...]:
        kept: list[FreshSidecar] = []
        skipped: set[str] = set()
        for sidecar in sidecars:
            reason = self._title_skip_reason(sidecar.event_title)
            if reason is None:
                kept.append(sidecar)
                continue
            cid = sidecar.condition_id
            log = logger.debug if cid in self._title_skips else logger.info
            log("discovery skip reason=%s cid=%s title=%r", reason, cid, sidecar.event_title)
            skipped.add(cid)
        self._title_skips = skipped
        return tuple(kept)
```

`grep -rn is_league_blacklisted src scripts tests` must be empty after step 5.

### 3. `src/trader/wallet_host.py`

Imports (let ruff sort them):
- `from trader.ctf_merge import install_adapter_merge`
- `from trader.two_sided_worker import TwoSidedWorker`
- `from trader.trading_mode import DotaStrategy, ExecutionMode` (replaces the `ExecutionMode`-only import)

After `select_title_blacklist`:

```python
def select_title_whitelist(
    strategy: DotaStrategy, clip_tables: Mapping[str, ClipTable], profile: GameProfile
) -> tuple[str, ...]:
    if strategy == "follow300":
        return ()
    table = clip_tables[profile.primary.profile_name]
    names = tuple(name for tier in table.tiers for name in tier.names)
    if not names:
        raise TradingDisabled(
            f"DOTA_STRATEGY=two_sided needs names in [clips.{profile.game}].tiers: "
            "they are the title whitelist"
        )
    return names
```

`Mapping`, `ClipTable`, `GameProfile` and `TradingDisabled` are already imported.

`WalletHost.__init__`:
- After `clip_tables: dict[str, ClipTable],` add the parameter `strategy: DotaStrategy,`.
- After `self._mode: ExecutionMode = mode` add `self._strategy: DotaStrategy = strategy`. It must come before the last line, `self._install_runtime_seams()`.

`_install_runtime_seams`: append as its last statement, after `self._install_lol_prior_tap()`:

```python
        if self._strategy == "two_sided":
            install_adapter_merge(engine)
```

`run()`, the `MarketDiscovery(...)` call: add a 7th argument after `select_title_blacklist(self._mode, profile.game),`:

```python
                select_title_whitelist(self._strategy, self.clip_tables, profile),
```

`_pick_and_run`: replace `worker = MatchWorker(` with

```python
        worker_class = TwoSidedWorker if self._strategy == "two_sided" else MatchWorker
        worker = worker_class(
```

The six arguments stay exactly as they are: `self, replace(choice.handoff, record_only=record.record_only), self._models[profile_name], self._mode, feed, feed.stale_seconds`. Leave the existing docstring. `TwoSidedWorker` is a `MatchWorker`, so it stays true.

Nothing else in `wallet_host.py` changes. `_worker_by_cid: dict[str, MatchWorker]` already accepts the subclass.

### 4. `src/trader/host_resources.py`

Import `DotaStrategy`, `read_dota_strategy` and `require_two_sided_start` from `trader.trading_mode`.

`open_wallet_host`: add a last parameter `strategy: DotaStrategy`. After

```python
        if cfg.engine.journal is not True:
            raise RuntimeError("engine journal is disabled in the config template")
```

insert:

```python
        if strategy == "two_sided":
            require_two_sided_start(
                mode=mode,
                games=games,
                signature_type=cfg.wallet.signature_type,
                has_builder_creds=cfg.secrets.has_builder_creds,
            )
        logger.info(
            "trader wallet: strategy=%s signature_type=%d funder=%s",
            strategy,
            cfg.wallet.signature_type,
            cfg.secrets.browser_address,
        )
```

Then `engine = Engine(...)` as before. In `return WalletHost(...)`, add `strategy,` after `template.clips,`.

`run_wallet_daemon`: after `reject_legacy_env()` add `strategy = read_dota_strategy()`. Change the call to `open_wallet_host(steam, archive_by_game, git_commit, mode, games, strategy)`.

McCabe check: `open_wallet_host` measures 9 today and becomes 10. Ruff's limit is "> 10", so it passes. Verify with the C901 command below. If it reports 11, move the `if strategy == "two_sided": require_two_sided_start(...)` block into a module-level `_require_strategy_start(strategy, mode, games, cfg)` in `host_resources.py`. Do not raise the limit.

### 5. Existing tests: mechanical updates only

- `tests/trader_discovery_fixtures.py` `make_discovery`:
  - Add the keyword parameter `title_whitelist: tuple[str, ...] = (),` after `title_blacklist`.
  - Pass it as the 7th `MarketDiscovery` argument.
- `tests/test_discovery_cadence.py:63` and `tests/test_trader_lol_discovery.py:77`: add `()` as the 7th `MarketDiscovery` argument.
- `tests/test_trader_wallet_host.py`:
  - `_bare_host`: add `host._strategy = "follow300"` and `host.clip_tables = {}`. Every `run()` and `_pick_and_run` test through `_bare_host` needs them.
  - `test_run_builds_one_discovery_per_assigned_game`: `FakeDiscovery.__init__` gets a 7th parameter `title_whitelist: tuple[str, ...]`, recorded into a new `whitelist_by_game: dict[str, tuple[str, ...]]`. Add `assert whitelist_by_game == {"dota": (), "lol": ()}`.
  - Nothing else in this file changes.
- `tests/test_trader_discovery.py`: rename `test_is_league_blacklisted_matches_the_league_not_the_teams` to `test_league_matches_reads_the_league_not_the_teams`. Replace every `discovery.is_league_blacklisted(` with `discovery.league_matches(`. Keep all six existing asserts and the docstring. Add:

```python
    assert discovery.league_matches(
        "Dota 2: Falcons vs Tundra (BO3) - BLAST Slam IV Group Stage", ("BLAST Slam",)
    )
    assert not discovery.league_matches(
        "Dota 2: Falcons vs Tundra (BO3) - PARI Universe", ("BLAST Slam",)
    )
```

- `tests/test_trader_host_resources.py`:
  - `ResourceLog.__init__`: add `self.host_strategies: list[object] = []`.
  - `_patch_host_runtime_fakes.fake_init`: replace `del args` with `log.host_strategies.append(args[-1])`.
  - Add two helpers and use them in `_install_resource_fakes`. Delete its inline `cfg = SimpleNamespace(...)` and its `Config` setattr, and call `_use_wallet_cfg(monkeypatch, signature_type=1, has_builder_creds=False)` and `_use_strategy(monkeypatch, "follow300")` instead:

```python
def _use_wallet_cfg(
    monkeypatch: pytest.MonkeyPatch, *, signature_type: int, has_builder_creds: bool
) -> None:
    cfg = SimpleNamespace(
        engine=SimpleNamespace(journal=True),
        wallet=SimpleNamespace(signature_type=signature_type, chain_id=1),
        secrets=SimpleNamespace(browser_address="0xfunder", has_builder_creds=has_builder_creds),
    )
    monkeypatch.setattr("trader.host_resources.Config", SimpleNamespace(load=lambda *_a, **_k: cfg))


def _use_strategy(monkeypatch: pytest.MonkeyPatch, strategy: DotaStrategy) -> None:
    monkeypatch.setattr("trader.host_resources.read_dota_strategy", lambda: strategy)
```

  - `test_dota_live_opens_wallet_and_steam`: add `assert log.host_strategies == ["follow300"]`. That is A's proof: no variable means `follow300`.

### 6. New tests

**`tests/test_trader_trading_mode.py`** (append; reuse `_env_get`, `DOTA`, `LOL`; import `read_dota_strategy` and `require_two_sided_start`):

```python
@pytest.mark.parametrize(
    ("env", "want"),
    [
        ({}, "follow300"),
        ({"DOTA_STRATEGY": ""}, "follow300"),
        ({"DOTA_STRATEGY": "  "}, "follow300"),
        ({"DOTA_STRATEGY": "two_sided"}, "two_sided"),
        ({"DOTA_STRATEGY": " two_sided "}, "two_sided"),
    ],
    ids=["missing", "empty", "blank", "two_sided", "padded"],
)
def test_read_dota_strategy_defaults_to_follow300(
    monkeypatch: pytest.MonkeyPatch, env: dict[str, str], want: str
) -> None:
    monkeypatch.setattr("trader.trading_mode.env_value", _env_get(env))
    assert read_dota_strategy() == want


@pytest.mark.parametrize("raw", ["follow300", "two-sided", "TWO_SIDED", "1"])
def test_read_dota_strategy_rejects_other_tokens(monkeypatch: pytest.MonkeyPatch, raw: str) -> None:
    monkeypatch.setattr("trader.trading_mode.env_value", _env_get({"DOTA_STRATEGY": raw}))
    with pytest.raises(session_types.TradingDisabled, match="DOTA_STRATEGY"):
        read_dota_strategy()


def test_two_sided_start_accepts_live_dota_deposit_wallet_with_builder_creds() -> None:
    require_two_sided_start(mode="live", games=(DOTA,), signature_type=3, has_builder_creds=True)


@pytest.mark.parametrize(
    ("mode", "games", "signature_type", "has_builder_creds", "match"),
    [
        ("paper", (DOTA,), 3, True, "--mode live"),
        ("live", (DOTA, LOL), 3, True, "only dota assigned, got 'dota,lol'"),
        ("live", (LOL,), 3, True, "only dota assigned, got 'lol'"),
        ("live", (DOTA,), 2, True, "signature_type 3"),
        ("live", (DOTA,), 3, False, "POLY_BUILDER_KEY"),
    ],
    ids=["paper", "dota_and_lol", "lol_only", "signature_type_2", "no_builder_creds"],
)
def test_two_sided_start_refuses_each_violation(
    mode: ExecutionMode,
    games: tuple[GameProfile, ...],
    signature_type: int,
    has_builder_creds: bool,
    match: str,
) -> None:
    with pytest.raises(session_types.TradingDisabled, match=match):
        require_two_sided_start(
            mode=mode, games=games, signature_type=signature_type, has_builder_creds=has_builder_creds
        )
```

(`ExecutionMode` comes from `trader.trading_mode`. `match` is a regex: `'` and `,` are literal.)

**`tests/test_trader_discovery.py`** (place right after `test_blacklisted_event_league_never_reaches_steam`):

```python
@pytest.mark.parametrize(
    ("title", "whitelist", "emitted"),
    [
        ("Dota 2: Aurora vs Team Secret (BO3) - BLAST Slam IV Group Stage", ("BLAST Slam",), 1),
        ("Dota 2: Aurora vs Team Secret (BO3) - PARI Universe", ("BLAST Slam",), 0),
        (None, ("BLAST Slam",), 0),
        ("Dota 2: Aurora vs Team Secret (BO3) - PARI Universe", (), 1),
    ],
    ids=["blast_slam", "other_league", "no_title", "empty_whitelist"],
)
def test_title_whitelist_keeps_only_listed_leagues(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    title: str | None,
    whitelist: tuple[str, ...],
    emitted: int,
) -> None:
    api = FakeSteamApi()
    prime_happy_api(api)
    write_sidecar(tmp_path, "0x1", sidecar_body(eventTitle=title))
    patch_discovery_time(monkeypatch, NOW_EPOCH)
    patch_alerts(monkeypatch)
    found = make_discovery(tmp_path, api, title_whitelist=whitelist)

    with caplog.at_level(logging.INFO):
        assert len(found.discover()) == emitted
        assert len(found.discover()) == emitted

    skips = [
        row
        for row in caplog.records
        if row.levelname == "INFO" and "reason=title_whitelist" in row.getMessage()
    ]
    assert len(skips) == 1 - emitted
```

The existing `test_blacklisted_event_league_never_reaches_steam` stays unchanged. It is the "blacklist works as before" proof.

**New `tests/test_trader_two_sided_host.py`**:

```python
# pyright: reportPrivateUsage=false, reportPrivateImportUsage=false, reportUnknownLambdaType=false

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import polymaker.engine as polymaker_engine
import pytest
from polymaker.config import Config, PathsConfig, Secrets
from polymaker.engine import Engine
from test_trader_wallet_host import _bare_host, _patch_run_to_discovery, _record_for, _run_engine
from trader_session_fixtures import build_discovered

from shared.constants.strategy import GRID_FEED_STALE_SECONDS
from trader.clip_rules import ClipTable, ClipTier
from trader.ctf_merge import _skip_fork_merge
from trader.engine_seams import (
    BookReadiness,
    ShutdownLatch,
    patch_engine_classes,
    restore_engine_classes,
)
from trader.game_profile import GAME_PROFILES
from trader.live_feed import FeedSource
from trader.paper_gateway import PaperGateway
from trader.process_lock import acquire_file_lock
from trader.session_core import CollateralCache
from trader.session_types import TradingDisabled
from trader.trading_mode import DotaStrategy
from trader.wallet_host import LIVE_TITLE_BLACKLIST, WalletHost, select_title_whitelist

DOTA = GAME_PROFILES["dota"]
LOL = GAME_PROFILES["lol"]
BLAST_ONLY = {"dota-map": ClipTable(5.0, (ClipTier(20.0, ("BLAST Slam",)),))}


def test_select_title_whitelist_is_the_clip_names_for_two_sided_only() -> None:
    tables = {
        "dota-map": ClipTable(
            5.0, (ClipTier(60.0, ("EPL World Series",)), ClipTier(20.0, ("BLAST Slam",)))
        )
    }
    assert select_title_whitelist("follow300", tables, DOTA) == ()
    assert select_title_whitelist("follow300", {}, LOL) == ()
    assert select_title_whitelist("two_sided", tables, DOTA) == ("EPL World Series", "BLAST Slam")
    with pytest.raises(TradingDisabled, match=r"\[clips\.dota\]\.tiers"):
        select_title_whitelist("two_sided", {"dota-map": ClipTable(5.0, ())}, DOTA)


def test_two_sided_run_filters_discovery_by_the_clip_names(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list[str] = []
    built: list[tuple[object, ...]] = []

    def record_discovery(*args: object) -> object:
        built.append(args)
        return object()

    async def no_refresh(self: object) -> bool:
        del self
        return False

    monkeypatch.setattr("trader.wallet_host.TRADER_DIR", tmp_path)
    monkeypatch.setattr("trader.wallet_host.require_brand_token", lambda: "test-token")
    monkeypatch.setattr("trader.wallet_host.DisirCatalog.refresh_now", no_refresh)
    host = _bare_host(_run_engine(events))
    host._mode = "live"
    host._strategy = "two_sided"
    host.clip_tables = BLAST_ONLY
    monkeypatch.setattr("trader.wallet_host.MarketDiscovery", record_discovery)
    _patch_run_to_discovery(monkeypatch, host, events)
    asyncio.run(host.run())
    assert [args[5:] for args in built] == [(LIVE_TITLE_BLACKLIST, ("BLAST Slam",))]
    assert "discovery" in events


@pytest.mark.parametrize(
    ("strategy", "worker_name"),
    [("follow300", "MatchWorker"), ("two_sided", "TwoSidedWorker")],
)
def test_pick_and_run_builds_the_worker_of_the_strategy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, strategy: DotaStrategy, worker_name: str
) -> None:
    monkeypatch.setattr("trader.wallet_host.TRADER_DIR", tmp_path)
    monkeypatch.setattr("trader.match_meta.TRADER_DIR", tmp_path)
    monkeypatch.setattr("trader.wallet_host.notify_in_background", lambda message: None)
    feed = SimpleNamespace(source=FeedSource.GRID, stale_seconds=GRID_FEED_STALE_SECONDS)

    async def fake_select(handoff: object, _should_close: object) -> object:
        return SimpleNamespace(feed=feed, handoff=handoff)

    built: dict[str, tuple[object, ...]] = {}

    def record_worker(name: str) -> type:
        class _Recorded:
            def __init__(self, *args: object) -> None:
                built[name] = args

            async def run(self) -> None:
                return None

        return _Recorded

    monkeypatch.setattr("trader.wallet_host.select_feed", fake_select)
    monkeypatch.setattr("trader.wallet_host.MatchWorker", record_worker("MatchWorker"))
    monkeypatch.setattr("trader.wallet_host.TwoSidedWorker", record_worker("TwoSidedWorker"))
    host = _bare_host(SimpleNamespace(metas={}))
    host._strategy = strategy
    discovered = build_discovered(grid_series_id="2995964")
    asyncio.run(host._pick_and_run(_record_for(discovered)))
    model = host._models["dota-map"]
    assert built == {
        worker_name: (host, discovered, model, "paper", feed, GRID_FEED_STALE_SECONDS)
    }


def _open_paper_host(
    tmp_path: Path,
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
    strategy: DotaStrategy,
) -> WalletHost:
    original_gateway = polymaker_engine.ExecutionGateway
    monkeypatch.setattr(polymaker_engine, "ExecutionGateway", PaperGateway)
    restore = patch_engine_classes()
    request.addfinalizer(lambda: restore_engine_classes(restore))
    cfg = Config(
        paths=PathsConfig(db=str(tmp_path / "paper.db"), journal_dir=str(tmp_path / "journal")),
        secrets=Secrets(_env_file=None),
    )
    engine = Engine(cfg, paper=True)
    host = WalletHost(
        None,
        {"dota": tmp_path},
        engine,
        "paper",
        acquire_file_lock(tmp_path / "paper.lock"),
        cast(Any, SimpleNamespace(cleanup=lambda: None)),
        {},
        restore,
        ShutdownLatch(),
        BookReadiness(),
        {},
        {},
        CollateralCache(),
        original_gateway,
        polymaker_engine.construct_quotes,
        polymaker_engine.reconcile,
        "commit",
        (DOTA,),
        {},
        strategy,
    )
    request.addfinalizer(host.close)
    return host


def test_follow300_host_keeps_the_fork_merge(
    tmp_path: Path, request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    host = _open_paper_host(tmp_path, request, monkeypatch, "follow300")
    assert "_maybe_merge" not in vars(host.engine)


def test_two_sided_host_stubs_the_fork_merge(
    tmp_path: Path, request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    host = _open_paper_host(tmp_path, request, monkeypatch, "two_sided")
    assert vars(host.engine)["_maybe_merge"] is _skip_fork_merge
```

Notes:
- The existing `run()` tests in `test_trader_wallet_host.py` idle only `DisirCatalog.run`. Their `refresh_now` makes a real Disir request with the `.env` brand token. The new run test patches `refresh_now`, `require_brand_token` and `TRADER_DIR` (for `close_ended_oddin_archives`), so it stays offline. Leave the old tests alone (out of scope). Mention this in `progress.txt`.
- Finalizers run last-in, first-out: `host.close` (restores the classes and `ExecutionGateway`), then `restore_engine_classes` (idempotent), then monkeypatch's undo. If `Engine(...)` or `WalletHost(...)` raises, the classes still come back.
- `"_maybe_merge" not in vars(engine)` proves that the fork's class method is what `engine._maybe_merge` resolves to. Nothing else in E sets that attribute (`grep -rn _maybe_merge src` shows only `ctf_merge.py`).
- `DiscoveredMatch.record_only` is `compare=False`, so the `replace(...)` copy equals `discovered`.
- If basedpyright rejects `Secrets(_env_file=None)` (pydantic-settings 2.15 types it), use `Secrets(PK="", BROWSER_ADDRESS="")`. Do not fall back to the bare `Secrets()`, which reads `E/.env`.
- If the real paper `WalletHost(...)` raises in a seam unrelated to this step, stop and report it. Do not stub seams to make it pass: the point is that the production paper path runs.

**`tests/test_trader_host_resources.py`** (append; import `logging` is already there; import `TradingDisabled` from `trader.session_types` and `DotaStrategy` from `trader.trading_mode`):

```python
def test_two_sided_live_dota_opens_the_host_with_the_strategy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    log = ResourceLog()
    _install_resource_fakes(monkeypatch, tmp_path, log)
    _assign(monkeypatch, (DOTA,))
    _use_strategy(monkeypatch, "two_sided")
    _use_wallet_cfg(monkeypatch, signature_type=3, has_builder_creds=True)
    caplog.set_level(logging.INFO)
    asyncio.run(run_wallet_daemon("commit", "live"))
    assert log.host_strategies == ["two_sided"]
    assert log.engine_paper == [False]
    assert log.loaded_games == ["production", "production-noxp"]
    assert "strategy=two_sided signature_type=3 funder=0xfunder" in caplog.text


def test_two_sided_refusal_opens_no_engine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    log = ResourceLog()
    _install_resource_fakes(monkeypatch, tmp_path, log)
    _assign(monkeypatch, (DOTA,))
    _use_strategy(monkeypatch, "two_sided")
    _use_wallet_cfg(monkeypatch, signature_type=1, has_builder_creds=True)
    with pytest.raises(TradingDisabled, match="signature_type 3"):
        asyncio.run(run_wallet_daemon("commit", "live"))
    assert log.engine_paper == []
    assert log.loaded_games == []
    assert log.host_strategies == []
```

### Feature test map

| Feature test | Where |
|---|---|
| `DOTA_STRATEGY` missing, empty, `two_sided`, garbage | `test_read_dota_strategy_*` |
| each start-check violation → `TradingDisabled` | `test_two_sided_start_refuses_each_violation`; wiring in `test_two_sided_refusal_opens_no_engine` |
| follow300 keeps the fork `_maybe_merge`; two_sided no-op | `test_follow300_host_keeps_the_fork_merge`, `test_two_sided_host_stubs_the_fork_merge` |
| `_pick_and_run` builds `TwoSidedWorker` only for two_sided | `test_pick_and_run_builds_the_worker_of_the_strategy` (also pins identical args and the Dota model) |
| BLAST Slam whitelist passes / drops other / empty passes all / blacklist as before | `test_title_whitelist_keeps_only_listed_leagues`, `test_league_matches_reads_the_league_not_the_teams`, unchanged `test_blacklisted_event_league_never_reaches_steam` |
| host log `strategy=two_sided signature_type=3` + funder | `test_two_sided_live_dota_opens_the_host_with_the_strategy` |
| whitelist wiring in `run()` | `test_two_sided_run_filters_discovery_by_the_clip_names`, updated `test_run_builds_one_discovery_per_assigned_game` |

## Refused starts and their texts

| Condition | Where it fails | `TradingDisabled` text |
|---|---|---|
| `DOTA_STRATEGY=foo` (any value other than blank or `two_sided`, `follow300` included) | `run_wallet_daemon`, before anything opens, even when idle | `DOTA_STRATEGY 'foo' is not two_sided; leave it unset for follow300` |
| two_sided, `--mode paper` | `open_wallet_host`, before Engine | `DOTA_STRATEGY=two_sided requires --mode live, got paper` |
| two_sided, Dota + LoL assigned | same | `DOTA_STRATEGY=two_sided requires only dota assigned, got 'dota,lol'` |
| two_sided, only LoL assigned | same | `... got 'lol'` |
| two_sided, `wallet.signature_type` ≠ 3 | same | `DOTA_STRATEGY=two_sided requires wallet.signature_type 3 (deposit wallet), got 2` |
| two_sided, any of the 3 builder vars empty | same | `DOTA_STRATEGY=two_sided requires POLY_BUILDER_KEY, POLY_BUILDER_SECRET and POLY_BUILDER_PASSPHRASE for relayer merges` |
| two_sided, `[clips.dota].tiers` has no names | `WalletHost.run`, before `engine.start` | `DOTA_STRATEGY=two_sided needs names in [clips.dota].tiers: they are the title whitelist` |
| two_sided, live, no `PK`/`BROWSER_ADDRESS` | existing `require_live_wallet`, first | `live trading requires PK and BROWSER_ADDRESS` (unchanged) |
| two_sided, nothing assigned | not refused: idle (decision 5) | — |

Each one raises out of `asyncio.run` in `orchestrator.main`, and the process exits 1 with the text in the log. Under `restart: unless-stopped` the container restart-loops with the same line. That is the existing behavior for every `TradingDisabled`.

## Other edge cases

| Case | What happens |
|---|---|
| BLAST Slam title without the `(BOx) - ` suffix | `parse_event_league` → None → dropped as `title_whitelist` (fail-closed; `choose_clip` has the same rule) |
| `… (BO3) - BLAST Slam IV Qualifier` | passes (substring). Clip tier hits too. Accepted: the owner's tier names are the whitelist |
| A team named "BLAST Slam" in a non-BLAST event | not a hit: only the league suffix is matched |
| A sidecar that is both blacklisted and whitelisted | dropped as `title_blacklist` (the blacklist is checked first) |
| An Oddin-fed BLAST Slam map | passes discovery (Dota profile). Clip from `[clips.dota-oddin]` (STEP-008 must list BLAST Slam there, or it is the satellite default plus a "default clip" Telegram line) |
| A (`DOTA_STRATEGY` unset) | `read_dota_strategy` → follow300; whitelist `()`; `MatchWorker`; fork `_maybe_merge`; plus one info line `trader wallet: strategy=follow300 …` |
| Paper service | unchanged. Its compose block sets no `DOTA_STRATEGY` |

## What later steps need from this step

**STEP-008 (`config_b`, `live_b`):**
- Env `DOTA_STRATEGY: two_sided`, the exact token. Any other non-empty value refuses start. Do not add `DOTA_STRATEGY` to `live`, `paper` or `compress`.
- `command`: `daemon --mode live`. Env `DOTA_TRADING_MODE: live` and `LOL_TRADING_MODE: "off"`: the assigned games must be exactly `dota`, and both variables must exist (`assigned_games`).
- The template is read from `BASE_DIR/config/trading.toml` (`session_config.TEMPLATE_PATH`), so mount `./config_b:/app/config:ro`.
  - `[wallet] signature_type = 3`.
  - `[clips.dota].tiers` must hold at least one name (`["BLAST Slam"]`): it is B's discovery whitelist, and empty refuses start.
  - `[clips.dota-oddin].tiers` with BLAST Slam gives Oddin-fed maps the same clip.
- In-container env names that this step reads:
  - `PK` and `BROWSER_ADDRESS` (`require_live_wallet` and `Secrets`; the log's funder is `BROWSER_ADDRESS`);
  - `POLY_BUILDER_KEY`, `POLY_BUILDER_SECRET` and `POLY_BUILDER_PASSPHRASE`, all three non-empty;
  - `POLYGON_RPC` (chain reads and merge receipts);
  - `ODDIN_BRAND_TOKEN` (`run()` requires it);
  - `STEAM_KEYS` (Dota discovery).
  - `POLY_RELAYER_URL` is not needed (the fork default).
- B's wallet db is `data/trader/wallet/live.db`, the same file name as A's, flocked per path. The separate volume `./data/trader_live_b:/app/data/trader` is mandatory. A shared volume makes B exit on "wallet lock is held".
- The LoL whitelist file: from my reading, the host builds `LolLeagueFilter` only for an assigned LoL profile (`discovery.py:195`; `_sync_lol_book_watch` returns when `"lol"` is not in `archive_by_game`). So `config_b` should not need `lol_league_whitelist.json`. STEP-008 must still confirm this, as its feature item says.
- The `stop_grace_period` decision stays with STEP-008 (STEP-005 note: 190 s merge + cancel + 20 s fence).

**STEP-009 (runbook):**
- Start log lines to check: `trader assigned: mode=live games=dota`, then `trader wallet: strategy=two_sided signature_type=3 funder=<BROWSER_ADDRESS_B>`.
- A refused start shows `TradingDisabled: DOTA_STRATEGY=two_sided requires …` in `docker compose logs live_b`, and the container restart-loops.
- A non-BLAST market logs `discovery skip reason=title_whitelist cid=… title=…` once at INFO.

## Do not touch

- `compose.yaml`, `config/**` (STEP-008), `src/trader/orchestrator.py`, `src/trader/match_worker.py`, `src/trader/two_sided_worker.py`, `src/trader/pair_merge.py`, `src/trader/ctf_merge.py`, `src/trader/engine_seams.py`, `src/trader/dust_sweep.py`, `src/trader/session_config.py`, `src/trader/clip_rules.py`, `src/strategy/**`, `src/backtest/**`, `scripts/**`.
- Existing tests, except the mechanical edits listed in section 5.
- `../poly-maker`. Do not stage the untracked `data/backtests/dota_maker/validation_*ts-regress-*` dirs.

## Verification (in E)

```bash
cd /Users/dimabytes/work/polymarket/dota_2_bot/esports-trader

# step tests (feature: trading_mode, wallet_host, discovery) + the new host file
PYTHONPATH=src:scripts:../prediction-market-backtesting uv run --group backtest python -m pytest \
  tests/test_trader_trading_mode.py tests/test_trader_host_resources.py \
  tests/test_trader_discovery.py tests/test_trader_two_sided_host.py \
  tests/test_trader_wallet_host.py -q

# other MarketDiscovery / host / worker users + A regression
PYTHONPATH=src:scripts:../prediction-market-backtesting uv run --group backtest python -m pytest \
  tests/test_discovery_cadence.py tests/test_trader_lol_discovery.py tests/test_trader_orchestrator.py \
  tests/test_trader_engine_seams.py tests/test_trader_unsettled_buy_recovery.py \
  tests/test_trader_unsettled_buy_activation.py tests/test_trader_match_lifecycle.py \
  tests/test_trader_two_sided_worker.py tests/test_trader_pair_merge.py tests/test_trader_ctf_merge.py \
  tests/test_strategy_core.py tests/test_follow300_replay.py -q

# the daemon module imports cleanly outside pytest (no import cycle via two_sided_worker / ctf_merge)
PYTHONPATH=src uv run python -c "import trader.orchestrator"

# leftovers and scope
grep -rn "is_league_blacklisted" src scripts tests            # expect nothing
grep -rn "DOTA_STRATEGY" src                                   # only src/trader/trading_mode.py
git diff --exit-code -- compose.yaml config src/trader/orchestrator.py src/trader/match_worker.py \
  src/trader/engine_seams.py src/trader/dust_sweep.py src/strategy
git -C ../poly-maker status --short                            # expect nothing
git status --short   # only the intended src/tests files + the untracked backtest dirs

# no comments/docstrings in new code; McCabe <= 10
git diff -U0 -- src | grep -E '^\+\s*(#|""")'                  # expect nothing
grep -nE '^\s*#|"""' tests/test_trader_two_sided_host.py | grep -v '# pyright:'   # expect nothing
uv run ruff check --select C901 src/trader/host_resources.py src/trader/wallet_host.py \
  src/trader/discovery.py src/trader/trading_mode.py

# typecheck (strict, whole project) and lint on staged files
uv run python -m basedpyright
git add src/trader/trading_mode.py src/trader/discovery.py src/trader/wallet_host.py \
  src/trader/host_resources.py tests/trader_discovery_fixtures.py tests/test_discovery_cadence.py \
  tests/test_trader_lol_discovery.py tests/test_trader_wallet_host.py tests/test_trader_discovery.py \
  tests/test_trader_host_resources.py tests/test_trader_trading_mode.py tests/test_trader_two_sided_host.py
make lint
git status --short   # only the untracked backtest dirs remain
```

Expected:
- All green. The import smoke prints nothing. Both greps are as stated. The `git diff --exit-code` is empty. 0 basedpyright errors. No C901 finding.
- If `make lint` reformats or re-sorts imports, re-stage and run it again.
- Leave `PYTEST_N` unset. The full serial suite is STEP-010's job. Run `make test` here too if time allows.
- No command here touches the network, Docker or the VPS. Do not run `docker compose`. Do not read or export any `*_B` key.

Review checklist for the diff:
- A's path:
  - `read_dota_strategy()` → `follow300`;
  - `select_title_whitelist` → `()`;
  - `_drop_skipped_titles` with only the blacklist gives the same output and log text as `_drop_blacklisted`;
  - `MatchWorker` gets the same six arguments;
  - no `install_adapter_merge`.
- The only new A-visible effect is the `trader wallet: strategy=follow300 …` info line.

Then, per the implement skill:
- One commit in E on `main`. Suggested message: `Pick the Dota strategy from DOTA_STRATEGY: two-sided worker, adapter merge seam and title whitelist.`
- Update `W/tasks/two-sided-live-b/feature.json` and append to `W/tasks/two-sided-live-b/progress.txt` as for the earlier steps (leave `passes` for the orchestrator's review).
  - Record decisions 1, 2, 5, 7, 8 and 11.
  - Record the STEP-008/009 handoffs above.
- No push.
