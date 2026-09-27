# Brief: sun-devin — VPS docker logs → live error taxonomy mapped to HEAD

Report: `$R/reports/sun-devin.md`. Work dir: `$R/work/sun-devin/`.

The owner asked for one agent to read the live logs on the VPS. The owner warned: **old log errors may
already be fixed**. For every error class, decide `open at HEAD` / `fixed in <commit>` / `unclear`.

## SSH rules (the VPS trades real money)

- Use `ssh sun '<command>'`. Read `$W/.shared-skills/vps-trader/SKILL.md` and `log-map.md` first.
- Allowed, read-only only: `docker compose -f <compose.yaml> ps|logs`, `docker ps`, `docker logs`, `ls`, `cat`,
  `head`, `tail`, `grep`, `zcat`, `wc`, `du`, `df`, `find`, `stat`; `git -C /root/work/esports-trader log|status|
  diff --stat`; `python3 /root/work/betting_workspace/.shared-skills/vps-trader/scripts/summarize.py` with
  `--today`, `--game dota`, `--match <id>`, `--live`, `--rebate`; and
  `cd /root/work/esports-trader && PYTHONPATH=src uv run python /root/work/betting_workspace/.shared-skills/vps-trader/scripts/core_state.py --all --quiet`.
  Read both scripts locally first (`$W/.shared-skills/vps-trader/scripts/`) to confirm they only read.
- Forbidden: `restart`, `stop`, `up`, `down`, `build`, `rm`, `exec` that changes state, editing any file, `git
  pull|checkout|reset`, writing anywhere on the VPS, sqlite writes, `docker inspect` output with env vars,
  reading `.env`, printing secrets (PK, keys, tokens, RPC URLs). Keep SSH load light: no parallel loops of SSH
  calls, pull logs in a few large chunks.

## Tasks

1. **Pull logs.** `live`, `paper`, `compress` (esports-trader compose) and `archive-dota`, `compact-dota`,
   `onchain` (collector compose), as far back as available. Save compressed copies in `$R/work/sun-devin/`
   (each under 200 MB; drop DEBUG spam if needed).
2. **Taxonomy.** Count by type and by day: tracebacks/exceptions, `trading_error`, `risk_halt`/`HALTED`, 429,
   Steam 400 / `GetRealtimeStats` failures, feed dead / exhausted / abandoned, `orphaned|unproven|unmapped|
   undispatched`, `model startup blocked`, discovery skips (`live-paper skip:` reasons), Disir catalog errors
   (new today), WS reconnects, heartbeat failures, stale books, dust, wedges (the awk wedge sweep in
   `log-map.md`).
3. **Map to HEAD.** For each class: first/last seen, frequency, affected matches, and whether the code path
   still exists at local HEAD or was fixed (`git log -S` / `--grep` in `$E`; VPS HEAD `00c3dd19`).
4. **Money impact.** Link classes to maps and PnL (`summarize.py`): sessions that died mid-map with inventory,
   missed maps (discovery), halts that blocked trading, orders stuck.
5. **Collector health.** Gaps in book capture (reconnects, lag) and onchain ingestion gaps: they feed the
   dataset and the backtest. Give dates and durations.
6. **VPS vs local.** `git status` / `git diff --stat` on the VPS checkout (untracked edits?), model names in the
   VPS `data/new_model/production/model.json` vs local.
