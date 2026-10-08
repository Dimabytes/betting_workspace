# Orchestrator notes (survives context compaction)

Owner decisions (in chat, 2026-10-08):
- Steps 1, 9, 10: one grok agent each (fleet `grok`), no plan/review.
- Steps 2-8: plan = Claude subagent opus effort xhigh; implement+fix = herdr `grok`; review = herdr `sol-xhigh` (feature-json-step-review) + herdr `swe-high` (feature-json-no-comments-review), in parallel.
- Bypass for herdr agents allowed by owner.
- Commit in W at the end: tasks/two-sided-live-b, .feature-json.config.json, .herdr-fleet.config.json (sol-xhigh added). No push.
- After STEP-010: run briefs/bt-sweep.md (grok), run 3 only if runs 1 and 2 both win.
- Planner runs one step ahead (pipelined) to save time.

Log:
- STEP-001 done, E 8dadf747, regression 20 maps identical. Untracked dirs ts-regress-before/after in E data/backtests (tracked dir tree; STEP-010 decides).
- STEP-002 plan done (plans/STEP-002.md).
- Note for STEP-010: env has PYTEST_N=10; serial run needs PYTEST_N unset.
- STEP-003 plan done. Handoffs: STEP-004 must branch on `merged` before `fill_for_key` (returns None for merge rows) and add a merged-row reader. STEP-009 must check `summarize --today` counts Polymarket MERGE activity in the day total.
- STEP-002 committed 1d380527; reviews: sol 1 major + 1 minor, devin comments; sent to s2-impl for fix.
- STEP-004 plan done. Handoffs: STEP-005 must call consume_core_outbox after apply_merge (True or False); a consume error must not turn the merge outcome into unknown. STEP-006: open_core must set the outbox cursor to the head (_outbox_head), else a clean core replays old fills/merges.
- STEP-002 passes (b86cdcdc). STEP-003 impl launched (s3-impl). STEP-005 planner launched. Helpers: source fleet/work/orchestrator/fleet.sh (closeagent, setpass, launch, mkimpl); review briefs: mkrev pattern in briefs/s2-rev.md / s2-com.md (sed s2->sN, commit range).
- STEP-003 committed 4a4018fb. Pre-existing failing tests per s3-impl (not verified by me): test_user_stream_is_bound_on_the_host_run_path (WalletHost no _mode, wallet_host.py:1238), test_home_renders_all_sections ('доступно' missing). STEP-010 must make full pytest pass.
- STEP-005 plan done. Handoffs: STEP-006 — PairMerger has no sweep(), MatchWorker._quiesce calls self._dust.sweep(force=True): widen the slot type; merge_all() after the proven fence and before end_snapshot / zero_token_sizes / unregister_worker; wait_inflight can take 190 s before orders cancel. STEP-007 calls install_adapter_merge only for two_sided. STEP-008: 200 s stop grace does not cover merge + fence (240 s would).
- STEP-003 passes (c779fa21). Handoff for STEP-005: hold_merge both tokens BEFORE relayer submit; release_merge on EVERY terminal outcome after core delivery (hold is process memory). wallet_store.py is 999 lines.
- STEP-004 committed 624fb55f; reviewers s4-rev/s4-com launched. STEP-006 planner launched early (all handoffs passed).
- STEP-004 passes (7b1b6990). Fix changed shared src/trader/core_recovery.py (accept_if_proven replays pending outbox before ledger read) -> STEP-010 must prove A regression (test_follow300_replay, test_strategy_core, recovery tests).
- STEP-006 plan done. For owner summary: (a) a late fill of a pre-restart order pulls both bids for the rest of that map (accepted fail-safe); (b) two-sided core ignores Budget: "9 x $20 = $180" is not a limit B enforces (STEP-008). Handoffs STEP-007: same ctor args in _pick_and_run, B still loads dota model, install_adapter_merge only for two_sided.
- STEP-005 committed da5ce546; reviewers s5-rev/s5-com launched. STEP-007, STEP-008 planners launched in parallel.
- STEP-008 plan done: stop_grace 240s (not 200s, merge 190s + fence); $180 budget shown but not enforced (config comments say so); blank TG_CHAT_ID_B does not stop B -> STEP-009 runbook needs env check printing booleans only.
- STEP-007 plan done: read_dota_strategy (unset->follow300), require_two_sided_start; whitelist from [clips.dota].tiers names; empty whitelist under two_sided refuses start; A gets one new info log line. Existing run() tests in test_trader_wallet_host.py hit real Disir via refresh_now (network) — note for STEP-010.
- STEP-005 review: sol 1 blocker (F1 lock vs tx thread) + 4 major (F2 eligibility at send, F3 mined merge lost on booking failure, F4 builder signing crosses phase boundary, F5 HTTP status as explicit rejection); devin none. Sent to s5-impl. s5-rev pane kept alive to verify F1-F5 fixes once (deviation from skill, money path).
- STEP-005 fix1 2fe56a67: sol verify -> F2/F4/F5 resolved, F1/F3 partly, new major N1. Sent round 2 to s5-impl. Plan: one more sol verify, then pass regardless (record leftovers for owner).
- 05:45 parallel lane: STEP-008 in worktree ../esports-trader-s8 branch two-sided-s8 (s8-impl). Cherry-pick onto main only when no agent works in E main. Then STEP-009 in same lane. Lane A: s5 fix2 -> s6 -> s7 in E main. Added 00-context rule: never print secrets (compose config interpolates .env).
- STEP-008 committed be0cc83c on two-sided-s8; reviewers s8-rev/s8-com launched.
- STEP-008 passes: 56af02ed on two-sided-s8 (cherry-pick TODO). STEP-009 launched in same worktree/branch.
- STEP-005 passes (ec9ebe90). STEP-008 cherry-picked to main a58d19c9 (tests ok). s5-rev verify-2 runs parallel with STEP-006.
- STEP-009 passes (set by agent). E8 commit 276798f1 (summarize.py) -> cherry-pick TODO at quiet point between s6 and s7. W commit 7fee213 (SKILL.md). STEP-010 must re-check runbook facts copied from plans (trader wallet log line, whitelist refusal text, discovery skip reason=title_whitelist, restart late-fill). log-map.md in vps-trader still says wallet cash MATCHED+CONFIRMED (B adds MERGED). After cherry-pick: git worktree remove ../esports-trader-s8 + delete branch two-sided-s8 (mine).
- STEP-006 committed 77a85364 (match_worker.py 1391 lines). STEP-005 verify2: F3,N1 resolved; F1 partly; NEW N2 major (deadline socket shutdown leaks dup descriptor) -> s5-fix3 grok agent at quiet point after s6 cycle.
- STEP-009 cherry-picked to main 2ebea5e6; worktree esports-trader-s8 + branch removed. s5-fix3 launched in E main while s6 review runs; s6 fix must wait for s5-fix3.
- STEP-006 review: sol 2 major (clean restart reuses durable execution identities; cancellation lets submitted final merge outlive worker/store) + 1 minor (test harness typing); devin findings. Waiting s5-fix3 before sending to s6-impl.
- s5-fix3 done f605a6b9 (F1 header-read deadline, N2 fd leak). STEP-005 fully closed.
- 06:46 STEP-007 launched in worktree ../esports-trader-s7 branch two-sided-s7 (base f605a6b9) parallel with s6 fix in E main. Cherry-pick after s6 fix. Then remove worktree+branch.
- s6-impl fix prompt got stuck as '[Pasted text]' in cursor input (lost ~10 min). Use send() from fleet.sh for follow-ups: flushes a stuck paste.
- STEP-007 committed 629657b0 on two-sided-s7; reviewers launched.
- STEP-007 review: sol 1 major (admission accepts non-BLAST clip tiers); devin comments. Sent to s7-impl 07:10.
- STEP-007 passes: d9b6d3f0 on two-sided-s7 (cherry-pick TODO after s6 fix commit).
- 07:20 STEP-006 passes (5ed8a8a3). STEP-007 cherry-picked as 2275bbb2 (255 tests ok), worktree s7 removed. STEP-010 gate launched (s10-grok).
- 07:43 STEP-010 green (E a7d72621, W f20b455). bt-sweep launched (grok).
- 08:23 bt-sweep done (E 903e0e57): n30 win, g4e-4 not, run 3 skipped. Orchestrator recheck of engine_pnl worst map/t matches. Final W commit next.
