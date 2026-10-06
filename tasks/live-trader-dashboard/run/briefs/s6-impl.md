# Brief: implement STEP-006

Role: implementer. Read and follow /Users/dimabytes/.claude/skills/feature-json-implement-step/SKILL.md.
- Slug: live-trader-dashboard. Step: STEP-006 only.
- Plan: /Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace/tasks/live-trader-dashboard/plans/STEP-006.md
- Edit code in /Users/dimabytes/work/polymarket/dota_2_bot/esports-trader (branch main). Commit there. Do not push.
- Do NOT set passes: true in feature.json. The orchestrator sets it after review. Do append to progress.txt (do not commit it).
- Run the tests the step adds plus the related existing tests, typecheck and lint. No live map runs on this machine.
- New code has no comments. If you refactor a function, remove its old comments.
- Report file /Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace/tasks/live-trader-dashboard/run/reports/s6-impl.md: line 1 commit hash(es), line 2 `Status: FINAL`, then changed files, test/typecheck commands with results.
- Stay in this session after the report. The orchestrator will send review findings here later.
- Browser check: `agent-browser --session s6-impl` against local Streamlit on 127.0.0.1 with fixtures, at 1366x768 and 1440x900. Stop your Streamlit process when done. Put screenshots in /Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace/tasks/live-trader-dashboard/run/work/s6-impl/.
