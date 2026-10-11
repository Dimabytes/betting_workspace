# STEP-006 plan: framework in our private repo, Makefile pin, docs (B1 without the crash check)

Base, checked 2026-10-11 02:00 CEST:
- `esports-trader` `main` at `420b2ec3` (clean, ahead of `origin/main` by 9, do not push).
- `betting_workspace` `main` at `8f5083e` (`AGENTS.md`, `.learnings/` clean).
- `prediction-market-backtesting` on `local-day-native` at `e2cfebb2c393a772e9b336166dfc6909e02e14b2`, clean.

## 0. Result in one paragraph

The two framework commits (`df17b59`, `e2cfebb`) move into a new private GitHub repo `Dimabytes/prediction-market-backtesting`. The checkout's current `origin` (evan-kolberg) is renamed to `upstream`, which stays read-only. Only one line of product code changes: `esports-trader/Makefile:9` pins `BACKTEST_FRAMEWORK_COMMIT` to the full `e2cfebb` SHA. After that, `make backtest-install` passes and rebuilds `_native_ext.cpython-313-darwin.so` in the framework checkout. The workspace learning and `AGENTS.md` are updated to match. The framework repo gets no new commits. Its only new state is remote config, one tracking branch, and a rebuilt gitignored `.so`.

## 1. What is true today (evidence)

### 1.1 Framework checkout

- `git -C ../prediction-market-backtesting remote -v`: one remote, `origin https://github.com/evan-kolberg/prediction-market-backtesting.git` (fetch and push).
- `git branch -vv`: `local-day-native e2cfebb` has no upstream. `v4.1-alpha c76e77a [origin/v4.1-alpha]`. Remote branches: `origin/v1`, `v2`, `v3`, `v4`, `v4.1-alpha`, and `origin/HEAD -> origin/v4.1-alpha`.
- `git tag --list | wc -l` is `0`. `gh api repos/evan-kolberg/prediction-market-backtesting/tags --jq length` is also `0`. So `git push origin --tags` is a no-op today.
- `git status --porcelain` shows nothing untracked, only ignored build and cache dirs. The session export `2026-10-09-203242-local-command-caveat…txt` from `fw-fork.md:27` is **already gone**, so there is nothing to delete.
- Pack size: `git count-objects -vH` reports `size-pack: 164.38 MiB`, and `local-day-native` has 478 commits. There is no LFS (`.gitattributes` only has a linguist line).
- `.gitignore` ignores `*.so` and `target/`.
- `.github/workflows/ci.yml` runs on **every** `push` and does a full Rust build, ruff, and pytest. `docs.yml` runs on `push` to `v4.1-alpha` when docs paths change. On a new private repo both would use Actions minutes on the Dimabytes account. CI would also be red, because the repo carries 14 pre-existing ruff findings (`.learnings/prediction-market-backtesting.md:31-33`).

### 1.2 GitHub and SSH

- `gh auth status`: `di-payflow` is the **active** account. `Dimabytes` is logged in but inactive. Both have `repo` scope.
- `GH_TOKEN="$(gh auth token --user Dimabytes)" gh api user --jq .login` returns `Dimabytes`, so a per-command token works without switching the active account.
- `GH_TOKEN=… gh api repos/Dimabytes/prediction-market-backtesting` returns **404**, so the repo does not exist yet. Upstream `v4.1-alpha` is still `c76e77af00ef53472a9da8f66dae7fdd2d3e5928`.
- `ssh -T git@github.com` returns `Hi Dimabytes!`. `~/.ssh/config` maps `github.com` to `~/.ssh/id_rsa`. The private `Dimabytes/esports-trader` (`gh repo view` reports `PRIVATE`) already pushes over `git@github.com:Dimabytes/…`, so the SSH URL in the step works for a private Dimabytes repo.

### 1.3 Makefile pin and `backtest-install`

- `esports-trader/Makefile:8-9`: `BACKTEST_FRAMEWORK ?= ../prediction-market-backtesting`, `BACKTEST_FRAMEWORK_COMMIT ?= c76e77af00ef53472a9da8f66dae7fdd2d3e5928`.
- `Makefile:85-94` `backtest-install` does these steps in order:
  1. Checks that the framework directory exists.
  2. Compares `git rev-parse HEAD` with the pin.
  3. Runs `uv sync --group backtest`. This is an exact sync.
  4. Runs `uv run --group backtest --with "maturin>=1.12,<2" maturin develop --release --manifest-path "$(BACKTEST_FRAMEWORK)/crates/python/Cargo.toml" --uv`.
- Today, `make backtest-install` exits 2 with `framework HEAD e2cfebb2c393a772e9b336166dfc6909e02e14b2 != pinned c76e77af00ef53472a9da8f66dae7fdd2d3e5928`. It fails at the pin check, before any side effect.
- `uv sync --group backtest --dry-run` reports `Would make no changes`, so the exact sync will not remove anything from the shared `.venv`.
- `crates/python/pyproject.toml`: `module-name = "prediction_market_extensions._native_ext"`, `python-source = "../.."`. This is a mixed project, so `maturin develop` copies the built `.so` into the python source dir (maturin README, "Mixed rust/python projects"). That dir is `prediction-market-backtesting/prediction_market_extensions/`, the same file the backtest imports through `PYTHONPATH`. It also installs an editable dist named `python` into `esports-trader/.venv`, whose `.pth` points at the framework root. Nothing breaks from that:
  - `pyrightconfig.json` `extraPaths` and `make test`/`backtest` `PYTHONPATH` already include `../prediction-market-backtesting`.
  - `git grep` finds no `import scripts|tests|strategies|backtests|live|main` in `esports-trader` `src/scripts/tests`, so the framework's regular packages `scripts`, `tests`, `strategies`, and `backtests` shadow nothing.
- Other places that mention the pin, none of which need a change:
  - `src/backtest/run.py:754` `read_framework_commit()` reads the HEAD SHA, not the pin.
  - The tests monkeypatch it.
  - `docs/experiments/*` and `docs/extraction-oracle.md:42` are historical records of `c76e77a`.

### 1.4 The binary

- The checkout `.so` is `prediction_market_extensions/_native_ext.cpython-313-darwin.so`: 7,821,776 bytes, mtime 2026-10-09 22:13:13, SHA-256 `15233bfcb05733e33e11523c087ba259cc499e277cbf288f2f1acb5a369e8a2c`.
- Scratch rebuild, done in `orchestrate/work/plan-006/` (log in `build.log`, hash in `so.sha256`): `git archive e2cfebb | tar -x`, then `.venv/bin/maturin build --release` with its own `CARGO_TARGET_DIR`. It took **25 s** from cold (`Finished release … in 23.33s`) and produced a `.so` with the **same SHA-256** `15233bfc…8a2c`. The build is reproducible on this toolchain (`cargo 1.97.1`, `rustc 1.97.1`; Cargo.toml requires `rust-version = "1.93.1"`).
- Baseline tests, run from `esports-trader` with `-p no:cacheprovider` and `PYTHONDONTWRITEBYTECODE=1`:
  - `tests/test_native.py -k fixed_raw`: `2 passed, 20 deselected`.
  - All of `test_native.py`: `22 passed`.
  - `test_telonex_data_source.py`: `47 passed`. These are the `df17b59` tests.
- `lsof` on the `.so` shows no open handle right now, and no `backtest.run` process is running.

### 1.5 Docs that say "read-only checkout of upstream"

- `betting_workspace/.learnings/prediction-market-backtesting.md:6`: "Local source checkout … (`origin` is upstream: `evan-kolberg/…`)".
- The same file, `:12-17`, says "Read-ONLY! Still. … Any further change to this repo needs the owner's explicit agreement first".
- The same file, `:37-46`, gives a rebuild that uses only `maturin build` plus unzip.
- `betting_workspace/AGENTS.md:13`: `└─ prediction-market-backtesting/     # local source checkout of the Nautilus backtesting library`.
- `esports-trader/docs/rebuild-order.md:26-33` points at the learning for the rebuild command. It stays correct once the learning names `make backtest-install`.

## 2. Decisions

1. **Use `GH_TOKEN="$(gh auth token --user Dimabytes)"` per `gh` command instead of `gh auth switch -u Dimabytes`.** The effect is the same: the repo is created by Dimabytes. A switch would flip the machine-global active account away from `di-payflow` while other agents and the user are working. If the orchestrator wants the literal command, run `gh auth switch -u Dimabytes`, create the repo, then run `gh auth switch -u di-payflow` right away.
2. **Disable GitHub Actions on the new repo before the first push** (`PUT …/actions/permissions enabled=false`). Upstream `ci.yml` fires on every push. On our repo it would spend private minutes on a gate we do not use, and it would go red on the pre-existing ruff findings. `esports-trader` is what tests the pin. This is one reversible repo setting.
3. **Push order: `local-day-native` first.** On an empty repo the first pushed branch becomes the default branch, so a clone on a new machine lands on the patched branch. Then push `v4.1-alpha` (`c76e77a`), so the base of the two patches is in our repo. `--tags` stays in as the step lists it. It pushes nothing, because there are 0 tags.
4. **Pin after the push.** The pinned SHA must be reachable on `origin` before `esports-trader` points at it.
5. **Build with `make backtest-install`, not a manual `maturin build`.** That is what the step requires, and it now passes the pin check. Back up the current `.so` to `orchestrate/work/impl-006/` first, and check that nothing has it open (`lsof`). On macOS, overwriting a signed dylib that a running process has mapped can kill that process. The expected result is a byte-identical `.so` (SHA `15233bfc…`), because the scratch build reproduced it. If the hash differs, `test_native.py` decides.
6. **The learning names `make backtest-install` as the rebuild and new-machine path.** The manual `maturin build` plus unzip command stays as the way to build a HEAD that is not pinned yet. The stance changes from "read-only" to: no commits without the owner's agreement, push only to `origin` and only when the owner asks, never to `upstream`.
7. **`AGENTS.md` changes minimally:** the layout comment, plus one Git Rules bullet. `docs/rebuild-order.md` and `esports-trader/AGENTS.md` do not change.
8. **No framework commits, no remote config beyond rename and add, no `pushurl` guard on `upstream`.** GitHub already denies push to `upstream` for both accounts (`push: false`, `fw-fork.md:43`).

## 3. Changes (run in this order)

`FW=/Users/dimabytes/work/polymarket/dota_2_bot/prediction-market-backtesting`, `C=/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader`, `W=/Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace`, `R=$W/tasks/sim-parity-fixes/orchestrate`.

### 3.1 Pre-flight (stop if any check fails)

```bash
git -C "$FW" branch --show-current          # local-day-native
git -C "$FW" rev-parse HEAD                 # e2cfebb2c393a772e9b336166dfc6909e02e14b2
git -C "$FW" status --porcelain             # empty
git -C "$FW" remote -v                      # only origin = evan-kolberg
GH_TOKEN="$(gh auth token --user Dimabytes)" gh api user --jq .login                    # Dimabytes
GH_TOKEN="$(gh auth token --user Dimabytes)" gh api repos/Dimabytes/prediction-market-backtesting  # 404
ssh -T git@github.com                       # Hi Dimabytes!
```

If `Dimabytes/prediction-market-backtesting` already exists, do not recreate or delete it. Check that it is private and empty (`gh api …/branches --jq length` → 0). Then skip `gh repo create`, run the two Actions commands in 3.2, and continue with 3.3. If it is not empty, stop and report.

There is nothing to delete for the session export: it is already absent (§1.1). Record that in `progress.txt`.

### 3.2 Create the private repo

```bash
GH_TOKEN="$(gh auth token --user Dimabytes)" gh repo create Dimabytes/prediction-market-backtesting --private
GH_TOKEN="$(gh auth token --user Dimabytes)" gh api -X PUT repos/Dimabytes/prediction-market-backtesting/actions/permissions -F enabled=false
GH_TOKEN="$(gh auth token --user Dimabytes)" gh api repos/Dimabytes/prediction-market-backtesting/actions/permissions --jq .enabled   # false
```

### 3.3 Remotes and push (in `$FW`)

```bash
cd "$FW"
git remote rename origin upstream
git remote add origin git@github.com:Dimabytes/prediction-market-backtesting.git
git push -u origin local-day-native     # ~164 MiB upload, 1–3 min
git push origin v4.1-alpha
git push origin --tags                  # "Everything up-to-date" (0 tags)
```

- `git remote rename` moves `refs/remotes/origin/*` to `upstream/*` and rewrites `branch.v4.1-alpha.remote`, so `v4.1-alpha` tracks `upstream/v4.1-alpha`. The VS Code key `branch.local-day-native.vscode-merge-base=origin/v4.1-alpha` stays. After the `v4.1-alpha` push, `origin/v4.1-alpha` exists again at the same `c76e77a`, so leave it.
- If a push fails, leave the remotes as they are now. Do not rename back. Fix the cause (SSH or network) and push again, or stop and report.
- If `defaultBranchRef` is not `local-day-native` after the push (§6), set it: `GH_TOKEN=… gh repo edit Dimabytes/prediction-market-backtesting --default-branch local-day-native`.

### 3.4 Pin (`esports-trader/Makefile:9`)

```make
BACKTEST_FRAMEWORK_COMMIT ?= e2cfebb2c393a772e9b336166dfc6909e02e14b2
```

This is the only product-code change, −1/+1 lines.

### 3.5 Build the extension (in `$C`)

```bash
SO="$FW/prediction_market_extensions/_native_ext.cpython-313-darwin.so"
lsof "$SO"                                   # no output; otherwise wait for that process, do not kill it
mkdir -p "$R/work/impl-006" && cp -p "$SO" "$R/work/impl-006/_native_ext.before.so"
shasum -a 256 "$SO"                          # 15233bfc…8a2c (before)
time make backtest-install                   # exit 0; uv sync is a no-op; cargo build ≤ ~30 s
shasum -a 256 "$SO"; stat -f '%Sm %z' "$SO"  # new mtime; expected SHA 15233bfc…8a2c again
git -C "$FW" status --porcelain              # still empty (the .so and target/ are ignored)
```

If the `test_native.py` check in §6 fails with the new binary, restore it with `cp -p "$R/work/impl-006/_native_ext.before.so" "$SO"` and stop and report. If the build itself fails, maturin does not copy anything and the old `.so` stays.

### 3.6 Docs in `betting_workspace`

**`.learnings/prediction-market-backtesting.md`**

Replace line 6 with these two bullets. Lines 5, 7, and 8 stay.

```markdown
- Checkout of the NautilusTrader prediction-market backtesting library. `origin` is our private repo `git@github.com:Dimabytes/prediction-market-backtesting.git`, branch `local-day-native`. `upstream` is `evan-kolberg/prediction-market-backtesting`, read-only: fetch only, no push, no PRs. Not a GitHub fork: GitHub has no private fork of a public repo.
- New machine: `git clone git@github.com:Dimabytes/prediction-market-backtesting.git` next to `esports-trader` (default branch `local-day-native`), `git remote add upstream https://github.com/evan-kolberg/prediction-market-backtesting.git`, then `make backtest-install` from `esports-trader`.
```

Replace the stance paragraph at lines 12-17. Add the pin bullet before the existing "The branch must stay checked out" bullet (`:19`), and keep that bullet and the rest of the section unchanged.

```markdown
No commits without the owner's explicit agreement, even small ones. Two
patches exist, both agreed with the owner on 2026-10-09, on `local-day-native`
on top of upstream `c76e77a`: `df17b59` (Rust diff of daily book files) and
`e2cfebb` (book prices to raw with integers). Both are on `origin`. Push only
to `origin`, and only when the owner asks.

- Pin: `esports-trader/Makefile` `BACKTEST_FRAMEWORK_COMMIT` is the full SHA of
  the branch head (`e2cfebb2c393a772e9b336166dfc6909e02e14b2`).
  `make backtest-install` refuses any other HEAD. A new framework commit moves
  the pin in the same esports-trader change.
```

Replace the rebuild paragraph at lines 37-46:

```markdown
Found and fixed 2026-10-09 with the owner's agreement. The fix is Rust: the
compiled `prediction_market_extensions/_native_ext.cpython-313-darwin.so` is
gitignored, so a checkout of the commit does nothing until the extension is
rebuilt. Rebuild after any change under `crates/` and on every new machine
(Rust >= 1.93.1 from rustup): `make backtest-install` from `esports-trader`.
It checks HEAD against the pin and runs `maturin develop --release`, which
writes the `.so` into this checkout. To build a HEAD that is not pinned yet:
`uvx --from "maturin>=1.12,<2" maturin build --release --manifest-path
crates/python/Cargo.toml -i ../esports-trader/.venv/bin/python --out <dir>`,
then unzip `prediction_market_extensions/_native_ext*.so` from the wheel into
the checkout. Check it took: `tests/test_native.py -k fixed_raw` passes only
with the new binary. `framework_commit` in a manifest does not prove which
binary ran.
```

**`AGENTS.md`**

Line 13, keeping the comment column:

```
└─ prediction-market-backtesting/     # Nautilus backtesting library: our private repo, upstream read-only
```

Add one bullet at the end of `## Git Rules`, after line 33:

```markdown
- `../prediction-market-backtesting`: `origin` is our private `Dimabytes/prediction-market-backtesting`, `upstream` is `evan-kolberg/prediction-market-backtesting`. Never push to `upstream`, never open PRs there.
```

### 3.7 Commits (no push of `esports-trader` or `betting_workspace`)

```bash
cd "$C" && git add Makefile && git commit -m "fix: STEP-006 - pin the backtest framework to e2cfebb"
cd "$W" && git add .learnings/prediction-market-backtesting.md AGENTS.md tasks/sim-parity-fixes/progress.txt \
  && git commit -m "Record STEP-006: the framework lives in our private repo, pinned at e2cfebb."
```

In `progress.txt`, write:
- The repo URL, visibility, and default branch.
- That Actions are disabled.
- The pushed refs with their SHAs.
- That the session export was already gone.
- The `.so` SHA before and after, and the `make backtest-install` run time.

Learnings to add:
- 0 tags, so `--tags` is a no-op.
- `maturin develop` writes the `.so` in-tree and adds an editable `python` dist to `.venv`.
- The `GH_TOKEN` per-command pattern.

Leave `passes` false.

## 4. Edge cases and risks

- **Global gh state.** Never leave `Dimabytes` as the active account. The final `gh auth status` must show `di-payflow` active.
- **Shared `.so`.** Every backtest on this Mac imports the same file. Run the `lsof` check (§3.5) before `make backtest-install`, and do not kill another process to free it.
- **Exact `uv sync`.** Today it is a no-op (dry run). If it reports removals on the day, stop and report instead of letting it prune the shared `.venv`.
- **The VPS or other machines** may hold an upstream clone or a manual copy of the framework. `fw-fork.md:87` notes the disk was not checked, and SSH is forbidden. This step does not touch them. Moving them to `origin` needs a deploy key or token there and is owner work. The live trader does not import the framework (`fw-fork.md:56`).
- **Push size.** 164 MiB is well under GitHub limits. Upstream already hosts the same objects, so no blob is over 100 MB.
- **The worktree `../esports-trader-cadence`** still pins `c76e77a` on its branch. It shares the same checkout and `.so`, so it needs no `backtest-install`. STEP-010 removes it.
- **The `.pth` from `maturin develop`** adds the framework root to `.venv` `sys.path`. It is harmless today (§1.3). A later exact `uv sync` removes that dist but keeps the in-tree `.so`, and imports through `PYTHONPATH` keep working.
- **Out of scope here:** the Nautilus crash check (B1.3) is STEP-014, and no backtest, training, or data rebuild runs in this step.

## 5. Not in this step

- No upstream PR.
- No framework commits.
- No changes to `docs/rebuild-order.md` or `esports-trader/AGENTS.md`.
- No `NAUTILUS_ZERO_FILL_SKIP_IDS` work.
- No push of `esports-trader` or `betting_workspace`.
- No `poly-maker` or `py_clob_client*`.

## 6. Verification

**Framework repo and GitHub** (in `$FW`):

```bash
git remote -v
# origin   git@github.com:Dimabytes/prediction-market-backtesting.git (fetch/push)
# upstream https://github.com/evan-kolberg/prediction-market-backtesting.git (fetch/push)
git status -sb                               # ## local-day-native...origin/local-day-native
git branch -vv                               # local-day-native e2cfebb [origin/local-day-native]; v4.1-alpha c76e77a [upstream/v4.1-alpha]
git rev-list --left-right --count origin/local-day-native...HEAD   # 0	0
git ls-remote origin                         # refs/heads/local-day-native e2cfebb2…; refs/heads/v4.1-alpha c76e77af…
git status --porcelain                       # empty
GH_TOKEN="$(gh auth token --user Dimabytes)" gh repo view Dimabytes/prediction-market-backtesting \
  --json visibility,defaultBranchRef --jq '.visibility+" "+.defaultBranchRef.name'      # PRIVATE local-day-native
GH_TOKEN="$(gh auth token --user Dimabytes)" gh api repos/Dimabytes/prediction-market-backtesting/actions/permissions --jq .enabled   # false
gh auth status                               # di-payflow still the active account
```

**`esports-trader`** (in `$C`; no live map running; do not set `PYTEST_N` on the VPS):

```bash
make backtest-install                        # exit 0 (was exit 2 on the pin check)
shasum -a 256 ../prediction-market-backtesting/prediction_market_extensions/_native_ext.cpython-313-darwin.so
# expected 15233bfcb05733e33e11523c087ba259cc499e277cbf288f2f1acb5a369e8a2c, with a new mtime

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:../prediction-market-backtesting uv run --group backtest \
  python -m pytest -q -p no:cacheprovider ../prediction-market-backtesting/tests/test_native.py -k fixed_raw
# 2 passed, 20 deselected

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:../prediction-market-backtesting uv run --group backtest \
  python -m pytest -q -p no:cacheprovider ../prediction-market-backtesting/tests/test_native.py \
  ../prediction-market-backtesting/tests/test_telonex_data_source.py
# 69 passed (22 + 47, baseline today)

PYTHONPATH=src:../prediction-market-backtesting uv run --group backtest python -c \
  "import prediction_market_extensions._native_ext as m; print(m.__file__)"
# …/prediction-market-backtesting/prediction_market_extensions/_native_ext.cpython-313-darwin.so

uv run python -m basedpyright                # 0 errors, 0 warnings, 0 notes (impl-005-fix at 420b2ec3)

PYTEST_N=8 PYTHONPATH=src:scripts:../prediction-market-backtesting uv run --group backtest \
  python -m pytest -q --ignore=tests/test_follow300_replay.py
# 3266 passed (impl-005-fix); smoke of the rebuilt binary, ~25–30 s

uv run python -m pre_commit run --files Makefile   # whitespace / end-of-file pass; python hooks skipped
git show --stat HEAD                         # Makefile | 2 +-
```

**`betting_workspace`** (in `$W`):

```bash
git show --stat HEAD     # AGENTS.md, .learnings/prediction-market-backtesting.md, tasks/sim-parity-fixes/progress.txt
rg -n -i 'is upstream:|Read-ONLY! Still|local source checkout' AGENTS.md .learnings/prediction-market-backtesting.md   # no output
rg -n 'Dimabytes/prediction-market-backtesting' AGENTS.md .learnings/prediction-market-backtesting.md          # AGENTS.md Git Rules bullet + learning Setup lines
```

Run time for the whole step is about 10 minutes, most of it the 164 MiB push. No backtest, training, or data rebuild runs.
