# STEP-007 plan: tape v3 — the sweep trades on the book row that showed it, the WS print kept as `print_timestamp_us` (B2)

Base: `esports-trader` `main` at `8ea2e4ba` (clean tree). The reference diffs were checked with `git apply --check` against that HEAD:
- `orchestrate/work/plan-007/step007_src.diff`: 4 files, +237/−52.
- `orchestrate/work/plan-007/step007_tests.diff`: 7 files, +286/−13.

The implementer can apply them as they are, or write the same code by hand from §3–§4.

## 0. Result in one paragraph

`onchain_retime.py` gets the v3 rule from `investigations/2026-10-08-liveb-postmortem/sim/replica/tape_align.py`. A print is late when the book row at its stamp is back past the deepest maker price its tx took on that token. In that case the tx trades at the start of the last run of book rows, inside 500 ms before the print, where the touch was at or through that price. Every other print keeps its WS time, and a tx takes its earliest time over both tokens.

The rewritten onchain day file keeps `timestamp_us` (the framework's clock, now the tape time) and adds `print_timestamp_us` (the earliest WS print of the tx). The framework never reads the new column. Instead, `run_batch` reads it back from the source tree as one `LatePrints` per token: the trade time and print time, in ns, of each fill heard after it trades. It passes them through `build_strategy_configs` into a new `DotaMakerConfig.late_prints` field, so STEP-008 can delay the fill notice. The two-sided config inherits the field.

Book reads are lazy: they happen only on a tree-cache miss. The cache key now includes the book and onchain stamps of every token-day the tape reads.

Scratch results:
- On 8 real maps of 2026-10-08, the product times equal `tape_align`'s on 7,368 of 7,368 txs; 128 txs move.
- The full suite is the HEAD set plus 12 new passing tests, with the same env-only failures.
- basedpyright reports 0 errors.
- All 12 mutations fail a test.

## 1. What happens today (evidence)

### 1.1 Retime and tree

- `src/backtest/onchain_retime.py:40-51` `load_trade_times`: the earliest WS print per tx hash over the `trades` day files.
- `:54-67` `add_timestamp_us`: keeps the rows whose tx has a print and appends `timestamp_us` = that print.
- `:70-82` `write_onchain_day`: adds `add_aggressor_side`, then the column, then writes the file.
- The docstring (`:2-16`) says the block time lags the print by 2.5 s p50 and that on the print time the sim got 468 of 503 real fills.
- `src/backtest/telonex_local.py:197-198`: `link_market_tokens` builds the paths with `_trades_day_paths`, then calls `load_trade_times` eagerly once per market, on a cache hit too.
- `:240-262` `_trades_day_paths` checks that the onchain dir exists and that no traded day lacks prints, then collects both tokens' `trades` days.
- `:309-342` `_link_onchain_days`: the cache key `"onchain-side-retime"` (`:327-335`) hashes both rewrite modules (`_rewrite_code_stamp` `:356-359`), the onchain source and the trades stamps (`:322`, `:333`). The build lambda is `:339`.
- `_link_cached_day` (`:391-425`) evicts the market's framework cache for that channel on a miss (`:413-414`).
- `create_telonex_source_tree` (`:460-490`) links every market into one root, and that root is the `source_root` that `run_batch` gets (`run.py:1029,1044`, `seed0_replay.py:153-162`).

### 1.2 What the framework reads (pinned `e2cfebb`)

- `prediction_market_extensions/backtesting/data_sources/telonex.py:3366-3381`: the onchain clock is the first present column of `timestamp_us, block_timestamp_us, …`.
- `:3207-3209`: `_column_to_ns` multiplies `*_us` by 1,000.
- `:1132-1138`: the window prefilter also prefers `timestamp_us`.
- No probe list names `print_timestamp_us` (`rg print_timestamp prediction_market_extensions`: no hits), so the framework ignores the extra column.
- A Nautilus fill gets a generated trade id (`nautilus_trader/backtest/engine.pyx:8282`, `trade_id=self._generate_trade_id(ts_now)`). So the strategy can join a fill to the tape only by (instrument, `ts_event`). That is why the hand-off is keyed by trade time (decision 4).

### 1.3 The investigation's v3 (`tape_align.py`, 93 lines)

- `:56-93` `load_trade_times`: `i` = the last book row ≤ stamp and `j0` = the first row ≥ stamp − `WINDOW_US` (500,000). It requires `i > j0`.
  - For a sell with `bid[i] > p+1e-9`, `through = bid ≤ p+1e-9`.
  - For a buy with `ask[i] < p−1e-9`, `through = ask ≥ p−1e-9`.
  - It takes the last through run. If the run starts after `j0`, the tx trades at that row; otherwise it keeps the print.
- `:33-53` `_sweep_depth` takes side = opposite of `maker_side`.
- `:25-30` `_touch` goes through `to_pydict`.
- `:19-22` `TAPE_ALIGN_MULTI` (v4) is an env switch. The step asks for v3, so it is not ported.
- The print lags the book row that shows the sweep by p50 25 ms and p95 192 ms over 1,364 touch-taking prints (`tape_align.py:4`, `print_lag.py`).

### 1.4 Strategy config path

- `run.py:497-605` `build_strategy_configs` builds one `payload` dict per match (`:555-576`; `"kernel"` at `:575`). The follow300 branch passes it as-is; the two-sided branch spreads it (`**payload`, `:596`).
- `run.py:624-681` `run_batch` calls it at `:652-662`. It is the only production caller: `rg "build_strategy_configs\(" src scripts` gives just `run.py:652`. `seed0_replay.py:157` reaches it through `run_batch`.
- `strategy.py:166-205` `DotaMakerConfig` is a msgspec `StrategyConfig` whose nested frozen dataclasses already round-trip (`kernel: MatchKernelConfig` `:184`). `two_sided_core.py:91` `TwoSidedCoreConfig(DotaMakerConfig)` inherits every field.

### 1.5 Data facts that shape the code (scratch scripts in `work/plan-007/`)

- **Memory.** A direct port of `_touch` (`to_pydict`) peaks at about 11.6 GB RSS on one 480k-row book day. The parity run that calls `tape_align` peaked at 11,185 MB. A streamed, vectorized touch takes 0.87 s and about 460 MB on that file (`touch_check.py`). Reusing `load_token_book` (`telonex_book.py:148-171`) gives the same prices but peaks at 2.3–2.7 GB (`book_reuse_check.py`).
- **One-sided days.** 9 of 150 sampled book days store one side as `list<null>` (see also the `shared/types/telonex.py:1-6` docstring). So the levels must be cast to `TELONEX_BOOK_LEVELS` (`:10`) before reading prices.
- **Missing book days.** Locally, 32 token-days have trades and onchain fills but no book file. `_link_stripped_book_days` already skips a missing book day (`telonex_local.py:279-281`), so the tape must keep the print on such a day, as `tape_align` does (`:65,72`). Every trades day has its onchain file (0 of 22,518 missing).
- **Sweep side.** `tape_align` takes the side as "opposite of `maker_side`", which is wrong on mirrored rows. The product's `add_aggressor_side` (`onchain_side.py:28`) is right on them. On 9034957701 this changes 2 of 1,605 tx times (`side_check.py`).
- Book day files are sorted by `timestamp_us` (150/150), so no sort is added.

## 2. Design decisions

1. **One product port, streamed.** `_read_touch` reads `bids/asks/timestamp_us` in 20,000-row batches, casts the levels to `TELONEX_BOOK_LEVELS`, and takes the best prices with `np.fmax.at` / `np.fmin.at` over `list_parent_indices`. An empty side reads 0.0 (bid) or 1.0 (ask), as in `tape_align`. `_trade_us` is `tape_align`'s loop with numpy `flatnonzero` in place of the while-walk; the result is the same (parity in §5).
2. **Sweep side from `add_aggressor_side`.** The rewrite already uses that function for the `side` column, so the tape and the framework agree on the aggressor. A tx's rows with the other side are ignored (first side wins), as in `tape_align`.
3. **`print_timestamp_us` = the tx's earliest WS print over both tokens.** That is when live first hears of the fill (`user_trade`). `timestamp_us` = the earliest tape time over both tokens. A tx without a print is still dropped.
4. **Hand-off as `LatePrints`, read back from the tree.** Each token gets `LatePrints(trade_ns, print_ns)`: sorted parallel tuples holding only the rows where print > trade, with the latest print per trade time. It is read from the rewritten onchain files with `load_late_prints(source_root, context)`, so cache hits and misses give the same data. It enters the config as `late_prints: tuple[LatePrints, LatePrints]`, indexed by token index like `instrument_ids`.
   - Why not the raw column: the strategy cannot see the trade tick that filled it (§1.2), and the full column per row would bloat the msgspec config. Only about 1.7% of txs move (128 of 7,368).
   - Nothing reads the field until STEP-008. It is required, not optional, so a caller cannot forget it (AGENTS: all args required).
5. **Lazy book read.** `link_market_tokens` wraps the tape in `functools.cache(lambda: load_tape_times(tape_days))`, and only a miss's build lambda calls it. A warm cache never opens a book file (tested).
6. **Cache key.** It covers the trades, book (when present) and onchain stamps of every `TapeDay` of both tokens. A new book file of either token re-times both tokens' onchain days. The `onchain_retime.py` code stamp changes too, so the first run after landing rewrites every onchain day once and evicts each market's `trade-ticks-v1` cache. Book caches stay.
7. **Missing book day.** `TapeDay.book` is `None` when the file is absent. The empty `_NO_BOOK` touch makes `_trade_us` keep the print, with no extra branch.
8. **Not changed.**
   - `replay_inputs.py` only fingerprints smoke inputs, and its docstring says "Book captures … stay unhashed", so it has no loader role here.
   - The strategy (STEP-008).
   - `poly-maker`.
   - Docs.

## 3. Changes

### 3.1 `src/backtest/onchain_retime.py` (+174/−29)

- **Docstring.** Replace `:2` with `"""Re-time Telonex onchain fills for the framework: book row of the sweep, else the WS print.`. After `:10`, add the v3 paragraph (print lag p50 25 ms and p95 192 ms; late print; start of the last through run inside `WINDOW_US`; other prints keep their time; a tx takes its earliest over both tokens). Rewrite `:12-15` so it says the copy adds `timestamp_us` plus `print_timestamp_us`, the earliest WS print of the tx.
- **Imports.** Change `:21` to `from typing import Any, cast`. Add `numpy as np`, `numpy.typing as npt`, `pyarrow.compute as pc`, `from shared.types.telonex import TELONEX_BOOK_LEVELS, TELONEX_BOOK_SCHEMA`, and `from shared.utils.telonex_book import NS_PER_US`.
- **Constants** after `logger` (`:29`): `WINDOW_US = 500_000`, `PRICE_EPSILON = 1e-9`, `BOOK_BATCH_ROWS = 20_000`, and `SWEEP_COLUMNS = ("tx_hash", "price", "maker_side", "maker_asset_id", "asset_id", "sibling_asset_id")`.
- **`RetimeStats` docstring:** "How many onchain rows took a tape time and how many had no print."
- **Replace `load_trade_times` (`:40-51`)** with these frozen dataclasses: `TapeDay(trades: Path, book: Path | None, onchain: Path)`, `TapeTime(trade_us: int, print_us: int)`, `LatePrints(trade_ns: tuple[int, ...], print_ns: tuple[int, ...])`, private `_Touch(timestamps_us, best_bid, best_ask)` (numpy arrays), `_NO_BOOK` (an empty `_Touch`), and `_Sweep(side: str, price: float)`. Then add:

```python
def _best_prices(
    levels: "pa.Array[Any]", *, empty: float, pick: np.ufunc
) -> npt.NDArray[np.float64]:
    typed: pa.Array[Any] = levels.cast(TELONEX_BOOK_LEVELS)
    best = np.full(len(typed), empty)
    prices = pc.struct_field(pc.list_flatten(typed), "price").to_numpy(zero_copy_only=False)
    pick.at(best, pc.list_parent_indices(typed).to_numpy(), prices)
    return best


def _read_touch(path: Path) -> _Touch:
    """Best bid (0.0 when none) and best ask (1.0 when none) per book row, in batches."""
    stamps = [np.empty(0, np.int64)]
    bids = [np.empty(0)]
    asks = [np.empty(0)]
    source = pq.ParquetFile(path)
    for batch in source.iter_batches(batch_size=BOOK_BATCH_ROWS, columns=TELONEX_BOOK_SCHEMA.names):
        stamps.append(batch.column("timestamp_us").to_numpy())
        bids.append(_best_prices(batch.column("bids"), empty=0.0, pick=np.fmax))
        asks.append(_best_prices(batch.column("asks"), empty=1.0, pick=np.fmin))
    return _Touch(timestamps_us=np.concatenate(stamps), best_bid=np.concatenate(bids), best_ask=np.concatenate(asks))


def _read_sweeps(path: Path) -> dict[str, _Sweep]:
    """Per tx on this token: the aggressor side and the deepest maker price it took."""
    table = add_aggressor_side(pq.read_table(path, columns=list(SWEEP_COLUMNS)), path)
    # tx_hash / side / price to_pylist; first side wins; sell keeps min price, buy keeps max


def _trade_us(touch: _Touch, sweep: _Sweep, print_us: int) -> int:
    stamps = touch.timestamps_us
    last = int(np.searchsorted(stamps, print_us, side="right")) - 1
    start = int(np.searchsorted(stamps, print_us - WINDOW_US))
    if last <= start:
        return print_us
    if sweep.side == "sell" and touch.best_bid[last] > sweep.price + PRICE_EPSILON:
        through = touch.best_bid[start : last + 1] <= sweep.price + PRICE_EPSILON
    elif sweep.side == "buy" and touch.best_ask[last] < sweep.price - PRICE_EPSILON:
        through = touch.best_ask[start : last + 1] >= sweep.price - PRICE_EPSILON
    else:
        return print_us
    hits = np.flatnonzero(through)
    if hits.size == 0:
        return print_us
    gaps = np.flatnonzero(~through[: hits[-1]])
    if gaps.size == 0:
        return print_us
    return int(stamps[start + gaps[-1] + 1])


def load_tape_times(days: Sequence[TapeDay]) -> dict[str, TapeTime]:
    """Per tx hash: its earliest trade time and earliest WS print over the given token-days."""
    # per day: touch = _NO_BOOK if day.book is None else _read_touch(day.book); sweeps = _read_sweeps(day.onchain)
    # per print: traded = stamp if no sweep else _trade_us(...); keep min trade and min print per tx
```

(The two `#` lines above only sum up the reference diff; the code has no comments.)

- **`add_timestamp_us` (`:54-67`)** takes `tape_times: dict[str, TapeTime]`. It keeps the rows whose tx has a print and appends `timestamp_us` = `trade_us` and `print_timestamp_us` = `print_us` (both int64). Its stats are unchanged.
- **`write_onchain_day` (`:70-82`)** takes `tape_times: dict[str, TapeTime]`. Docstring: "…the aggressor `side` and the tape clock columns."
- **New `read_late_prints(paths: Sequence[Path]) -> LatePrints`** after `write_onchain_day`. It reads `timestamp_us, print_timestamp_us`, keeps the rows where `print > trade`, takes the max print per trade time, sorts by trade time, and multiplies by `NS_PER_US`.

### 3.2 `src/backtest/telonex_local.py` (+54/−22)

- **Docstring (`:9-15`).** `timestamp_us` is re-timed "to the book row that showed the sweep or else the WebSocket print", and the print itself is kept as `print_timestamp_us`.
- **Imports (`:35`).** Import `LatePrints, TapeDay, TapeTime, load_tape_times, read_late_prints, write_onchain_day`. `cache` and `Callable` are already imported.
- **`link_market_tokens` (`:197-198`).** Use `tape_days = _tape_days(context, capture_root, days)` and `tape_times = cache(lambda: load_tape_times(tape_days))`. At `:235-236`, pass `tape_days=tape_days, tape_times=tape_times`.
- **Rename `_trades_day_paths` → `_tape_days` (`:240-262`)** and return `tuple[TapeDay, ...]`. The checks at `:246-257` stay. Add `book_dir = asset_channel_dir(capture_root, BOOK_CHANNEL, token_id)`. For each day with a trades file, append `TapeDay(trades=path, book=book if book.is_file() else None, onchain=onchain_dir / f"{day}.parquet")`.
- **`_link_onchain_days` (`:309-342`).**
  - Parameters: `tape_days: Sequence[TapeDay]` and `tape_times: Callable[[], dict[str, TapeTime]]`.
  - Docstring: "…with the `side` and tape clock columns."
  - Replace `:322` with `tape_stamps = tuple(_file_stamp(path) for day in tape_days for path in (day.trades, day.book, day.onchain) if path is not None)`.
  - `:333` becomes `*tape_stamps`.
  - `:339` becomes `write_onchain_day(fills.resolve(), out, tape_times())`.
- **New, after `:342`:**

```python
def load_late_prints(root: Path, context: MarketContext) -> tuple[LatePrints, LatePrints]:
    """Each token's late prints, read back from the tree's rewritten onchain day files."""
    first, second = (
        read_late_prints(
            sorted(outcome_dir(root, ONCHAIN_CHANNEL, context.market_slug, index).glob("*.parquet"))
        )
        for index in (0, 1)
    )
    return first, second
```

### 3.3 `src/backtest/run.py` (+7/−1)

- Import `from backtest.onchain_retime import LatePrints` after `:75`. Change `:156` to `from backtest.telonex_local import create_telonex_source_tree, load_late_prints`.
- In `build_strategy_configs`, add `late_prints: Mapping[int, tuple[LatePrints, LatePrints]],` before `two_sided` (`:507`). Add `"late_prints": late_prints[context.match_id],` after `:575`.
- In `run_batch`, add `late_prints={context.match_id: load_late_prints(source_root, context) for context in contexts},` after `:660`.

### 3.4 `src/backtest/strategy.py` (+2)

Import `from backtest.onchain_retime import LatePrints` before `:63`, and add `late_prints: tuple[LatePrints, LatePrints]` after `kernel` (`:184`). `TwoSidedCoreConfig` inherits it.

## 4. Tests

| Step bullet | Test |
|---|---|
| a sweep the book showed before its print → `timestamp_us` = book row, `print_timestamp_us` = print | `test_onchain_retime.py::test_sweep_the_book_showed_before_its_print_trades_at_that_row[sell_sweep,buy_sweep]` and `test_add_timestamp_us_keeps_printed_rows_and_drops_the_rest` |
| a trade with no earlier book row stays on the print | `test_print_keeps_its_time_without_an_earlier_sweep_row[book_still_through,through_before_window,one_row,never_through]` and `test_print_keeps_its_time_on_a_day_without_book` |
| the loader passes the column to the strategy adapter | `test_backtest_validation.py::test_tree_hands_both_tokens_the_late_print_of_a_sweep`, `test_backtest.py::test_strategy_configs_carry_no_policy_switches` (pass-through) and `test_live_archives.py::test_match_kernel_config_survives_msgspec` (round trip) |
| LoL fixture updated for the new column | `test_lol_backtest.py::test_execution_source_rewrites_onchain_else_without_local_telonex` (`:441-509`) |

### 4.1 `tests/test_onchain_retime.py` (rewritten, 11 items)

The helper `_write_day(tmp_path, *, name, prints, book, maker_side, price) -> TapeDay` writes trades, book rows (time, best bid, best ask) and one maker fill per tx on token `OWN`. The fixtures are `SELL_SWEEP_BOOK` and `BUY_SWEEP_BOOK`: the touch is through 0.45/0.55 at 1.7 s and back by 1.9 s, and the print is at 2.0 s.

The tests:
- the two sweep cases → `TapeTime(1_700_000, 2_000_000)`
- the 4 keep-the-print cases
- a missing book day (`replace(day, book=None)`)
- earliest trade and print over both tokens
- a `list<null>` ask side (asserted with `pa.types.is_null` on the read schema) still moves a sell sweep
- `add_timestamp_us` adds both columns and drops the unprinted tx
- `read_late_prints` drops on-time rows and keeps the latest print per trade time, in ns

### 4.2 `tests/test_backtest_validation.py` (+69)

- Import `load_late_prints` and `LatePrints, TapeDay, TapeTime, load_tape_times`.
- `write_valid_capture_day` (`:456-490`): the onchain row gets `"price": "0.70"`.
- `test_telonex_tree_links_both_tokens_where_the_framework_globs` (`:433-453`) also asserts `print_timestamp_us == [1_500]`.
- New tests:
  - `test_tree_hands_both_tokens_the_late_print_of_a_sweep`: token 111's book has asks 0.68@1,000, 0.72@1,200 and 0.69@1,400, and the print is at 1,500. Both tokens get `LatePrints((1_200_000,), (1_500_000,))`.
  - `test_tree_keeps_print_times_on_a_day_without_book`: token 111's book day is unlinked in `tmp_path`, so both tokens get empty `LatePrints`.
  - `test_onchain_rewrite_reads_the_tape_only_on_a_miss`: count calls through a monkeypatched `backtest.telonex_local.load_tape_times`. Tree 1 is a miss (`[2]`). Tree 2 is a hit with no read. After token 222's book is rewritten in place, tree 3 reads again (`[2, 2]`).

### 4.3 `tests/test_lol_backtest.py` (+8/−1)

- In `test_execution_source_rewrites_onchain_else_without_local_telonex`, the book fixture at `:468-471` changes: rows `{"timestamp_us": 1}` become rows with `bids` 0.4 and `asks` 0.6, because `_read_touch` reads `TELONEX_BOOK_SCHEMA` columns.
- Add `assert list(rewritten["print_timestamp_us"]) == [1]` after `:504`.

### 4.4 Call sites of the new required field

- `tests/test_backtest.py`: add the `NO_LATE_PRINTS` and `LATE_PRINTS` constants before `VENUE` (`:92`). At `:1009`, pass `late_prints={id: (LATE_PRINTS, NO_LATE_PRINTS)}` and assert the payload carries it. At `:1063`, pass empty prints.
- `tests/test_feed_schedules.py:863`: empty prints.
- `tests/test_backtest_maker.py`: add the `NO_LATE_PRINTS` constant and pass it in `build_maker_config` (`:202`). `test_backtest_two_sided_core.py:95` copies the fields, so it needs no edit.
- `tests/test_live_archives.py:115`: one non-empty and one empty `LatePrints`, plus `decoded.late_prints == config.late_prints` after `DotaMakerConfig.parse(config.json())`.

## 5. Scratch evidence (`work/plan-007/`)

- **Parity** (`parity_check.py`, run from `esports-trader/` with `PYTHONPATH=work/plan-007/et/src`, 68 s, log `parity_2026-10-08.log`). `tape_align.load_trade_times`, with its `_sweep_depth` swapped for the product aggressor side, was compared with the product `load_tape_times` on the first 8 catalog maps with all 3 channels on 2026-10-08:
  - txs equal: 7,368 of 7,368
  - txs moved: 128
  - `print_us` == earliest WS print: 7,368 of 7,368
  - product total: 8.49 s for 16 token-days
  - biggest map (9035256154, two 21.7 MB books): 2.3 s and 743 MB max RSS
- **Mutations** (`mutate.py`, log `mutate.log`). All 12 fail a test:
  - the run starts at the window edge
  - no `TELONEX_BOOK_LEVELS` cast
  - `print_timestamp_us` written as the trade time
  - the earliest late print kept instead of the latest
  - an on-time print counted as late
  - the latest print per tx kept
  - the latest trade per tx kept
  - the sweep ignored
  - the book left out of the key
  - an eager tape read
  - a missing book day required
  - the late prints swapped between tokens
- **Suites.**
  - Full suite without the golden replay: HEAD scratch has 3,244 passed and 22 failed; patched scratch has 3,256 passed and the same 22 failed (`base_full.log`, `patch_full.log`, identical sets).
  - basedpyright on the whole patched scratch: 0 errors, 0 warnings, 0 notes (`pyright_full.log`).
  - ruff check and format: clean on the 11 files.

## 6. Behavior changes and risks

- **Sim fills change.** About 1.7% of txs trade up to 500 ms earlier. Maker fills placed after a sweep go away. Every backtest result and the follow300 golden drift: `tests/test_follow300_replay.py` stays ignored until STEP-014, which rewrites the goldens. Do not re-pin goldens in this step.
- **One-off cost after landing.** The `onchain_retime.py` code stamp changes every onchain key, so each market rewrites its onchain days once, reads both tokens' books (about 1.1 s per map, measured at 8.49 s for 8 maps), and evicts its `trade-ticks-v1` framework cache. Book caches and strip rewrites stay. Warm runs afterwards read no book.
- **Same-time ordering.** A moved trade sits exactly on the book row's `timestamp_us`. Nautilus's order between a book delta and a trade tick at equal `ts_event` decides whether the sweep row or the trade comes first. That is the same framework and data path the investigation's `run_with_align.py` measured, so the step keeps parity, but the order is the framework's, not ours.
- **`late_prints` has no reader until STEP-008.** A reviewer may flag a dead field; the step text asks for the hand-off ("…прокидывает колонку до адаптера стратегии"). STEP-008 keys it by fill `ts_event` (ns). A book-driven fill on the very same ns as a moved trade would also be delayed. That is rare, and STEP-008 owns it.
- **Raw book, not stripped.** The tape reads `book_snapshot_full` as captured, our own resting size included, like `tape_align`. That is the book live saw. The strip only feeds the framework's queue.
- **The v4 `TAPE_ALIGN_MULTI` switch is not ported** (the step names v3).

## 7. Verification

Run from `esports-trader/` on `main`. Do not run during a live map (AGENTS.md), and do not set `PYTEST_N` on the VPS.

```bash
# targeted (scratch: 303 passed + 9 archive-index env failures on HEAD; patched 315 passed + the same 9)
PYTHONPATH=src:scripts:../prediction-market-backtesting uv run --group backtest python -m pytest -q \
  tests/test_onchain_retime.py tests/test_backtest_validation.py tests/test_lol_backtest.py \
  tests/test_backtest.py tests/test_feed_schedules.py tests/test_live_archives.py \
  tests/test_backtest_maker.py tests/test_backtest_two_sided_core.py

# full suite without the golden replay ("Tests pass" for STEP-001..013); expect HEAD's count + 12 passed, no new failures
PYTEST_N=8 PYTHONPATH=src:scripts:../prediction-market-backtesting uv run --group backtest python -m pytest -q \
  --ignore=tests/test_follow300_replay.py

# typecheck (scratch: 0 errors, 0 warnings, 0 notes)
uv run python -m basedpyright

# ruff + repo hooks on the touched files
uv run python -m pre_commit run --files src/backtest/onchain_retime.py src/backtest/telonex_local.py \
  src/backtest/run.py src/backtest/strategy.py tests/test_onchain_retime.py tests/test_backtest_validation.py \
  tests/test_lol_backtest.py tests/test_backtest.py tests/test_feed_schedules.py tests/test_live_archives.py \
  tests/test_backtest_maker.py

# no new comments: expect no output
git diff -U0 -- src tests | rg '^\+\s*#|^\+.*\s#\s'

# optional real-data parity (needs data/raw/telonex for 2026-10-08; ~70 s, peaks ~11 GB because tape_align is the old to_pydict reader)
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src uv run python \
  ../betting_workspace/tasks/sim-parity-fixes/orchestrate/work/plan-007/parity_check.py 2026-10-08 8
# expect: TOTAL maps 8, txs 7368, equal 7368, moved 128
```

The 22 scratch-only failures have env causes:
- 13 need `.env`: `test_trader_wallet_host.py` ×12 and `test_trader_unsettled_buy_recovery.py::test_user_stream_is_bound_on_the_host_run_path`.
- 9 `test_lol_backtest.py::test_main_always_filters_lol_before_plan_or_cache` cases need `data/archive_index/index.parquet`, which the `git archive` copy lacks.

They fail the same way on HEAD scratch.

Not run in this step: `tests/test_follow300_replay.py` (STEP-014), any backtest, training, data rebuild, or live start.

## 8. Commit

Paths:
- `src/backtest/onchain_retime.py`
- `src/backtest/telonex_local.py`
- `src/backtest/run.py`
- `src/backtest/strategy.py`
- `tests/test_onchain_retime.py`
- `tests/test_backtest_validation.py`
- `tests/test_lol_backtest.py`
- `tests/test_backtest.py`
- `tests/test_feed_schedules.py`
- `tests/test_live_archives.py`
- `tests/test_backtest_maker.py`

Add them by name, not with `-A`. At the time of planning, `AGENTS.md` had an uncommitted one-line change made by someone else (mtime 09:49). It stays out of this commit.

Message: `fix: STEP-007 - put the sweep on the book row that showed it and keep the WS print as print_timestamp_us`.

Do not push. Do not set `passes`.
