---
name: shrink
description: Interview-driven simplification of one pipeline script (or one slice of a big one) - measure what every code path contributes, ask every trade-off upfront with numbers, produce a staged plan, then implement it step by step. Use when the user invokes /shrink <file> [slice], or asks to simplify/shrink/упростить a collect/pipeline script.
---

# Shrink — plan and execute the simplification of one pipeline part

Reference result: `01_build_universe.py` went 893 → 602 lines, 3 parquet outputs → 1,
758-line type-error baseline → zero, every deletion backed by a measured number.
This skill exists so the next file takes 1 hour of decisions instead of 4 hours of churn.

The deliverable is first a **plan** built from measurements and the user's answers.
Code changes start only after the user approves the plan.

## Non-negotiable ordering

The single biggest time sink observed: polishing code (types, names, extraction) that
was deleted 20 minutes later, because the keep/delete decision came after the polish.
Work strictly in this order, never interleave:

1. **SCOPE** — target, slice, success metric, who else is editing
2. **UNDERSTAND** — read everything, build the consumer map, check for a vendor SDK
3. **MEASURE** — one batched pass over real data; set up the verification harness
4. **ASK** — every trade-off, with its number attached, before touching any code
5. **PLAN** — data contract first, deletion cascades second, code polish last
6. **IMPLEMENT** — one decision per commit, rebuild-diff after each step

Never improve the types, names, or structure of a block whose keep/delete decision
is not yet made.

## Phase 1 — Scope

- No target given → ask which file.
- `git status` on the target. Uncommitted changes → ask if another agent/session is
  editing it right now, and stop until the file has one owner. (Three tools editing
  `01_build_universe.py` concurrently caused failed edits, a KeyError on a renamed
  field, and one forced 16-file commit.)
- File over ~700 lines → do NOT plan line-by-line. First produce a map: responsibilities
  with line ranges (fetch / validate / load / parse / classify / build / publish / CLI),
  line count each. Ask the user which slice to attack first. Slices follow responsibility
  seams, never arbitrary line counts. Phases 2–3 (consumer map, measurements) still cover
  the WHOLE file — deletions cascade across slices; only the edit scope is sliced.
- Fix the **success metric** once, in writing, before measuring. "Rows in the output"
  and "rows that survive to the final consumer (link/model)" give opposite verdicts on
  the same branch — two sessions measured title_search with different metrics and
  concluded "must keep" and "delete" on the same day. Downstream survival is the
  default metric.
- Ask the blast radius: which downstream scripts may be touched, which may be knowingly
  left broken for a later pass ("остальные скрипты дальше не трогай" was an explicit,
  correct scoping decision).

## Phase 2 — Understand

- Read the target file fully, and `betting_workspace/.learnings/dota_2_model.md`. Trace the real flow end to end:
  inputs → transformations → outputs. No deletion proposals yet.
- **Consumer map, grep-backed.** For every output artifact, every column, every
  manifest/metadata field, every enum value: who reads it, `file:line`. Also the
  reverse direction: consumers reading keys that are never written (found in the wild:
  a script read three manifest keys the manifest never wrote and silently fell back to
  a hardcoded date). Zero-reader fields and never-written keys are the first cut list.
  Do this BEFORE any discussion — mid-argument "someone downstream reads this" claims
  were wrong twice and cost a round-trip each.
- **Vendor SDK check — step zero of any typing work.** If the file parses a third-party
  payload, check PyPI for an official client before improving a single type annotation.
  Hand-writing the Gamma TypedDicts + converters took ~1.5h and was deleted the same
  day for `polymarket-client` pydantic models. Scope the SDK honestly: it covered
  parsing but not fetching (raw verbatim bytes were still needed for deterministic
  offline rebuild), so the hand-rolled fetcher stayed.
- **Knob audit.** For every CLI flag and threaded parameter: does any caller actually
  vary it today (grep Makefile, docs, scripts)? Not varied → module constant or
  required argument, and the flag plus every signature it threads through goes on the
  cut list — in one pass, not two.

## Phase 3 — Measure

- **Verification harness first, deletions second.** Establish the frozen rebuild
  command (e.g. rebuild from the cached raw snapshot with a fixed `--as-of`) and record
  the invariant counts (rows per output, key distributions). Every later change is
  proven by rebuild + count/frame diff against this baseline — never by reading the
  diff and nodding. This harness is what catches the branch that looked dead but
  guarded a live predicate.
- **One batched measurement pass** over real data, producing one table: for every
  fallback branch, discovery source, guard, enum value, metadata field —
  raw contribution AND downstream survival. Twenty questions, one pass. Interleaving
  explain → measure → edit per item is where the 4 hours went.
- Measurement scripts are real `.py` files in the scratchpad run with `PYTHONPATH=src`,
  not escaped `python -c` one-liners (those produced 7+ quoting tracebacks).
- Measure your own claims too. Before defending any "we need this because X", check X
  against the data: three plausible reasons were given for keeping a derived parquet,
  and all three were wrong on the real data (2807/2807 timestamps identical, 0 events
  without contracts, aggregates one groupby away).

## Phase 4 — Ask (everything upfront, batched)

Present one findings table in chat first — candidate → number → what breaks if removed
→ recommendation. Recommend the most aggressive shape that works: default to delete
when downstream survival is ~0, regardless of raw row count. Then AskUserQuestion in
batches:

1. **Data-loss trade-offs** — each deletion candidate with its number ("this fallback
   rescues 47 of 63,977 markets (0.07%), of which 0 link — delete?").
2. **Output shape** — how many artifacts, which columns, AND the file name AND the
   directory, as one decision. (Rename and relocation done as separate passes = three
   edit+lint+verify cycles for one layout decision.)
3. **Granularity** — collapse enums/statuses/metadata to values something actually
   consumes? Analytics granularity is cheap to re-add later; deferring it is the default.
4. **Contract tightening** — which optional args become required, which knobs become
   constants, which `parse_* -> T | None` become raising `require_*`.
5. **Blast radius confirmation** and the verification bar (full old-vs-new frame diff,
   or counts only).

Rules for the questions:
- A guard is either a technical check or a business rule ("one series_winner per
  event"). Never batch-delete across that line; business rules get their own question.
- Renaming a **persisted** field (raw cache key, parquet column) is a data migration,
  not a rename — the question must say "old cache becomes unreadable, refetch required".
- If the user must choose between crash / tag-as-excluded / silent skip for junk rows,
  first check whether the schema can even represent the excluded row (no key = no row).

## Phase 5 — Plan

Order inside the plan mirrors the dependency order of decisions:

1. **Contract**: sources kept, output artifacts, columns (derived from the row type),
   type width at the boundary, required args. Fixed first — everything else hangs off it.
2. **Cascades**, stated explicitly before editing: "drop source X ⇒ merge logic Y and
   dedupe Z become no-ops ⇒ delete those too, and scope the raw-dir glob so X's stale
   cache stops being read." One cascade = one move = one commit.
3. **Structure**: one conversion at the read boundary (no dict `.get()` in business
   logic), checks placed by kind — per-response shape checks at fetch (trust boundary),
   file-set invariants at validate, business filters at load. Fold second read passes
   into the loop that already reads the data.
4. **Polish** (only for surviving code): verb names, frozen dataclasses instead of
   tuples, hoist inline expressions, docstrings, per `.learnings/dota_2_model.md`.

Each plan step names: the change, the expected proof (counts before == after, or the
intended difference spelled out), and the commit message with decision drivers and
numbers ("title search added 76 events, 0 linked").

If the work spans sessions or slices, write the approved plan to
`docs/plans/<target>_shrink.md` and keep a done/remaining checklist in it.

## Phase 6 — Implement

Only after plan approval. Per step: edit → rebuild → diff counts against the harness →
`make lint-all` (twice if the first run auto-fixes) → commit. One decision per commit.

- Unexpected diff in the rebuild → stop and report, do not patch forward. The observed
  case: a regex was deleted because its *label* was unused, but its *predicate* was
  load-bearing — BO2 scorelines silently fell into series_winner. The rebuild diff
  caught it; the regex came back as a guard returning `other`.
- An intended difference must be isolated and named (e.g. "microsecond padding in
  market_start_date, 6485 rows, only consumer is pd.to_datetime").

## Pattern catalog

What to hunt for, with real before/after from this repo. Each pattern names its
qualifying question — the number or grep that justifies it.

**1. Fallback chains → one source per concept.**
Qualify: per-field hit counts. `gameStartTime` 62,960 / `eventStartTime` 0 /
event fallback 47 (0.07%, none linked).
```python
# before: hidden priority walk across two levels
def scheduled_ts(event, market=None) -> int | None:
    for value in (market.get("gameStartTime"), market.get("eventStartTime"),
                  event.get("startTime"), event.get("eventDate")):
        ts = parse_utc_ts(value)
        if ts is not None: return ts
# after: two honest functions, one field each
def parse_event_start_ts(event) -> int | None: ...   # event.startTime, always
def parse_market_start_ts(market) -> int | None: ... # market.gameStartTime, always
```

**2. Data source that costs code but contributes nothing.**
Qualify: downstream survival. `title_search=Dota`: +76 events, 0 ever linked → the
query, its TypedDict fields, its cache folders, and its junk-filter (`is_dota_event`)
all go — the source's defenses die with the source.

**3. Dead branches for inputs that cannot occur.**
Qualify: trace the caller. Resume-from-cache branch when the caller always stages into
a fresh `TemporaryDirectory` → `path.exists()` is never true. `isinstance` guards on
values a typed boundary already guarantees.

**4. Guards that an earlier `continue`/`raise` already covers.**
Qualify: is it unreachable, or a real invariant in the wrong place? Different fates:
unreachable → delete; real invariant → move next to where the data is built, once.
```python
contracts = pd.DataFrame(contract_rows, columns=CONTRACT_COLUMNS)
- if contracts.empty: raise ...                          # user sees this himself
- if contracts["conditionId"].isin(("None", "")).any():  # loop already `continue`s
+ if contracts["conditionId"].duplicated().any(): raise  # kept — real invariant, moved here
```

**5. Optional → required deletes the None-ladder.**
Qualify: "how can the mixed/absent case even occur given the real workflow?" (atomic
folder swap ⇒ mixed stamps impossible). `--as-of` required killed an optional-stamp
path, a 13-line inference function, and a second full read of every raw page.

**6. CLI knob → module constant.**
Qualify: no caller varies it today. Delete the flag AND the parameter from every
signature it threads through (`tag_ids` went through four functions), in one pass.

**7. `parse_* -> T | None` + manual check → raising `require_*` — at trust boundaries only.**
Grep every caller first: where `None` is a real branch
(`parse_ts(a) or parse_ts(b)`), the soft version stays. Thin wrapper, not a global
contract change.

**8. Official SDK over hand-written vendor schema.**
Qualify before adopting: parse the entire raw archive through it (11,564 events,
256,180 markets, 0 failures) and diff the rebuilt output (62,920 rows × 21 cols
identical). Count the actual new transitive deps (2). Keep what the SDK doesn't cover.

**9. N output artifacts → 1.**
Qualify: is each extra file derivable from the main one? (`series` = groupby(event_id);
`inventory` = `contracts[inventory_status == "candidate"]`, 4325 == 4325). Keep the
policy as columns (`inventory_status`, `exclusion_reason`), not as materialized views.

**10. Enum space → the values downstream consumes.**
5 contract kinds → 3: what we take (`map_winner`, `series_winner`) and `other`.
Per-reason exclusion labels deferred: "это потом я для анализа добавлю".
Caution: the deleted label may guard a live predicate — the BO2-scoreline regex had to
return as a guard. Delete labels, re-verify predicates with the rebuild.

**11. Metadata → its real readers.**
The manifest went from ~90 lines of dict-building to 6 keys after listing actual
consumers (publish: `artifacts.sha256`; next script: `artifacts` + `data_as_of`;
human: ≤5 counts). Everything only a hypothetical future analyst reads gets cut.

**12. Anonymous tuple → frozen dataclass** (`.learnings/dota_2_model.md` rule; agents still regress
mid-refactor — watch for it in your own edits).
```python
- def classify_contract(...) -> tuple[str, str, int | None]
+ @dataclass(frozen=True)
+ class ContractClassification:
+     contract_kind: MarketContractKind
+     classification_method: MarketClassificationMethod
+     game_number: int | None
```

**13. Column list derived from the row type.**
```python
- CONTRACT_COLUMNS = ["conditionId", "market_id", ...]   # 27 hand-listed strings
+ CONTRACT_COLUMNS: list[str] = list(MarketContractRow.__annotations__)
```
With `Literal` types on categorical columns, basedpyright rejects a typo'd key or an
illegal value before the parquet is written.

**14. Convert once at the read boundary; ban `.get()` in business logic.**
`TypedDict.get("quesiton")` passes basedpyright silently — typing that doesn't check
is fake. One converter (or SDK parse) at read time; attribute access everywhere else.

**15. Inline decision ladder → named classifier returning a named result.**
When a branch computes only a value, it is a function, not a branch:
`classify_inventory(kind, best_of) -> InventoryClassification`.

**16. Hoist the inline expression — then question its existence.**
`sorted(grouped, key=lambda v: (len(v), v))` → hoisted to a named variable →
"why sort at all?" → `build_frames` already sorts the parquet → deleted. Hoisting is
often just the step that makes deletion visible.

**17. Rename to what the value is.** `cutoff` → `as_of` (it never cut anything),
`dataset_manifest` → `universe_manifest` (dataset = training data), `normalized_text`
→ `clean_text` (hot helper, short name). A name nobody can defend usually marks a
field nobody needs — `universe_ts` died as a name and then as a field.

## Pitfalls — each of these burned real time once

- Deleting by label without checking the predicate (the scoreline regression).
- Arguing keep/delete from plausibility instead of measuring (three wrong reasons for
  `series.parquet`, full retraction after measuring).
- Typing/naming/extracting a block, then deleting it (the `cast`/`Mapping`/TypedDict
  chain rewritten 5 times before the SDK deleted all of it).
- Answering "who reads this?" from memory instead of grep (wrong twice).
- Removing a knob from the CLI but leaving it threaded through four signatures.
- Renaming a persisted field without treating it as a cache migration (`KeyError`
  on 62 cached pages).
- Deciding artifact name and artifact directory in separate passes.
- Two agents/sessions (or the user) editing the target file concurrently.
- Escaped `python -c` one-liners for measurement instead of a scratch file.
- Forgetting `make lint-all` exits 1 on its first run when hooks auto-fix — run it
  again before concluding the code is broken.
