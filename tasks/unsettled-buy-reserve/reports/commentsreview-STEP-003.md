# Comments review: STEP-003 (commit c72792c9, esports-trader)
Status: FINAL

Scope: the full diff of `c72792c9752b180068485618690b5886e1b38c36` — new `src/trader/unsettled_buy_recovery.py`, edits to `engine_seams.py`, `session_core.py`, `wallet_host.py`, and tests. Every added comment, docstring, and suppression was inventoried (30 added lines carrying `#` or `"""`). No commented-out code, banners, TODOs, noqa, pragma, or workaround sermons exist in this diff.

## Findings

1. `tests/test_trader_session_core.py:1095` — `"""A resolved venue keeps delivering BuySettled while the BUY is still active."""` on `test_sync_inputs_repeats_buy_settled_until_the_order_leaves` — **DELETE**. It restates the test name; house-style test docstrings in this file add specifics the name cannot carry (e.g. `:648` "198.35 vs 198.3567 is one CLOB tick"), this one adds nothing.

No MUST KILL items: no comment papers over an own-code surprise or masks a suppressable real bug.

## Skips (kept, with the clause that saved each)

### Suppressions — all convention-matching, none hide a live bug

- `src/trader/unsettled_buy_recovery.py:2` `# pyright: reportPrivateUsage=false, reportMissingTypeStubs=false` — keep. The module's job is the prescribed seam into the frozen vendored polymaker (`gateway._io`, `gateway._client`); the fork cannot gain public accessors, so private access is forced by an external dependency. Same header on ~12 trader modules (`engine_seams.py:3`, `wallet_host.py:3`, `session_core.py:3`, …). `reportMissingTypeStubs` covers untyped `py_clob_client_v2` — external dependency.
- `src/trader/unsettled_buy_recovery.py:3` `# pyright: reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnknownArgumentType=false` — keep. Pedantic strictness rules against untyped vendor JSON; identical trio on `wallet_store.py:3` and `fill_parsing.py:3`.
- `tests/test_trader_unsettled_buy_recovery.py:3` `# pyright: reportPrivateUsage=false, reportUnknownLambdaType=false, reportAttributeAccessIssue=false` — keep. Tests deliberately inject fakes into engine internals; same suppression set as `test_trader_wallet_host.py:3` and precedent across `tests/`.
- `tests/test_trader_unsettled_buy_recovery.py:654,665,703` `# type: ignore[method-assign]` — keep. Assigning async stubs over real methods is the established monkeypatch pattern; the identical `host.close = close  # type: ignore[method-assign]` already exists at `test_trader_wallet_host.py:420` and ~15 more sites.

### The ponytail marker

- `src/trader/unsettled_buy_recovery.py:21-22` `# ponytail: one complete trades read can lag and prove zero. Upgrade path: require the same sum on two passes before proving zero.` — keep. The workspace's always-on ponytail rule requires deliberate simplifications to carry exactly this marker (ceiling + upgrade path). It documents accepted residual risk that code cannot express; `feature.json:37` and plan `STEP-003.md:86` explicitly accept this limitation.

### Docstrings — contract-defining, matching house style (every src/trader module and function carries one)

- `src/trader/unsettled_buy_recovery.py:1` module docstring — keep: module-purpose docstring convention; every `src/trader` module opens with one.
- `:35` `parse_terminal_buy_proof` — keep: return contract, None means "not proof" (distinct from proven zero).
- `:48` `eligible_rest_rows` — keep: defines eligibility (unproven + aged), not obvious from the name.
- `:61` `collect_rest_buy_proofs` — keep: "a failed token contributes no proof" is the error-semantics contract.
- `:132` `_proofs_from_trades` — keep: None-means-unprovable-including-zero contract; a successful empty reply is a real zero proof.
- `src/trader/engine_seams.py:64` `_ignore_order_terminal` — keep: the default exists because the frozen `Engine.start` builds the stream late — behavior forced by an external dependency we cannot reshape.
- `src/trader/engine_seams.py:151` `_on_order` — keep: "same raw object after journaling" is the contract the proof parser depends on (raw `size_matched` must survive).
- `src/trader/engine_seams.py` `install_rest_fill_recovery` docstring edits — keep: extends the existing seam docstring with the error-isolation contract (one failed read does not skip the other recovery or the snapshot).
- `src/trader/session_core.py:93` `BuyCancelClock` — keep: field-lifetime contract (kept until the core drops the order).
- `src/trader/session_core.py:632` `_settled_buy_events` — keep: "Repeat" encodes the deliberate per-cycle re-delivery contract (FR-6), not visible from the name.
- `src/trader/session_core.py:683` `_observe_buy_lifetime` — keep, borderline: the log strings self-describe, but the docstring pins the metric contract — wait spans cancel-to-removal, not placement — which the plan required and the body does not state.
- `src/trader/session_core.py:924` `_settled_buys` — keep: lazy-read condition (query only when an active BUY has a venue binding) is a deliberate DB-access contract.
- `src/trader/wallet_host.py:797` `_bind_order_terminal` — keep: binding timing is forced by the frozen Engine's lifecycle.
- `src/trader/wallet_host.py:803` `_on_order_terminal` — keep: "existing row" encodes the STEP-003 no-insert boundary.
- `src/trader/wallet_host.py:814` `_resolve_confirmed_buy` — keep: "the ledger itself" names the authority (ledger CONFIRMED, not journal seq).
- `src/trader/wallet_host.py:828` `_apply_unsettled_proofs` — keep: commit-before-wake ordering is the mandated contract.
- `src/trader/wallet_host.py:846` `_wake_unsettled_sessions` — keep: "a missing worker still leaves the row settled" is the workerless-settlement guarantee.
- `src/trader/wallet_host.py:854` `_reconcile_unsettled_buys` — keep, borderline: mostly narrates the three-call pipeline, but conveys that alerts run only on still-open rows after resolution.
- `src/trader/wallet_host.py:873` `_alert_stale_unsettled_buys` — keep: "one stable alert" (dedup contract) plus "does not prove or release" (no-side-effect guarantee).
- `tests/test_trader_unsettled_buy_recovery.py:1` module docstring — keep: test-module docstring convention.

## Summary

One DELETE finding: the restatement docstring at `tests/test_trader_session_core.py:1095`. Everything else is a house-convention docstring carrying a real contract, a mandated ponytail marker, or a suppression whose rule is pedantic or whose target is the frozen vendored fork. The diff is clean of corpses, banners, and sermons.
