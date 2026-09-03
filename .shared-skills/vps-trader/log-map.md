# Trader log map

Read from SKILL.md when a reason/kind is unclear. Do not load this for a simple PnL question.

## Per-match files

Host trees: `data/trader_live/<id>/` (live process), `data/trader_paper/<id>/`
(paper process), plus the pre-rollout `data/live_paper/<id>/` tape. In-container
the process still writes `data/trader/<match_id>/`.

| File | Role |
|---|---|
| `match.json` | Schema 5 + `game`. Start document, then finalized with `final`. Winner from last snapshot's surviving ancients. `pnl` is engine handoff, often zero after flatten. Missing `game` on read → `dota` |
| `session.jsonl` | Trade tape. Schema 6. One compact JSON object per line |
| `state.jsonl` | Raw Steam `GetRealtimeStats` plus `request_started_at_utc` / `received_at_utc`. Huge. Tail only. Dota Steam source |
| `grid_state.jsonl` | Raw GRID socket frames. LoL archives this; Dota GRID source does too |
| `execution_cleanup.json` | `{match_id, condition_id}` once orders on that market are proven gone |

Skip the `wallet/` directory when listing matches.

## `session.jsonl` kinds

**session_start.** Public ids, model name/trained_at, `execution_mode` (`live` or `paper`), sidecar binding (teams live in `match.json` and in `sidecar_binding.outcomes`).

**signal.** One per feed event. `reason=model` means the booster ran. Anything else is a skip label. `entry_block` is why a model tick did not open (schema 2+).

**quote.** Engine placement batch. `decision` is `normal` or `reduce_only`. `fv_source` is `model` or `engine`. `placed[]` has token/side/price/size. `canceled[]` is order ids.

**fill.** Durable fill. `side` BUY/SELL, `position_after`, `net_cash` (engine cash after this fill), `second` (Steam game second), `ts_utc` (wall clock), `is_maker`, `fill_key`. `summarize.py --match` labels the fill `yes` or `no` from `yes_token_id` / `no_token_id`. Yes is not always the team that won. PaperGateway fills are simulated.

**tick_size_change.** Collector tick strings.

**trading_error.** `phase` + `error_type` only. Never exception text.

**session_end.** `terminal_reason`, leftover `positions` by token id, `net_cash`, `inventory_value`, `equity`. Null cash means trading never started or values were not finite.

Telegram / docker (prefix stays `live-paper`; first identity line is `GAME · live|paper · kind`):

```
live-paper session started
DOTA · live · map_winner
Aurora vs Team Secret · map 1
dota2-aurora-secret-game1
8944931337

live-paper session finished
DOTA · live · map_winner
Aurora vs Team Secret · map 1
dota2-aurora-secret-game1
8944931337
net +1.7600
realized 1.2500  imv 0.5000  rebate 0.0100
leftover yes 2.0000  no 0.0000
```

Finish, feed-dead, and exhaustion reuse the identity block. `net = realized + imv + rebate` when realized and imv are both present, else `n/a`.

## Signal reasons

| reason | Meaning |
|---|---|
| `model` | In window, books usable, booster called |
| `pre_horn` | Not state 5 with `second >= 0`. Draft/strategy/spawn clocks are not minute nine |
| `paused` | Steam pause |
| `outside_window` | `second > 599` (model window is 0..599 inclusive) |
| `finished` | Post-game |
| `stale` | Feed watchdog, not a fresh snapshot |
| `missing_book` | MDS has no usable YES or NO book |
| `one_sided_book` | Bid or ask missing |
| `crossed_book` / `nonfinite_pair` / `out_of_range_pair` / `pair_out_of_tolerance` | Broken YES/NO pair |
| `missing_prior` | Map-load prior missing |
| `resume_locked` | Restart mid-match, entries blocked |
| `sidecar_fault` | Sidecar gone/changed/not tradeable |
| `model_error` / `trading_error` | Fail closed that tick |

## Entry blocks (on `reason=model`)

| entry_block | Meaning |
|---|---|
| `none` | Entry allowed |
| `cutoff` | `second >= 540`, no new buys |
| `min_delta` | `abs(fair - market) < 0.01` |
| `nw_velocity` | 30s net-worth move above 350. Dota and LoL. |
| `missing_nw` | No net-worth for the velocity gate. Dota and LoL. |
| `off_grid` | CLOB tick coarser than 0.01 |
| `position_open` | Still in the clip, no second entry |
| `no_edge` | Join bid has no edge vs fair |

Trading window: Steam `game_state==5` and `0 <= second <= 599`. State 2 draft often has a positive clock; that is still `pre_horn`. LoL is GRID-only.

## Wallet sqlite (`live.db`)

Tables: `fill_ledger`, `fill_outbox`, `positions`, `token_cid`, `wallet_day`, `wallet_identity`.

`fill_ledger.cash_delta` is signed engine cash. `SUM` over MATCHED+CONFIRMED is wallet inventory, not a match report. Map tokens to a match via `session_start.sidecar_binding.outcomes[].tokenId` or `match.json` `yes_token_id` / `no_token_id`.

Do not print PK, browser address, Steam keys, or Telegram tokens. `.env` stays closed unless a health check specifically needs `STEAM_KEYS` presence (yes/no only).

## Docker logs worth grepping

```
live-paper session started
live-paper session finished
live-paper session exhausted
live-paper session abandoned
trader assigned
trader idle
discovery emit:
live-paper rebind:
live-paper skip:
live-paper feed_selected:
live-paper launch:
risk_halt
HALTED
429
GetRealtimeStats
model startup blocked
```

`discovery emit` / `rebind` / `skip` / `feed_selected` / `launch` are INFO. Cycle
summaries (`discovery cycle:`) are DEBUG. `skip` reasons:
`own_final`, `occupied_other_cid`, `own_cleanup`, `cid_in_use`,
`no_usable_feed`, `unreadable_archive`, `duplicate_owned_archives`,
`canonical_id_collision`, `pinned_binding`. The same skip is not logged every
tick.

| Line | Fields |
|---|---|
| `discovery emit:` | `game`, `match_id`, `steam_match_id`, `grid_series_id`, `map`, `slug`, `cid`, `archive_id_kind=grid\|steam` |
| `live-paper rebind:` | `old_match_id`, `new_match_id`, `cid`, `map`, `steam_match_id`, `grid_series_id` |
| `live-paper skip:` | `reason`, `match_id`, `cid`, `record_cid`, `archive_cid`, `steam_match_id`, `grid_series_id` |
| `live-paper feed_selected:` | `match_id`, `feed_source`, `steam_delay_s`, `grid_delay_s`, `cid` |
| `live-paper launch:` | `match_id`, `cid`, `archive_id_kind`, `steam_match_id`, `grid_series_id`, `map` |

`match_id` is the archive directory. When GRID series is known it is
`grid-<series>-m<map>` even if the picker chose Steam. `steam_match_id` is the
Valve id. `archive_id_kind=steam` is the numeric fallback when there is no
series id.

Steam 400 on one `server_steam_id` for a whole game, while neighbors return 200, is a known Valve miss. The feed dies after 30 consecutive non-2xx, then WalletHost backoff 60/120/240s, max 3 restarts.
