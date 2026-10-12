# prediction-market-backtesting

## Setup

- Project path: `../prediction-market-backtesting` (sibling of `betting_workspace/`)
- Checkout of the NautilusTrader prediction-market backtesting library. `origin` is our private repo `git@github.com:Dimabytes/prediction-market-backtesting.git`, branch `local-day-native`. `upstream` is `evan-kolberg/prediction-market-backtesting`, read-only: fetch only, no push, no PRs. Not a GitHub fork: GitHub has no private fork of a public repo.
- New machine: `git clone git@github.com:Dimabytes/prediction-market-backtesting.git` next to `esports-trader` (default branch `local-day-native`), `git remote add upstream https://github.com/evan-kolberg/prediction-market-backtesting.git`, then `make backtest-install` from `esports-trader`.
- Kept next to `esports-trader` so agents can read the library source instead of guessing from the installed package.
- `esports-trader` consumes this stack as a package (Nautilus + this framework). This checkout is not our product.

## Stance

No commits without the owner's explicit agreement, even small ones. Two
patches exist, both agreed with the owner on 2026-10-09, on `local-day-native`
on top of upstream `c76e77a`: `df17b59` (Rust diff of daily book files) and
`e2cfebb` (book prices to raw with integers). Both are on `origin`. Push only
to `origin`, and only when the owner asks.

- Pin: `esports-trader/Makefile` `BACKTEST_FRAMEWORK_COMMIT` is the full SHA of
  the branch head (`e2cfebb2c393a772e9b336166dfc6909e02e14b2`).
  `make backtest-install` refuses any other HEAD. A new framework commit moves
  the pin in the same esports-trader change.
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
rebuilt. Rebuild after any change under `crates/` and on every new machine
(Rust >= 1.93.1 from rustup): `make backtest-install` from `esports-trader`.
It checks HEAD against the pin and runs `maturin develop --release`, which
writes the `.so` into this checkout. To build a HEAD that is not pinned yet:
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

## Cache trap for A/B runs of onchain code

A tree-cache miss evicts the market's framework `trade-ticks-v1`; a hit does not.
Switch the code back to a version whose tree files are cached, and the framework
replays the ticks of the other version. Before each run of an A/B pair, delete
`~/.cache/nautilus_trader/telonex/trade-ticks-v1` (about 300 MB, rebuilt on use).

## Before any change (only if the user asked)

Read the upstream agent rules in this repo: `../prediction-market-backtesting/AGENTS.md`.

Those rules stay in the library repo on purpose. Do not copy them here. They cover L2 book replay, README/docs limits, realism priorities, verification, and PR hygiene.
