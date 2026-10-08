# s5-com — comment review of STEP-005
Status: FINAL

Scope: `git -C esports-trader diff 7b1b6990..da5ce546` (STEP-005: `src/trader/ctf_merge.py` new, `src/trader/pair_merge.py` new, `tests/test_trader_ctf_merge.py` new, `tests/test_trader_pair_merge.py` new, `scripts/merge_probe.py` constants moved out).

## Inventory

The diff adds **zero prose comments**: no narration, no docstrings, no banners, no commented-out code, no `noqa` / `type: ignore` / `pyright: ignore` inline suppressions. The only comments added are four file-level `# pyright:` pragma headers. `scripts/merge_probe.py` diff touches no `def` (imports + constant move only), and its pre-existing pyright header (lines 24-26) is unchanged — out of scope.

Repo context: basedpyright runs `typeCheckingMode: strict` over `src`/`scripts`/`tests` (`pyrightconfig.json`), so these pragmas are live config, not dead text. Every existing `src/trader` module carries the same file-level header (`wallet_host`, `match_worker`, `dust_sweep`, `engine_seams`, `core_*`, ...), because the package deliberately treats `_`-names as package-internal seams onto the frozen poly-maker `Engine`.

## Findings

No findings. Nothing flagged DELETE or MUST KILL.

## Skips (each pragma, with keep clause)

1. `src/trader/ctf_merge.py:1-2` — `# pyright: reportMissingTypeStubs=false, reportUnknownVariableType=false, reportUnknownMemberType=false` / `reportUnknownArgumentType=false, reportPrivateUsage=false`
   - Keep: `reportMissingTypeStubs` and the three `reportUnknown*` rules are pedantic strict-mode diagnostics over stub-less vendor libs (`web3`, `py_builder_relayer_client`, `py_builder_signing_sdk`); they catch no bugs.
   - Keep: `reportPrivateUsage` covers `polymaker.merge._to_bytes32` and `engine._maybe_merge` — poly-maker is frozen and cannot be reshaped (keep clause: behavior forced by external dependency we cannot reshape).
   - Honest note (not a flag): the pragma also covers `relay._post_request`, which was a choice — the vendor ships a public `RelayClient.execute_deposit_wallet_batch` (`client.py:325`) that does exactly this submit. Using it would narrow what the pragma hides, but the pragma itself still cannot be deleted because the frozen poly-maker symbols force it.

2. `src/trader/pair_merge.py:1` — `# pyright: reportPrivateUsage=false, reportMissingTypeStubs=false`
   - Keep: `reportMissingTypeStubs` — `py_clob_client_v2` is untyped; pedantic rule.
   - Keep: `reportPrivateUsage` — forced by frozen poly-maker privates `engine._chain_lock`, `engine._wake_cid`, `gateway._io`, `gateway._client` (`ExecutionGateway` is polymaker's). Matches established package-friend pattern (`match_worker.py:473`, `dust_sweep.py:251`, `engine_seams.py` all reach `engine._wake_cid` under the same pragma).
   - Honest note (not a flag): two covered accesses are own-code — `host._consume_core_outbox`, `host._budget_cache` (`wallet_host.py:1057,700`). They could be renamed public, but the pragma is needed regardless for the frozen symbols, so the suppression survives.

3. `tests/test_trader_ctf_merge.py:1` — `# pyright: reportPrivateUsage=false, reportMissingTypeStubs=false`
   - Keep: tests intentionally monkeypatch vendor/frozen internals (`RelayClient._post_request`, `engine._maybe_merge`, `engine._merging`, `engine._aux_tasks`, `requests.Response._content`); the rule is pedantic in test context and the targets are unreshapeable.

4. `tests/test_trader_pair_merge.py:1` — `# pyright: reportPrivateUsage=false`
   - Keep: same — tests poke `merger._task`, `merger._disabled`, `merger._paused_until_s` and fake names mirroring privates; exercising internals is the point of the test seam. Pedantic in test context.

## Decision

`Status: FINAL` — no findings. The added code is comment-free; all four pragmas are kept under the pedantic-rule and forced-external clauses. The two own-code private reaches in `pair_merge.py` and the avoidable `relay._post_request` in `ctf_merge.py` are noted above for the step reviewer's awareness; neither changes the suppressions, which are forced by frozen poly-maker symbols either way.
