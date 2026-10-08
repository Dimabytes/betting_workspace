# s8-com — comment review of STEP-008 (be0cc83c)
Status: FINAL

## Scope

`git diff 2fe56a67..be0cc83c` — one commit, "Add wallet B: config_b and the live_b compose service for the two-sided trader." Files: `compose.yaml`, `config_b/trading.toml` (new), `tests/test_trader_compose.py`, `tests/test_trader_session_config.py`. Rules applied: `feature-json-no-comments-review` SKILL + `review.instructions` (new code gets no comment pass just because the sibling config has them — config_b is a new file, so every comment in it is in scope even where copied verbatim from `config/trading.toml`).

## Coverage check

- `compose.yaml`: the added `live_b` service and `compress` changes contain zero comments. Nothing to review.
- `tests/test_trader_compose.py`: diff adds `LIVE_B_ENVIRONMENT`, `LIVE_B_BIND_SOURCES`, `_binds_by_target`, `test_docker_compose_config_live_b_is_wallet_b` and edits two existing tests — none contain comments. The module-header comment (`test_trader_compose.py:3`) and the pyright suppressions (`:4-6`) predate this diff, are untouched by it, and sit at module level — out of scope.
- `tests/test_trader_session_config.py`: diff adds `test_config_b_is_a_deposit_wallet_on_blast_slam` and extends one import — no comments inside. All other comments in the file are in functions this diff did not touch — out of scope.
- `config_b/trading.toml`: new file, 106 lines, all comments in scope.

## Findings

All findings are in `config_b/trading.toml` (line numbers at be0cc83c).

1. `config_b/trading.toml:12-14`
   ```
   # account_cap is ours: session_config strips it before writing the fork config.
   # daily_loss_kill stays, poly-maker reads it. Guards are 2 * account_cap / min
   # entry. Wallet B: the two-sided core enforces neither cap (see profiles.dota-map).
   ```
   DELETE. Three-line justification = confession. Every fact is our-code: `session_config.py:40-41` (ConfigTemplate docstring) already states account_cap is ours/stripped and daily_loss_kill stays; `_guard_floor_usdc` computes the 2*cap/MIN_ENTRY_PRICE guard in code; "the two-sided core enforces neither cap" is a surprise in our own code.
   MUST KILL: `account_cap_usdc` inside `[risk]` — a key that looks fork-owned but is ours forces prose to explain ownership. Move our-only caps into their own table (e.g. `[session]`) so `[risk]` carries only keys poly-maker reads; then ownership is structural and needs no comment.

2. `config_b/trading.toml:24` `# fair value` — DELETE. Banner narrating a key group.

3. `config_b/trading.toml:28` `# volatility` — DELETE. Banner.

4. `config_b/trading.toml:32-37`
   ```
   # sizing / inventory
   # Wallet B: base_size_usdc 20 matches the $20 BLAST Slam clip, the map level unit.
   # 9 x $20 = $180 is reported as map room but not enforced: the two-sided brakes
   # are NET_MAX_SHARES, pair merges and the wallet's cash. A bid is ORDER_SHARES = 20
   # shares (src/strategy/two_sided.py), not dollars. Pairs merge through the pUSD
   # adapter, so poly-maker's merge_min_size is unused.
   ```
   DELETE. Banner plus a five-line justification. Verified true (`two_sided.py:7-8` ORDER_SHARES=20, NET_MAX_SHARES=50; `session_config.py:167` merge_min_size rides base_size_usdc) — which makes it worse: the comment exists because the file tells two lies, `base_size_usdc` is not dollars on the two-sided path and hand-duplicates the `clips.dota` BLAST Slam tier (20 = 20).
   MUST KILL: `profiles.dota-map.base_size_usdc` (wallet B) — single-source the map clip from the `clips.dota` tier (or rename the knob for what it is on this path) so the value-20 coincidence needs no paragraph.
   MUST KILL: the reported-but-unenforced "9 x clip" map room — either enforce it in the two-sided core or stop reporting it; reported-not-enforced limits are the kind of surprise this comment is confessing.

5. `config_b/trading.toml:40` `# regimes` — DELETE. Banner.

6. `config_b/trading.toml:48` `# lifecycle / exits` — DELETE. Banner.

7. `config_b/trading.toml:55` `# Clip only. Other knobs copy dota-map. Independent Oddin tuning is a later full table.` — DELETE. Clip-only satellites are already enforced by `_SATELLITE_SCHEMA`/`_resolved_satellite` (session_config.py:160) and rejected loudly by the pinned-schema check; "a later full table" is a TODO sermon. Nothing to reshape — the code already says it.

8. `config_b/trading.toml:59,63,72,80` `# fair value` / `# volatility` / `# regimes` / `# lifecycle / exits` — DELETE. Same banners, lol-map.

9. `config_b/trading.toml:67-69`
   ```
   # sizing / inventory
   # Same key set as dota-map. merge_min_size rides this profile clip.
   # The live rung is [clips.lol]. q_max_usdc sits at the session guard floor.
   ```
   DELETE. Banner plus narration of derivations the code performs: `merge_min_size` rides the clip via `DOLLAR_MULTIPLES`, `q_max_usdc` is set to the guard floor in `read_template` (session_config.py:110-112). Our-code narration.

10. `config_b/trading.toml:86-89`
    ```
    # The clip is the Polymarket title suffix after (BOx) -. A name is a
    # case-insensitive substring of that suffix. Two hits take the smaller clip.
    # Oddin does not read this table. A name also matches qualifiers that contain it.
    # Wallet B: these tier names are also the title whitelist (DOTA_STRATEGY=two_sided).
    ```
    DELETE. Sentences 1-2 and 4 restate `choose_clip`'s docstring nearly verbatim (clip_rules.py — title suffix match, case-insensitive substring, smaller clip wins). "Oddin does not read this table" is enforced by `_clip_tables` keying (satellite reads `clips.<game>-<feed`). The last line confesses a real surprise: `clips.dota.tiers[].names` doubles as the two-sided title whitelist.
    MUST KILL: `clips.dota.tiers[].names` dual role — make the coupling structural: the two-sided whitelist should read the clip tier names as its single source (if it already does, the comment is pure narration; if a second list exists elsewhere, merge them).

11. `config_b/trading.toml:100-102`
    ```
    # Satellite feeds get their own table, keyed clips.<game>-<feed>. Tiers only:
    # the satellite profile's base_size_usdc stays the default clip. clips.dota-oddin
    # covers maps quoting off the Oddin feed; it never touches GRID maps.
    ```
    DELETE. Restates the `_clip_tables`/`_satellite_clip_table` docstrings and the tiers-only schema enforcement (session_config.py:227-230, 263-270). Our-code narration.

## Skips (kept)

- `config_b/trading.toml:2` `# 0 EOA, 1 email/magic, 2 Gnosis Safe, 3 POLY_1271 deposit wallet.` — KEEP. Non-obvious value forced by an external protocol: `signature_type` is the Polymarket/py-clob-client enum consumed by the frozen fork; TOML cannot name the variant, `2` vs `3` is the only wallet A↔B semantic difference, and the pinned schema (`_TEMPLATE_SCHEMA["wallet"]`) admits only the raw int. Without the legend the value is a bare magic number.
- `config_b/trading.toml:3` `# PK and BROWSER_ADDRESS stay in env, never here.` — KEEP, weakest of the two. The credential path is forced by an external dependency (poly-maker reads PK/BROWSER_ADDRESS from env, not config) and the comment guards a commit-time trust boundary the loader can't: `_require_template_table` would reject a stray `pk` key, but only after the secret was already committed. One line that prevents a one-way leak.
