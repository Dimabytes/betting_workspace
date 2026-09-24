---

## name: herdr-fleet

description: Run many coding agents in parallel through Herdr (devin SWE-2, codex Luna, cursor Grok) for research or review fan-out. Use when the user asks to orchestrate several models via herdr, spawn N subagents in herdr panes, or split a big investigation across devin/codex/cursor agents that report through files.

# Herdr fleet

Load the `herdr` skill first for CLI basics. This file is what two 2026-09-23 runs learned
(LoL live-gap analysis, 10 agents; LoL fast-feed search, 14 agents).

## Layout

- Max 10 agents per tab. More than 10 → another tab. Do not squeeze agents into the caller's tab.
- `herdr tab create --workspace "$HERDR_WORKSPACE_ID" --label <name> --cwd "$R" --no-focus`.
- 2 columns × 5 rows. `--ratio` is the share the **original** pane keeps:
  split root `right 0.5`, then each column `down 0.2`, `down 0.25`, `down 0.3333`, `down 0.5`.
- Always `--no-focus`. Parse pane ids from the JSON.

## Launch (via `scripts/launch.sh`)

Set `R=<run dir>` first. The script cd's the pane to `$R` (stray relative writes land there, not in the code repo), starts the agent, sends the standard prompt.

| kind     | model                    | args after `--`                                                                              |
| -------- | ------------------------ | -------------------------------------------------------------------------------------------- |
| devin    | SWE-2 Max                | `--model swe-2-max --permission-mode bypass`                                                 |
| codex    | GPT-6 Luna max fast      | `-m gpt-6-luna -c 'model_reasoning_effort="max"' -c 'service_tier="priority"' --approve-for-me` |
| cursor   | Grok 4.7 High            | `--model grok-4.7-high --force --trust --sandbox disabled --add-dir <code repo>`             |
| opencode | Muse Spark 1.3 Free xhigh | `--auto` — model+variant+permissions from `$R/opencode.json` (see below)                     |

codex Fast = `service_tier="priority"` (luna's fast tier; user config.toml sets `"default"`, so pin
it with `-c`). Verify in the welcome card: `GPT-6-Luna max fast`. `--approve-for-me` routes approvals
through the auto-review model inside the workspace-write sandbox — NOT `--dangerously-bypass-…`
(YOLO).

opencode's bypass flag is `--auto` (auto-approves everything not explicitly denied). The TUI has no
variant flag (`--variant` dumps help; `model#variant` errors in v1). Drop this into
`$R/opencode.json` before `agent start`; the pane cd's to `$R` so it is picked up:

```json
{
  "agent": { "build": { "model": "opencode/muse-spark-1.3-contributor-free", "variant": "xhigh" } },
  "permission": { "*": "allow", "question": "deny", "plan_enter": "deny", "plan_exit": "deny" }
}
```

`"*": "allow"` is the config-level bypass (verified: runs shell without prompting); the three denies
stop the agent opening interactive prompts (question, plan mode) that would hang an unattended pane.
Verify in the footer: `Build auto · Muse Spark 1.3 Free · xhigh`. For one-off `opencode run`,
`-m opencode/muse-spark-1.3-contributor-free --variant xhigh` works.

- Model ids drift. Check: `cursor-agent --list-models`, `devin models list | grep -oE 'swe-2[a-z0-9-]*' | sort -u`, `codex debug models`, `opencode models opencode --verbose` (lists variants per model).
- All four CLIs have web search, Exa and Brave; `tvly` works from the shell.

## Which model for what

| model | good at                                                                                      | watch out                                                                                                               | time per deep task |
| ----- | -------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------- | ------------------ |
| devin | web research, browser reverse engineering (signed APIs, protobuf over MQTT), Discord digging | most wrong claims came from devins, one badly wrong latency; ends its turn with a WIP report; queued prompts need Enter | 15 min – 2.5 h     |
| grok  | protocols, endpoints, code reading, exact numbers (matched my re-check to 0.05 s)            |                                                                                                                         | 15–30 min          |
| luna  | local data and stats, careful cohort definitions; 0 refuted claims in 6 tasks                | slow on big tapes (up to 2 h);                                                                                          | 35 min – 2 h       |
| muse  | free tier (opencode zen), 1M ctx                                                             | untested in fleet runs                                                                                                  |                    |

## Gotchas

- **Bypass is blocked by the Claude Code auto-mode classifier** ("Create Unsafe Agents") until the user says bypass is allowed in chat. Ask once, up front.
- **devin, cursor and opencode run auto-approve** (`bypass`, `--force --trust`, `--auto` + `"*": "allow"`) — no approval gate exists between an agent and `rm -rf`. codex runs `--approve-for-me`: the reviewer model gates each command and the sandbox still confines writes. Either way, the no-destruction rule in `00-context.md` is required; do not launch without it.
- **Devin ignores** `--permission-mode smart` when the env has `DEVIN_PERMISSION_MODE=bypass`. Trust the footer, not argv: it must say `(bypass permissions on)`.
- **Follow-ups: use** `scripts/prompt.sh`**.** Prompting a working devin queues the text; the footer shows
  `Press Enter to send queued messages now` and herdr may report `done` while it waits. The script presses Enter.
- `agent_status` alone lies (idle/done while still writing, or done with a half-written report). Completion = `Status: FINAL` in the report (`scripts/watch.sh`).
- **Cursor may stop with** `Agent stopped retrying` (connection). `prompt.sh <name> continue` resumes it.
- **Shared browser state.** One agent ran `agent-browser close --all` and closed every session,
  including the owner's logged-in Discord. Every agent uses `--session <name>`; never `close --all`.
  A logged-in profile (Discord) belongs to one agent only: the profile dir is locked.
- A second task: cursor and codex can take a new prompt in the same session (keeps context). Devin: fresh pane per new task.
- Agents leave capture processes running. At the end: `pkill -f "$R/work/<name>/"` per agent, and
  check for relative-path children (`ps -axo pid,command | grep <script>`).

## Files, not chat

```
$R/00-context.md       goal, known facts/numbers, hard rules, report format, agent list
$R/briefs/<name>.md    one brief per agent
$R/reports/<name>.md   agent output (follow-ups: <name>-<topic>.md)
$R/work/<name>/        agent scratch scripts and outputs
$R/work/orchestrator/  your own checks (notes.md)
```

Hard rules to put in `00-context.md`:

- you run with auto-approve — no permission prompt will stop you. Never delete, overwrite, or destroy anything you did not create this run: no `rm`, no `mv` onto existing files, no `git clean`/`reset --hard`/`checkout --`, no `kill`/`pkill` outside your own `work/<name>/` processes, no dropping/truncating files, dirs, rows, tables, or branches. If something is in the way, write beside it or stop and report;
- read-only on code repos; write only to `work/<name>/` and the report; the owner may edit the repo during the run (read `git show HEAD:<path>` if a file looks half-written);
- no SSH to prod; no tests/training/full backtests unless the brief allows; run Python from the project venv;
- no accounts, no money, no signups; browser via `agent-browser --session <name>`, never `close --all`;
- keep work files under ~200 MB each (aggregate, sample, or parquet);
- every claim with `file:line` / URL + date / command + numbers; mark `verified` / `likely` / `speculative`;
- put `Status: FINAL` on line 2 of the report only when it is complete.

## Watch

One background watcher per agent: `R=... scripts/watch.sh <name> [report-basename]` with `run_in_background`.
It exits on DONE (report says `Status: FINAL`, changed after the watcher started, stable 3 min),
IDLE (agent not working, report stable 10 min, not final — nudge it), BLOCKED, GONE, TIMEOUT (90 min).
That wakes you. No polling in between.

## Verify

- Give the most suspicious topic to 2 agents of different models. Overlap finds more. In the fast-feed run
  three agents agreed on Oddin; two devins disagreed on one latency by 15× and only a re-check settled it.
- Re-check every key number yourself with a small script before it goes in the final report. Keep a "refuted" list.
- Close only panes and tabs you created, and only when the user no longer needs them.
