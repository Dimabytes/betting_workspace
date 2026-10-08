# STEP-008: `config_b` and the `live_b` service in `compose.yaml`

Task: `W/tasks/two-sided-live-b/feature.json`, step STEP-008.
Code repo: `E = /Users/dimabytes/work/polymarket/dota_2_bot/esports-trader`, branch `main`.
Base: STEP-001..004 committed (`7b1b6990`), then STEP-005 (`plans/STEP-005.md`), STEP-006 (`plans/STEP-006.md`), STEP-007 (feature text: `DOTA_STRATEGY=two_sided`, start checks live + dota only + `signature_type 3` + builder creds, title whitelist = names of `[clips.dota].tiers`).
No Figma, no UI.

Read `W/AGENTS.md` and `E/AGENTS.md` first. Rules that matter here:
- **New code gets no comments and no docstrings** (review config). This covers the new test functions and helpers. The TOML comments in `config_b/trading.toml` are the exception: the feature asks for them, and the copied A comments stay.
- No `dict[str, Any]` for our own data. No anonymous multi-field tuples.
- Service A must not change. The `live` block in `compose.yaml` and `config/trading.toml` must stay byte-identical (STEP-010 checks the diff). `paper` is dead: do not touch it.
- `../poly-maker` is frozen.
- **Never read, print or use the `*_B` values.** Tests use `docker compose config --no-interpolate`, which prints `${PK_WALLET_B}` and not the value. `docker compose config` is allowed. `docker compose up/build/run/start/restart` is not. No SSH, no network, no backtests.

## Precondition

Start only after STEP-005, STEP-006 and STEP-007 are committed. In E:

```bash
git log --oneline -6
git status --short    # only the untracked data/backtests/dota_maker/validation_*ts-regress-* dirs
grep -n "^MERGE_CALL_TIMEOUT_S" src/trader/pair_merge.py      # 190.0
grep -n "^FENCE_TIMEOUT_S" src/trader/match_worker.py         # 20.0
grep -n "^DRAIN_TIMEOUT_S" src/trader/engine_seams.py         # 5.0
grep -n "DOTA_STRATEGY\|two_sided" src/trader/trading_mode.py # STEP-007 env name and token
grep -rn "title_whitelist" src/trader/wallet_host.py src/trader/discovery.py
```

- If STEP-007 used a different env name or token than `DOTA_STRATEGY` / `two_sided`, use STEP-007's names in the compose block and in the test.
- The whitelist must come from the names in `[clips.dota].tiers`. If STEP-007 reads it from somewhere else, stop and write that in `progress.txt`. `config_b` would not filter tournaments then.

## Goal

1. New `config_b/trading.toml`: a copy of `config/trading.toml` with B's wallet type, caps, $20 clips and BLAST Slam as the only tier. Comments say what each 20 means and that 9 × $20 is not enforced.
2. New `live_b` service in `compose.yaml`: same image recipe and command as `live`, wallet B secrets, its own state tree, `config_b`, no LoL mounts, `stop_grace_period: 240s`.
3. `compress` also compresses `data/trader_live_b`.
4. Tests pin all of the above. `test_trader_compose.py` gets an underscore-safe service regex.

## Findings (read the code before deciding)

### F1. LoL with `LOL_TRADING_MODE=off`: nothing LoL is read (the feature's "check and record")

- `run_wallet_daemon` → `assigned_games(mode)` returns only the profiles whose mode env equals `live`. With `LOL_TRADING_MODE=off` that is `(dota,)`.
- `archive_by_game` is built from those games only (`host_resources.py:230`). `LOL_ARCHIVE_ROOT` is never read.
- `LolLeagueFilter` (the only reader of `config/lol_league_whitelist.json`, through `load_canonical_league_whitelist`) is built in `discovery.py:195` only when `profile.game == "lol"`, and in `WalletHost._sync_lol_book_watch`, which returns at once when `"lol" not in self._archive_by_game` (`wallet_host.py:841`).
- `_load_strategy_models(games)` loads only dota catalogs. `data/lol/models` is not read.
- No module reads the whitelist at import time.

Result: `config_b/` holds only `trading.toml`. `live_b` gets no `/archive/lol` mount, no `LOL_ARCHIVE_ROOT` and no `./data/lol/models` mount. STEP-007 edits `discovery.py`, so re-run this grep after it lands and confirm the `lol` guards are unchanged:

```bash
grep -n "LolLeagueFilter(\|load_canonical_league_whitelist\|\"lol\" not in self._archive_by_game\|archive_by_game = " \
  src/trader/discovery.py src/trader/wallet_host.py src/trader/host_resources.py src/trader/lol_league_filter.py
```

Write the result in `progress.txt` (text below).

### F2. Other files under `/app/config`

`session_config.TEMPLATE_PATH` and `shared/utils/engine_cadence.TEMPLATE_PATH` both read `/app/config/trading.toml`. The `./config_b:/app/config:ro` bind covers both. `dashboard/live_hub.py` reads `config/trading.toml` too, but that dashboard is A-only and is not part of B.

The backtest also calls `read_engine_cadence()`, and it reads A's `config/trading.toml`. So B's `[engine]` must equal A's `[engine]`, or live B quotes on a cadence the backtest never tested. The `config_b` test pins this.

### F3. The image has no `.env`, so a blank `*_B` value does not fall back to A's keys

- `env_value()` (`shared/utils/environment.py`) falls back to `BASE_DIR/.env` when the env value is empty.
- `Dockerfile.dockerignore` excludes `esports-trader/.env`, so `/app/.env` does not exist in the image. `pydantic` `Secrets` reads env first, then the missing `.env`.
- If a `*_B` variable is missing from the VPS `.env`, compose warns `The "PK_WALLET_B" variable is not set. Defaulting to a blank string.` and passes `PK=""`. The result:
  - Blank `PK` / `BROWSER_ADDRESS`: `require_live_wallet` raises `TradingDisabled`. B crash-loops and never trades. It fails closed.
  - Blank `POLY_BUILDER_*`: `has_builder_creds` is False. STEP-007's start check raises `TradingDisabled`. It fails closed.
  - Blank `TG_CHAT_ID_B`: `notify` has no chat, so **B trades without Telegram**. This does not fail closed. The STEP-009 runbook must check it (handoff below).

### F4. `test_trader_compose._service_blocks` cannot see `live_b`

`SERVICE_KEY = re.compile(r"^  ([a-z0-9-]+):", re.MULTILINE)` has no `_`. The line `  live_b:` does not match, so the whole `live_b` block is folded into `blocks["live"]`. Any text assertion on A's block would then see B's keys. Fix: `[a-z0-9_-]+`.

### F5. Shutdown time of B (why `stop_grace_period: 240s`)

`SIGTERM` → `run_daemon` cancels the main task → `WalletHost.teardown()`:

| phase | bound | source |
|---|---|---|
| latch closes (no new places) | 0 | `teardown` |
| worker `finally` → `_quiesce(False)`: cell cleared, so the core pulls both bids on the next quoter tick (≤ 2 s, the quoter still runs) | — | STEP-006 decision 15 |
| `wait_inflight()` on a threshold merge already in flight | **190 s** (`MERGE_CALL_TIMEOUT_S`), plus the `_chain_lock` wait and one `read_fresh_balances` RPC before it | `pair_merge.py` |
| `stop_quoter`, `cancel_entry_buys`, `cancel_market` | HTTP, ~1–5 s | `match_worker._quiesce` |
| fence | **20 s** (`FENCE_TIMEOUT_S`) | `match_worker._quiesce` |
| drain in-flight place/cancel | **5 s** (`DRAIN_TIMEOUT_S`) | `teardown` |
| `cancel_all`, `engine.shutdown()` | HTTP / WS close, a few s | `teardown` |

The bounded parts sum to 215 s. The unbounded HTTP/RPC parts are usually under 15 s, so the worst single-merge case is ~230 s. With `200s` (feature text), Docker would SIGKILL B before the fence and `cancel_all`. With `240s` there is about 10–25 s of slack.

A normal stop with no merge in flight takes ~25–30 s, like A. Docker waits only until the process exits, so the long grace costs nothing on a normal stop.

Cases that 240 s does not cover, accepted:
- **Two B maps, each with a threshold merge in flight at SIGTERM.** `_chain_lock` serializes them, so the worst case is ~2 × 190 s + 25 s. Both relayer calls would have to hang to the 190 s bound at the same moment. Both bids are already pulled within one quoter tick (cell cleared → REDUCE_ONLY; the latch blocks new places), so a SIGKILL at 240 s leaves no resting B bids. What is lost is the MERGED ledger row for a merge the chain may have done. That is the same state as STEP-005's `unknown` outcome.
- **SIGTERM during the final `merge_all()`.** `_finish_terminal → _quiesce(True)` is not shielded, so the cancel interrupts the merge at once. The grace period does not matter there. The guard is the runbook: B is restarted only when `summarize.py --two-sided --restart-check` says SAFE (STEP-009).

## Decisions

1. **`stop_grace_period: 240s`**, not the feature's `200s`. Reason: F5. A test pins `grace ≥ MERGE_CALL_TIMEOUT_S + FENCE_TIMEOUT_S + DRAIN_TIMEOUT_S`. If someone later raises one of those bounds, the test fails, and nobody needs a YAML comment to explain the number. A's `60s` does not change.
2. **"9 × $20 = $180" is not a limit B enforces, and `config_b` says so instead of pretending.**
   - `requote_two_sided` ignores `state.budget` (STEP-006 decision 2). The clip becomes `TwoSidedPolicy.level_usdc`, and B's map room shows only as `state.budget.cap_room_usdc`.
   - No knob is added. Enforcing it means a strategy code change, and a loss limit or programmatic cap is a Non-Goal.
   - What actually bounds a B map:
     - `NET_MAX_SHARES = 50` net shares (≤ $45 at the 0.90 band edge);
     - pair merges at ≥ $130 held;
     - the wallet's pUSD (~$190 per the runbook). The venue rejects BUYs past it.
   - The TOML comments say this. `progress.txt` tells the owner in one line.
   - The numbers stay as the feature lists them: clip 20, `base_size_usdc` 20, `account_cap_usdc` 100000.0.
3. **No `${VAR:?}` (required-variable) syntax for the `*_B` keys.** Compose interpolates the whole file for every command. A missing B key would then break `docker compose logs/restart/up live` for A, and `docker compose config --services` in any checkout without the B keys, such as a worktree with no `.env` (`test_docker_compose_config_services_stdout` would fail). Plain `${PK_WALLET_B}` fails closed at runtime (F3). The one key that does not fail closed, `TG_CHAT_ID_B`, gets a runbook check (STEP-009).
4. **`DOTA_TRADING_MODE: live` and `LOL_TRADING_MODE: "off"` are literals, not `${...}`.** A's `${DOTA_TRADING_MODE}` toggle must not switch B. To stop B, run `docker compose stop live_b`. `"off"` is quoted because YAML 1.1 parsers read a bare `off` as boolean false. Compose 2.31 keeps it a string, but the VPS version may differ.
5. **`live_b` is inserted right after the `live` block, before `paper`.** The diff is pure additions after line 50, and the `live` lines stay byte-identical. No YAML anchors: sharing `build`/`logging` through an anchor would mean editing the `live` block.
6. **`config_b/trading.toml` = `cp config/trading.toml` plus the listed value changes and four comment edits.** The `[profiles.lol-map]` and `[clips.lol]` tables stay. Reason: `diff config/trading.toml config_b/trading.toml` then shows exactly B's differences. `read_template` ignores tables for games that are not loaded, and STEP-007's start check rejects a LoL assignment for two-sided anyway.
7. **`[clips.dota] default` stays `5.0`.** For B the default clip cannot be reached: the STEP-007 whitelist drops every title that does not match a tier name. And the clip only feeds the unenforced `level_usdc` (decision 2).
8. **The `config_b` test goes in `tests/test_trader_session_config.py`**, the `read_template` test file that already imports `choose_clip` and `tomllib`. It checks the `ConfigTemplate` that `materialize_wallet_config_dir` writes out. That write is pinned for any template by `test_materialized_config_follows_the_real_template_and_loads`. It does not call `Config.load`, so it never parses the local `.env`.
9. **The `live_b` compose test is one new JSON test plus small edits to the existing ones.** It compares `live_b.environment` to an exact dict, which pins `${PK_WALLET_B}` and every other mapping in one assert. It checks binds by target. It checks that no other service has `DOTA_STRATEGY` or any `_B}` reference.

## Order of edits (paths relative to E)

### 1. `config_b/trading.toml` (new)

Write exactly this (it is `config/trading.toml` with the B edits applied):

```toml
[wallet]
# 0 EOA, 1 email/magic, 2 Gnosis Safe, 3 POLY_1271 deposit wallet.
# PK and BROWSER_ADDRESS stay in env, never here.
signature_type = 3

[engine]
debounce_ms = 100
quoter_tick_s = 2.0
catalog_refresh_s = 3600.0
reconcile_interval_s = 20.0

# account_cap is ours: session_config strips it before writing the fork config.
# daily_loss_kill stays, poly-maker reads it. Guards are 2 * account_cap / min
# entry. Wallet B: the two-sided core enforces neither cap (see profiles.dota-map).
[risk]
ws_stale_halt_s = 30.0
user_ws_blind_halt_s = 15.0
heartbeat_halt_failures = 3
max_order_error_rate = 0.25
account_cap_usdc = 100000.0
daily_loss_kill_usdc = 100000000.0

[profiles.dota-map]
# fair value
micro_levels = 3
flow_ewma_halflife_s = 30.0

# volatility
vol_short_halflife_s = 10.0
vol_long_halflife_s = 120.0

# sizing / inventory
# Wallet B: base_size_usdc 20 matches the $20 BLAST Slam clip, the map level unit.
# 9 x $20 = $180 is reported as map room but not enforced: the two-sided brakes
# are NET_MAX_SHARES, pair merges and the wallet's cash. A bid is ORDER_SHARES = 20
# shares (src/strategy/two_sided.py), not dollars. Pairs merge through the pUSD
# adapter, so poly-maker's merge_min_size is unused.
base_size_usdc = 20.0

# regimes
event_cooloff_s = 20.0
event_jump_ticks = 15
event_sweep_mult = 4.0
event_sweep_frac = 0.8
trend_flow_z = 1.5
trend_vol_ratio = 2.0

# lifecycle / exits
end_date_taper_days = 7.0
reduce_only_hours = 0.0
halt_before_hours = 0.0
exit_urgency_s = 300.0

[profiles.dota-oddin-map]
# Clip only. Other knobs copy dota-map. Independent Oddin tuning is a later full table.
base_size_usdc = 20.0

[profiles.lol-map]
# fair value
micro_levels = 3
flow_ewma_halflife_s = 30.0

# volatility
vol_short_halflife_s = 10.0
vol_long_halflife_s = 120.0

# sizing / inventory
# Same key set as dota-map. merge_min_size rides this profile clip.
# The live rung is [clips.lol]. q_max_usdc sits at the session guard floor.
base_size_usdc = 5.0

# regimes
event_cooloff_s = 20.0
event_jump_ticks = 15
event_sweep_mult = 4.0
event_sweep_frac = 0.8
trend_flow_z = 1.5
trend_vol_ratio = 2.0

# lifecycle / exits
end_date_taper_days = 7.0
reduce_only_hours = 0.0
halt_before_hours = 0.0
exit_urgency_s = 300.0

# The clip is the Polymarket title suffix after (BOx) -. A name is a
# case-insensitive substring of that suffix. Two hits take the smaller clip.
# Oddin does not read this table. A name also matches qualifiers that contain it.
# Wallet B: these tier names are also the title whitelist (DOTA_STRATEGY=two_sided).
[clips.dota]
default = 5.0
tiers = [
  { clip = 20.0, names = ["BLAST Slam"] },
]

[clips.lol]
default = 5.0
tiers = []

# Satellite feeds get their own table, keyed clips.<game>-<feed>. Tiers only:
# the satellite profile's base_size_usdc stays the default clip. clips.dota-oddin
# covers maps quoting off the Oddin feed; it never touches GRID maps.
[clips.dota-oddin]
tiers = [
  { clip = 20.0, names = ["BLAST Slam"] },
]
```

Then check:

```bash
diff config/trading.toml config_b/trading.toml
```

Expected hunks, and nothing else:
- `signature_type` 2 → 3;
- the last `[risk]` comment line;
- `account_cap_usdc` 30000.0 → 100000.0;
- the dota-map sizing comment and `base_size_usdc` 300.0 → 20.0;
- dota-oddin `base_size_usdc` 5.0 → 20.0;
- the added whitelist comment line;
- the `[clips.dota]` tiers (EPL 60 + BLAST 200 → BLAST 20);
- the `[clips.dota-oddin]` tiers (PARI 140 → BLAST 20).

Edge notes:
- Keep every float as a float literal (`20.0`, `100000.0`). `read_template` checks `type(value) is float`, so `20` would raise `TradingDisabled`.
- PARI Universe is dropped from `[clips.dota-oddin]` on purpose (Non-Goal: B whitelist is BLAST Slam only).
- `[clips.dota].tiers` must never be empty in `config_b`. STEP-007's rule is that an empty whitelist lets everything through, so an empty tier list would make B trade every tournament. The test below pins the tier list.
- "A name also matches qualifiers": BLAST Slam qualifier titles pass the whitelist too. The backtest's BLAST Slam set used the same substring rule. Accepted.

### 2. `compose.yaml`

**2a.** Insert this block between the blank line after the `live` block (current line 50) and `  paper:` (current line 51). Do not touch any `live` line.

```yaml
  live_b:
    build:
      context: ..
      dockerfile: esports-trader/Dockerfile
    init: true
    restart: unless-stopped
    command:
      [
        "uv",
        "run",
        "--frozen",
        "--no-dev",
        "python",
        "-m",
        "trader.orchestrator",
        "daemon",
        "--mode",
        "live",
      ]
    stop_grace_period: 240s
    environment:
      DOTA_ARCHIVE_ROOT: /archive/dota
      DOTA_TRADING_MODE: live
      LOL_TRADING_MODE: "off"
      DOTA_STRATEGY: two_sided
      STEAM_KEYS: ${STEAM_KEYS}
      TG_BOT_API_TOKEN: ${TG_BOT_API_TOKEN}
      TG_CHAT_ID: ${TG_CHAT_ID_B}
      PK: ${PK_WALLET_B}
      BROWSER_ADDRESS: ${BROWSER_ADDRESS_B}
      POLY_BUILDER_KEY: ${POLY_BUILDER_KEY_B}
      POLY_BUILDER_SECRET: ${POLY_BUILDER_SECRET_B}
      POLY_BUILDER_PASSPHRASE: ${POLY_BUILDER_PASSPHRASE_B}
      ODDIN_BRAND_TOKEN: ${ODDIN_BRAND_TOKEN}
      POLYGON_RPC: ${POLYGON_RPC}
    volumes:
      - /var/lib/polymarket-dota-archive:/archive/dota:ro
      - ./data/trader_live_b:/app/data/trader
      - ./data/new_model:/app/data/new_model:ro
      - ./src:/app/src:ro
      - ./config_b:/app/config:ro
      - ./.git:/app/.git:ro
    logging:
      driver: local
      options:
        max-size: "10m"
        max-file: "5"

```

(One blank line after the block, as between the other services.)

- No comment on `stop_grace_period`. The number is explained by the test (decision 1) and by `progress.txt`.
- `./data/new_model` stays. STEP-006 says B still loads the dota model for `MatchStart` provenance and the journal pin.
- `./.git` stays, for `git_commit` provenance, as in A.
- Not added: `LOL_ARCHIVE_ROOT`, `/archive/lol`, `./data/lol/models` (F1), `POLY_RELAYER_URL` (the fork default is the production relayer), `ALL_PROXY`/`HTTPS_PROXY` (A has none).

**2b.** `compress` command: append two tokens after `"/app/data/trader_paper",`:

```yaml
        "--root",
        "/app/data/trader_paper",
        "--root",
        "/app/data/trader_live_b",
      ]
```

**2c.** `compress` volumes: add after the `trader_paper` line:

```yaml
      - ./data/trader_paper:/app/data/trader_paper
      - ./data/trader_live_b:/app/data/trader_live_b
      - ./src:/app/src:ro
```

`compress_archives.scan_roots` logs `root_missing` for a missing root and does not crash. The short bind syntax also creates the host dir (`create_host_path: true`). B has no `core_trace.jsonl`, and the compressor only touches whitelisted files that exist. Its `wallet` dir is skipped (`WALLET_DIR_NAME`).

### 3. `tests/test_trader_compose.py`

Keep the existing file pragmas and helpers. Changes:

**3a. Imports** (top-level; ruff `PLC0415` bans function-level imports):

```python
from trader.engine_seams import DRAIN_TIMEOUT_S
from trader.match_worker import FENCE_TIMEOUT_S
from trader.pair_merge import MERGE_CALL_TIMEOUT_S
```

**3b. Constants:**

```python
SERVICE_KEY = re.compile(r"^  ([a-z0-9_-]+):", re.MULTILINE)
WALLET_ENV_KEYS = (
    "PK",
    "BROWSER_ADDRESS",
    "POLY_BUILDER_KEY",
    "POLY_BUILDER_SECRET",
    "POLY_BUILDER_PASSPHRASE",
)
LIVE_B_ENVIRONMENT = {
    "DOTA_ARCHIVE_ROOT": "/archive/dota",
    "DOTA_TRADING_MODE": "live",
    "LOL_TRADING_MODE": "off",
    "DOTA_STRATEGY": "two_sided",
    "STEAM_KEYS": "${STEAM_KEYS}",
    "TG_BOT_API_TOKEN": "${TG_BOT_API_TOKEN}",
    "TG_CHAT_ID": "${TG_CHAT_ID_B}",
    "PK": "${PK_WALLET_B}",
    "BROWSER_ADDRESS": "${BROWSER_ADDRESS_B}",
    "POLY_BUILDER_KEY": "${POLY_BUILDER_KEY_B}",
    "POLY_BUILDER_SECRET": "${POLY_BUILDER_SECRET_B}",
    "POLY_BUILDER_PASSPHRASE": "${POLY_BUILDER_PASSPHRASE_B}",
    "ODDIN_BRAND_TOKEN": "${ODDIN_BRAND_TOKEN}",
    "POLYGON_RPC": "${POLYGON_RPC}",
}
LIVE_B_BIND_SOURCES = {
    "/archive/dota": "/var/lib/polymarket-dota-archive",
    "/app/data/trader": "/data/trader_live_b",
    "/app/data/new_model": "/data/new_model",
    "/app/src": "/src",
    "/app/config": "/config_b",
    "/app/.git": "/.git",
}
```

**3c. `test_compose_commands_set_mode_per_service`:** add

```python
    assert _mode_after_flag(_command_tokens(blocks["live_b"])) == "live"
```

**3d. `test_docker_compose_config_services_isolated_state`:**
- `assert names == {"live", "live_b", "paper", "compress"}, sorted(names)`.
- Replace the paper/compress key loop body with:

```python
    for keys in (paper_keys, compress_keys):
        for key in (*WALLET_ENV_KEYS, "LIVE_TRADING"):
            assert key not in keys, sorted(keys)
```

- Compress: `assert compress_targets >= {"/app/data/trader_live", "/app/data/trader_paper", "/app/data/trader_live_b"}`, plus:

```python
    compress_command: object = compress.get("command")
    assert isinstance(compress_command, list)
    assert "/app/data/trader_live_b" in compress_command
```

- Leave the rest (`live`/`paper` state binds, model mounts) as it is.

**3e. `test_docker_compose_config_services_stdout`:** expected set `{"live", "live_b", "paper", "compress"}`.

**3f. New helper and test** (no docstrings):

```python
def _binds_by_target(service: object) -> dict[str, dict[str, object]]:
    assert isinstance(service, dict)
    volumes: object = service.get("volumes") or []
    assert isinstance(volumes, list)
    binds: dict[str, dict[str, object]] = {}
    for volume in volumes:
        assert isinstance(volume, dict)
        binds[str(volume["target"])] = volume
    return binds


def test_docker_compose_config_live_b_is_wallet_b() -> None:
    services: object = _load_compose_config().get("services")
    assert isinstance(services, dict)
    live_b = services["live_b"]
    assert isinstance(live_b, dict)
    assert live_b.get("environment") == LIVE_B_ENVIRONMENT
    binds = _binds_by_target(live_b)
    assert set(binds) == set(LIVE_B_BIND_SOURCES)
    for target, source_suffix in LIVE_B_BIND_SOURCES.items():
        assert str(binds[target]["source"]).endswith(source_suffix), target
        assert (binds[target].get("read_only") is True) == (target != "/app/data/trader"), target
    grace_s = int(str(live_b.get("stop_grace_period")).removesuffix("s"))
    assert grace_s >= MERGE_CALL_TIMEOUT_S + FENCE_TIMEOUT_S + DRAIN_TIMEOUT_S
    for name, service in services.items():
        if name == "live_b":
            continue
        assert "DOTA_STRATEGY" not in _environment_keys(service), name
        assert "_B}" not in json.dumps(service), name
```

Notes:
- `docker compose config --no-interpolate --format json` prints `stop_grace_period` as `"240s"` and the environment as a dict. Both were checked on Compose 2.31 with a scratch file. The interpolated YAML form is `4m0s`, which is why the test uses the JSON.
- Keep `int(...)`. If someone writes `4m`, the parse fails loudly, which is the point.
- The last loop is what pins "`DOTA_STRATEGY` only in `live_b`" and "no B key leaks into A, paper or compress".

### 4. `tests/test_trader_session_config.py`

- Import changes:
  - `from shared.constants.paths import BASE_DIR`, before `from shared.constants.strategy ...`;
  - `from trader.clip_rules import choose_clip` becomes `from trader.clip_rules import ClipChoice, ClipTable, ClipTier, choose_clip`.
- New test (no docstring), next to `test_read_template_satellite_clips_default_to_profile_clip`:

```python
def test_config_b_is_a_deposit_wallet_on_blast_slam(monkeypatch: pytest.MonkeyPatch) -> None:
    config_a = tomllib.loads(session_config.TEMPLATE_PATH.read_text(encoding="utf-8"))
    monkeypatch.setattr(session_config, "TEMPLATE_PATH", BASE_DIR / "config_b" / "trading.toml")
    document = session_config.read_template(DOTA)
    blast_slam = (ClipTier(20.0, ("BLAST Slam",)),)
    assert document.wallet == {"signature_type": 3}
    assert document.engine == config_a["engine"]
    assert document.account_cap_usdc == 100000.0
    assert document.profiles["dota-map"]["base_size_usdc"] == 20.0
    assert document.profiles["dota-oddin-map"]["base_size_usdc"] == 20.0
    assert document.clips == {
        "dota-map": ClipTable(5.0, blast_slam),
        "dota-oddin-map": ClipTable(20.0, blast_slam),
    }
    title = "Dota 2: Team Falcons vs Tundra Esports (BO3) - BLAST Slam IV Group Stage"
    assert choose_clip(document.clips["dota-map"], title) == ClipChoice(20.0, "BLAST Slam")
```

- `config_a` is read **before** the monkeypatch, so it is A's committed file. The `engine` equality is F2's backtest/live cadence parity.
- `document.clips ==` pins the whitelist source: one tier, one name, never empty.
- `tests/test_dota_map_config.py` needs no change. It must still pass, which proves `config/trading.toml` is untouched.

### 5. `progress.txt` (in W) and `feature.json`

Append (adjust the hash):

```
## <date> - STEP-008

- `config_b/trading.toml` (A's file + signature_type 3, account_cap 100000, $20 clips, BLAST Slam only) and service `live_b` in `compose.yaml`. `compress` also scans `data/trader_live_b`. The `live` block and `config/trading.toml` are unchanged. Commit `<hash>`. `passes` left false for review.
- LoL check: with `LOL_TRADING_MODE=off` the host reads neither `config/lol_league_whitelist.json` nor the LoL archive nor `data/lol/models`. `assigned_games` gives `(dota,)`. `archive_by_game`, `LolLeagueFilter` (discovery.py:195, wallet_host `_sync_lol_book_watch`) and `_load_strategy_models` follow it. `config_b` has only `trading.toml`, and `live_b` has no LoL mounts.
- **Learnings for future iterations:**
  - Decision 1: `stop_grace_period: 240s`, not 200 s. One in-flight merge (190 s) + cancel + fence 20 s + drain 5 s + cancel_all ≈ 230 s. A test pins grace ≥ MERGE_CALL_TIMEOUT_S + FENCE_TIMEOUT_S + DRAIN_TIMEOUT_S. Two maps merging at once, or SIGTERM during the final merge, are not covered. Guard: restart-check SAFE.
  - Decision 2 (owner): 9 × $20 = $180 is shown as map room but not enforced. B's real brakes are NET_MAX_SHARES 50, merges at $130 held, and the wallet's pUSD.
  - Decision 3: no `${VAR:?}`. A missing B key would break every compose command for A. A blank PK / builder key fails closed at start. A blank TG_CHAT_ID_B does not: the runbook must check it.
  - `test_trader_compose.SERVICE_KEY` now allows `_`. Before, `live_b` was folded into `blocks["live"]`.
  - `config_b` [engine] must equal `config` [engine]: the backtest reads A's file through `read_engine_cadence`.
```

Leave STEP-008 `passes: false`. The orchestrator flips it after review (the convention in `progress.txt`).

## Edge cases and how this step handles them

| case | result |
|---|---|
| `*_B` var missing on the VPS | compose warns, passes `""`. PK/BROWSER/builder: `TradingDisabled`, crash-loop, no trading. TG: B trades silently. Runbook check (STEP-009) |
| A's `DOTA_TRADING_MODE=off` | B unaffected (literal `live`) |
| `config_b` mount missing | B would read the image's `/app/config` (A's file, sig type 2). STEP-007's `signature_type == 3` check refuses to start |
| Empty `[clips.dota].tiers` in `config_b` | STEP-007 would let every tournament through. The test pins one BLAST Slam tier |
| `live_b` text block parsed by the old regex | fixed (F4) |
| `docker compose config --services` in a worktree without `.env` | blank-string warnings only. Passes (decision 3) |
| `data/trader_live_b` missing on first `up` | bind creates it (as root). Runbook still creates it empty first |
| SIGTERM with a threshold merge in flight | ≤ ~230 s, inside 240 s (F5) |
| Two maps merging at SIGTERM / SIGTERM during the final merge | not covered by grace. Bids already pulled. Restart-check guard (F5) |
| `docker compose up -d` with no service names | also starts `live_b`. The runbook always names services |

## Risks to watch (not code in this step; for the STEP-009 runbook)

- B shares `POLYGON_RPC`, `ODDIN_BRAND_TOKEN`, `STEAM_KEYS` and `TG_BOT_API_TOKEN` with A.
  - Steam adds one `GetLiveLeagueGames` call per 30 s discovery cycle. That is negligible.
  - The RPC gets B's chain reads (reconcile every 20 s, merges).
  - Oddin may get a second WS session on the same brand token if both pick Oddin for one BLAST Slam map.
  - After B starts, watch A's log for RPC 429s and Oddin reconnects.
- B is a second Python process with the dota models loaded. Check `docker stats --no-stream` after the first start. The VPS OOM-killed A before.

## What STEP-009 needs from this step

- Names:
  - service `live_b`;
  - container `esports-trader-live_b-1` (project name `esports-trader`);
  - image `esports-trader-live_b`, built by the first `up`;
  - host tree `/root/work/esports-trader/data/trader_live_b`, with the wallet db at `data/trader_live_b/wallet/live.db`;
  - config `config_b/trading.toml`.
- `summarize.py --root /root/work/esports-trader/data/trader_live_b --two-sided` reads the host tree, not the container.
- Deploy: `docker compose up -d --no-deps live_b compress`.
  - `compress` is recreated because its command and volumes changed.
  - `live` is not recreated, because its definition is unchanged. Check `docker inspect -f '{{.State.StartedAt}}' esports-trader-live-1` before and after.
  - Always name services. A bare `docker compose up -d` or `restart` includes `live_b`.
- Env check before `up`. It prints booleans only, never a value. Every entry must be `True`:

  ```bash
  docker compose config --format json live_b | python3 -c 'import json,sys; env=json.load(sys.stdin)["services"]["live_b"]["environment"]; print({k: bool(v) for k, v in env.items()})'
  ```

  Also check that `docker compose config --services` prints no `variable is not set` warning for a `*_B` name.
- Stop / rollback: `docker compose stop live_b`.
  - It waits up to 240 s if a merge is in flight; otherwise ~25–30 s.
  - `restart: unless-stopped` keeps it stopped across reboots.
- Logs: `docker compose logs --tail 200 -f live_b`. `compress` logs now list three roots.
- A missing PK / builder key shows as a `TradingDisabled` crash loop in `logs live_b`. A missing `TG_CHAT_ID_B` shows as no Telegram lines.
- Do not stop or restart B while a session is open (F5). Use `--two-sided --restart-check`.

## Do not touch

- The `live` and `paper` blocks of `compose.yaml`; `config/**`; `Dockerfile`, `Dockerfile.dockerignore`, `Makefile`.
- `src/**`: this step is config + compose + tests only. `summarize.py` and `SKILL.md` are STEP-009.
- `tests/test_dota_map_config.py`; every other test file except the two named above.
- `../poly-maker`. Do not stage the untracked `data/backtests/dota_maker/validation_*ts-regress-*` dirs.

## Verification (in E)

```bash
cd /Users/dimabytes/work/polymarket/dota_2_bot/esports-trader

# compose: services and B's resolved shape (no secret values printed)
docker compose config --services            # live, live_b, paper, compress (any order)
docker compose config --no-interpolate --format json | python3 -c '
import json, sys
s = json.load(sys.stdin)["services"]
b = s["live_b"]
print(b["stop_grace_period"], b["environment"]["DOTA_STRATEGY"], b["environment"]["PK"])
print(sorted(v["target"] + ("" if v.get("read_only") else " rw") for v in b["volumes"]))
print(s["compress"]["command"][-4:])
'
# expect: 240s two_sided ${PK_WALLET_B}; six targets, only /app/data/trader rw; trader_paper + trader_live_b roots

# A is byte-identical (both must print nothing)
git diff ac475db2 -- config/trading.toml
diff <(git show ac475db2:compose.yaml | awk '/^  live:$/{f=1;next} /^  [a-z_]+:$/{f=0} f') \
     <(awk '/^  live:$/{f=1;next} /^  [a-z_]+:$/{f=0} f' compose.yaml)

# B's config differs from A's only where intended
diff config/trading.toml config_b/trading.toml

# step tests (serial; PYTEST_N unset)
PYTHONPATH=src:scripts:../prediction-market-backtesting uv run --group backtest python -m pytest \
  tests/test_trader_compose.py tests/test_dota_map_config.py tests/test_trader_session_config.py -q

# no comments/docstrings in new test code (expect only pre-existing lines; review the hunks)
git diff -U0 tests/ | grep -E '^\+.*(#|""")' | grep -v '# pyright:'

# typecheck (strict, whole project) and lint on staged files
uv run python -m basedpyright
git add config_b/trading.toml compose.yaml tests/test_trader_compose.py tests/test_trader_session_config.py
make lint
```

Expected:
- Everything is green. basedpyright reports 0 errors.
- `check-yaml` and `check-toml` in `make lint` cover `compose.yaml` and `config_b/trading.toml`. If `make lint` reformats or re-sorts imports, re-stage and run it again.
- `docker compose config --services` prints `variable is not set` warnings only in a checkout whose `.env` lacks the `*_B` names. The local `.env` has them, so expect none here.
- No command here builds, starts or runs a container, or touches the network.

Then:
- One commit in E on `main`. Suggested message: `Add wallet B: config_b and the live_b compose service for the two-sided trader.`
- `progress.txt` entry as in section 5. `passes` stays false.
- No push.
