# prediction-market-backtesting

## Setup

- Project path: `../prediction-market-backtesting` (sibling of `betting_workspace/`)
- Local source checkout of the NautilusTrader prediction-market backtesting library (`origin` is upstream: `evan-kolberg/prediction-market-backtesting`).
- Kept next to `esports-trader` so agents can read the library source instead of guessing from the installed package.
- `esports-trader` consumes this stack as a package (Nautilus + this framework). This checkout is not our product.

## Stance

Read-ONLY!

## Before any change (only if the user asked)

Read the upstream agent rules in this repo: `../prediction-market-backtesting/AGENTS.md`.

Those rules stay in the library repo on purpose. Do not copy them here. They cover L2 book replay, README/docs limits, realism priorities, verification, and PR hygiene.
