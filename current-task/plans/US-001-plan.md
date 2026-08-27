# US-001 Implementation Plan

Story: **US-001** — «Зависимость kalshi-sdk, env-режимы и таблица [kalshi]»

Repo: `/root/work/dota_2_model` on `main`. This story is config/deps foundation only. Do **not** implement anything in `/root/work/poly-maker` (frozen). Do **not** rebuild Docker (US-015). Do **not** import `kalshi` from production live-paper modules (US-002 owns `kalshi_client.py` as the sole SDK boundary).

`progress.txt` / `learnings.txt` do not exist yet.

---

## Verified SDK pin

Checked 2026-08-26 against PyPI `https://pypi.org/pypi/kalshi-sdk/json` and the 12.0.0 wheel RECORD:

| Item | Value |
|---|---|
| PyPI name | `kalshi-sdk` |
| Latest version | **12.0.0** (uploaded 2026-08-16; no newer release) |
| Pin | `"kalshi-sdk==12.0.0"` in `[project].dependencies` |
| Import name | **`kalshi`** (top-level package in the wheel; `from kalshi import KalshiClient`) |
| Requires-Python | `>=3.12` (we are 3.13) |
| Direct deps | `cryptography>=43,<51`, `httpx>=0.27,<1`, `pydantic>=2.4,<3`, `typing-extensions>=4.5`, `websockets>=14,<18` |

`cryptography` is new to this lock. `httpx`, `pydantic`, and `websockets==15.0.1` are already in `uv.lock` and satisfy the ranges. No extras (`pandas`/`polars`/`http2`/`all`).

Precedent for exact `==` in this repo: `nautilus_trader[polymarket,visualization]==1.226.0` in the backtest group. Resolved Question: pin `kalshi-sdk` with `==`, stricter than the usual `[project]` lower bounds.

If PyPI has a newer version at implement time, pin **that** latest with `==` and record it; do not use `>=`.

Do **not** run bare `uv add kalshi-sdk` — it writes a lower bound. Use either `uv add 'kalshi-sdk==12.0.0'` or a manual `pyproject.toml` edit plus `uv lock`.

The package must live in **`[project] dependencies`**, not a dependency group. The live-paper image runs `uv sync --frozen --no-dev` (`Dockerfile`); a group extra would not land in the image at US-015.

---

## Binding decisions (Resolved Questions / Non-Goals / overlay)

Cross-ref `current-task/feature.json` Resolved Questions and `docs/plans/kalshi-overlay.md` § Config and env.

1. **`KALSHI_TRADING` is a new env.** Do not extend `ExecutionMode` / `LIVE_TRADING` / `src/live_paper/trading_mode.py`. Polymarket paper/live stays as today.
2. **`[kalshi]` is parsed by our template reader and is not a poly-maker table.** `read_template` requires the exact table set. Materializers write only `engine`/`risk`/`wallet` into `config.toml` and the dota-map profile into `strategy.toml`. Copying `[kalshi]` into the generated fork files is a bug (fork `StrategyProfile` is `extra="forbid"`; the fork has no Kalshi schema).
3. **`base_size_usd` is the only Kalshi money knob and is not derived from `base_size_usdc`.** No `DOLLAR_MULTIPLES` for Kalshi. No USDC↔USD conversion.
4. **Keys: path only, never inline PEM.** Overlay and story name `KALSHI_KEY_ID` + `KALSHI_PRIVATE_KEY_PATH`. The upstream SDK also accepts `KALSHI_PRIVATE_KEY` (inline PEM) and `KalshiClient.from_env()`. This story must not read `KALSHI_PRIVATE_KEY`, must not call `from_env()`, and must not `Path.read_text` the PEM. Store the path string, not file contents. `TradingDisabled` messages are fixed labels (same as `require_live_wallet`) — do not interpolate key id, path, or any PEM blob.
5. **`off` / `observe` need no keys. `paper` / `live` require both key id and path** (empty string counts as missing). Overlay: observe is public REST matcher only; paper still needs a read-scoped key later because Kalshi WS handshakes authenticate — this story only enforces the env gate, it does not open WS.
6. **Default `KALSHI_TRADING` is `off`.** Unset / blank → `off`. Unknown token (including `1`, `true`, `PAPER`) → `TradingDisabled`. Exact lowercase tokens only.
7. **`KALSHI_SUBACCOUNT` default `0`.** Integer, non-negative. Invalid → `TradingDisabled`.
8. **No WalletHost / orchestrator / match_worker wiring in this story.** `load_kalshi_settings()` raising `TradingDisabled` *is* the startup-error contract; US-005 / US-013 will call it at boot. US-001 tests the loader, not the daemon.
9. **No `kalshi_client.py`, no Docker rebuild, no `docs/live-paper.md` edit** (US-002 / US-015).
10. **poly-maker is frozen.** Zero file changes there.

---

## Current code to reuse (do not reinvent)

| Existing | Reuse how |
|---|---|
| `shared.utils.common.env_value` | Same env/.env reader as `trading_mode.py` |
| `live_paper.session_types.TradingDisabled` | Fail-closed startup type already used for missing `PK` |
| `tests/test_live_paper_trading_mode.py` `_env_get` + `monkeypatch.setattr(..., env_value)` | Copy this isolation; do not rely on `os.environ` alone (`env_value` also reads repo `.env`) |
| `session_config._require_template_table` | Exact key set, exact `type()`, non-negative finite numbers |
| `session_config._toml_table_lines` | Test helper `_write_template` already uses it |
| `tests/conftest.py` `force_paper_trading_flag` | Same autouse pattern for `KALSHI_TRADING=off` so pytest never inherits operator `.env` |

`ConfigTemplate` is constructed in one place (`read_template`). Adding a field is safe; nothing else builds it by hand.

---

## Design

### 1. `src/live_paper/kalshi_config.py` (new)

```python
KalshiTradingMode = Literal["off", "observe", "paper", "live"]

@dataclass(frozen=True)
class KalshiSettings:
    trading: KalshiTradingMode
    key_id: str | None
    private_key_path: str | None
    subaccount: int

def load_kalshi_settings() -> KalshiSettings:
    """Read Kalshi env. paper/live require key id and PEM path; never load PEM bytes."""
```

Rules inside `load_kalshi_settings` (no optional args; all imports at module top):

- `KALSHI_TRADING` via `env_value`; `None` or blank after strip → `"off"`. Else membership in `{"off", "observe", "paper", "live"}` or raise `TradingDisabled` with a **fixed** allowed-list message (do not echo the raw value).
- `KALSHI_SUBACCOUNT`: missing → `0`; else `int(...)`; `ValueError` or `< 0` → `TradingDisabled`.
- `KALSHI_KEY_ID` / `KALSHI_PRIVATE_KEY_PATH`: treat missing/`None`/blank as absent. If `trading in {"paper", "live"}` and either absent → `TradingDisabled("paper trading requires KALSHI_KEY_ID and KALSHI_PRIVATE_KEY_PATH")` (and the live equivalent). `off`/`observe` keep `None`s even if keys are present (storing present keys is allowed, not required).
- Never read `KALSHI_PRIVATE_KEY`. Never open the path. Default dataclass `repr` then cannot contain PEM contents; still assert that in tests.
- One- or two-line docstrings on the dataclass and the loader (AGENTS.md).

Do not import `kalshi` in this file.

### 2. `config/dota-map.toml` — new table at end of file

```toml
# Live-paper Kalshi only. session_config parses this table and does not copy it
# into the generated poly-maker config.toml / strategy.toml.
# base_size_usd is independent of profiles.dota-map.base_size_usdc.
[kalshi]
base_size_usd = 10.0
book_stale_s = 30.0
private_ws_blind_s = 15.0
reconcile_interval_s = 20.0
fence_timeout_s = 20.0
```

All five values are TOML floats (`10.0`, not `10`) so `_require_template_table` `type() is float` passes.

Update the existing comment above `base_size_usdc` that currently says it is the only money number in the file: it remains the only **Polymarket USDC** clip; Kalshi has its own USD clip in `[kalshi]`.

### 3. `src/live_paper/session_config.py`

- `_TEMPLATE_TABLES = frozenset({"engine", "risk", "profiles", "wallet", "kalshi"})`
- Error string becomes: must carry exactly the engine, risk, profiles, wallet and kalshi tables.
- New `_TEMPLATE_SCHEMA["kalshi"]` with the five float keys. Extra keys fail via existing `_require_template_table` (`set(table) != set(schema)`).
- New frozen dataclass `KalshiProfile` with those five fields (this is the “frozen dataclass профиля”; env settings stay in `kalshi_config.py`).
- `ConfigTemplate` gains `kalshi: KalshiProfile`. Update the class / `read_template` docstrings: five tables, not four; USDC limits still derive only from `base_size_usdc`; Kalshi size is not in that cascade.
- After validating the `kalshi` dict, `KalshiProfile(base_size_usd=..., ...)` (or `KalshiProfile(**cast(dict[str, float], kalshi_table))` once the schema pin has run).
- **`materialize_config_dir` / `materialize_wallet_config_dir` / `_write_config_toml` / `_write_strategy_toml` must not mention or serialize `kalshi`.** They already only pass `document.engine/risk/wallet/profile`. Leave that as-is. Do not add `document.kalshi` to any generated file.
- Do not add Kalshi keys to `DOLLAR_MULTIPLES` or to `document.profile`.

### 4. `tests/conftest.py`

Add an autouse fixture next to `force_paper_trading_flag`:

`monkeypatch.setenv("KALSHI_TRADING", "off")`

Operator `.env` already has `LIVE_TRADING=1`; the same landmine will exist once `KALSHI_TRADING` is set. Tests that need other modes patch `live_paper.kalshi_config.env_value` (not merely `setenv`), matching `test_live_paper_trading_mode.py`.

### 5. Existing tests that assume four tables (will go red until updated)

`tests/test_dota_map_config.py`

- Module docstring / “only money number” wording: two clips, two currencies, no derivation.
- `test_template_splits_and_profile`: `set(parsed) == {"engine", "risk", "profiles", "wallet", "kalshi"}`.
- New `EXPECTED_KALSHI` dict with the five committed floats; assert raw types are `float`, and `read_template().kalshi` matches. Do **not** feed this table to `StrategyProfile` / `RiskConfig` / `EngineConfig`.

`tests/test_live_paper_session_config.py`

- `_write_template` must emit `[kalshi]` when `template` has that key. Today it only writes wallet/engine/risk/profiles. After the committed file gains `[kalshi]`, every test that `tomllib.loads` the real template and rewrites via `_write_template` will otherwise drop the table and `read_template` will raise.
- `test_template_validation_rejects_foreign_tables` match string: include `kalshi`.
- `test_template_schema_rejects_typo_keys_and_bad_literals`: add `("kalshi", "not_a_real_key", 1.0)` and optionally `("kalshi", "base_size_usd", -1.0)`.
- `test_materialized_config_follows_the_real_template_and_loads` and `test_materialized_values_follow_a_modified_template`: after parse, `assert "kalshi" not in generated_config` and `not in generated_strategy` (and markets). Changing `base_size_usdc` to 70 must leave `read_template().kalshi.base_size_usd == 10.0`.
- Optional lock: add `"kalshi"` and `"base_size_usd"` to the token list in `test_materializer_has_no_tuning_literals` so the write helpers cannot start serializing the table.

`tests/test_live_paper_wallet_host.py` — `test_materialize_wallet_config_dir_is_empty_markets`: `assert "kalshi" not in generated`.

---

## Ordered implementation steps

Work in `/root/work/dota_2_model`. One story, smallest diffs, no new abstractions.

### Step 1 — Pin and sync the SDK

1. Insert `"kalshi-sdk==12.0.0",` into `[project] dependencies` in `pyproject.toml` (alphabetically after `httpx`, before `lightgbm`).
2. `uv lock` then `uv sync` (or `make install`). Confirm `uv.lock` gained a `kalshi-sdk` 12.0.0 package and `cryptography`.
3. Verify: `uv run python -c "import kalshi"` exits 0.

### Step 2 — Env settings module + its tests

1. Create `src/live_paper/kalshi_config.py` as designed above.
2. Create `tests/test_live_paper_kalshi_config.py` (cases in Test plan).
3. Add the `KALSHI_TRADING=off` autouse fixture in `tests/conftest.py`.

### Step 3 — `[kalshi]` table + parser

1. Append the `[kalshi]` table to `config/dota-map.toml`; fix the `base_size_usdc` “only money number” comment.
2. Edit `src/live_paper/session_config.py`: `_TEMPLATE_TABLES`, schema, `KalshiProfile`, `ConfigTemplate.kalshi`, `read_template` validation + construction, docstring/error-string updates. Do not touch the `_write_*` materializers except to keep them kalshi-free.
3. Update `_write_template` and the existing assertions listed above.
4. Extend `test_dota_map_config.py` / session-config / wallet-host tests as listed.

### Step 4 — Quality gate

See Verification. `make lint-all` skips untracked files (`AGENTS.md`): `git add` the new Python files in `dota_2_model` before lint, then re-run.

### Step 5 — Bookkeeping (after green)

- `dota_2_model` commit on `main` (implement-step skill). Message focuses on why: Kalshi env/TOML foundation so later stories have a pinned SDK and a table the fork never sees.
- Set `userStories` US-001 `passes: true` in `betting_workspace/current-task/feature.json`.
- Append `current-task/progress.txt` per the implement skill (do not replace).
- Do not commit `/root/work/poly-maker`. Do not docker compose.

---

## Test plan

### New: `tests/test_live_paper_kalshi_config.py`

Patch `live_paper.kalshi_config.env_value` with the `_env_get` helper from `test_live_paper_trading_mode.py`. Cases:

| Test | Setup | Expect |
|---|---|---|
| default off | mapping `{}` | `trading=="off"`, `key_id is None`, `private_key_path is None`, `subaccount==0` |
| four modes | `off` / `observe` (no keys); `paper` and `live` with both keys | `trading` matches; subaccount default 0 |
| observe without keys | `KALSHI_TRADING=observe` only | succeeds |
| paper without keys | `KALSHI_TRADING=paper` | `TradingDisabled`, match `KALSHI_KEY_ID` / `KALSHI_PRIVATE_KEY_PATH` |
| paper missing only path or only key id | one of the two set | same `TradingDisabled` |
| live without keys | `KALSHI_TRADING=live` | `TradingDisabled` |
| unknown token | `KALSHI_TRADING=true` (and `1`, `PAPER`) | `TradingDisabled` |
| subaccount | `KALSHI_SUBACCOUNT=3` | `3`; `abc` and `-1` raise |
| PEM never leaks | paper with key id + path; mapping also contains `KALSHI_PRIVATE_KEY` with a `-----BEGIN PRIVATE KEY-----` blob | `load_kalshi_settings()` succeeds; `repr(settings)` and `str(exc)` of a forced `TradingDisabled` contain no `BEGIN` / `PRIVATE KEY`; inline env var is ignored |

Do not add a pytest that imports `kalshi` unless ruff is happy with it; the import check is a verification command below.

### Extend: `tests/test_dota_map_config.py`

- Five-table split includes `kalshi`.
- `EXPECTED_KALSHI` values and float types.
- `read_template().kalshi` equals that profile.
- `base_size_usd` is not inside `document.profile`; `base_size_usdc` is not inside `document.kalshi`.

### Extend: `tests/test_live_paper_session_config.py`

- Unknown `[kalshi]` key → `TradingDisabled`.
- Generated `config.toml` / `strategy.toml` / `markets.toml` have no `kalshi` key/table.
- `base_size_usdc` change does not move `base_size_usd`.
- `_write_template` round-trips `[kalshi]` so other rewrite tests stay valid.

### Extend: `tests/test_live_paper_wallet_host.py`

- Process-wide materialized `config.toml` has no `kalshi`.

No browser. No network in tests. No SDK mock (this story does not call the SDK).

---

## Verification

Run inside `/root/work/dota_2_model`:

```bash
uv run python -c "import kalshi"
make test
git add src/live_paper/kalshi_config.py tests/test_live_paper_kalshi_config.py
make lint-all
```

Exact meaning:

- `make test` → `PYTHONPATH=src:scripts:../prediction-market-backtesting uv run --group backtest python -m pytest`
- `make lint-all` → `uv run python -m pre_commit run --all-files` (ruff check/format + basedpyright strict + trailing-whitespace / check-toml). New untracked files are skipped until `git add`.

All three must be clean. Do not treat a local `pytest tests/test_live_paper_kalshi_config.py` as sufficient — existing template tests are the regression surface.

---

## Risks / open points (none block US-001)

1. **Daemon does not yet fail at process start** if `KALSHI_TRADING=paper` with no keys, because this story does not call the loader from `WalletHost`. Intentional; wire-up is US-005/US-013. Tests cover the gate.
2. **`_write_template` omission is the likely red-suite cause** if Step 3 is split badly: add the table to the committed file and the parser in the same change as the test helper.
3. **`cryptography` wheels** must resolve on the VPS Python 3.13 slim image at US-015; US-001 only updates the lock. If lock/sync fails here, stop and report.
4. **SDK env-name overlap** (`KALSHI_KEY_ID`, `KALSHI_PRIVATE_KEY_PATH`, plus undocumented-for-us `KALSHI_PRIVATE_KEY` / `KALSHI_DEMO`). Our loader is independent. US-002 must not switch to `KalshiClient.from_env()` without a wrapper that still refuses inline PEM in logs.
5. **Do not put `[kalshi]` into generated fork TOML “just in case”.** That would break `Config.load` once a Kalshi key lands in the dota-map profile, and it is a Resolved Question.
6. Re-check PyPI latest at implement time; pin with `==` either 12.0.0 or a newer latest if one exists.

No Figma. No poly-maker patch. No new dependency other than `kalshi-sdk==12.0.0`.
