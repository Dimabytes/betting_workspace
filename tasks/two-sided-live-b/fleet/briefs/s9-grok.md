# Brief: s9-grok — STEP-009 alone (plan + implement, no separate review)

You are the only agent for STEP-009. No planner and no reviewer run for this step.

## Where

- Code: worktree `/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader-s8` (E8), branch `two-sided-s8`, own `.venv`. It has STEP-001..005 and STEP-008 (`56af02ed`). STEP-006/007 are being built in the main E checkout right now: never cd into, edit, stage, stash or commit in the main E checkout. Read their plans: `W/tasks/two-sided-live-b/plans/STEP-006.md`, `plans/STEP-007.md`.
- Skill doc: `W/.shared-skills/vps-trader/SKILL.md`. Commit ONLY that file in W on main (`git -C W add .shared-skills/vps-trader/SKILL.md` then commit just it; W has other uncommitted files: never stage them).
- E8 has no `.env`. Do not create or link one.

## Do

1. Read `~/.agents/skills/feature-json-implement-step/SKILL.md` and follow it. Task slug: `two-sided-live-b`. Step: `STEP-009`. No plan file: write your short plan at the top of your report.
2. Do every item in the step's `changes`. `summarize.py` stays stdlib only.
3. Handoffs you must cover:
   - STEP-003 added MERGED ledger rows. Check that `summarize --today` (and the B tree via `--root`) counts merge cash in the day total; fix it in summarize.py if not, with a self-check case.
   - Runbook env check for B keys must print only booleans (set / empty), never values. A blank `TG_CHAT_ID_B` does not stop B at start, so the runbook checks it.
   - `live_b` stop_grace_period is 240 s (merge up to 190 s + fence + drain), not 200 s.
   - 9 × $20 = $180 is shown as map room but the two-sided core does not enforce it; B's brakes are NET_MAX_SHARES 50, merges at $130 held, and wallet cash. Say so in the runbook.
   - After a B restart, a late fill of a pre-restart order pulls both bids for the rest of that map (fail-safe). Say so.
   - Start log line (STEP-007 plan): `trader wallet: strategy=… signature_type=… funder=…`; an empty BLAST Slam whitelist under two_sided refuses start. Use the exact strings from the plans; STEP-010 re-checks them against the final code.
   - Never print secrets in runbook commands: `docker compose config` interpolates `.env`; use `--services` or `--no-interpolate`.
4. Run `python3 src/dashboard/summarize.py --self-check`, the dashboard tests, `make lint` on staged, and the typecheck in E8.
5. Commit summarize.py (and its tests) in E8 on `two-sided-s8` (one commit). Commit SKILL.md in W on main (one commit, only that file).
6. Set `passes: true` for STEP-009 in `W/tasks/two-sided-live-b/feature.json` (edit only that value). Append to `W/tasks/two-sided-live-b/progress.txt`.

Report `R/reports/s9-grok.md`: plan, files changed, commands with results, both commit hashes, which runbook facts come from plans (not yet in code) so STEP-010 can re-check them, open issues.
