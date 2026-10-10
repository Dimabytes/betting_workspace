# STEP-001 plan: sign the size the CLOB client signs, close a BUY on a sub-cent remainder (A1 + A2)

Code: `esports-trader` `main` at `3d2394e1` (clean `git status`). Paths below are relative to `esports-trader/`. Client library (read-only, do not edit): `.venv/lib/python3.13/site-packages/py_clob_client_v2`, version 1.0.2. Planner scratch (copies of `src/` and `tests/`, probes, logs, reference diffs): `betting_workspace/tasks/sim-parity-fixes/orchestrate/work/plan-001/` (below: `work/plan-001/`).

No Figma links: the step has no `designReference`. `tasks/sim-parity-fixes/progress.txt` does not exist yet.

## 0. Result in one paragraph

The core stores and sends `share_floor(x)` as the order size. `py_clob_client_v2` signs `floor(size * 100) / 100` in float, so 4,586 of the 100,000 sizes 0.01…1000.00 reach the venue one or more cents smaller than the core remembers (17.08 → 17.07). After a full fill, the core sees a 0.01 remainder and keeps the BUY and its rung. A second bug: a remainder below one cent (fills 8.81356 + 11.18 on an order of 20) also keeps the BUY, because `_apply_known_fill` compares the floored filled and submitted sizes. The fix adds one function, `floor_to_signed_shares`, to `src/shared/utils/trading.py`. It is used at the three places where a size goes to the venue: `buy_share_quantity`, `_leg_target` and `_sell_quantity`. `_apply_known_fill` gets a one-line partial rule: partial only if `share_floor(submitted − filled) > 0.0`, which is the same as ≥ 0.01. One deliberate deviation from the step text: the step's formula `floor(share_floor(x) * 100) / 100` is not stable. For 1,160 of the 100,000 cents its result is itself a cent the client shrinks again (0.58 → 0.57, which the client signs as 0.56; at clip $200 / price 0.18, 1111.10 is signed as 1111.09). So the function repeats the client's round-down until the value stops changing: at most 4 extra steps, at most 0.04 shares below `share_floor`. Size: +15/−6 production lines in 4 files, +104/−5 test lines in 5 existing files (2 of the changed test lines are fixture values). Verified on a scratch copy: the 4 new behavior tests fail on HEAD and pass with the patch. The full suite without the golden replay has the same failure set as HEAD (environment-only, see §6). basedpyright reports 0 errors and ruff is clean.

## 1. What is broken today (evidence)

### 1.1 Size (A1)

- The client: `round_down(x, sig_digits)` = `floor(x * (10**sig_digits)) / (10**sig_digits)` (`py_clob_client_v2/order_builder/helpers.py:5-6`). `get_order_amounts` applies it to the BUY size (`order_builder/builder.py:77`) and the SELL size (`builder.py:87`). `ROUNDING_CONFIG` sets `size=2` for every tick size (`builder.py:36-43`), so the rule is the same on every market.
- Our path: the core plan's `PlaceOrder.quantity` becomes `Quote(size=place.quantity)` (`src/trader/session_core.py:863-872`). poly-maker `gateway.place` then calls `create_order` and stores the requested `q.size` in `OpenOrder` (`poly-maker/src/polymaker/execution/gateway.py:171-172`, `:199`; see `runs/2026-10-10-sim-followup/reports/livebugs-hold.md` §1).
- Core sizes come from `share_floor(x) = floor(x * 100 + 1e-6) / 100` (`src/shared/utils/trading.py:22-24`). The `+1e-6` keeps 17.08 as 17.08, but the client drops it: `17.08 * 100 == 1707.9999999999998` (`work/plan-001/check_signed_size.py`).
  - follow300 BUY: `buy_share_quantity` (`trading.py:14-16`), called through `_buy_qty` (`src/strategy/quoting.py:147-148`, used at `:295`).
  - two-sided BUY: `_leg_target` (`src/strategy/two_sided_quoting.py:101-108`).
  - SELL (follow300 exit and recovery): `_sell_quantity` (`src/strategy/quoting.py:462-466`), used by `_join_ask_sell` (`:474`), `_choose_sell_target` (`:495`) and `_sell_covers_inventory` (`:458`).
- Scale (probes `work/plan-001/check_fixed_point.py`, `find_cases.py`):
  - 4,586 of 100,000 cents (0.01…1000.00) are signed smaller than the core value.
  - follow300: number of prices 0.01…0.99 at which the clip size is cut: $5 → 4, $60 → 6, $140 → 4, $200 → 10, backtest $300 → 9.
  - two-sided: 206 of 3,001 net positions 0.00…30.00 (step 0.01; `NET_MAX_SHARES = 30`, `src/strategy/two_sided.py:8`) give a cut leg size.
  - Live B on 2026-10-08: 236 of 12,424 BUY quotes were cut, with 11 long holds of 60–80 s (`livebugs-hold.md` §1).
- HEAD reproduction in the core (probe `work/plan-001/find_cases2.py` on an unpatched copy):
  - follow300 L0 at 0.63 with clip $100 plans 158.73; the client signs 158.72.
  - two-sided leg at net 4.38 plans 17.08; the client signs 17.07.
- A live fixture in the repo already shows the bug. `tests/test_trader_session_core.py:926` expects 264.15 and `:952` expects 303.03. The live book in the same fixture lists the bot's own 0.66 bid at 303.02 (`:946`).

### 1.2 Sub-cent remainder (A2)

- The rule is `partial = share_floor(filled_qty) < share_floor(owner.submitted_qty)` (`src/strategy/lifecycle.py:687`). For fills 8.81356 + 11.18 on 20, `share_floor(19.99356) = 19.99 < 20.0`, so `partial` is true:
  - the order stays in `state.orders` (`lifecycle.py:704-705`);
  - `rung_occupied` keeps the rung busy (`lifecycle.py:250-253`), and `place_missing` skips a busy rung (`quoting.py:645-646`).
- Live: two such holds on 9034957701, 76.4 s and 62.2 s (`livebugs-hold.md` §3). Since `live-a-audit-fixes` STEP-004 the hold lasts until CONFIRMED instead of 60 s, but the BUY is still held.

## 2. Design decisions

1. **Signed size = the client's round-down repeated until it stops changing**, not the step's single `floor(share_floor(x) * 100) / 100`.
   - Why: FR-1 says the remembered size must equal the signed size, and the step asks for an idempotent function. The single formula fails both on 1,160 of 100,000 cents. Examples: 0.58 → 0.57, signed 0.56; 8.04 → 8.03, signed 8.02; 1111.11 → 1111.10, signed 1111.09.
   - Where it matters: 9 of the 1,160 fall in the two-sided range 5…20 shares (8.04, 8.29, 8.54 …). There are also 5 follow300 clip cases: $200 at 0.18, 0.33, 0.38 and $300 at 0.27, 0.57.
   - Cost: on the other 98,840 cents the loop returns the same value as the single formula. It takes at most 4 extra steps and drops at most 0.04 shares below `share_floor`. The probe covered 117,196 inputs (every cent, clip sizes, two-sided leg sizes, 6-decimal inventory sums) and found 0 results that are not a client fixed point.
   - Effect on the test list: "equals `round_down` on every cent" and "idempotent" cannot both hold. The test asserts the stable version instead. On every cent the result equals the client's `round_down` repeated until it stops changing, and calling the function on its own result returns the same value.
2. **Name and place**: `floor_to_signed_shares(size)` in `src/shared/utils/trading.py`, next to `share_floor`, plus a private `_clob_round_down(size)`. The verb-first name matches `floor_to_grid` and `ceil_to_grid` in the same module (AGENTS.md: function names are action verbs).
3. **No client import in `src/shared`**. `py_clob_client_v2` is only a transitive dependency through `polymaker` (`pyproject.toml:13`, `:29`), and the backtest uses `shared` too. Production keeps a two-line copy of the client rule. The test imports the client's real `round_down`, so a client upgrade that changes the rule fails the test.
4. **`share_floor` stays as is** (step text). Its other callers compare sizes; they do not send them, so they stay: `quoting.py:325`, `:400`; `lifecycle.py:233`; `core_recovery.py:76-77`; `wallet_store.py:28`. They agree with the new size because `min_order_size` is a whole share. A whole share is a client fixed point, and the loop steps down one cent at a time. So the signed size is never below `min_order_size` when `share_floor(x)` is not.
5. **Partial rule**: `remaining = owner.submitted_qty - filled_qty` and `partial = share_floor(remaining) > 0.0`.
   - `share_floor` returns whole cents, so `> 0.0` is exactly the step's `>= 0.01` without a new literal. It mirrors `drop_share_residue` (`trading.py:29`).
   - Float checks: 0.0064 → 0.0 (closed); `20 - 19.99 = 0.010000000000001563` → 0.01 (partial); `17.08 - 17.07 = 0.00999999999999801` → 0.01 (partial); overfill −0.004 → −0.01 (closed).
6. **Rejected: nudging the float up**, i.e. sending `nextafter(17.08)` so that the client signs 17.08. It would keep the full size and leave no SELL dust. But the core would then store sizes like 17.080000000000002, which contradicts the resolved "17.08 → 17.07" test and puts non-cent sizes into journals and sqlite.
7. **Out of scope**: no backtest adapter change (§5, risk 2), no golden smoke rewrite (STEP-014), no `dust_sweep.py` change (§3.5).

## 3. Changes

Reference patch (verified on the scratch copy): `work/plan-001/step001_src.diff` (production) and `work/plan-001/step001_tests.diff` (tests).

### 3.1 `src/shared/utils/trading.py`

Switch `buy_share_quantity` (`:16`) and add the two functions after `share_floor` (`:22-24`):

```python
def buy_share_quantity(*, base_size_usdc: float, price: float) -> float:
    """Buy size in whole share ticks, never rounded above the dollar budget."""
    return floor_to_signed_shares(base_size_usdc / price)

...

def _clob_round_down(size: float) -> float:
    return math.floor(size * 100.0) / 100.0


def floor_to_signed_shares(size: float) -> float:
    """Largest size at or below share_floor(size) that py_clob_client_v2's round_down keeps."""
    signed = share_floor(size)
    while _clob_round_down(signed) != signed:
        signed = _clob_round_down(signed)
    return signed
```

`buy_share_quantity` already sits above `share_floor` and calls it, so a call to a function defined further down is fine: module names resolve at call time.

### 3.2 `src/strategy/two_sided_quoting.py`

- `:5`: import `floor_to_signed_shares` instead of `share_floor`. The file has no other `share_floor` use.
- `:101`: `size = floor_to_signed_shares(scale_order_size(...))`. The `min_order_size` check at `:109` now sees the signed size.

### 3.3 `src/strategy/quoting.py`

- `:5`: add `floor_to_signed_shares` to the import. Keep `share_floor`; it is still used at `:325` and `:400`.
- `:463`: `qty = floor_to_signed_shares(state.inventory[token_index].qty)`.
  - This changes the size for both `_join_ask_sell` and `_choose_sell_target`.
  - It also changes the coverage check `_sell_covers_inventory` (`:457-459`): a held SELL of 17.07 now covers inventory of 17.08, which is right, because 17.07 is all the client can sign.
- `_buy_qty` (`:147-148`) needs no edit; it goes through `buy_share_quantity`.

### 3.4 `src/strategy/lifecycle.py`

`:686-687`:

```python
    filled_qty = owner.filled_qty + event.qty
    remaining = owner.submitted_qty - filled_qty
    partial = share_floor(remaining) > 0.0
```

The rest of `_apply_known_fill` stays the same:

- A closed BUY goes through the existing `_complete_buy_order` (`:602-610`), which drops the order and marks the rung `done`. For two-sided the rung is the token slot, so `rung_occupied` becomes false and the next cycle bids again.
- A SELL with a sub-cent remainder is dropped by `_drop_active` (`:708-709`). `_credit_sell_inventory` (`:518-532`) already books its inventory leftover as residue.

### 3.5 Not changed

- `src/trader/dust_sweep.py:99-102` sizes a FAK SELL with `share_floor` and keeps no core order. A cut cent there leaves 0.01, which the next sweep picks up (0.01 is a client fixed point). Nothing is held, so this is outside A1.
- `src/trader/engine_seams.py:975-984` (`sell_drop_reason`): the SELL size still satisfies `<= held + HALF_SHARE_TICK`, because the new size is never above `share_floor(inventory)`.
- `_held_sell_target` / `_sell_leftover` (`quoting.py:443-454`): this keeps an existing order at the same price. It is not a new venue size.
- Backtest: no code change. `_submit_place` sends `instrument.make_qty(Decimal(str(place.quantity)))` (`src/backtest/strategy.py:700-708`), so the backtest now posts the signed size, the same as live.
- Test helpers that compute the expected size with `buy_share_quantity` follow automatically: `tests/test_backtest_maker.py:502`, `:2090`; `tests/test_strategy_core.py:1211-1215`; `tests/test_strategy_budget.py:197`, `:394`; `tests/test_trader_shared_budget.py:63`.

## 4. Tests

Every test below was run on the scratch copy and passes with the patch. The four behavior tests in §4.2–§4.4 fail on HEAD at the assertion named.

### 4.1 `tests/test_trader_session_quoting.py`, next to `test_share_floor_does_not_leave_a_0_01_remainder` (`:69-71`)

- Header at `:3`: `# pyright: reportPrivateUsage=false, reportMissingTypeStubs=false`. Without it basedpyright fails on the client import (`Stub file not found for "py_clob_client_v2.order_builder.helpers"`). The repo already uses this header for client imports (`tests/test_trader_clob_transport.py:8`, `src/trader/clob_transport.py:3`).
- Imports: `from py_clob_client_v2.order_builder.helpers import round_down`, and `buy_share_quantity, floor_to_signed_shares, share_floor` from `shared.utils.trading`.

```python
def test_signed_shares_are_what_the_clob_client_signs_on_every_cent() -> None:
    for cents in range(1, 100_001):
        size = cents / 100.0
        expected = round_down(size, 2)
        while round_down(expected, 2) != expected:
            expected = round_down(expected, 2)
        signed = floor_to_signed_shares(size)
        assert signed == expected
        assert floor_to_signed_shares(signed) == signed


def test_signed_shares_drop_the_cents_the_client_drops() -> None:
    assert floor_to_signed_shares(17.08) == 17.07
    assert floor_to_signed_shares(20.0) == 20.0
    assert floor_to_signed_shares(0.58) == 0.56
    assert buy_share_quantity(base_size_usdc=140.0, price=0.46) == 304.33
```

The whole file runs in about 3 s.

### 4.2 `tests/test_strategy_core.py` (follow300 core)

These tests use the existing helpers `_prime`, `_balanced`, `_buy_places`, `_sell_places`, `_accept_all`, `_feed`, `_signal`, `_wake` and `_fill_of`.

- `test_bad_cent_buy_plans_the_size_the_client_signs`: `_prime(books=_balanced(ts_ns=0, bid0=0.63, ask0=0.65), anchor=0.64)` gives BUY places `[(0.63, 158.72), (0.62, 161.29), (0.61, 163.93)]`. HEAD gives 158.73.
- `test_bad_cent_inventory_sells_the_signed_size_and_closes_on_its_fill`: same flow as `test_sell_to_dust_after_done_rung_starts_fresh_ladder` (`:939`).
  - Fill the top BUY with 17.08, exit at `10 * NS` with `delta=0.0`, and ack the cancels. The SELL quantity is 17.07 (HEAD: 17.08).
  - Send `OrderAccepted` and a 17.07 fill. The SELL is gone from `state.orders`, with no 0.01 SELL remainder.
  - The 0.01 of real inventory that stays is the client's limit (§5, risk 3).

### 4.3 `tests/test_strategy_late_fills.py` (A2)

- Add `rung_occupied` to the `strategy.lifecycle` import (`:27-38`) and `RestingOrder` to the `strategy.types` import (`:40-54`).
- Helper `_top_buy_of_twenty_shares() -> tuple[StrategyState, RestingOrder]`: call `_prime` and `_accept_all`, then `replace_order` sets the top BUY's `submitted_qty=20.0`. This is the same pattern as `_gone_clip` (`:313-320`), without the gone status.
- `test_subcent_remainder_completes_the_buy_and_frees_the_rung`: after fills of 8.81356 and 11.18 the order is not in `state.orders`, and `rung_occupied(level_index)` is false. On HEAD the order stays.
- `test_cent_remainder_keeps_the_buy_partial`: after a 19.99 fill the order is still there, `partially_filled` is true and the rung is busy. This test also passes on HEAD; it guards against loosening the rule too far.

### 4.4 `tests/test_strategy_two_sided.py` (A1 and A2 together: a 17.07 fill on a 17.08 order no longer happens)

- Add `rung_occupied` to the import at `:6`.
- `test_bad_cent_leg_closes_on_the_signed_fill`:
  - Start from `replace(_idle(radiant_index=0), inventory=_inventory(radiant_index=0, radiant_qty=4.38, dire_qty=0.0))` and run `BookUpdate(_even_books(ts_ns=T0))` + `Wake`. The radiant leg is 17.07 (HEAD: 17.08).
  - Then `OrderAccepted` + `Fill(qty=17.07)`: the order is gone and `rung_occupied(level_index=0)` is false.

### 4.5 `tests/test_trader_session_core.py` fixture values

`test_raw_sub_cent_book_keeps_the_resting_rungs` is the only existing test the patch breaks. In the full patched suite, only its two parameters fail, at the size assertion (`:979-982`). Update:

- `:926`: `(0.53, 264.15)` → `(0.53, 264.14)` (clip $140 / 0.53).
- `:952`: `(0.66, 303.03)` → `(0.66, 303.02)` (clip $200 / 0.66; the live book at `:946` already shows 303.02).

The rest of the test (strip, then no cancel and no place) passes unchanged; the file gives 105 passed.

## 5. Behavior changes and risks

1. **Cut sizes shrink by up to 0.04 shares.** It is 0.01 for most cut cents and more only on the 1,160 cents that need several steps. Budget, notional and caps use the smaller size.
   - The backtest changes with it, so the golden follow300 smoke drifts: 7 of 8 fixtures fail with the patch.
   - On HEAD 1 of 8 already fails: `dota_8933879286`, `fills length 35 != 36` (`work/plan-001/replay_et_base.log`).
   - STEP-014 rewrites the goldens. This step runs pytest with `--ignore=tests/test_follow300_replay.py`.
2. **In the backtest, a sub-cent remainder keeps resting in Nautilus.**
   - Polymarket instruments use size precision 6 (`nautilus_trader/adapters/polymarket/common/parsing.py:224`).
   - After the core closes a BUY on a sub-cent remainder, the adapter still keeps the `LiveOrder`, because `_credit_venue_fill` compares exact `Decimal`s (`src/backtest/strategy.py:1256-1265`). The core never cancels it, so it rests until the `game_end` sweep (`strategy.py:536-537`).
   - Effect: a rare extra sub-cent fill, and `live_order_seconds` and reserved-notional telemetry count the order. Live marks such orders MATCHED instead.
   - The effect is under $0.01 per case and this step does not address it. If wanted, it belongs to the adapter work in STEP-008/009.
3. **Selling bad-cent inventory still leaves 0.01 real shares**, because the client cannot sign 17.08 as a float. What changes is that the SELL order no longer hangs on a phantom 0.01. The 0.01 of inventory is below `min_order_size` and goes down the existing dust paths: `end_episode_if_idle` (`lifecycle.py:228-247`) and `dust_sweep`.
4. **A real sub-cent order still open on the venue would be forgotten by the core** (at most 0.009999 shares, under $0.01). In both live dust cases the venue had already reported MATCHED (`livebugs-hold.md` §3). A later fill would go through the existing late-fill path (`_owner_for_fill` → record, `lifecycle.py:613-620`).
5. **Deploy**: orders restored from sqlite keep their old core size, so a 0.01 remainder on them stays partial until the STEP-004 CONFIRMED release. No migration and no schema change.
6. Nothing changes for two-sided legs of exactly 20.0 (6,901 of 10,631 accepted B BUYs, `livebugs-hold.md` §1) or for any size the client already signs unchanged.

## 6. Verification

Run from `esports-trader/` in the main checkout. Do not run the suite during a live map on the VPS, and set `PYTEST_N` only locally.

```bash
cd /Users/dimabytes/work/polymarket/dota_2_bot/esports-trader

# targeted (scratch: 244 passed in 3.5 s)
PYTHONPATH=src:scripts:../prediction-market-backtesting uv run --group backtest python -m pytest -q \
  tests/test_trader_session_quoting.py tests/test_strategy_core.py tests/test_strategy_late_fills.py \
  tests/test_strategy_two_sided.py tests/test_trader_session_core.py

# full suite without golden replay ("Tests pass" for STEP-001..013; scratch: 25-34 s)
PYTEST_N=8 PYTHONPATH=src:scripts:../prediction-market-backtesting uv run --group backtest python -m pytest -q \
  --ignore=tests/test_follow300_replay.py

# typecheck (scratch: 0 errors, about 12 s)
uv run python -m basedpyright

# ruff check + format + repo hooks on the touched files
uv run python -m pre_commit run --files src/shared/utils/trading.py src/strategy/two_sided_quoting.py \
  src/strategy/quoting.py src/strategy/lifecycle.py tests/test_trader_session_quoting.py \
  tests/test_strategy_core.py tests/test_strategy_late_fills.py tests/test_strategy_two_sided.py \
  tests/test_trader_session_core.py
```

Expected result: 0 failures.

- On the scratch copy the full run gave 3211 passed and 21 failed. HEAD in the same scratch setup failed the identical 21 tests (the `diff` of the two FAILED lists is empty).
- Those 21 fail only because of the copy: no `compose.yaml`, no docker, `ODDIN_BRAND_TOKEN` unset. In the real checkout the three affected files pass on HEAD (`tests/test_trader_compose.py`, `tests/test_trader_wallet_host.py`, `tests/test_trader_unsettled_buy_recovery.py`: 128 passed).

Optional, not a gate: `tests/test_follow300_replay.py` takes about 70 s and is expected to show 7 of 8 failing (§5, item 1).

This step needs no backtest, training or data rebuild.

## 7. Commit

In `esports-trader` on `main`, add only the 9 paths listed above (`git add <paths>`). Use the repo's message style, for example `fix: STEP-001 - send the size the CLOB client signs and close a BUY on a sub-cent remainder`. Do not push.
