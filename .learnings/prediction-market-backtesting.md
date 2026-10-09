# prediction-market-backtesting

## Setup

- Project path: `../prediction-market-backtesting` (sibling of `betting_workspace/`)
- Local source checkout of the NautilusTrader prediction-market backtesting library (`origin` is upstream: `evan-kolberg/prediction-market-backtesting`).
- Kept next to `esports-trader` so agents can read the library source instead of guessing from the installed package.
- `esports-trader` consumes this stack as a package (Nautilus + this framework). This checkout is not our product.

## Stance

Read-ONLY! Still. Two patches exist, both agreed with the owner on
2026-10-09, on the local branch `local-day-native` on top of upstream
`c76e77a`: `df17b59` (Rust diff of daily book files) and `e2cfebb` (book
prices to raw with integers). Any further change to this repo needs the
owner's explicit agreement first; do not add commits, even small ones, on
your own.

- The branch must stay checked out: esports-trader runs the checkout through
  `PYTHONPATH=src:../prediction-market-backtesting`, so whatever branch is
  checked out is what every backtest executes. Check with
  `git -C ../prediction-market-backtesting branch --show-current`. Every run
  manifest records `framework_commit`, so a run on the wrong branch is visible.
- What the patch does: `_load_order_book_deltas_day` diffs a daily-layout book
  file (`<channel>/<slug>/outcome_id=<n>/<date>.parquet`) with the Rust reader
  the blob store already used, over the row groups whose timestamp statistics
  touch the window plus the one before. Before, the whole day went through
  `pd.read_parquet` and a Python diff: 21 GB to read a 1.87M-row day and 22 GB
  to diff its 632k window rows. After: 9.2 GB and 36 s for that map, deltas
  cache identical row for row on three maps, fills and quote tapes identical.
- `.ruff.toml` is the repo's lint config; `make lint` there is
  `uv run ruff check .` and `uv run ruff format --check .`. The file carries 14
  pre-existing findings; the patch adds none.

## Fixed bug: book prices off by one raw unit (`e2cfebb`)

Found and fixed 2026-10-09 with the owner's agreement. The fix is Rust: the
compiled `prediction_market_extensions/_native_ext.cpython-313-darwin.so` is
gitignored, so a checkout of the commit does nothing until the extension is
rebuilt. Rebuild after any change under `crates/`:
`uvx --from "maturin>=1.12,<2" maturin build --release --manifest-path
crates/python/Cargo.toml -i ../esports-trader/.venv/bin/python --out <dir>`,
then unzip `prediction_market_extensions/_native_ext*.so` from the wheel into
the checkout. Check it took: `tests/test_native.py -k fixed_raw` passes only
with the new binary. `framework_commit` in a manifest does not prove which
binary ran.

Before the fix:
`crates/core/src/time.rs` `fixed_raw_value` turns a book price into Nautilus
raw with float math: `round(round(v*10^p)/10^p * 1e16)`. Nautilus `Price` uses
integers: `round(v*10^p) * 10^(16-p)`. They differ for 0.56, 0.69, 0.81 (and 36
of 999 prices at 0.001 tick): the book level is `5600000000000001`, an order
at 0.56 is `5600000000000000`. Trade ticks are converted by Nautilus and are
right. Effect: the engine's queue snapshot (`get_quantity_at_level`, exact raw
match) finds no level, so every order at those prices starts first in the
queue; a level DELETE does not reset it either. All backtests are affected:
follow300 `LIVE` seed0 fills about 246 extra BUY orders out of 4253 at those
three prices (estimate from neighbour prices). Evidence and the probe that
reads the engine's private queue maps:
`investigations/2026-10-08-liveb-postmortem/SIMULATOR-PLAN.md`, section
"Пункт 2", and `.../sim/queue_probe/`.

## Before any change (only if the user asked)

Read the upstream agent rules in this repo: `../prediction-market-backtesting/AGENTS.md`.

Those rules stay in the library repo on purpose. Do not copy them here. They cover L2 book replay, README/docs limits, realism priorities, verification, and PR hygiene.
