# sun-devin — VPS docker logs → live error taxonomy mapped to HEAD
Status: FINAL

Scope: read-only audit of the `sun` VPS (docker logs for `live`, `paper`, `compress`, `archive-dota`, `compact-dota`, `onchain`), 793 synced live match archives (Aug 30 → Sep 26), collector parquet/validator state, VPS↔local git/model comparison. VPS trader HEAD `00c3dd19` (clean tree), local HEAD `bbb28897` (merge one commit ahead, adds `databet_*` + series-run backtest tooling; 3 `src/trader` files differ, none of them live-path). Collector VPS HEAD `7805712`, clean.

## Summary

- **S1 — Disir catalog flicker launches pinned-Oddin sessions with `oddin_match_id=None`** → `CorruptFeedPin` kills the match task, the rebind guard then refuses every relaunch (`ValueError refusing to rebind`) while discovery keeps emitting the market. Confirmed twice today (matches `9016905513`, `9017026154`); 10 of 14 all-time orphan session tapes sit in the Oddin era. Open at both HEADs; no commit addresses it. Today's occurrences happened after positions were flat, so no money loss was confirmed — mid-window it burns all 3 crash-restarts and orphans a live match.
- **S2 — Every deploy `docker compose restart` kills live sessions mid-flight and is the direct trigger of the S1 window** (Disir catalog `_open` roster is empty for ~40 s after each boot). Six wallet-host boots today; each truncates session tapes (no `session_end`, no `final`, no `execution_cleanup.json`).
- **S2 — Collector retains crossed books:** 1102/3782 Dota partitions (~29%) contain crossed-book rows; `parquet-validator.ts` records but never rejects them (asserted by its own test). Downstream filtering unverified — dataset/backtest contamination risk.
- **S2 — Heartbeat stale-id 400 loops → dead-man halt:** two `heartbeat_down_halting` events today (17:49, 18:05), each ~10–15 s blind with CRITICAL `blind:` alerts per open market. Root cause in frozen `poly-maker` gateway; auto-recovery works via resync.
- **S2 — Late joins miss the model window:** 11/333 Dota sessions joined >60 s after horn (worst +2502 s); several missed essentially all quoting edge.
- **S3 — Orphan tapes destroy post-mortem truth:** 14/331 Dota sessions lack `session_end`; 13/14 also lack `execution_cleanup.json`; `placed[]` records carry no order ids, so resting-order state is unprovable from tape. All residuals were ≤0.009 shares — no stranded inventory confirmed.
- **Money:** Polymarket-level day PnL +188.60 (cash basis incl. rebate +18.15; open mark 0.00); per-map realized: +187.87 / +11.25 / +1.43 / −7.50 / ~−23 (orphan `9017026154`). No confirmed net loss from today's crash loops.

## Findings table

| ID | Sev | Layer | Title | Conf | Impact |
|----|-----|-------|-------|------|--------|
| F1 | S1 | discovery→launch | Oddin pin emitted without `oddin_match_id` → `CorruptFeedPin` crash + rebind refusal orphan loop | confirmed | loss of all live quoting on affected map; can burn restart budget mid-window |
| F2 | S2 | ops/deploy | `docker compose restart` kills live sessions; restart = trigger of F1's cold-boot window | confirmed | truncated archives, ~2 min signal gaps, restarts consumed |
| F3 | S2 | collector→dataset | Crossed books kept in 29% of Dota partitions; validator never rejects | confirmed (data); downstream exposure unproven | possible label/feature contamination |
| F4 | S2 | engine (frozen) | Lost heartbeat POST → stale id 400 loop → `heartbeat_down_halting` ~10–15 s blind | confirmed | quoting halt windows ×2/day |
| F5 | S2 | discovery/coverage | 11/333 late joins >60 s post-horn, up to +42 min | confirmed | missed/partial model window |
| F6 | S3 | archive/observability | Orphan tapes: no `session_end`, no cleanup file, `placed[]` lacks order ids | confirmed | resting-order state unprovable; maps vanish from day PnL |
| F7 | S3 | archive | match.json rewritten at 14:35 UTC with no identified writer | observed | debuggability gap |
| F8 | S4 | engine→data-api | `positions_failed` 429 bursts skip reconcile rounds; api-key 400s at boot | confirmed | benign, delayed reconcile ≤5 min |
| F9 | S4 | logging | `discovery emit` lines are 46% of live-log volume | confirmed | noise buries events |

## Findings detail

### F1 — S1 — Oddin pin without match id: crash loop + rebind refusal

**Mechanism.** `_sources_for_sidecar()` stamps the Oddin source onto a market that already has a GRID/Steam source. When `unique_oddin_match_id()` returns `None` — card absent from `DisirCatalog._open` (cold boot ~40 s until first bootstrap), name-orientation mismatch, or card dropped by the 60 s `refresh_matches` pass when the match finishes — the emit still carries the pinned `oddin` source with `oddin_match_id=None`. `_build_feed()` then raises `CorruptFeedPin("match '…' binds oddin but has no match id")` at `esports-trader/src/trader/feed_selection.py:90-129`; emit construction at `src/trader/discovery.py:656-671`; None-conditions at `src/trader/oddin_discovery.py:80-113`.

The failed task is retried by the launcher with `MAX_CRASH_RESTARTS = 3` and 60/120/240 s backoff (`src/trader/wallet_host.py:1327-1344`). On relaunch the stored archive pin is compared against the fresh emit's `market` block; any drift in mutable sidecar-derived fields (`tick_size`, `min_order_size`, …) trips `ValueError refusing to rebind`, permanently orphaning the match even though discovery keeps emitting it (emits continued ~4 h on `9017026154` while the market stayed open).

**Evidence (2026-09-26 live log).**

- `9016905513`: emit 12:47:02 carried `oddin_match_id=None` (40 s after the 12:46:28 deploy boot — catalog still cold) → `CorruptFeedPin` 12:47:18. Session tape shows a 113 s signal gap (second 1425→1538). Recovered on next relaunch; closed cleanly, realized +11.25, rebate +0.53.
- `9017026154` (map 2): CorruptFeedPin at 14:28:02 and 14:29:58; `ValueError refusing to rebind` between them. The map had already finished quoting (last fill 13:49, position flat); session orphaned.
- `oddin bind skipped: no name match for LGD Gaming vs Team Yandex` ×93 in the log — the same card flickering in/out of the Disir open list.
- Archive-wide: 14 orphan Dota session tapes; 10 in the Oddin era (since ~Sep 19). Earlier GRID-era orphans have different causes.
- `DisirCatalog` cold-boot: `_open` empty until first bootstrap; `refresh_matches` (60 s) flips `is_closed` cards out mid-run — both windows confirmed in `src/trader/oddin_discovery.py` / catalog code; covered by test commit `8ddf839f` (fake-clock scan/refresh test).

**Money impact.** Today's two victims were already flat → zero confirmed loss. Counterfactual is the problem: mid-window occurrence burns 3 restarts (60+120+240 s) then abandons the match while it is still open and quotable — a full model-window forfeiture plus whatever resting orders are left (see F6).

**Status.** OPEN at VPS `00c3dd19` and local `bbb28897`. Candidate directions (not implemented): never emit a pinned oddin source when `unique_oddin_match_id` is None (emit unpinned/`waiting_for_feed` or fall back to the GRID source); narrow the rebind guard to id-provenance fields only, not mutable sidecar fields; prime the catalog before the first post-boot emit.

### F2 — S2 — Deploy restarts kill live sessions and open the F1 window

**Mechanism.** Deploys run `docker compose restart`; the wallet host re-boots in-container. Today: 6 boots (12:04, 12:21, 12:46, 14:27, 14:40, 20:19 UTC) inside one container — the first five align with deploy commits (e.g. `00c3dd19` at 20:18:59 → boot 20:19), only the last is a docker-level restart. Each boot hard-kills running match workers mid-tape: no `session_end`, no `final`, no cleanup file; maker state rebuilt from REST on resume (~7–11 s blind reconnect + cancel-all sweep).

**Evidence.** Boot timestamps vs deploy commit times (log + `git log`); session tape of `9016905513` truncated at second 1425 (≈12:46 kill), relaunch crashed at 12:47:18 exactly inside the catalog cold-boot window — the compound F2→F1 failure. `9016945136` and `9017026154` tapes end orphaned at deploy boundaries.

**Money impact.** Today's kills hit flat/post-window sessions; realized +168ish across the day anyway. The systematic costs: (a) every deploy during a live map forfeits ~2 min of signals/quotes; (b) every boot opens the CorruptFeedPin lottery for every pinned-Oddin market.

**Status.** OPEN. Graceful drain on SIGTERM (write `session_end`/`final`/`execution_cleanup.json` before exit) would fix the archive half; debouncing emits until the Disir roster is primed fixes the F1 half.

### F3 — S2 — Crossed books retained by the collector (29% of partitions)

**Mechanism.** `polymarket-collector/src/parquet-validator.ts:796-887` computes `crossedBookRows`, emits a warning, and still accepts the partition — asserted as intended behavior by `src/parquet-validator.test.ts:675`.

**Evidence.** compact-dota validation output: 1102 of 3782 Dota partitions (29.1%) with `crossedBookRows > 0` (daily replay over Aug 9 → Sep 26; partitions otherwise present every day, sizes consistent).

**Money impact.** Unproven but high ceiling: crossed books are impossible market states; if `prepare_dataset` or the book-derived feature/label readers do not filter them, they contaminate training rows and backtest midpoints → decisions trained on bad prices. I could not confirm a downstream filter in this pass (see Open questions). This is the single largest data-validity exposure found.

**Status.** OPEN / unclear — needs the dataset-side trace (cross-repo item).

### F4 — S2 — Heartbeat stale-id loop → dead-man halt

**Mechanism.** The poly-maker gateway chains heartbeats by POSTing the previous `heartbeat_id`; a lost POST response leaves the client holding an id the server never stored → subsequent POSTs 400. `gateway.py:412-437` clears `_hb_id` after failure; `engine.py:412-437` converts ≥3 failures into `hb_blind` → `heartbeat_down_halting` (all quoting stopped, CRITICAL `blind:` alert per market); `engine.py:546-568` resyncs local order state on recovery. Local `engine_seams.py:486-515` wraps boot-time failures in a grace window — boot 400s are held, mid-run ones are not. The 400 response body echoes the server-expected `heartbeat_ids`, but the client cannot adopt it (frozen upstream).

**Evidence (today).** 59 heartbeat lines, 36 invalid-id events, 18 heartbeat-endpoint HTTP 400s, 2 `heartbeat_down_halting` at 17:49:08 and 18:05:40, each resolving in ~10–15 s after 3–4 failures; 6 api-key 400s at each boot (benign, clear in ~10 s); 41 WS reconnects.

**Money impact.** ~10–15 s blind halt per event ×2 today while markets were open — quoting paused, maker orders left untouched on the book during the halt. No fill anomalies observed.

**Status.** OPEN in `../poly-maker` (frozen — described, not patched). Recovery path verified working.

### F5 — S2 — Late joins: 11/333 sessions joined >60 s after horn

**Evidence.** `joined_at_utc − horn_at_utc` across 333 Dota sessions: median −75 s (healthy), but tail: `8974185539` +2300 s, `grid-3006033-m2` +2502 s, `8977325814` +2075 s, `9007118998` +1830 s, `8974217041` +1067 s, plus six more >60 s. `grid-3006033-m2` never got a feed (`waiting_for_feed`) and `9007118998` had zero fills. Cause is structural: map-N markets and late-appearing Disir/GRID cards surface after the horn.

**Money impact.** Sessions joining minutes late forfeit most of the 9-minute model window — direct edge loss on those maps; flat outcome by luck of filling windows so far.

**Status.** OPEN (structural). Worth an alert when a Dota session is still unfed >90 s post-horn.

### F6 — S3 — Orphan tapes make resting-order state unprovable

**Evidence.** 14/331 Dota sessions lack `session_end`; 13/14 also lack `execution_cleanup.json` (written only on handled stop paths; deploy SIGKILL/SIGTERM never reaches it). `placed[]` quote records carry no order ids, so the tape cannot prove no resting orders survived. All orphans' last positions were ≤0.009 shares and `polymarket_today` shows `n_open=3` worth $0.00 — no stranded inventory. But orphan maps also never get `final` written → they drop out of `summarize --today` day sums (telegram `sum_net` = `session_end` maps only), so map-level PnL bookkeeping silently loses these maps.

**Status.** OPEN. Cheap fix: write `execution_cleanup.json` (or a `killed` marker + order snapshot) on every teardown path, and record order ids in `placed[]`.

### F7 — S3 — Unaccounted `match.json` rewrite

**Evidence.** `9017026154/match.json` has mtime 14:35 UTC with `market.tick_size=0.001` — written after the last crashed launch (14:29:58) and with no writer found among the three known `match.json` write paths (launch pin, `finalize_match`, `_apply_sidecar_update`). The tape's `tick_size_change` record (a CLOB WS event, second 1733 ≈ 14:10) matches the collector's own tick flip logged at 14:03, so the value is plausible — but the write path is unexplained. If an unknown writer mutates `market` post-pin, the F1 rebind guard can trip on real drift too.

**Status.** OPEN question — needs a write-path audit (possibly collector-side or a replay tool).

### F8 — S4 — Benign-but-noisy request failures

- `positions_failed` (HTTP 429 from the data-api positions endpoint): 35 today, 13:20→20:41. The gateway returns `{}` on failure → the reconcile round is skipped rather than trusting an empty snapshot — correct behavior; worst effect is inventory reconcile delayed to the next poll (≤5 min).
- API-key HTTP 400 ×6: one per boot, clears in ~10 s.
- WS reconnects ×41: normal churn across market sockets.

### F9 — S4 — Log volume dominated by emits

`discovery emit` = 1506 of 3241 lines (46%) in today's 9 h window. Real events (crash, halt, skip) drown in emit spam — the taxonomy above took repeated narrowing greps. Suggest demoting emits to DEBUG or per-market dedup.

## Error taxonomy — live log 2026-09-26 (12:04–20:57 UTC, 3241 lines)

| Class | Count | First / last | Notes |
|-------|-------|--------------|-------|
| discovery emit | 1506 | — | volume noise (F9) |
| skip: `league_not_whitelisted` | 145 | each boot | expected filter |
| heartbeat-related lines | 59 | boot→20:41 | incl. 36 invalid-id, 18 endpoint 400s |
| WS reconnects | 41 | — | churn |
| `positions_failed` (429) | 35 | 13:20 / 20:41 | F8 |
| skip: `title_blacklist` | 21 | 12:46+ | new feature working |
| timeouts | 18 | — | mixed request timeouts |
| tracebacks | 16 | — | crash/relaunch handling |
| risk/blind events | 15 | 17:49, 18:05 clusters | F4 |
| session ends | 15 | — | normal + restart-driven |
| Disir catalog events | 11 | — | incl. bind flicker (F1) |
| `canceled_mid_cycle` | 7 | — | benign cancel path |
| orphan/undispatched lines | 7 | — | reaper reporting |
| api-key 400 | 6 | boots | clears ~10 s |
| task failures | 3 | 20:44 LoL quote task etc. | recovered |
| `CorruptFeedPin` | 2 | 12:47, 14:28 | F1 |
| `heartbeat_down_halting` | 2 | 17:49, 18:05 | F4 |
| `no_usable_feed` skips | 2 | — | market closed before feed |
| `refusing to rebind` | 1 | 14:29 | F1 |

Archive-wide session tape stats (4 weeks, 331 sessions): `trading_error` ×6 total (5 `place:validation` Aug-era, 1 `cancel:cancel_failed`) — historical, not seen since; `late_fill` ×5 (Aug 31–Sep 21). Orphans 14. core_state `--all` sweep: **all VERDICT: OK — zero wedges/stuck-order states** in 648 archives.

## Per-map Dota PnL — 2026-09-26

| Match | Map | Feed | Fills | session_end | realized |
|-------|-----|------|-------|-------------|----------|
| 9016905513 | 1 | oddin | 4 | yes | +11.25 (+0.53 rebate) |
| 9016945136 | 1 | oddin | 0 | NO (orphan) | 0.00 — blacklisted-title session launched pre-deploy |
| 9017026154 | 2 | oddin | 11 | NO (orphan) | ~−24.8 cash; map lost to day sums (F6) |
| 9017174132 | 1 | oddin | 5 | yes | −7.50 |
| 9017299527 | 2 | oddin | 4 | yes | +187.87 |
| grid-3008569 | 1 | grid | 0 | yes | 0.00 |
| grid-3008569 | 2 | grid | 2 | yes | +1.43 |

Polymarket-level (`summarize.py` semantics `cash = −buy + sell + redeem + rebate`; `pnl = cash + open mark`): buy 1547.03, sell 1717.48, redeem 0, rebate +18.15 → cash +188.60, open mark 0.00 → PnL +188.60. Open inventory: 3 positions ≈ $0. Maker rebate accrued since payout: +6.39 (Dota +6.18, 46 fills, 7 matches). Note orphan `9017026154`'s ~−24.8 sits in the wallet ledger but not in map sums (F6).

## Architecture / performance / debuggability notes

- The restart-boundary design is the systemic weakness: discovery emits are trusted immediately after boot while the Disir catalog takes ~40 s to prime; launches on stale-but-pinned archives hit `CorruptFeedPin`; the rebind guard then converts a transient data gap into a permanent orphan (F1+F2 compound).
- Mutable sidecar fields (`tick_size`, `min_order_size`) participate in the rebind identity check — collector tick flips (observed 0.01→0.001 at 14:03 on `9017026154`) or any rewrite (F7) turn into rebind refusals. Pin identity should be id-only; mutable fields should update in place (the live `_apply_sidecar_update` path already exists for this).
- Restart budget (`MAX_CRASH_RESTARTS=3`, 60/120/240 s) is spent on deterministic failures that cannot succeed (missing id, rebind refusal) — backoff retries should distinguish retryable from permanent launch errors.
- Session journal gaps: orphan tapes lose `session_end`/`final`/cleanup → the archive cannot distinguish crash-vs-kill-vs-clean and day PnL silently drops maps (F6). Order ids absent from `placed[]` blocks resting-order forensics.
- Feed mix shift: zero Steam-fed Dota sessions in the whole sample; September ≈ 240 GRID vs 76 Oddin — Oddin is new enough that its defects (F1) cluster in the last week.
- Logging: emit spam (F9); heartbeats/alerts are greppable but the event field is not consistently structured (mixed plain-text and JSON lines).

## Checked and OK

- Title blacklist works: 21 skips today; `9016945136`'s 12:26 launch predates the 12:45 deploy — timeline-consistent, not a bypass. (Commits `3100f0ba`/`66c24c68`/`4e07b363`.)
- No stranded inventory: all 14 orphan tapes end ≤0.009 sh; `polymarket_today` open mark $0.00.
- `core_state.py --all --quiet` over all local archives: every verdict OK; no wedges. Seven `canceled_mid_cycle` today are the benign cancel path.
- `task_died_restarting` (20:44, LoL quote task): recovered, forced reconcile showed 0 open orders / 0 positions, session net +1.47.
- Collector archive continuity: daily partitions present Aug 9 → Sep 26; `market stream reconciled` heartbeat gaps ≤3 min; `discovery poll issue` WARN ×16 over two days (keyset timeouts, id-not-found) all recovered.
- compact-dota on schedule; onchain healthy: 5-min cadence, expected ~65-min nightly recompute gaps, `lastFullDay=09-25`, `uncoveredTokens=[]`.
- Models identical VPS↔local: production `20260924T183900Z`, production-noxp `20260924T183921Z` (+ research pair). Oddin→`production-noxp` satellite at $200 clip is intended design (`00c3dd19`), not mispinning.
- `tick_size_change` tape record = CLOB WS event, not sidecar corruption — matches collector's 14:03 tick flip.
- Historical `trading_error`/`late_fill` counts are old and stopped — no open regression there.
- VPS trees clean: trader `00c3dd19`, collector `7805712`; local trader is one merge ahead (`bbb28897`, +5644/−147 mostly series-run backtest tooling; trader-path diff limited to new `databet_*` modules + 4-line codec change — not deployed on VPS).

## Open questions / Needs from VPS

1. Do `src/prepare_dataset` / backtest readers filter crossed-book rows? (F3 — highest open exposure; needs dataset-side trace.)
2. Who rewrote `9017026154/match.json` at 14:35 UTC? Enumerate all write paths incl. replay/maintenance tools (F7).
3. Can we get `docker inspect` restart counts / an external supervisor log to confirm the in-container reboot mechanism rather than inferring from boot lines? (Only read-only commands were permitted; current evidence is boot-line inference.)
4. Should orphan teardown write a `killed` marker + open-order snapshot? Today it writes nothing (F6).
5. Oddin-era orphan cluster began ~Sep 19 — confirm first Disir deploy date to bound F1's introduction window precisely.
