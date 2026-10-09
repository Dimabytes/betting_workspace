# prediction-market-backtesting

## Setup

- Project path: `../prediction-market-backtesting` (sibling of `betting_workspace/`)
- Local source checkout of the NautilusTrader prediction-market backtesting library (`origin` is upstream: `evan-kolberg/prediction-market-backtesting`).
- Kept next to `esports-trader` so agents can read the library source instead of guessing from the installed package.
- `esports-trader` consumes this stack as a package (Nautilus + this framework). This checkout is not our product.

## Stance

Read-ONLY! Still. One patch exists, agreed with the owner on 2026-10-09, and
it lives on the local branch `local-day-native` (commit on top of upstream
`c76e77a`). Any further change to this repo needs the owner's explicit
agreement first; do not add commits, even small ones, on your own.

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

## Before any change (only if the user asked)

Read the upstream agent rules in this repo: `../prediction-market-backtesting/AGENTS.md`.

Those rules stay in the library repo on purpose. Do not copy them here. They cover L2 book replay, README/docs limits, realism priorities, verification, and PR hygiene.
