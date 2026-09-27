# Brief: live-devin — live execution, wallet, orders, risk, recovery, latency

Report: `$R/reports/live-devin.md`. Work dir: `$R/work/live-devin/`.

## Scope (read line by line)

`src/trader/{wallet_host.py, wallet_store.py, core_execution.py, core_persistence.py, core_recovery.py,
core_session_io.py, core_state_report.py, core_trace.py, core_trace_codec.py, replay_core_trace.py,
session_journal.py, paper_gateway.py, dust_sweep.py, fill_parsing.py, trade_backfill.py, clob_transport.py,
execution_policy.py, trading_mode.py, state_writer.py, notify.py, orchestrator.py, process_lock.py,
host_resources.py, session_config.py}` and the execution parts of `engine_seams.py`. Read `../poly-maker`
(read-only) to know what the Engine does. Also read `$W/.shared-skills/vps-trader/log-map.md` for known
wedge classes.

## Questions

1. **Order state machine.** place → ack → live → cancel → proven gone; unknown results; orphaned results;
   wedges. Known fixed classes: `canceling` after a lost ack (`5e256028`), dust FAK venue id (`8ae05d3b`),
   double count that stranded shares (`eb78cbe0`). Hunt siblings: fills before ack, fills after cancel,
   partial fill + replace, restart mid-order, user-WS gaps, REST backfill double counting.
2. **Position and cash vs exchange truth.** Sources of fills (user WS, REST, onchain), reconciliation
   (`reconcile_interval_s=20`), drift handling, YES+NO merge, dust sweep, late fills into closed archives.
3. **Risk.** Caps derived in `session_config.py` from clips (`daily_loss_kill`, `max_total_exposure`,
   `max_event_group_loss`, `max_market_notional`): right for Oddin $200 + Steam/GRID $60 + LoL $5? Halts
   (`ws_stale_halt_s=30`, `user_ws_blind_halt_s=15`, heartbeat, `max_order_error_rate`): can a halt strand
   inventory? Do halts clear?
4. **Restart and recovery.** `resume_locked`: after a restart mid-map, does the bot still sell open positions?
5. **Latency.** Feed tick → model → quote → order submit: debounce 100 ms, quoter tick 2 s, blocking I/O on
   the event loop (sqlite, json, gzip, HTTP), model inference time. Where does live lose seconds vs the
   backtest's 85 ms assumption? Measure from local `core_trace` / `session.jsonl` timestamps where possible.
6. **Paper realism.** Paper gateway fills vs live. If paper results feed decisions, how biased are they?
7. **Error handling.** Swallowed exceptions, fail-open paths, retries that can place twice.

## Allowed

Read local archives (`session.jsonl`, `core_trace`) to confirm hypotheses; `replay_core_trace` on local
archives. No SSH (sun-devin reads VPS logs; add requests under "Needs from VPS").
