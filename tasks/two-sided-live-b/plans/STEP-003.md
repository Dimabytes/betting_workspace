# STEP-003: Ledger: `WalletStateStore.apply_merge` and status `MERGED`

Task: `W/tasks/two-sided-live-b/feature.json`, step STEP-003.
Code repo: `E = /Users/dimabytes/work/polymarket/dota_2_bot/esports-trader`, branch `main`.
Base: STEP-001 is `8dadf747`. STEP-002 (`src/strategy/**`, `src/backtest/two_sided_strategy.py`, `tests/test_strategy_two_sided.py`) touches no file this step touches.
No Figma, no UI.

Read `W/AGENTS.md` and `E/AGENTS.md` first. Rules that matter here: function names are verbs, arguments are required (keyword-only for the new public method), no `dict[str, Any]`, no branches for inputs that cannot occur. Do not narrate the code in comments. The review config says **new code gets no comments and no docstrings**. When a function changes, delete its docstring rather than rewrite it.

## Precondition

Start only after STEP-002 is committed. `git status --short` in E must list only the two untracked `data/backtests/dota_maker/validation_*ts-regress-*` directories. Reason: `make lint` runs pre-commit, which stashes unstaged changes while it runs and type-checks the whole project. If STEP-002's work is still uncommitted in the same tree, its files get stashed in the middle of the run and its errors show up in this step's check.

## Goal

A merge of N pairs is written to `fill_ledger` in one commit. That is two rows, `merge:{tx_hash}:{token_id}`, with status `MERGED`. Each row has size N and cash +0.5·N. Both positions drop by N and keep their avg. Each row gets one acked `merged` outbox row. A repeat with the same tx changes nothing. Every ledger reader counts MERGED in sizes and cash, and no reader treats a MERGED row as a BUY or SELL fill. Nothing in production calls `apply_merge` yet (PairMerger is STEP-005), so service A behaves exactly as before.

## Decisions (made without the owner, with reasons)

1. **The `side` column of a merge row is `"MERGE"`, not `"SELL"`.** A merge is not a trade. With `"SELL"`, any later `WHERE side='SELL'` reader would count merges as sells. Only two places parse `Side(row["side"])` from the ledger: `ledger_position` and `fill_for_key`. This step handles both on `status`. Every other SQL reader filters on `status IN (...)` or on `side='BUY'` (`core_persistence` booked/unsettled SQL, `dashboard/wallet.py` booked qty), so a `"MERGE"` row never reaches them. I checked this with `grep -rn fill_ledger src scripts`.
2. **Other merge row columns:** `price = 0.5`, `size = qty`, `ts = time.time()` at apply, `clob_trade_id = tx_hash`, `maker_order_id = ""`, `pre_size`/`pre_avg` = that token's store position before the merge, `cash_delta = 0.5 * qty`, `is_maker = 0`. Reasons: a merge has no venue order, so an empty `maker_order_id` can never join an `unsettled_buys.venue_id`. `is_maker = 0` because it is not a maker fill and must never feed a rebate count.
3. **The outbox row is inserted with `acked = 1`.** `pending_outbox` feeds the journal and risk replay (`WalletHost.drain_outbox`), and a merge is not a journal fill. `core_outbox_after` ignores `acked`, so the core still sees the row (STEP-004). This is the feature text.
4. **Positions are reduced with a clamp at 0, not a raise.** The merge already happened on chain when `apply_merge` runs. If the store holds less than `qty` (a MATCHED BUY failed while the relayer call ran), raising would lose a real on-chain event. The new size is `max(0, size − qty)`, and avg becomes 0 at size 0. Cash is still +qty, because the pUSD did arrive. Chain reconcile fixes any size drift as it does today. `ledger_position` replays merges with the same helper, so a restart or a FAILED rebuild gets the same answer.
5. **Idempotency check:** inside the transaction, `ledger_status` of the first leg's key. Both legs are inserted in one commit, so one key exists exactly when the other does. Calling with the same tx and the token order reversed hits the other leg's key, which also exists, and returns False.
6. **`apply_merge` calls `self.ensure_utc_day()` before the commit**, like `apply_matched_fill` and `_commit_confirmed_row`. The UTC-day row must be opened with the pre-change equity.
7. **After commit:** update `self.positions` for both tokens, call `note_chain_floor(token_id, now)` for both, and `self._net_cash += qty`. It does **not** call `_fill_credited_handler` (there is no `Fill`). `wrap_risk_from_ledger.evaluate` already copies `running_net_cash` on every evaluate. It does **not** call `_stamp_settle` either: it is not in the feature text, `_last_fill_ts` is a fill clock, and see edge case "stale REST" below.
8. **`fill_for_key` filters MERGED in SQL** (`AND status != ?`) and returns None for a merge key. It has no new branch. Callers: `drain_outbox` (never sees merge rows, because they are acked), `has_unacked_matched` (only `matched` events), `WalletHost` line ~1040 and `scripts/backfill_trader_fills.py` (CLOB trade ids only), and `consume_core_outbox` (see the STEP-004 handoff).
9. **`src/dashboard/summarize.py` `cmd_wallet`:** cash includes `'MERGED'`, so it matches `WalletStateStore.ledger_net_cash`. `fill_rows` excludes `'MERGED'`. The file stays stdlib-only. It currently reads only A's `live.db`. `--root` for B is STEP-009.
10. **Not changed, with reasons:**
    - `src/trader/core_session_io.py`: STEP-004 owns the `merged` branch.
    - `src/trader/core_persistence.py`: its SQL filters `side='BUY'`.
    - `src/dashboard/wallet.py` / `balance.py`: a `merged` outbox event goes to `BalanceTracker._apply`'s else branch. That branch counts an evidence gap and forces a balance re-read, which is correct after a merge. The B dashboard is a Non-Goal.
    - `scripts/backfill_trader_fills.py` `sqlite_cash`: it compares to CLOB trade cash, which has no merges.
    - `_rebuild_inflight_from_ledger`, `matched_keys_for_tokens`, `has_unacked_matched`: they already skip non-MATCHED/CONFIRMED rows. Tests prove it.
11. **Names.** `_format_merge_key`, `_subtract_merged_shares` and `_insert_merge_leg` are verbs, per the AGENTS rule. The siblings `_position_after_fill` and `_position_after_backfill` are older and are not renamed.
12. **No `merge_leg_for_key` reader in this step.** It has no caller until STEP-004 (YAGNI). See the handoff.

## Order of edits (all paths relative to E)

### 1. `src/trader/wallet_store.py` (927 lines now; it must stay under 1000)

a) Constants, right after `_LEDGER_SUPERSEDED = "SUPERSEDED"` (line ~57):

```python
_LEDGER_MERGED = "MERGED"
_MERGE_SIDE = "MERGE"
_MERGE_LEG_PRICE = 0.5
```

b) `ledger_position` (line ~225). Select `l.status` too, add MERGED to the filter, and branch on status. Delete its docstring.

```python
    def ledger_position(self, token_id: str) -> Position:
        rows = self._conn.execute(
            "SELECT l.status, l.side, l.price, l.size, l.ts FROM fill_ledger l "
            "JOIN (SELECT fill_key, MIN(seq) AS seq FROM fill_outbox GROUP BY fill_key) o "
            "ON o.fill_key = l.fill_key "
            "WHERE l.token_id=? AND l.status IN (?, ?, ?) ORDER BY o.seq",
            (token_id, _LEDGER_MATCHED, _LEDGER_CONFIRMED, _LEDGER_MERGED),
        ).fetchall()
        pos = Position(token_id, 0.0, 0.0)
        for row in rows:
            if row["status"] == _LEDGER_MERGED:
                pos = _subtract_merged_shares(pos, row["size"])
            else:
                replay = Fill(token_id, Side(row["side"]), row["price"], row["size"], "", row["ts"], is_maker=True)
                pos = _position_after_fill(pos, replay)
        return pos
```

(Let ruff-format wrap the `Fill(...)` call. Keep the existing multi-line layout.)

c) New public method `apply_merge`, placed right after `apply_failed_fill` (line ~484, before `running_net_cash`):

```python
    def apply_merge(self, *, tx_hash: str, token_ids: tuple[str, str], qty: float) -> bool:
        now = time.time()
        self.ensure_utc_day()
        merged: list[Position] = []
        with self._conn:
            if self.ledger_status(_format_merge_key(tx_hash, token_ids[0])) is not None:
                return False
            for token_id in token_ids:
                pos = self.position(token_id)
                new_pos = _subtract_merged_shares(pos, qty)
                self._insert_merge_leg(
                    key=_format_merge_key(tx_hash, token_id),
                    tx_hash=tx_hash,
                    pos=pos,
                    qty=qty,
                    ts=now,
                )
                self._write_position_row(new_pos)
                merged.append(new_pos)
        for new_pos in merged:
            self.positions[new_pos.token_id] = new_pos
            self.note_chain_floor(new_pos.token_id, now)
        self._net_cash += qty
        return True
```

`token_ids` is `(yes_token, no_token)`. A pair of token ids is the per-token pair pattern the repo already uses, not an anonymous multi-field value. `return False` inside `with self._conn:` commits nothing, the same as `apply_matched_fill`.

d) `running_net_cash` (line ~486): delete the docstring. It says "MATCHED+CONFIRMED", which is now false. The body does not change.

e) `ledger_net_cash` and `ledger_net_cash_for_tokens` (lines ~507–525): `status IN (?, ?, ?)` with `_LEDGER_MERGED` added to the parameters in both. Delete both docstrings (they say MATCHED and CONFIRMED only).

f) `fill_for_key` (line ~569): SQL `"... FROM fill_ledger WHERE fill_key=? AND status != ?"` with params `(key, _LEDGER_MERGED)`. Delete the docstring.

g) New private method right after `_insert_outbox` (line ~809):

```python
    def _insert_merge_leg(
        self, *, key: str, tx_hash: str, pos: Position, qty: float, ts: float
    ) -> None:
        self._conn.execute(
            "INSERT INTO fill_ledger("
            "fill_key, clob_trade_id, maker_order_id, token_id, side, price, size, ts,"
            " status, pre_size, pre_avg, cash_delta, is_maker)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                key,
                tx_hash,
                "",
                pos.token_id,
                _MERGE_SIDE,
                _MERGE_LEG_PRICE,
                qty,
                ts,
                _LEDGER_MERGED,
                pos.size,
                pos.avg_price,
                _MERGE_LEG_PRICE * qty,
                0,
            ),
        )
        self._conn.execute(
            "INSERT INTO fill_outbox(fill_key, event, acked) VALUES(?,?,1)",
            (key, "merged"),
        )
```

h) Module-level helpers, next to `_position_after_fill` (line ~907):

```python
def _subtract_merged_shares(pos: Position, qty: float) -> Position:
    new_size = pos.size - qty
    if new_size <= 0:
        return Position(pos.token_id, 0.0, 0.0)
    return Position(pos.token_id, new_size, pos.avg_price)


def _format_merge_key(tx_hash: str, token_id: str) -> str:
    return f"merge:{tx_hash}:{token_id}"
```

Do not change anything else in the file. `_rebuild_inflight_from_ledger`, `matched_keys_for_tokens` and `has_unacked_matched` stay as they are (decision 10).

### 2. `src/dashboard/summarize.py` `cmd_wallet` (line ~667)

```python
    cash = conn.execute(
        "SELECT COALESCE(SUM(cash_delta), 0) AS s FROM fill_ledger"
        " WHERE status IN ('MATCHED', 'CONFIRMED', 'MERGED')"
    ).fetchone()["s"]
    ...
    n_fills = conn.execute(
        "SELECT COUNT(*) AS n FROM fill_ledger WHERE status != 'MERGED'"
    ).fetchone()["n"]
```

Nothing else changes in this file. No new imports.

### 3. Tests: append to `tests/test_trader_wallet_store.py`

Add `from dashboard import summarize` to the imports. No docstrings on the new tests. Shared setup:

```python
MERGE_TX = "0xmerge1"


def _open_store_with_pair(db_path: Path) -> WalletStateStore:
    store = WalletStateStore(db_path)
    yes_key = fill_key("clob-y", "order-y")
    no_key = fill_key("clob-n", "order-n")
    yes_buy = Fill(YES_TOKEN, Side.BUY, 0.40, 30.0, yes_key, 10.0, is_maker=True)
    no_buy = Fill(NO_TOKEN, Side.BUY, 0.55, 20.0, no_key, 11.0, is_maker=True)
    assert store.apply_confirmed_fill(yes_buy, yes_key).applied_new is True
    assert store.apply_confirmed_fill(no_buy, no_key).applied_new is True
    return store
```

Cash before the merge: −12 − 11 = −23. After merging 20: −3.

Tests (exact expectations):

1. `test_apply_merge_moves_sizes_and_cash_once`
   - `apply_merge(tx_hash=MERGE_TX, token_ids=(YES_TOKEN, NO_TOKEN), qty=20.0) is True`.
   - Store and `ledger_position`: YES 10.0 @ 0.40, NO 0.0 @ 0.0.
   - `running_net_cash`, `ledger_net_cash()`: `approx(-3.0)`. `ledger_net_cash_for_tokens({YES_TOKEN})`: `approx(-2.0)`, `({NO_TOKEN})`: `approx(-1.0)`.
   - The YES leg row (`SELECT side, price, size, status, cash_delta, pre_size, pre_avg, is_maker, clob_trade_id, maker_order_id`): `("MERGE", 0.5, 20.0, "MERGED", 10.0, 30.0, 0.40, 0, MERGE_TX, "")`.
   - `SELECT event, acked FROM fill_outbox WHERE fill_key LIKE 'merge:%' ORDER BY seq` → `[("merged", 1), ("merged", 1)]`.
   - Snapshot the ledger row count, outbox row count, both positions and `running_net_cash`. Then `apply_merge` with the same tx returns False, the reversed `(NO_TOKEN, YES_TOKEN)` returns False, and every snapshot value is unchanged.
2. `test_merge_rows_stay_out_of_fill_readers`
   - After the merge: `fill_for_key(f"merge:{MERGE_TX}:{YES_TOKEN}") is None`.
   - `ledger_status` of that key is `"MERGED"`.
   - `matched_keys_for_tokens({YES_TOKEN, NO_TOKEN}) == ()`, `has_unacked_matched({YES_TOKEN, NO_TOKEN}) is False`.
   - `[item.event for item in store.pending_outbox()] == ["confirmed", "confirmed"]`.
   - The last two items of `core_outbox_after(after_seq=0, tokens=frozenset({YES_TOKEN, NO_TOKEN}))` have events `["merged", "merged"]` and fill keys `[merge:…:YES, merge:…:NO]` in that order.
3. `test_reopen_restores_positions_and_cash_after_merge`
   - Merge, `close()`, open a new `WalletStateStore` on the same file.
   - YES 10.0, NO 0.0, `running_net_cash == approx(-3.0)`, `inflight(YES_TOKEN) == 0`, `_last_fill_ts[YES_TOKEN] == 10.0` (the fill time, not the merge time; this exercises `_rebuild_inflight_from_ledger`).
4. `test_failed_fill_after_merge_replays_the_merge`
   - YES: CONFIRMED BUY 20 @ 0.40. MATCHED BUY 10 @ 0.45 under key `fill_key("clob-late", "order-late")` (`apply_matched_fill`). NO: CONFIRMED BUY 20 @ 0.55.
   - Merge 20. Then `apply_failed_fill(late_fill, late_key) is True`.
   - YES size 0.0 (replay 20 → merge 20; the failed 10 is gone). `running_net_cash == approx(1.0) == approx(ledger_net_cash())`. The arithmetic: −8 − 4.5 − 11 + 20, then the FAILED returns 4.5.
5. `test_merge_moves_the_chain_floor_for_both_tokens(tmp_path, monkeypatch)`
   - Patch `wallet_store.time.time` with a clock, as `test_chain_floor_moves_on_confirm_backfill_and_failed` does. Open and fill at 1_000.0, merge at 1_100.0.
   - `chain_read_floor(YES_TOKEN) == chain_read_floor(NO_TOKEN) == 1_100.0`.
6. `test_summarize_wallet_counts_merge_cash_not_fill_rows(tmp_path, monkeypatch, capsys)`
   - `_open_store_with_pair`, merge 20, `close()`.
   - `monkeypatch.setattr(summarize, "LIVE_WALLET_CANDIDATES", (db_path,))`, then `summarize.cmd_wallet()`.
   - The output contains `ledger_net_cash=-3.0000`, `fill_rows=2` and `nonzero_positions=1`.

## Edge cases

- **Repeat after success** (PairMerger retries the apply after a crash between commit and in-memory updates): impossible within one process, because there is no await. After a restart the in-memory state is rebuilt from the file, which already holds the merge. A repeat returns False.
- **Store holds less than qty** (FAILED during the relayer call): clamp to 0, credit full cash (decision 4). Chain reconcile corrects any size drift.
- **Stale chain read after the merge:** `note_chain_floor(now)` makes `_apply_chain_balance` ignore older blocks. `ledger_position` also includes the merge, so a ledger restore cannot undo it.
- **Stale REST positions after the merge:** `reconcile_positions` ignores a size-up larger than `max(1 share, 2%)`. A PairMerger merge is at least 5 pairs, so a stale REST read cannot undo it. A final `merge_all` under 1 share could be re-added by a stale REST read, but `zero_token_sizes` follows it at map end (STEP-006). Accepted, and no settle stamp is added.
- **Float residue:** `size − qty` can leave about 1e-6 shares. That is dust below `min_order_size`, the same as SELL replay today.
- **Day roll:** `ensure_utc_day` runs before the commit, so the new day's baseline is pre-merge equity.
- **Service A:** A never writes a MERGED row. Every SQL change only adds a status that is absent from A's file, so A's numbers are identical.

## What later steps need from this step

- **STEP-004:** each merge produces two outbox rows, `event == "merged"`, `acked = 1`, `fill_key = merge:{tx}:{token_id}`, in `token_ids` order. `core_outbox_after` returns them. `fill_for_key` returns **None** for them. Today `consume_core_outbox` does `fill = store.fill_for_key(...)` and then `if fill is None: return`. So until STEP-004 the core cursor stops at the first merged row. That is harmless now, because nothing calls `apply_merge`. STEP-004 must branch on `item.event == "merged"` **before** calling `fill_for_key`. It must also add a ledger reader, for example `WalletStateStore.merge_leg_for_key(key) -> MergeLeg | None` (a frozen dataclass with `token_id` and `qty`; `SELECT token_id, size FROM fill_ledger WHERE fill_key=? AND status='MERGED'`). The `size` column is the full merged qty, not clamped. `lifecycle.apply_merged` clamps at 0 itself.
- **STEP-005:** call `store.apply_merge(tx_hash=..., token_ids=(yes, no), qty=amount_raw / 1_000_000)` in the event loop with no await before it. True means newly applied. False means this tx is already in the ledger: still run `consume_core_outbox` and clear the in-flight flag, but do not send a second Telegram line. `apply_merge` does not wake the worker and does not touch the collateral cache; PairMerger does both.
- **STEP-006:** `MatchWorker.end_snapshot` uses `ledger_net_cash_for_tokens`, so the end-of-map net already includes merge cash. No extra code.
- **STEP-009:** (a) `summarize.py --today` folds Polymarket activity through `_FOLD_KINDS = {"TRADE", "REDEEM", "MAKER_REBATE"}`. If the data API reports adapter merges as type `"MERGE"`, B's day fold loses merged pairs from open marks with no matching cash, and day PnL comes out low. Check one B activity row on the VPS and add the kind if needed. (b) `.shared-skills/vps-trader/log-map.md` line ~138 says "SUM over MATCHED+CONFIRMED is wallet inventory". For B it is MATCHED+CONFIRMED+MERGED.

## Do not touch

`src/trader/core_session_io.py`, `src/trader/core_persistence.py`, `src/trader/wallet_host.py`, `src/dashboard/wallet.py`, `src/dashboard/balance.py`, `scripts/**`, `src/strategy/**`, `config/**`, `compose.yaml`, any existing test body, and `../poly-maker`. Do not stage the untracked `data/backtests/dota_maker/validation_*ts-regress-*` directories.

## Verification (in E)

```bash
cd /Users/dimabytes/work/polymarket/dota_2_bot/esports-trader

# step tests
PYTHONPATH=src:scripts:../prediction-market-backtesting uv run --group backtest python -m pytest \
  tests/test_trader_wallet_store.py -q

# every test that writes or reads fill_ledger / fill_outbox / consume_core_outbox
PYTHONPATH=src:scripts:../prediction-market-backtesting uv run --group backtest python -m pytest \
  tests/test_trader_core_persistence.py tests/test_trader_core_execution.py \
  tests/test_trader_core_recovery.py tests/test_trader_session_core.py \
  tests/test_trader_engine_seams.py tests/test_trader_trade_backfill.py \
  tests/test_backfill_trader_fills.py tests/test_trader_unsettled_buy_activation.py \
  tests/test_trader_unsettled_buy_recovery.py tests/test_trader_wallet_host.py \
  tests/test_trader_wallet_sidecar.py tests/test_trader_host_resources.py \
  tests/test_trader_live_execution.py tests/test_trader_match_lifecycle.py \
  tests/test_trader_shared_budget.py tests/test_dashboard.py \
  tests/test_dashboard_live_hub.py tests/test_dashboard_home.py -q

# summarize stays stdlib-only and its self-check still passes
python3 src/dashboard/summarize.py --self-check

# file size rule
wc -l src/trader/wallet_store.py   # must be < 1000

# only the three intended files changed
git status --short

# typecheck (strict, whole project) and lint on staged files
uv run python -m basedpyright
git add src/trader/wallet_store.py src/dashboard/summarize.py tests/test_trader_wallet_store.py
make lint
```

Expected: all green and `self-check ok`. `git status` shows only the three files plus the untracked backtest dirs. If `make lint` reformats a file, re-stage it and run `make lint` again. Leave `PYTEST_N` unset. A full serial suite is STEP-010's job, but run `make test` here too if time allows.

Then, per the implement skill:
- One commit in E on `main`. Suggested message: `Record pair merges in the wallet ledger as MERGED rows.`
- Set `passes: true` for STEP-003 in `W/tasks/two-sided-live-b/feature.json`.
- Append to `W/tasks/two-sided-live-b/progress.txt`. Record decisions 1, 4 and 8 there, plus the STEP-004 handoff about `fill_for_key` returning None for merge keys.
- No push.
