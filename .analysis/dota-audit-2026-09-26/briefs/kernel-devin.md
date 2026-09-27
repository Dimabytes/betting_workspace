# Brief: kernel-devin — strategy kernel and live/backtest adapter parity

Report: `$R/reports/kernel-devin.md`. Work dir: `$R/work/kernel-devin/`.

## Scope (read line by line)

- `src/strategy/*` (engine, lifecycle, quoting, policy, signals, scheduling, kill_gate, mid_spike, budget,
  series_link, types).
- Adapters: `src/backtest/strategy.py`; `src/trader/{session_core.py, session_engine.py, session_quoting.py,
  session_types.py, engine_seams.py, cadence.py}`.
- `src/shared/constants/strategy.py`, `config/trading.toml`, the `policy` header of a recent live
  `$E/data/trader/*/core_trace.jsonl(.gz)`, the LIVE backtest manifest.

## Questions

1. **Kernel logic.** Rung prices (L0/L1/L2 vs tick size and the [0.40, 0.85) bounds); which token is favoured
   (sign of Δ̂ ↔ radiant/dire token); hysteresis (enter 2c, exit 1.5c); cutoff 480; spread gate; lonely-L0;
   `mid_spike` (10 s lookback, 0.10); kill gate; stale signals (16 s entry, 45 s exit); SELL price (ceil of
   fair vs ask; clipping near 0.99/1.00; fair ≥ 1); `exit_settle` 10 s; `sell_min_life` 1 s;
   `hold_unconfirmed_sell`; dust; position accounting across partial fills; episode lifecycle
   (`position_open` blocks a new BUY); rounding (0.01 grid, 2-decimal shares).
2. **Parity of inputs.** Do live and backtest give the kernel the same inputs: book snapshot shape, time
   source (monotonic vs exchange), cadence, feed freshness, fill/ack timing? List each difference and its
   direction (backtest optimistic or pessimistic). Is any logic duplicated in the adapters (a gate computed in
   `backtest/strategy.py` one way and in `session_quoting.py` another way)?
3. **poly-maker overrides.** Live runs poly-maker's Engine patched by `engine_seams.py`. Which kernel
   decisions can the fork alter in live (requote, merge, risk)? Which live behaviour does the backtest not model
   at all?
4. **Config drift.** Constants vs `trading.toml` vs manifest vs live `core_trace` `policy`. E.g.
   `BASE_SIZE_USDC=100` vs live clips $60/$200: does any rule depend on size and behave differently?
5. **Tests.** Which critical kernel behaviours are pinned by tests, and which are not (read `tests/`, do not
   run them).

## Allowed

Run the kernel on recorded inputs, e.g. `PYTHONPATH=src uv run python -m trader.replay_core_trace --archive
data/trader/<dir>` on local archives (read-only). No pytest.
