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

**signal.** One per feed event. `reason=model` means the booster ran. Anything else is a skip label. `entry_block` is why a model tick did not open (schema 2+), BUY side only. `exit_state` is the resting SELL's status (`live`, `canceling`, `pending`, `unknown`, or `none`) and `pos_yes` / `pos_no` are the held shares. `exit_state=canceling` with a nonzero position is a wedged exit. Rows written before 2026-09-22 omit all three.

**quote.** Engine placement batch. `decision` is `normal` or `reduce_only`. `fv_source` is `model` or `engine`. `placed[]` has token/side/price/size. `canceled[]` is order ids.

**fill.** Durable fill. `side` BUY/SELL, `position_after`, `net_cash` (engine cash after this fill), `second` (Steam game second), `ts_utc` (wall clock), `is_maker`, `fill_key`. `summarize.py --match` labels the fill `yes` or `no` from `yes_token_id` / `no_token_id`. Yes is not always the team that won. PaperGateway fills are simulated.

**tick_size_change.** Col**Blind spots.** `quote` rows are only written on a real place/cancel — "no rows" means either no targets or every target skipped inside the core. `entry_block` labels the BUY side only; it never says why a SELL is absent. For "bought but not selling" or "quoting stopped", replay the core state instead of guessing:

```bash
cd /root/work/esports-trader
PYTHONPATH=src uv run python /root/work/betting_workspace/.shared-skills/vps-trader/scripts/core_state.py <match_id>
PYTHONPATH=src uv run python /root/work/betting_workspace/.shared-skills/vps-trader/scripts/core_state.py --all --quiet
```

It prints a `VERDICT:` line first, then inventory, orders with status and age, fair, book, and the plan `requote` makes on the next wake. It exits 1 when any match is wedged. `--all` sweeps the recent live matches; add `--paper` for the paper root. One match costs 15-40s.

Wedged-exit signature: a SELL outside `status=live` for more than 30s. `sell_occupied` counts it and blocks every new sell for the rest of the map, whatever its status — `canceling` after a lost cancel ack (fixed in `5e256028`; pre-fix traces still show it), `pending` after a place that never reached the venue. A BUY stuck the same way pins its rung through `rung_occupied`.

Read the `digests` line, not a mismatch count: each state group is compared against what the live core recorded. `orders=OK inventory=OK` means the printed fields are the recorded ones byte for byte. A `DIFF` group is a struct the code changed after the trace was written — `mid_spike=DIFF` on pre-`28fd65f8` traces is that, and it does not touch the rest.

The trace is monotonic-clock based, so order ages are internal to that trader process. `written=...` on the `trace` line is the wall-clock age of the file. `CoreTrace` buffers and never fsyncs, so a live match's tail lags by a few rows.

**Wedge sweep from the logs, no replay.** The trader resends a cancel every cycle, so a pinned `cancel=` with `place=0` and a nonzero position is the same wedge, visible across every match at once:

```bash
docker compose logs live --since 12h | grep " requote " | awk '
{ c=-1;p=-1;pn=0;py=0;cid="";
  for(i=1;i<=NF;i++){ if($i~/^cid=/)cid=substr($i,5);
    else if($i~/^cancel=/)c=substr($i,8)+0; else if($i~/^place=/)p=substr($i,7)+0;
    else if($i~/^pos_no=/)pn=substr($i,8)+0; else if($i~/^pos_yes=/)py=substr($i,9)+0 }
  if(c>0&&p==0&&(pn>0||py>0)){ if(run[cid]==0)start[cid]=$4; run[cid]++;
    if(run[cid]>best[cid]){best[cid]=run[cid];bs[cid]=start[cid];be[cid]=$4;bc[cid]=c} }
  else run[cid]=0 }
END{ for(k in best) if(best[k]>60) printf "%s wedged_lines=%d %s..%s cancel=%d\n",k,best[k],bs[k],be[k],bc[k] }'
```

Over the 12h that contained the 2026-09-22 wedge it printed one line and no false positives: `0xfd0c4a wedged_lines=3143 10:44:26..11:03:59 cancel=1`. The `requote` line comes from the fork, not from `src/`.

**Warnings that name this class directly.** `docker compose logs live | grep -E 'orphaned|unproven|unmapped|undispatched'`:

- `trader order result orphaned kind=cancel|place ids=...` — no worker owned the venue result, so no core saw its ack and no `quote` row was written.
- `trader core cancel unproven id=cNN side=... age_s=...` — the watchdog: this order waited past 30s for a cancel the venue never proved. Also goes to Telegram as `trader cancel unproven`.
- `trader core cancel ack unmapped venue=...` — a proven cancel named a venue id this core never mapped.
- `trader core order undispatched id=cNN reason=...` — a place that never reached the venue, rejected at the seam.

ix traces still show it). Cheap corroboration without a replay: `docker compose logs live | grep requote` — `cancel=N` pinned nonzero with `place=0` and nonzero `pos_*` is the same wedge. `digest_mismatch>0` after a code change is expected; the state dump stays valid.

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
