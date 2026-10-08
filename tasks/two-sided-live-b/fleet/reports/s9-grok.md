# s9-grok — STEP-009
Status: FINAL

## Plan

No plan file. Decisions, taken because nobody is answering:

1. `--root DIR` replaces only the live tree and the first wallet candidate (`DIR/wallet/live.db`). Paper and legacy stay the host defaults. Restart-check already skips non-live trees, so B's gate does not see A's paper or legacy. `--today` still lists those trees. The runbook says rows tagged `[paper]` and `[legacy]` are A's. `polymarket_today` uses the funder in the selected `live.db`.
2. `--two-sided` changes only `--restart-check`. Any open session in the live tree is `UNSAFE open_map`, including a flat map past second 480. Without the flag the Follow300 rule is unchanged.
3. The day number is the activity fold, not sqlite. `_FOLD_KINDS` had no `MERGE`, so a merge dropped the open mark and never added the collateral. `MERGE` `usdcSize` is cash in, same sign as `REDEEM`. `SPLIT` is not counted. Sqlite `MERGED` was already in `cmd_wallet` and is not added again. No live activity row was available: no SSH.
4. `DayFold.merge` and `n_merge` default to 0 so existing dashboard fixtures still construct. `fold_polymarket_day` always sets them. The uppercase `HOST_TREES` / `LIVE_WALLET_CANDIDATES` stay constant (basedpyright). The override is `_root_override`, so the existing wallet test that monkeypatches `LIVE_WALLET_CANDIDATES` still works.
5. Self-check, called from `check_restart_block`, covers the merge fold and `--root` (live tree, funder `0xb`, `open_map` vs flat-past-cutoff). `tests/test_dashboard.py` calls `check_root_scope` and folds a `MERGE` plus a `SPLIT`.
6. The env check prints `set` or `empty` only. A blank `TG_CHAT_ID_B` does not stop B: `notify.py` logs `telegram message skipped: TG_BOT_API_TOKEN or TG_CHAT_ID not set` and returns. The runbook says do not start until that line says `set`.
7. `live_b` `stop_grace_period` is already `240s` in this worktree. The runbook says 240s, not 200s.
8. `9 × $20 = $180` is map room. The runbook names the brakes: `NET_MAX_SHARES` 50, merges at `$130` held and at least 5 pairs, wallet cash. The core ignoring `Budget` is STEP-006 and is not in this worktree. `config_b/trading.toml` already says the cap is not enforced.

Worktree: `/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader-s8`, branch `two-sided-s8`. No edits in the main esports-trader checkout. No `.env`.

## Files

Committed in E8, `276798f15499965f1d8ee87689a60478ca1a8b76`:

- `src/dashboard/summarize.py` — `--root`, `--two-sided`, `MERGE` in the day fold, self-check
- `tests/test_dashboard.py` — merge cash assert and `check_root_scope`

Committed in betting_workspace `main`, `7fee213a70e293a88cb426314b79c3d85a41ccb6`, and only that file:

- `.shared-skills/vps-trader/SKILL.md`

Edited and left uncommitted, as the brief allows only the skill commit in this repo:

- `tasks/two-sided-live-b/feature.json` — STEP-009 `passes` set to true. Other steps untouched.
- `tasks/two-sided-live-b/progress.txt` — STEP-009 entry appended.

## Commands

| Command | Result |
|---|---|
| `python3 src/dashboard/summarize.py --self-check` | `self-check ok` |
| dashboard pytest (`test_dashboard*.py` plus `test_summarize_wallet_counts_merge_cash_not_fill_rows`) | 174 passed, 1 failed |
| `uv run ruff check` and `ruff format --check` on the two files | passed, already formatted |
| `uv run python -m basedpyright` | 0 errors, 0 warnings, 0 notes |
| `make lint` on the staged files | ruff check, ruff format, basedpyright, trailing whitespace, end-of-file, large-files passed. Commit hook repeated that and passed |

The failed test is `tests/test_dashboard_app.py::test_home_renders_all_sections` (`доступно` missing). It fails the same way on `56af02ed` with these edits stashed. STEP-003 already recorded it. Not this step.

## Runbook facts STEP-010 must re-check

These strings are in the skill. They are copied from the plans. They are not in the s8 tree (`rg` of `src` found no `trader wallet:`, no `DOTA_STRATEGY`, no `title_whitelist`).

| Fact in the skill | Source | In E8 code now |
|---|---|---|
| `trader assigned: mode=live games=dota` | `host_resources.py` format `trader assigned: mode=%s games=%s` | yes, the format string |
| `trader wallet: strategy=two_sided signature_type=3 funder=<BROWSER_ADDRESS_B>` | STEP-007 plan: `trader wallet: strategy=%s signature_type=%d funder=%s`, funder = `browser_address` | no |
| Empty whitelist refuses start: `DOTA_STRATEGY=two_sided needs names in [clips.dota].tiers: they are the title whitelist` | STEP-007 plan refusal table | no |
| `discovery skip reason=title_whitelist` | STEP-007 plan | no |
| Late fill of a pre-restart order pulls both bids for the rest of the map (`ownership_unresolved`) | STEP-006 plan decision 16 | the pull reason exists in `two_sided_quoting.py`; the restart path that sets it does not |
| `9 × $20 = $180` is not enforced; brakes are `NET_MAX_SHARES` 50, merges at `$130` and ≥ 5 pairs, wallet cash | STEP-006 plan (core ignores `Budget`); constants and the `config_b` comment are in this tree | constants and comment yes; the worker that ignores `Budget` is not in this tree |
| Final merge runs before Telegram `trader session finished` | STEP-006 plan order. Prefix exists in `notify.py`. Ledger cash already includes `MERGED` | order is not in this tree |
| Activity type `MERGE`, `usdcSize` counted as cash in | STEP-003 handoff. Not checked against a live data-api row | the fold does this now |

Already in this tree, and written as such in the skill: `stop_grace_period: 240s`; blank chat does not stop the process; `trader collateral cache empty: BUY blocked until first REST read` (there is no log that prints a positive balance); `merge match=%s pairs=%.6f tx=%s`; Telegram `trader merge: match … tx …`.

## Open issues

- `test_home_renders_all_sections` still fails. Pre-existing. STEP-010 owns a green full pytest.
- `.shared-skills/vps-trader/log-map.md` still says wallet cash is MATCHED+CONFIRMED. For B it is MATCHED+CONFIRMED+MERGED. Left unedited so the workspace commit stayed one file.
- `276798f1` is on `two-sided-s8`, not on main. Cherry-pick is not this step.
- `feature.json` and `progress.txt` are dirty on purpose.
- `--root` does not hide A's paper or legacy match rows.
- 240s does not cover two maps merging at once, or SIGTERM during the final merge. The runbook gate is `restart_check SAFE`. That limit is from the STEP-008 plan.
