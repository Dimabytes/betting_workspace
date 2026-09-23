---
name: herdr-fleet
description: Run many coding agents in parallel through Herdr (devin SWE-2, codex Luna, cursor Grok) for research or review fan-out. Use when the user asks to orchestrate several models via herdr, spawn N subagents in herdr panes, or split a big investigation across devin/codex/cursor agents that report through files.
---

# Herdr fleet

Load the `herdr` skill first for CLI basics. This file is what the 2026-09-23 LoL research run learned.

## Layout

- Max 10 agents per tab. More than 10 → another tab. Do not squeeze agents into the caller's tab.
- `herdr tab create --workspace "$HERDR_WORKSPACE_ID" --label <name> --no-focus`.
- 2 columns × 5 rows. `--ratio` is the share the **original** pane keeps:
  split root `right 0.5`, then each column `down 0.2`, `down 0.25`, `down 0.3333`, `down 0.5`.
- Always `--no-focus`. Parse pane ids from the JSON.

## Launch (via `scripts/launch.sh`)

Set `R=<run dir>` first. The script cd's the pane to `$R` (stray relative writes land there, not in the code repo), starts the agent, sends the standard prompt.

| kind   | model          | args after `--`                                                                              |
| ------ | -------------- | -------------------------------------------------------------------------------------------- |
| devin  | SWE-2 Max      | `--model swe-2-max --permission-mode bypass`                                                 |
| codex  | GPT-6 Luna max | `-m gpt-6-luna -c 'model_reasoning_effort="max"' --dangerously-bypass-approvals-and-sandbox` |
| cursor | Grok 4.7 High  | `--model grok-4.7-high --force --trust --sandbox disabled --add-dir <code repo>`             |

- Model ids drift. Check: `cursor-agent --list-models`, `devin models list | grep -oE 'swe-2[a-z0-9-]*' | sort -u`, `codex debug models`.
- Default mix: mostly devin, 2–3 luna (cheap, good for tape/stats), 1–2 grok (fast, strong on code reading).
- Rough time per deep task: grok 15–30 min, luna ~40 min, devin 60–80 min.

## Gotchas

- **Bypass is blocked by the Claude Code auto-mode classifier** ("Create Unsafe Agents") until the user says bypass is allowed in chat. Ask once, up front.
- **Devin ignores `--permission-mode smart`** when the env has `DEVIN_PERMISSION_MODE=bypass`. Trust the footer, not argv: it must say `(bypass permissions on)`.
- Prompting a working devin queues the text: footer `Press Enter to send queued messages now` → `herdr agent send-keys <name> enter`.
- `agent_status` alone lies (idle/done while still writing). Completion = report file stable + status not `working` (`scripts/watch.sh`).
- A second task: cursor and codex can take a new prompt in the same session (keeps context). Devin: fresh pane per new task.

## Files, not chat

```
$R/00-context.md       goal, known facts/numbers, hard rules, report format
$R/briefs/<name>.md    one brief per agent
$R/reports/<name>.md   agent output (follow-ups: <name>-<topic>.md)
$R/work/<name>/        agent scratch scripts and outputs
```

Hard rules to put in `00-context.md`: read-only on code repos; write only to `work/<name>/` and the report; no SSH to prod; no tests/training/full backtests unless the brief allows; run Python from the project venv; every claim with `file:line` + command + numbers; mark `verified` / `likely` / `speculative`.

## Watch

One background watcher per agent: `R=... scripts/watch.sh <name> [report-basename]` with `run_in_background`. It exits on DONE / BLOCKED / GONE / TIMEOUT (90 min), which wakes you. No polling in between.

## Verify

- Give the most suspicious topic to 2 agents of different models. Overlap finds more.
- Re-check every key number yourself with a small script before it goes in the final report. In the LoL run three agents repeated one wrong claim, and one generalized from a single capture. Keep a "refuted" list in the report.
- Close only panes and tabs you created, and only when the user no longer needs them.
