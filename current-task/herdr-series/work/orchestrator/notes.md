# Herdr experiment notes

Task: series-market feature.json. Plan + implement via Devin SWE-2 Max in Herdr. Review via built-in Grok 4.7 high. US-004 via one built-in Grok 4.7 subagent.

## US-001 plan

- started: 2026-09-25 (local evening)
- agent: us001plan
- pane: w1:pEM tab w1:tBP (series-us001)
- argv: devin --model swe-2-max --permission-mode bypass
- footer before prompt: `(bypass permissions on)`, model line `SWE-2 Max`
- prompt accepted, status working, reading AGENTS.md
- watcher: watch.sh us001plan us001 7200 (report herdr-series/reports/us001.md)
- DONE 2026-09-25T22:33:54Z, report size 24332, plan line 2 is `Status: FINAL`
- esports-trader was clean on `main` at handoff (`a647900b`)

## US-001 implement

- agent: us001impl pane w1:pEN
- argv: devin --model swe-2-max --permission-mode bypass
- footer before prompt: `(bypass permissions on)`, model line `SWE-2 Max`
- prompt accepted, status working
- planner pane w1:pEM closed after plan was FINAL
- watcher: watch.sh us001impl us001impl 7200
- DONE 2026-09-25T23:02:53Z, commit 3a804f01, report size 3415
- review grok-4.7-high: Comments: none. passes set true. implementer pane w1:pEN closed after written skip.

## US-002 plan

- agent: us002plan pane w1:pEP tab w1:tBQ (series-us002)
- argv: devin --model swe-2-max --permission-mode bypass
- footer before prompt: `(bypass permissions on)`, model line `SWE-2 Max`
- prompt accepted, status working
- watcher: watch.sh us002plan us002 7200
- DONE 2026-09-25T23:56:48Z, report size 35591, plan line 2 is `Status: FINAL`
- esports-trader still clean at `3a804f01` at handoff
- planner pane w1:pEP closed after plan was FINAL

## US-002 implement

- agent: us002impl pane w1:pEQ
- argv: devin --model swe-2-max --permission-mode bypass
- footer before prompt: `(bypass permissions on)`, model line `SWE-2 Max`
- prompt accepted, status working
- watcher: watch.sh us002impl us002impl 7200
- TIMEOUT 2026-09-26T01:57:41Z while still working, report size 0. AwaitShell did not return on that exit.
- Report landed later: 2026-09-26 05:20 local, Status FINAL, commit 861c142d. Agent idle, tree clean.
- Review grok-4.7-high: 2 blockers (series types stuck in run.py; empty book raises kill the shard).
- Fix sent to the same us002impl pane. New watcher timeout 4h.
- DONE, fix commit 185fdf2a. Both blockers addressed. passes set true.
- implementer pane w1:pEQ closed after the fix.

## US-003 plan

- agent: us003plan pane w1:pEW tab w1:tBW (series-us003)
- argv: devin --model swe-2-max --permission-mode bypass
- footer before prompt: `(bypass permissions on)`, model line `SWE-2 Max`
- prompt accepted, status working
- watcher: watch.sh us003plan us003 10800
- DONE, plan size 39607, line 2 `Status: FINAL`. esports-trader clean at `185fdf2a`.
- planner pane w1:pEW closed after the plan.

## US-003 implement

- agent: us003impl pane w1:pEX
- argv: devin --model swe-2-max --permission-mode bypass
- footer before prompt: `(bypass permissions on)`, model line `SWE-2 Max`
- prompt accepted, status working
- watcher: watch.sh us003impl us003impl 14400
- Report FINAL at 12:54 local, commit 4dab09f4, agent idle. Review grok-4.7-high: Comments: none. passes set true.
- US-004 handed to one built-in grok-4.7-high subagent (not Herdr).
- 2026-09-26 13:17: user said stop everything. No series backtests left running. Do not start the next seed until the user says so.
- 2026-09-26 13:31: user asked to run Dota series-line seed 0 on 6 shards, then one LoL series-line seed. Dota is shell 743218.
- 2026-09-26 14:02: LoL seed 0 uses SHARDS=4, not 6. Do not start it until the Dota seed exits.
