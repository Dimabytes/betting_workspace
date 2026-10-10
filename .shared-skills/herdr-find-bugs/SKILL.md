---
name: herdr-find-bugs
description: Investigate bugs through Herdr with user-selected agents in two phases, starting with the orchestrator's own research and ending with independently checked findings and an implementation handoff. Use when invoked or when the user requests a multi-agent bug hunt through Herdr, including incorrect data, unintended behavior, and measured performance problems.
---

# Herdr find bugs

Find where the system behaves differently from its intended behavior. Deliver a consolidated, evidence-based report that another developer can use for fixes. The orchestrator investigates, directs the agents, resolves disagreements, and owns the final conclusions.

## Scope and dependencies

- Read `herdr` first, then `herdr-fleet`, before operating the fleet. Use their current scripts and procedures for configuration, session pinning, panes, prompts, watchers, report collection, and cleanup. This skill adds an investigation method; it does not replace the fleet launcher.
- Default to investigation, not product changes. Existing authorization for read-only production inspection remains valid; do not ask again for it. A bug hunt does not itself authorize repairs, live actions, restarts, deployments, or pushes.

## 1. Investigate before delegating

Do enough personal research to design specific assignments. Do not launch the whole fleet with variations of “look for bugs.”

1. Establish the intended behavior from documentation, configurations, tests, and callers. Distinguish a defect from an intentional policy or a requested new feature. Record unclear expectations instead of inventing them.
2. Build an initial map of suspicious areas and observable invariants. Examples to select when relevant: identity/side/units, precision and rounding, event ordering, deduplication, retries, reconnect/backfill, partial writes, recovery, stale state, resource growth, and repeated work. These are prompts for inspection, not a mandatory checklist for every project.
3. State what evidence would establish or refute each leading hypothesis. Choose bounded reproductions, small fixtures, real-data checks, or profiling that can discriminate between explanations.

The orchestrator normally handles authorized production reads and downloads, then delegates local analysis. If an agent needs direct remote access, its brief must explicitly specify the already-authorized scope. Avoid multiple agents repeatedly downloading or scanning the same large dataset.

## 2. Allocate the two phases

Default to about half the requested agents in phase 1 and reserve the remainder for phase 2, preserving a useful mix of the requested models in both phases. With odd counts, allocate by task fit and keep the exact total. User-specified allocation takes precedence. Follow-ups to existing agents do not silently create extra agents.

Phase 1 explores different high-value areas, with deliberate overlap on the most consequential boundaries. Prefer different models for overlapping checks when the requested composition allows it. Keep some exploration independent of the orchestrator's suspicions so the initial map does not become a blind spot.

Do not start phase-2 briefs before is phase-1 finished.

## 4. Review phase 1 and direct phase 2

Maintain a hypothesis ledger in the run directory. Give each distinct candidate a stable ID and record its mechanism, evidence, uncertainty, source report, and next discriminating check. Merge duplicate reports of one root cause without hiding distinct failure modes.

## 5. Validate behavior and impact

For important findings, personally recheck the key numbers and at least one causal example. Match validation depth to the claim:

Keep unresolved issues actionable by stating the missing evidence and the next useful experiment. Do not force a complicated fix into the audit; describe required behavior and acceptance checks when the implementation is uncertain.

## 6. Produce the combined report

Collect the fleet reports and verify completion, source links, and version coverage. A report marked FINAL still needs substantive review. Do not present an unfinished report as agent-complete; label any orchestrator completion explicitly. If access or execution is blocked, deliver supported findings with the gap clearly stated.

Write in the user's language:

- `FINAL_REPORT.md`: one authoritative combined report, not just an index of agent opinions.
- Preserve the fleet index, source manifest, hypothesis ledger, reports, and reproduction artifacts beside them. If updating an existing run, reuse its established report names and IDs.

The full report includes scope and evidence coverage; a deduplicated priority table; detailed findings; rejected hypotheses; unresolved questions and untested areas; and links to evidence.
