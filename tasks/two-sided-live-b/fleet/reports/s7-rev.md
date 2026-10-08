# s7-rev — STEP-007 strict maintainability and correctness review
Status: FINAL

Scope: `git -C E diff f605a6b9..629657b0`, inspected from the committed tree with `git show`. Review only; no edits, staging, commits, network, or trading actions in E.

Instructions read: fleet context and brief, W/AGENTS.md, E/AGENTS.md, feature-json-step-review skill, W/.feature-json.config.json review instructions, STEP-007 feature requirements, plan, and implementer report.

Review priorities: preserve Follow300/service A when DOTA_STRATEGY is unset; fail closed for each invalid two-sided start; prevent B from trading non-BLAST-Slam maps; assess structural growth and new comments/docstrings.

## Scope and result

Reviewed all twelve changed files and the surrounding committed startup, discovery, host lifecycle, worker, and merge code. The reviewed commit is `629657b00cc899698e623a77e1554ba2328aa6e4` on `two-sided-s7`; the branch/worktree was not checked out or modified.

**Request changes for one major admission-policy gap against the BLAST-only review constraint.** No additional structural maintainability findings or Follow300/service A correctness regressions were identified in this diff. No blocker/minor/nit findings.

## Confirmed finding

### Major — two-sided admission accepts non-BLAST clip tiers

`src/trader/wallet_host.py:162` (`select_title_whitelist`, lines 162–169); regression assertion at `tests/test_trader_two_sided_host.py:46`.

The two-sided whitelist is all names from the primary Dota clip table, and the only validation is that this tuple is nonempty. With the required live/Dota/signature-3/builder settings satisfied, a clip table containing `EPL World Series` or only `PARI Universe` therefore enables those events for TwoSidedWorker. The committed default template contains EPL and BLAST, and the new test explicitly expects both to become the two-sided whitelist. The subsequent Dota source/map gates contain no BLAST restriction, and the worker dispatcher only checks strategy. This does implement the plan's tier-name rule, but that rule alone does not enforce FR-7/the review brief's BLAST-only constraint. A wrong/copied config is accepted rather than refused.

Concrete fix: at the two-sided startup/admission boundary, validate that the configured whitelist is the permitted BLAST Slam whitelist and reject every other tier name with TradingDisabled; continue deriving the whitelist from the validated tiers as required by STEP-007. Keep the generic discovery matcher and Follow300 behavior unchanged. Change the EPL-accepting assertion to a refusal test and cover mixed BLAST+EPL, non-BLAST-only, and BLAST-only tables. STEP-008 still needs its dedicated B config with only BLAST Slam.

Evidence: the isolated committed-source reproduction retains an EPL map for the actual committed template, and accepts a PARI-only clip table. A tuple containing only BLAST Slam correctly drops EPL, missing titles, and BLAST in a team name.

Limit: a correctly mounted STEP-008 B config containing only BLAST Slam does not trigger this finding. STEP-007 has no such config change, and this review does not claim that a deployed B process was observed trading another league. The finding is the missing enforcement of the brief's explicit invariant, including a regression test that accepts its violation. Resolution can be a small guard at this existing boundary; no strategy registry or discovery refactor is necessary.

## Validation

Ran the following command in E (final run: exit 0):

```sh
PYTHONDONTWRITEBYTECODE=1 \
UV_CACHE_DIR=/Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace/tasks/two-sided-live-b/fleet/work/s7-rev/uv-cache \
uv run --offline --no-sync python -B \
  /Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace/tasks/two-sided-live-b/fleet/work/s7-rev/check_committed_boundaries.py
```

The scratch script reads functions directly from the reviewed commit via git show and executes selected pure functions, filter methods, and startup/dispatcher bodies with fake environment, profile, sidecar, resource, and worker boundaries. Signatures' annotations are removed for isolated wiring checks; the function bodies are unchanged. No project imports, secrets, venue, Docker, or model/data reads. These checks establish the specified boundary behavior, not full integration/replay equivalence.

- Strategy parser: 5 accepted/default and 4 rejected cases passed.
- Two-sided startup predicate: 48 combinations across mode, assignments, signature types 0–3, and credential presence passed.
- Follow300 empty whitelist and two-sided empty-whitelist refusal passed.
- Follow300 blacklist output/dedup parity: 7 ordinary titles, 2 policies, 2 cycles passed.
- BLAST-only filter correctly excludes other leagues, missing titles, and a BLAST-named team outside a BLAST event.
- Confirmed the non-BLAST tier admission counterexample above.

- Additional committed-body checks passed: 7 startup refusals occur before Engine construction and close the lock/config directory; Follow300 bypasses the two-sided signature/builder checks; empty whitelist refuses WalletHost.run before engine.start; both worker selections preserve the same six constructor arguments.

## Startup-refusal audit

| Input / violation | Committed behavior | Evidence |
|---|---|---|
| DOTA_STRATEGY absent, empty, whitespace | follow300 | Executed parser checks |
| two_sided with optional outer whitespace | two_sided | Executed parser checks |
| Other nonempty strategy, including literal follow300 | TradingDisabled before resource opening | Executed parser; daemon call at host_resources.py:237 precedes resource setup |
| two_sided + paper | TradingDisabled requiring --mode live | Predicate and open_wallet_host body checks |
| two_sided + LoL only, or Dota+LoL | TradingDisabled requiring only Dota | Predicate and open_wallet_host body checks |
| two_sided + signature_type 0, 1, or 2 | TradingDisabled requiring signature_type 3 | Predicate and open_wallet_host body checks |
| two_sided + missing builder credentials | TradingDisabled naming the three builder variables | Predicate and open_wallet_host body checks; read-only inspection confirms has_builder_creds requires all three |
| two_sided + no names in Dota tiers | TradingDisabled before engine.start | Executed selector and WalletHost.run body checks |
| live + no PK/BROWSER_ADDRESS | Existing require_live_wallet rejection remains first in open_wallet_host | Static trace; no wallet credentials read |
| No games assigned | Idle before open_wallet_host, including two_sided | Static trace; accepted task/plan decision |
| Nonempty tiers naming non-BLAST events | Accepted | Major finding above |

The mode/game/wallet/builder checks are wired before Engine construction at host_resources.py:171–177. Their cleanup restores patched classes and closes the temporary config and lock. The empty-whitelist check is later, at wallet_host.py:1243, but precedes engine.start at line 1250 as the plan requires.

## Follow300/service A and live-money paths

- With DOTA_STRATEGY absent, the parser returns follow300; the selector returns `()` without reading clip tables; the dispatcher builds MatchWorker with the original six arguments. The constructor stores the strategy before installing seams.
- install_adapter_merge is called only under the two-sided condition at wallet_host.py:813–814. Follow300 leaves the fork's _maybe_merge method intact. This was inspected statically; the implementer's real-paper-engine seam tests are separate evidence.
- The replacement blacklist/filter pass preserves ordinary existing title outcomes and dedup state in the isolated comparison. Matching changes from lower to casefold as explicitly required by the plan; existing live blacklist names are Streamers and Winline. No whitelist is applied to A or LoL Follow300.
- A's additional info line includes strategy, signature type, and funder, as explicitly accepted in the plan/resolved questions.
- The diff adds no quote sizing/pricing logic and changes no cancel/fence paths. TwoSidedWorker is selected only for two_sided; its existing PairMerger replaces DustSweeper and its core drops SELL. The newly enabled merge seam suppresses the fork's merge only for B. No additional SELL, wrong-size/price, or missing-cancel defect was found in STEP-007. This is not a re-audit of the STEP-006 implementation.
- compose.yaml, config, strategy core, backtest, dust_sweep, and fork code are outside this diff and remain unchanged by STEP-007.

## Maintainability assessment

- No additional structural maintainability findings. Strategy parsing and validation are centralized in trading_mode; worker and merge selection each need one small condition at their owning host seam; discovery retains one filtering/logging pass.
- No file crosses 1,000 lines in this diff. wallet_host was already 1,649 lines and becomes 1,672; discovery shrinks from 819 to 818. The new host tests are isolated in a 172-line file; the existing 2,981-line host test file receives only six lines.
- No new comments/docstrings in the reviewed diff apart from the allowed pyright pragma in the new test file. The renamed league-match test retains its pre-existing docstring; it is not a newly added comment. Refactored production league/filter functions remove their old docstrings.
- git diff --check passed.

## Test-suite evidence and limits

No repository pytest suite, basedpyright, or mutating lint hooks were run by this reviewer. The broader results below are the implementer's reported evidence, not independently reproduced results:

- Step tests: 220 passed / 12 failed. Failures are existing host-run tests missing ODDIN_BRAND_TOKEN in the s7 worktree.
- Regression selection: 316 passed / 9 failed. One host-run failure has the same missing token; eight Follow300 seed0 replay cases lack dataset parquet files in that worktree.
- Targeted rerun: 59 passed plus the existing multi-game host-run test failing before its new assertion.
- basedpyright: 0 errors/warnings/notes; staged lint/hooks and C901: reported passing; daemon import smoke: reported passing.

Consequently the full required A replay/integration validation has not been demonstrated green for this exact worktree. These existing environmental failures are not presented as new diff defects. Finish that validation in the intended prepared checkout before declaring the overall feature passed.

## Decisions and boundaries

- The brief's explicit BLAST-only constraint takes priority for this review over the plan's broader tier-name admission behavior. The finding records that conflict rather than silently treating any configured tournament as authorized for B.
- Kept the report and scratch reproduction as the only authored files. No edits/staging/commits in E or the s7 worktree; no push, checkout, VPS, live trading, relayer submission, key use, or data mutation.
- Existing untracked ts-regress-before/after backtest directories were observed and left alone.
- No delegation or user questions were needed. All review decisions and evidence are recorded here.
