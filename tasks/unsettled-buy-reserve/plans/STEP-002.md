# STEP-002 — durable BUY reserve, shared budgets, compatible checkpoint

## Scope and baseline

Implement only STEP-002: B1 schema/helpers without production insertion sites, B4 budget integration, and B6 checkpoint/rollback work. STEP-001 is complete in `54b7ce50`; the next pending priority is STEP-002. Sources: `feature.json:75`, `feature.json:80`, `feature.json:98`, `progress.txt:14`.

Path convention: `src/…`, `tests/…`, `Makefile`, and config paths refer to `/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader`; `.shared-skills/…` refers to `/Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace`; task paths refer to this run directory. Original design: `/Users/dimabytes/.claude/plans/pasted-content-id-c855-vast-flute.md` (called `original-plan` below).

Read the latest working tree before implementing. At planning inspection, main included unrelated test fixes `f24b5d72` and `117c61d4`, and AGENTS.md, pyproject.toml, tests/conftest.py, uv.lock had pre-existing edits. Preserve them, avoid broad staging, and do not reset main to STEP-001. Evidence: `work/planner2/baseline.txt:1`, `work/planner2/baseline.txt:2`, `work/planner2/baseline.txt:6`; ownership rules: `00-context.md:11`, `00-context.md:12`.

The requested planning skill has been read fully; there are no extra `plan.instructions` in the workspace configuration. This plan specifies implementation and verification; the planner changes no code or deployment state. Sources: `/Users/dimabytes/.claude/skills/feature-json-create-step-plan/SKILL.md:8`, `/Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace/.feature-json.config.json:9`, `00-context.md:12`.

## Existing seams to preserve

- `WalletStateStore` creates the ledger before calling `migrate_core_schema`, then commits. Add the new table to that migration; no separate host initialization is needed. Sources: `src/trader/wallet_store.py:146`, `src/trader/core_persistence.py:212`.
- Core BUY reserve already excludes gone, and recovery already leaves gone intact while allowing recovery verification. Do not reimplement STEP-001. Sources: `src/strategy/budget.py:14`, `src/strategy/lifecycle.py:375`, `src/strategy/lifecycle.py:392`.
- Current wallet/map reserve combines core reserve, pending place-command reserve, and orphan store orders; held cost comes from wallet positions. Add the durable term to both reserve totals and retain the existing components. Sources: `src/trader/session_budget.py:29`, `src/trader/session_budget.py:39`, `src/trader/session_budget.py:52`, `src/trader/session_budget.py:66`.
- Checkpoint currently serializes status/reason literally and restore validates only pending/live/canceling/unknown; schema is 3. Encode the marker at the checkpoint boundary, without expanding that validator. Sources: `src/trader/core_persistence.py:26`, `src/trader/core_persistence.py:314`, `src/trader/core_persistence.py:379`, `src/trader/core_persistence.py:443`.
- Existing persistence helpers execute writes without committing individually; `persist_core_snapshot` owns its commit. Future HTTP insertion must compose with cancel outcome in one caller-owned transaction. Sources: `src/trader/core_persistence.py:578`, `src/trader/core_persistence.py:688`, `src/trader/core_session_io.py:28`, `src/trader/core_session_io.py:62`, `src/trader/wallet_host.py:458`; future requirement: `feature.json:134`.

## Implementation sequence

### 1. Schema and typed row API in core_persistence.py

Extend `_CORE_SCHEMA` beside core_commands with exactly:

```sql
CREATE TABLE IF NOT EXISTS unsettled_buys (
    venue_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    token_id TEXT NOT NULL,
    price REAL NOT NULL,
    qty REAL NOT NULL,
    proven INTEGER NOT NULL DEFAULT 0,
    resolved INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS unsettled_buys_resolved_session
    ON unsettled_buys(resolved, session_id);
```

Requirement/placement: `feature.json:84`, `src/trader/core_persistence.py:74`. Do not introduce a foreign key or join requiring a living session/core; the row must survive worker removal and restart (`feature.json:23`, `feature.json:87`).

Add a frozen `UnsettledBuy` dataclass with named fields for the row above, booleans for proven/resolved, floats for quantities/prices/times, and a small explicit row decoder. Return `tuple[UnsettledBuy, ...]` from the list helper; follow existing typed persistence row conventions and required arguments. Sources: `src/trader/core_persistence.py:180`, `src/trader/core_persistence.py:670`, `AGENTS.md:31`, `AGENTS.md:33`.

Public contracts to implement (all arguments required; conn passed explicitly):

| Helper | Contract |
|---|---|
| `insert_unsettled_buy(conn, *, venue_id, session_id, token_id, price, qty) -> None` | Use INSERT OR IGNORE. qty is the submitted total, never the current remaining size. Set both timestamps from one wall-clock reading; proven/resolved start at 0. A duplicate preserves the entire existing row, including proof, resolution and timestamps. |
| `prove_unsettled_buy(conn, venue_id, matched_qty) -> None` | Update only an existing unresolved row: qty becomes the terminal matched quantity, proven becomes 1, updated_at advances. Missing/resolved rows are no-ops. Repeat proof is harmless; never insert, reopen, or overwrite a resolved/manual-resolution row. |
| `resolve_settled_buys(conn) -> tuple[str, ...]` | Resolve all qualifying open rows across every session. Set resolved=1 and updated_at; retain qty and proven. Return only venue IDs newly resolved by this invocation, in deterministic order; subsequent calls return (). |
| `unsettled_buy_notional(conn, session_id: str &#124; None) -> float` | Sum each unresolved row's residual notional; None means all sessions, including missing/detached sessions. An empty table/result returns 0.0. |
| `open_unsettled_buys(conn) -> tuple[UnsettledBuy, ...]` | Return every unresolved row, proven and unproven, with creation/update times for STEP-003's aging/proof/alert work. Use deterministic ordering, e.g. created_at, venue_id. |
| `resolved_unsettled_buys(conn, session_id) -> dict[str, float]` | Return this session's resolved venue IDs mapped to retained qty, including zero; retain resolved rows for repeated delivery in STEP-003. |

Required functions and meanings: `feature.json:85`, `feature.json:86`, `feature.json:115`; original design: `original-plan:61`, `original-plan:67`, `original-plan:69`. Guarding resolved proof writes preserves the explicitly excluded reopening behavior (`feature.json:48`) and the later manual-zero resolution (`feature.json:141`).

None of these write helpers commits or opens a connection context manager that would commit the caller's work. Keep proof, resolution, and future insert/cancel transactions composable. Do not call them automatically from initialization, snapshot persistence, WalletHost or engine seams in this step. Sources: `feature.json:34`, `feature.json:48`, `src/trader/wallet_host.py:459`.

### 2. Ledger-derived arithmetic

Import `HALF_SHARE_TICK` directly from `shared.utils.trading`, avoiding a persistence → wallet_store → persistence cycle. The constant is 0.005; wallet_store already imports core_persistence. Sources: `src/shared/utils/trading.py:16`, `src/trader/wallet_store.py:18`, `progress.txt:7`.

For each venue ID, use ledger size sums with these exact predicates:

- booked: `maker_order_id = venue_id AND side = 'BUY' AND status IN ('MATCHED', 'CONFIRMED')`.
- final: `maker_order_id = venue_id AND side = 'BUY' AND status = 'CONFIRMED'`.
- unresolved reserve: `max(0.0, qty - booked) * row.price`.
- resolution: `final >= qty - HALF_SHARE_TICK`, with no additional proven requirement.

The aggregation key is maker_order_id, not clob_trade_id, token_id, fill_key prefix, outbox ack, or an in-memory inventory counter. Use COALESCE for missing fills. Apply the floor per row before summing, so an overfilled row cannot subsidize another row. Sources: `feature.json:86`, `original-plan:62`, `original-plan:66`, `src/trader/wallet_store.py:60`.

Prefer set-based SQL with one ledger aggregation/join per helper, shared status predicates, and the unresolved/session filter applied to the rows being counted. Avoid one SELECT per row and avoid materializing all sessions for a map-specific query. The new index must serve open-row/session filtering; preserve `_OPEN_BUY_WHERE` and its index unchanged. Sources: `original-plan:70`, `original-plan:76`, `src/trader/core_persistence.py:33`, `tests/test_trader_core_persistence.py:318`. Inspect EXPLAIN if the join would repeatedly scan the ledger; only add a narrowly justified maker_order_id index if that inspection shows a need, rather than adding unrelated migrations.

Use a conditional UPDATE that checks the qualifying final sum and returns newly updated IDs (or an equivalent operation entirely within the caller transaction). A stale candidate list must not be treated as newly resolved on repeat. This implements `feature.json:85` without adding transaction ownership to the helper.

MATCHED reduces reserve but leaves the row open. FAILED changes the ledger row's status, so a fresh budget read restores reserve automatically. SUPERSEDED, MINED, RETRYING, FAILED and SELL rows contribute to neither final nor booked as applicable; do not change ledger writers. Sources: `feature.json:24`, `src/trader/wallet_store.py:302`, `src/trader/wallet_store.py:375`, `src/trader/wallet_store.py:704`.

### 3. Shared budget integration

In `session_budget.py`, import `unsettled_buy_notional` and add:

- `_account_reserved`: `unsettled_buy_notional(store._conn, None)`.
- `_map_reserved`: `unsettled_buy_notional(store._conn, core.session_id)`.

The existing budget function then subtracts the account reserve from both spendable cash and account room, and the map reserve from this map's room. Include rows when their core is absent or not yet loaded. Sources: `feature.json:87`, `src/trader/session_budget.py:29`, `src/strategy/budget.py:23`.

Keep pending-command reserve and existing orphan-order exclusion unchanged. The single-count gone scenario needs a real venue binding, so its store order is excluded by existing core venue ownership; a detached terminal order has a durable row and no standing store order. Do not hide all ordinary orders behind the new table or modify cap/clip policy. Sources: `src/trader/session_budget.py:41`, `src/trader/session_budget.py:54`, `src/trader/session_core.py:409`, `feature.json:92`.

The durable row may coexist briefly with a still-canceling core before the future event is drained; this step's single-count assertion is after gone is applied. HTTP/WS transaction ordering and the no-reserve-gap integration test belong to STEP-004. Sources: `src/trader/session_core.py:908`, `feature.json:138`.

### 4. Checkpoint marker and old-code compatibility

Define exactly one production `UNSETTLED_CANCEL_REASON = "unsettled"` in core_persistence.py. It is a storage marker, not a new BlockReason or a cancellation policy. Sources: `feature.json:88`, `src/strategy/types.py:10`, `original-plan:136`.

At snapshot time, for a gone order only:

- Serialize status as unknown.
- Serialize cancel_reason as the constant.
- Preserve every other field, including ack_reason, filled_qty, submitted_qty, rung ownership and episode/inventory data.
- Do not mutate the in-memory order or add schema fields/version bumps.

At restore time, decode exactly unknown + reserved reason back into gone; ordinary unknown, pending, live and canceling keep their current meaning. Keep the marker as the restored cancel_reason; it remains reserved for subsequent checkpoint writes. Keep `_require_status`'s old four-status validation, so raw checkpoint status gone is still rejected. Use a small status-decoding helper if it improves clarity; do not broaden the format accepted by the generic validator. Sources: `feature.json:88`, `feature.json:89`, `src/trader/core_persistence.py:314`, `src/trader/core_persistence.py:369`, `src/trader/core_persistence.py:426`.

Leave `CORE_SCHEMA_VERSION = 3`, v1/v2 upgrades, inventory restoration and episode restoration unchanged. Gone survives both `load_core_snapshot` and subsequent Recovery because STEP-001 already skips it in `_mark_restored`. Sources: `src/trader/core_persistence.py:26`, `src/trader/core_persistence.py:242`, `src/trader/core_persistence.py:491`, `src/trader/core_session_io.py:65`, `src/strategy/lifecycle.py:375`.

For the old-code compatibility test, exercise the serialized checkpoint through the pre-STEP-002 order restoration behavior (four-status validation with no marker decoding), then restore the complete state. A focused test-only legacy restore adapter copied from the pre-step `restore_orders` body is sufficient; alternatively patch only a newly isolated status-decoding seam to use the unchanged old `_require_status(order.status)`. Do not claim compatibility by merely roundtripping through the new decoder or accepting gone in the old set. Avoid a pytest dependency on Git or copying an entire production module. Reference implementation: `src/trader/core_persistence.py:426` at `54b7ce50`; expectations: `original-plan:195`, `feature.json:89`.

### 5. Rollback notes in betting_workspace

Add a focused part-B rollback subsection to `.shared-skills/vps-trader/SKILL.md`, near the existing restart/rollback guidance (`.shared-skills/vps-trader/SKILL.md:125`, `.shared-skills/vps-trader/SKILL.md:153`).

Specify:

1. Before a separately authorized rollback, inspect the correct live wallet database with `SELECT COUNT(*) FROM unsettled_buys WHERE resolved=0;`.
2. If nonzero, wait for reconciliation or explicitly accept the previous cancellation behavior for those orders; old code ignores these money rows. Do not describe an unknown/marker checkpoint as a fresh core.
3. Version-3 checkpoints remain loadable by old code; old code sees unknown + unsettled and handles it as waiting for cancel. Inventory, episode and sell_only are retained.
4. Old replay tooling cannot decode CancelUnsettled/BuySettled traces; distinguish replay-tool incompatibility from checkpoint compatibility.
5. Steps 001–004 deploy together only after STEP-004 and a separate user command.

Requirement: `feature.json:90`, `feature.json:29`, `original-plan:138`, `original-plan:140`. The real wallet path is documented at `.shared-skills/vps-trader/SKILL.md:24`. No SSH, restart, actual rollback SQL mutation, manual-resolution procedure or incident note is performed here; the latter two are STEP-004 (`00-context.md:15`, `feature.json:141`, `feature.json:142`).

The documentation change belongs to the separate betting_workspace notes commit, not the STEP-002 esports-trader code commit (`feature.json:40`). The planner only specifies it; the implementer/orchestrator applies it.

## Automated verification to add

### Persistence: tests/test_trader_core_persistence.py

Use real WalletStateStore/ledger methods for supported status transitions; ledger is initialized before core schema (`tests/test_trader_core_persistence.py:110`, `tests/test_trader_core_persistence.py:383`, `src/trader/wallet_store.py:146`). Requirements below derive from `feature.json:91` and `original-plan:185`.

| Scenario | Expected assertions |
|---|---|
| Empty table; migration twice; close/reopen | Table/index exist, empty lists/maps and notional=0; persisted rows survive reopen. Check index columns resolved, session_id. |
| Initial qty=8 at price p; prove 8; MATCHED 8 | Reserve goes 8p → 0; row remains open, resolver returns (). |
| Same MATCHED becomes FAILED | Reserve returns to 8p without reopening/updating the durable row; it was never resolved. Position rollback uses the real ledger method. |
| MATCHED → CONFIRMED | No duplicate ledger size counted; resolver returns venue once, repeated resolver returns (); reserve=0 and resolved map keeps qty=8. |
| Insert 8; CONFIRMED 3; proof=5; second CONFIRMED 2 | Before proof reserve=5p; after proof reserve=2p; row resolves only after final total=5. |
| Proof zero with no fills | proven=1; reserve=0; resolver closes immediately, map includes venue:0.0. |
| Full CONFIRMED without proof | final covers submitted qty; resolves with proven still false, retained qty unchanged. |
| SUPERSEDED 8 | Reserve remains 8p and row stays open; produce it using the write-down watermark path. |
| Other venue and SELL ledger rows | Neither contributes to this BUY's booked/final; same trade across distinct maker_order_ids stays isolated. |
| booked exceeds qty in one row; second row still reserved | Overfill floors only the first row to zero; other reserve remains positive. |
| Duplicate insert after proof and after resolution | Original session, token, price, reduced qty, flags, created_at and updated_at are all preserved. |
| Proof for absent/resolved row; repeated proof/resolve | No implicit insert, no reopening or resolved qty rewrite; no duplicate returned IDs. |
| Multiple sessions, proven and unproven rows | Map filtering is correct; global/open/resolve APIs include all sessions without a core_sessions join. |
| Confirmation threshold | final within HALF_SHARE_TICK resolves; a whole 0.01 share shortfall remains open. |

FAILED then CONFIRMED on the same fill_key is already ignored by wallet_store; if testing later recovery, use a new real fill key rather than changing that unrelated ledger behavior. Source: `src/trader/wallet_store.py:307`.

Checkpoint coverage in the same test module:

- gone → snapshot → encode/decode → apply_checkpoint restores gone and rung.live_id; serialized order is unknown + the single reserved reason, version stays 3, and input state is unchanged.
- Preserve submitted/filled quantity, ack_reason, both token inventories/cost bases, active episode/counter/gross spend, archives and sell_only. Use inventory-backed held rung lots when needed so apply_checkpoint's existing orphan-inventory recovery rule does not confound the assertion.
- Ordinary unknown with an ordinary/empty reason stays unknown; ordinary statuses remain unchanged; raw gone status is rejected.
- `test_unsettled_reason_is_reserved`: constant is absent from get_args(BlockReason) and {"reprice", "kill", "recovery"}.
- `test_gone_checkpoint_loads_with_old_status_set`: legacy restoration yields unknown + unsettled, accepted by already_canceling, while restoring real inventory/episode/sell_only rather than empty_state.

Sources: `feature.json:89`, `src/trader/core_persistence.py:499`, `src/strategy/lifecycle.py:292`.

### Budget: tests/test_trader_shared_budget.py

Reuse `_core`, `_wallet`, `budget_from_orders` and existing quote fixtures (`tests/test_trader_shared_budget.py:88`, `tests/test_trader_shared_budget.py:151`, `tests/test_trader_shared_budget.py:165`).

- Seed an accepted BUY with a bound venue, an unsettled row, and apply CancelUnsettled explicitly. Assert core reserve=0, cash/account/map reserve reflects the durable row exactly once; other normal core orders remain counted.
- Remove map A's core while retaining its row. Map B's account room and cash decrease by A's reserve, while B's own map room is unaffected. Repeat after closing/reopening the store before A's core is loaded.
- Proof 0 releases cancellation reserve after resolution; partial proof + ledger reduces remaining reserve, while the booked portion moves into held cost. Assert account room reflects held+reserved, not a false release of all spent money.
- MATCHED → FAILED restores reserve and removes held cost; CONFIRMED closes the row but continues counting purchased inventory in cap usage.
- Same-session durable row contributes to map room even when absent from state.orders; another session contributes only globally.
- Empty unsettled table preserves existing budget behavior and unresolved-command/orphan accounting.

Requirements: `feature.json:87`, `feature.json:92`; arithmetic: `src/trader/session_budget.py:30`, `src/trader/session_budget.py:66`. Explicit fixture insertion is intentional; live creation is forbidden until STEP-004 (`feature.json:34`).

### Recovery: tests/test_trader_core_recovery.py

Build a valid active episode/rung/order record and venue binding; seed the table explicitly; persist with `persist_core_snapshot`, close/reopen store, instantiate a new LiveCore and `load_core_snapshot`. Assert restored gone retains the occupied rung and venue ID; emit Recovery, drain, and assert it is still gone. Durable notional survives independently of the core snapshot and is visible before restore. Sources: `feature.json:92`, `src/trader/core_session_io.py:28`, `src/trader/core_session_io.py:65`, `src/trader/session_core.py:525`.

Do not add resolved-row BuySettled-on-attach delivery yet; STEP-003 owns that behavior (`feature.json:115`).

## Commands and completion checks

Run from the esports-trader checkout, outside a live map. Use the current project venv via uv; do not use `make test`, add test dependencies, alter addopts, or use another agent's basetemp. These run-context instructions override the older generic make-test wording in feature.json. Sources: `00-context.md:19`, `00-context.md:21`, `00-context.md:23`, `AGENTS.md:21`.

Targeted verification after implementation:

```bash
PYTHONPATH=src:scripts:../prediction-market-backtesting uv run --group backtest --with pytest-xdist python -m pytest -n 10 --dist worksteal --basetemp=/tmp/pytest-implementer-step002 -q tests/test_trader_core_persistence.py tests/test_trader_shared_budget.py tests/test_trader_core_recovery.py tests/test_strategy_late_fills.py tests/test_core_trace.py tests/test_trader_session_core.py
```

Then the required broad local suite:

```bash
PYTHONPATH=src:scripts:../prediction-market-backtesting uv run --group backtest --with pytest-xdist python -m pytest -n 10 --dist worksteal --basetemp=/tmp/pytest-implementer-step002 -q tests -k "not current_policy_smoke"
```

Do not run or regenerate the slow backtest smoke goldens for this persistence-only step; its acceptance does not name them, and kernel/policy/replay behavior is unchanged by the scoped implementation. If scope actually expands into those areas, run the golden according to `00-context.md:25`, investigate drift and do not silently rewrite fixtures. Sources: `feature.json:94`, `00-context.md:24`, `00-context.md:25`.

Typecheck:

```bash
uv run python -m basedpyright
```

Use the project's actual basedpyright whole-project command so the configured exclusions apply. Sources: `.pre-commit-config.yaml:16`, `.pre-commit-config.yaml:18`, `.pre-commit-config.yaml:21`, `pyrightconfig.json:2`.

Review `git diff --check` and the file-specific diff. Stage only STEP-002 implementation/tests when the implement skill requires a commit, inspect `git diff --cached`, then run:

```bash
make lint
```

Rerun checks only when lint makes relevant edits or failures require a fix. make lint includes Ruff checks/format and basedpyright; it runs on staged paths. Sources: `Makefile:24`, `.pre-commit-config.yaml:1`, `.pre-commit-config.yaml:16`, `feature.json:95`.

Recheck main HEAD/status before recording results: unrelated fixes may advance the baseline. The progress log's old adapter/refill/smoke failures are historical, not permission to ignore any newly failing test; record the exact failing test, current HEAD, and whether it reproduces without STEP-002's changes. Preserve concurrent edits/commits. Sources: `progress.txt:11`, `work/planner2/baseline.txt:2`, `00-context.md:11`.

Final scope audit: code changes should be core_persistence.py, session_budget.py, and persistence/budget/recovery tests plus narrowly necessary fixtures. Production call-site search must find no new insertion/proof/resolution/event-delivery path; HTTP/WS activation, settled_buys in LiveSources, sync_inputs, wait_ms and own-book filtering remain for later steps. Sources: `feature.json:34`, `feature.json:108`, `feature.json:114`, `feature.json:134`, `feature.json:136`.

The implementer follows its own skill for the single STEP-002 main commit, and records exact changes/tests/limitations in progress.txt; workspace rollback notes follow the separate notes-commit rule. This planning task marks neither passes nor implementation completion. Push/deployment/SSH are excluded. Sources: `feature.json:40`, `00-context.md:12`, `00-context.md:14`, `00-context.md:15`.

No UI, browser/curl checks or Figma links are applicable: this step is SQLite/core budget persistence with an empty designReference (`feature.json:82`, `feature.json:101`).
