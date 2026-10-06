# Context: feature.json orchestration, live-trader-dashboard

- Workspace repo (skills, tasks): /Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace
- Task folder: /Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace/tasks/live-trader-dashboard (slug `live-trader-dashboard`)
  - feature.json, plan.md (behavior spec), progress.txt, plans/<step-id>.md
  - run/ is the orchestration run dir (briefs, reports). Never commit run/.
- Code repo: /Users/dimabytes/work/polymarket/dota_2_bot/esports-trader (branch main). Read its AGENTS.md before any code work.
- Project rules: /Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace/AGENTS.md
- Phase config: /Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace/.feature-json.config.json
- Skills are plain files. Read the SKILL.md at the path the brief gives and follow it.

## Hard rules

- You run with auto-approve. No permission prompt will stop you. Never delete, overwrite, or destroy anything you did not create this run, except edits your brief explicitly allows: no `git clean`, `git reset --hard`, `git checkout --`, `git push`, no `kill`/`pkill` outside your own processes, no dropping/truncating files, dirs, rows, tables, or branches.
- Never edit ../poly-maker. It is frozen. Read-only.
- No SSH to the VPS / prod. No accounts, no money, no real orders.
- This machine has no live map. Local tests are allowed.
- Run Python from the project venv (`uv run` in esports-trader).
- Browser only via `agent-browser --session <your name>`; never `agent-browser close --all`.
- Write your report to the file the brief names (file, not chat). When it is complete, make its line 2 exactly: `Status: FINAL`. Then reply with only the report path.
- Do not run golden tests: `tests/test_extraction_oracle.py` and `tests/test_follow300_replay.py`. They run very long. Do not run the full `make test` either. Run only the tests your step adds or touches.
