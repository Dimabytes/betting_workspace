# STEP-001 plan: CS2 archive in the collector, CS2 onchain from 2026-10-23

Step: `STEP-001` of `tasks/cs2-live/feature.json` (priority 1, `passes: false`).
Repo: `../polymarket-collector`, branch `main` (clean, HEAD `2bbcc4e`).
Docs outside the repo: `../esports-trader/AGENTS.md:19` and `betting_workspace/.learnings/polymarket-collector.md`.
No Figma or design links exist for this step.

The step adds a sixth Compose service `archive-cs2` (tag `100780`) and a third archive root
(`ARCHIVE_ROOT_CS2`) to `compact`, `onchain` and `ship`. CS2 takes part in onchain work from
`ONCHAIN_START_DATE_CS2=2026-10-23` only. Dota/LoL keep `ONCHAIN_START_DATE`.

Do not deploy, do not `git push`, do not touch STEP-002.

## 1. Facts this plan rests on

- Map filter accepts only `Game N`: `src/selection.ts:15` `MAP_WINNER_TITLE_PATTERN = /^Game ([1-9][0-9]*) Winner$/`.
- Public Gamma, 2026-10-23 18:27 UTC, `tag_id=100780&active=true&closed=false&limit=20`:
  CS2 map markets are `('child_moneyline', 'Map 1 Winner')` ×19 and `('child_moneyline', 'Map 2 Winner')` ×19;
  series are `('moneyline', 'Match Winner')` ×19. Other pairs (`cs2_odd_even_*`, `totals`, `map_handicap`) stay unknown pairs.
  The first page of 100 events held 99 series markets and 182 map markets.
  So only the title regex changes; `sportsMarketType` stays `child_moneyline`.
- Game lists are hard-coded in 9 source places:
  `src/app.ts:251-254`, `src/onchain-app.ts:282-283, 407, 419-422`, `src/onchain-plan.ts:54`,
  `src/onchain-day.ts:68`, `src/onchain-import-main.ts:69, 127-130`, `src/onchain-repair-main.ts:32-35`,
  `src/ship-app.ts:129-139`, `src/telegram.ts:31-36`.
- Config structs with two roots: `src/config.ts:22-30, 62-67`, `src/onchain-config.ts:16-35, 38-78, 160-172`,
  `src/onchain-import.ts:146-183`, `src/ship-config.ts:11-13, 31-33`.
- The planner treats a game without a manifest as not ready: `src/onchain-plan.ts:170-193`.
  With a third game in `GAMES` and no date rule, every day since `ONCHAIN_START_DATE` would reopen.
- CS2 sidecars carry old `startAt` values (test default `2020-01-01T00:00:00Z`, `src/onchain.testing.ts:149`).
  `couldTradeDuring` (`src/onchain-catalog.ts:233-243`) then counts them as could-trade on every earlier day.
  The date rule must cut them before coverage, conflicts, scan and publish.
- Boot sweep keeps chunk staging unless every root has a manifest: `src/onchain-app.ts:124-129` (`Option.all`).
  A CS2 root without pre-boundary manifests would pin Dota/LoL staging of those days forever.
- Reconcile skips a missing `manifests/onchain` dir: `src/onchain-reconcile.ts:140-145` (`NotFound` → `[]`).
- Repair lists only existing manifests per root: `src/onchain-repair.ts:265-268, 297-299`. A CS2 root with no
  pre-boundary manifests repairs nothing there.
- Ship is manifest-driven per target and tolerates a missing manifests dir: `src/ship.ts:115-117`.
- Publishing a game day with zero tokens works: `src/onchain-publish.test.ts:315` (`tokens: []`).
- `docker.test.ts` counts today: `init`/`restart`/`stop_grace_period` 5 (`:93-95`), `oom_score_adj` 2 (`:96`),
  `- type: bind` 10 (`:257`).
- `rrsync` is not installed locally (`which rrsync` → `rrsync not found`), so `describe.skipIf(!hasRrsync)`
  blocks in `src/ship.test.ts:40` and `src/ship-app.test.ts:70` skip on this machine.
- `yarn check` = `yarn typecheck && yarn diagnostics && yarn lint && yarn test` (`package.json:16`).

## 2. Design decisions

1. **One root descriptor carries the start date.** Add `readonly startDate?: UtcDate` to the existing
   `CatalogRoot` (`src/onchain-catalog.ts:56-59`). Absent means "no lower bound".
   Compaction (`src/app.ts`) keeps building roots without it, so compaction is unchanged.
   Add one pure helper next to it and route every onchain decision through it:

   ```ts
   export const takesPart = (root: CatalogRoot, date: UtcDate): boolean =>
     root.startDate === undefined || root.startDate <= date
   ```

   `UtcDate` is a `YYYY-MM-DD` string; string order is date order (the planner already uses `date < args.today`).

2. **Delete both `GAMES` constants.** `src/onchain-plan.ts:54` and `src/onchain-day.ts:68` duplicate the root list.
   The games of a day become `args.roots.filter((root) => takesPart(root, date))`, in root list order
   (Dota, LoL, CS2). No test passes a subset of roots (`grep -n "roots: \[" src/onchain*.test.ts` → empty),
   so existing tests keep their meaning.

3. **The iteration start stays `ONCHAIN_START_DATE`.** `listUnfinishedDays` and the lag loop in
   `runOnchainTick` keep `startDate` as the first listed day. `validateOnchainSettings` rejects
   `ONCHAIN_START_DATE_CS2` earlier than `ONCHAIN_START_DATE` with a message that names both. This keeps all
   signatures and test calls unchanged.

4. **`ONCHAIN_START_DATE_CS2` is required, no default**, like `ONCHAIN_START_DATE`. Compose pins
   `ONCHAIN_START_DATE_CS2: "2026-10-23"` (quoted: unquoted YAML can parse it as a timestamp).
   It is not read from `.env`.

5. **Legacy import keeps no bound for Dota/LoL.** The import exists for Dota/LoL days before
   `ONCHAIN_START_DATE`, so their import roots carry no `startDate`. Only the CS2 import root carries
   `ONCHAIN_START_DATE_CS2`. `accept` rejects a CS2 file dated before it before any staged read or copy.

6. **No new abstractions.** No per-game config map, no game registry. Each config struct gets one more
   field, each root list gets one more entry. One small builder `importRoots(settings)` in
   `src/onchain-import.ts` replaces the identical inline lists of `onchain-import-main.ts` and
   `onchain-repair-main.ts` (two callers, same settings type).

7. **Comments.** `.feature-json.config.json` review rule: new code gets no new comments. Fix or delete
   existing comments that become false ("both games", "BOTH games' manifests", "Dota, then LoL",
   "no start date"). Do not add new JSDoc blocks.

## 3. Changes by file

### 3.1 Discovery and collect service

- `src/selection.ts:15`:
  `export const MAP_WINNER_TITLE_PATTERN = /^(?:Game|Map) ([1-9][0-9]*) Winner$/`.
  `classifyPair` (`:43-56`) already reads `match[1]`; no other change.
- `src/telegram.ts:31-36` `gameLabel`: add `if (polymarketTagId === "100780") return "cs2"`.
  Update the line-31 doc to list CS2 `100780`.
- `compose.yaml`: insert `archive-cs2` right after `archive-lol` (`:27-50`), a copy of it with
  `POLYMARKET_TAG_ID: "100780"` and bind `source: /var/lib/polymarket-cs2-archive` → `target: /data`.
  Same `image: polymarket-collector:latest`, no `build:`, same `init`, `restart`, `stop_grace_period: 30s`,
  `oom_score_adj: -500`, `env_file`, disk and logging settings.

### 3.2 compact (third root)

- `src/config.ts`: `CompactSettings` gets `readonly cs2ArchiveRoot: string` (`:22-30`);
  `compactConfig` gets `cs2ArchiveRoot: Config.string("ARCHIVE_ROOT_CS2")` (`:62-67`).
  Fix the `:22` doc ("walks the Dota archive, then the LoL archive").
- `src/app.ts:251-254` `compactMain`: append `{ game: "cs2", archiveRoot: settings.cs2ArchiveRoot }`.
  Locks, layout, sweeps and `compactGames` already loop over `targets`. Fix the `:239-240` doc ("both games").
- `src/compaction-scheduler.ts`: no code change; `compactGames` already isolates per-game failures (`:163-185`).
- `compose.yaml` `compact`: `ARCHIVE_ROOT_CS2: /data-cs2` and bind
  `/var/lib/polymarket-cs2-archive` → `/data-cs2`.

### 3.3 ship (third root)

- `src/ship-config.ts`: `cs2ArchiveRoot` in `ShipSettings` (`:11-13`) and
  `cs2ArchiveRoot: Config.string("ARCHIVE_ROOT_CS2")` in `rawConfig` (`:31-33`).
- `src/ship-app.ts:129-139` `buildShipTargets`: append `{ game: "cs2", archiveRoot: settings.cs2ArchiveRoot }`.
  It lands in `${target}:cs2/`, the existing `tank/polymarket/cs2/` dataset dir. Fix the "both archive locks"
  doc at `shipMain`.
- `src/ship.ts`, `src/ship-plan.ts`, `src/ship-prune.ts`, `src/ship-alerts.ts`: no change (game is a string).
- `compose.yaml` `ship`: `ARCHIVE_ROOT_CS2: /data-cs2` and the same bind.

### 3.4 onchain config

- `src/onchain-config.ts`:
  1. Extract the date check at `:124-137` into an exported pure function and call it twice:

     ```ts
     export const validateUtcDay = (name: string, raw: string): Effect.Effect<UtcDate, ConfigurationError> =>
       Effect.gen(function* () {
         const date = yield* Schema.decodeEffect(UtcDate)(raw).pipe(
           Effect.mapError((error) => new ConfigurationError({
             message: `${name} must be a YYYY-MM-DD UTC date: ${error.message}`
           }))
         )
         if (Option.isNone(utcDayBoundsUs(date)))
           return yield* new ConfigurationError({ message: `${name} is not a real UTC day, got "${raw}"` })
         return date
       })
     ```

  2. `OnchainSettings` (`:16-35`) gets `cs2StartDate: UtcDate` and `cs2ArchiveRoot: string`.
     `RawOnchainSettings` (`:38-56`) gets `cs2StartDate: string` and `cs2ArchiveRoot: string`.
     `rawConfig` (`:58-78`) gets `cs2StartDate: Config.string("ONCHAIN_START_DATE_CS2")` and
     `cs2ArchiveRoot: Config.string("ARCHIVE_ROOT_CS2")`.
  3. In `validateOnchainSettings`: `const cs2StartDate = yield* validateUtcDay("ONCHAIN_START_DATE_CS2", raw.cs2StartDate)`,
     then fail when `cs2StartDate < startDate`:
     `ONCHAIN_START_DATE_CS2 (${cs2StartDate}) must not be before ONCHAIN_START_DATE (${startDate})`.
     Pass both new fields into `OnchainSettings.of` (`:160-172`).
- `compose.yaml` `onchain`: `ARCHIVE_ROOT_CS2: /data-cs2`, `ONCHAIN_START_DATE_CS2: "2026-10-23"`, CS2 bind.

### 3.5 onchain catalog, planner, day

- `src/onchain-catalog.ts`:
  - `CatalogRoot` gets `readonly startDate?: UtcDate`; add `takesPart` (section 2.1).
    Import `UtcDate` as a type from `./schema/primitives.ts`.
  - `conflictCouldTradeDuring` (`:250-255`, one caller) gets a `games: ReadonlySet<string>` argument:
    `conflict.claims.some((claim) => games.has(claim.game) && couldTradeDuring(claim, startUs, endUs))`.
  - Fix the `:197` doc ("Loads both archives'").
- `src/onchain-plan.ts` `listUnfinishedDays` (`:147-210`):
  - Delete `GAMES` (`:54`) and `rootOf` (`:157-158`).
  - Per date: `const roots = args.roots.filter((root) => takesPart(root, date))`.
    Merge the two loops (`:170-192`) into one loop over `roots`: read the manifest, compute
    `couldTradeTokens(args.catalog, root.game, startUs, endUs)`, add uncovered tokens, set `allReady = false`
    when the manifest is absent.
  - Conflicts (`:195-197`): pass `new Set(roots.map((root) => root.game))` to `conflictCouldTradeDuring`.
  - Fix the header comment `:17-23` ("BOTH games'").
- `src/onchain-day.ts` `collectOnchainDay` (`:198-380`):
  - Delete `GAMES` (`:68`).
  - First line of the body: `const roots = args.roots.filter((root) => takesPart(root, args.date))`.
    Every later use reads `roots`, never `args.roots`:
    - `couldTrade` map (`:249-254`): `roots.map((root) => [root.game, couldTradeTokens(...)])`.
    - `scanChunk` call (`:285-293`): `roots`.
    - publish loop (`:331-351`): `for (const root of roots)`, `couldTrade.get(root.game)`.
    - staging cleanup (`:353-358`): `for (const root of roots)`.
  - Result before the boundary: no CS2 token in `allCouldTrade`, no CS2 entry in `publishableEntries`,
    no CS2 chunk artifact, no CS2 parquet, no CS2 manifest, and no `eth_getLogs` when only CS2 tokens are open.
  - `coveredTokens` stays unchanged: it is only asked about tokens of taking-part games.
  - Fix the header comment `:60-66` ("BOTH games' manifests").
- `runOnchainPass` (`:387-461`): no change; it forwards `roots` and `startDate`.

### 3.6 onchain app and Telegram

- `src/onchain-app.ts`:
  - `sweepOnchainWork` (`:96-151`): read manifests only of
    `roots.filter((catalogRoot) => takesPart(catalogRoot, entry))` at `:124-128`.
  - `OnchainPassReport.fills` (`:78`): `{ dota; lol; cs2 }`; `:275-284` add `else if (root.game === "cs2") fills.cs2 += rows`.
  - `onchainMain`: `ensureArchiveLayout` loop (`:407`) gets `settings.cs2ArchiveRoot`.
    Roots (`:419-422`):

    ```ts
    { game: "dota", archiveRoot: settings.dotaArchiveRoot, startDate: settings.startDate },
    { game: "lol", archiveRoot: settings.lolArchiveRoot, startDate: settings.startDate },
    { game: "cs2", archiveRoot: settings.cs2ArchiveRoot, startDate: settings.cs2StartDate }
    ```

  - Starting log (`:431-438`): annotate `cs2StartDate`. Telegram start message gets it too (below).
    STEP-002 reads this line to confirm the applied date.
  - `lastFullDay` / `lagDays` (`:287-311`): no code change. They read `plans`, and the planner no longer
    lists pre-boundary days for CS2.
- `src/telegram.ts`:
  - `formatOnchainStarted(stateDir, startDate, cs2StartDate)` (`:102-108`) adds the line `cs2StartDate=<date>`.
  - `formatOnchainPass` (`:110-119`) adds `fillsCs2=${report.fills.cs2}` after `fillsLol`.

### 3.7 onchain import and repair CLIs

- `src/onchain-import.ts`:
  - `loadOnchainImportSettings` (`:146-183`): add `cs2ArchiveRoot: Config.string("ARCHIVE_ROOT_CS2")` and
    `cs2StartDate: Config.string("ONCHAIN_START_DATE_CS2")`; add `ARCHIVE_ROOT_CS2` to the non-empty list;
    validate the date with `validateUtcDay("ONCHAIN_START_DATE_CS2", raw.cs2StartDate)` from
    `./onchain-config.ts`; return `cs2StartDate: UtcDate`. Fix the `:145` doc ("no start date").
  - New `export const importRoots = (settings: ...) : ReadonlyArray<CatalogRoot>` returning dota and lol without
    `startDate` and `{ game: "cs2", archiveRoot: settings.cs2ArchiveRoot, startDate: settings.cs2StartDate }`.
  - `acceptOnchainImport` (`:457`): `rootOf` (`:498`) maps game → `CatalogRoot`; `manifestFor` reads
    `.archiveRoot`. In the file loop, right after the `unknown game` reject (`:561`), add
    `if (!takesPart(root, file.date)) { entries.push(reject(\`before onchain start date ${root.startDate}\`)); continue }`.
    The reject comes before the staged-file read, hash, probe and copy: no file is written for it.
- `src/onchain-import-main.ts`: `usage` (`:24-26`) shows `--game <dota|lol|cs2>`; `:69-73` accepts `cs2`
  (`--game must be dota, lol or cs2`); `:127-130` uses `importRoots(settings)`. Fix the header `:18-22`.
- `src/onchain-repair-main.ts:32-35`: `importRoots(settings)`. Repair logic stays manifest-driven.

### 3.8 Docs and notes

- `docs/polymarket_dota_archive_contracts.md`:
  - §3.1: one line that Compose pins the tag per collect service (Dota `102366`, LoL `65`, CS2 `100780`).
  - §3.2 (`:85-113`): `groupItemTitle matches /^(?:Game|Map) ([1-9][0-9]*) Winner$/`; one sentence that CS2
    names map markets `Map N Winner`.
  - §3.3 (`:119`): "new `Game N Winner` / `Map N Winner`".
  - §12.1 item 1 (`:700-702`): order Dota, LoL, CS2.
  - §13.2 (`:849`): "all games" instead of "обеих игр".
  - New short §13.3 "Onchain: дата начала игры": each game takes part in an onchain day from its own start
    date (`ONCHAIN_START_DATE` for Dota/LoL, `ONCHAIN_START_DATE_CS2=2026-10-23` for CS2). Before it the game
    needs no manifest, gets no chunks, parquet or manifest, and does not block or reopen the day. The same rule
    applies to planning, token coverage, conflicts, scan, publish, readiness/lag and staging cleanup. Only closed
    UTC days run, so CS2 day 2026-10-23 is processed after it closes on 2026-10-24 UTC. Moving
    `ONCHAIN_START_DATE` for Dota/LoL is not part of this. Legacy import rejects CS2 days before the CS2 date.
  - §14 (`:923`) already says "в каждом archive root"; no change.
- `docs/docker-compose.md`:
  - Intro and "First start": six services; CS2 root creation:
    `sudo mkdir -p /var/lib/polymarket-cs2-archive` and `sudo chown 10001:10001 /var/lib/polymarket-cs2-archive`.
  - New section "Add the CS2 archive" with the order: create the root; `docker compose build archive-dota`;
    `docker compose up -d archive-cs2`; wait until `metadata/markets/*.json` appear; then
    `docker compose up -d --force-recreate --no-deps compact onchain ship` outside 03:00 UTC and with no import
    running; `docker compose logs --since 5m archive-cs2 compact onchain ship`.
  - Compact paragraph (`:53-70`): `/data-cs2`, `ARCHIVE_ROOT_CS2`, order Dota, LoL, CS2.
  - On-chain section (`:74-124`): `ONCHAIN_START_DATE_CS2` pinned in Compose; CS2 day 2026-10-23 runs after
    2026-10-24 00:00 UTC; Dota/LoL `ONCHAIN_START_DATE` does not move; `--game cs2` for inventory.
  - Ship section (`:126-168`): ship owns new CS2 days on the NAS `cs2/`.
  - `:255` `--no-deps` list includes `archive-cs2`; `:263` order Dota, LoL, CS2.
  - Keep every string `src/docker.test.ts:218-245` asserts.
- `docs/learnings.md`: prepend one entry (≤8 lines, no paths/commands), e.g.
  "### A game joins onchain on its own start date": CS2 joined later than Dota/LoL. A day lists, scans and
  publishes only games whose start date is on or before it, so old CS2 sidecars never reopen published days.
  Moving the CS2 date back reopens and rescans every earlier day; do not do it.
- `../esports-trader/AGENTS.md:19`: CS2 on the NAS is shared from now on. `ship` owns every CS2 day from the
  first `archive-cs2` day on. `src/cs2/s08_fetch_telonex_books.py` writes only days before that day, never a day
  that `ship` sent or prune removed from the VPS.
- `betting_workspace/.learnings/polymarket-collector.md`: "Compose" table gets `archive-cs2`
  (`/var/lib/polymarket-cs2-archive` → `/data`, `"100780"`) and the third root on `compact`/`onchain`/`ship`;
  six services; order Dota → LoL → CS2; `ONCHAIN_START_DATE_CS2=2026-10-23` pinned in Compose;
  "Discovery" lists `Map N Winner`; "Ship" gets the s08 rule from the line above.

## 4. Tests

Add cases to existing files. Reuse `withDualArchiveRoots`, `writeSidecar`, `makeSidecar`, `writeManifest`,
`manifestJson`, `setup`/`runPass`/`dayArgs`, `chainFor`/`yesterday`, `settingsOf`. One new test helper is enough:
`withCs2Root(startDate)` in `src/onchain.testing.ts` (temp dir, returns `{ game: "cs2", archiveRoot, startDate }`).
`makeSidecar()` already starts in 2020, so any CS2 sidecar is an "old CS2 token".

1. `src/selection.test.ts` (`:51-67` case table): `Map 1 Winner` → 1, `Map 2 Winner` → 2, `Map 10 Winner` → 10,
   `" Map 1 Winner "` → 1, `map 1 winner` → none, `Map 0 Winner` → none, `Map 01 Winner` → none,
   `Maps 1 Winner` → none, `Map Winner` → none, `Map 1 Winner` with type `totals` → none.
2. `src/telegram.test.ts`: `gameLabel("100780") === "cs2"`; `formatCompactorStarted(["dota","lol","cs2"])`;
   `formatOnchainStarted` with the `cs2StartDate=` line.
3. `src/docker.test.ts`: `archive-cs2` block (`POLYMARKET_TAG_ID: "100780"`, `ARCHIVE_ROOT: /data`,
   `source: /var/lib/polymarket-cs2-archive`, no `build:`, `oom_score_adj: -500`, `PROCESS_ROLE: "collect"`);
   counts `init`/`restart`/`stop_grace_period` 6, `oom_score_adj` 3, `- type: bind` 14
   (10 + archive-cs2 1 + compact 1 + onchain 1 + ship 1); `archive-cs2:` occurs once;
   `ARCHIVE_ROOT_CS2: /data-cs2` and `target: /data-cs2` in compact, onchain, ship;
   `ONCHAIN_START_DATE_CS2: "2026-10-23"` in onchain; runbook contains the CS2 `mkdir`/`chown` lines and
   `docker compose up -d archive-cs2`.
4. `src/config.test.ts`: compact env and `toEqual` (`:225-235`) include `cs2ArchiveRoot: "/data-cs2"`;
   the `it.effect.each` list (`:250`) adds `ARCHIVE_ROOT_CS2`.
5. `src/app.test.ts`: `withCompactRoots` (`:72-89`) adds a CS2 root and `ARCHIVE_ROOT_CS2`; expect the lock in
   all three roots (`:588-602`), `games=dota,lol,cs2` (`:562`), finished order `["dota","lol","cs2"]` (`:649`).
6. `src/compaction-scheduler.test.ts`: turn "a fatal Dota scan still runs LoL" (`:386`) into three roots with the
   middle (LoL) scan broken: Dota and CS2 compact, failure logged for LoL, finished order dota → cs2 after the
   failure. Extend "a failed day does not stop the queue" (`:418`) to three games, finished `["dota","lol","cs2"]`.
7. `src/onchain-config.test.ts`: `raw()` gets `cs2StartDate: "2026-10-23"`, `cs2ArchiveRoot: "/srv/archive/cs2"`
   (keep `startDate: "2026-08-01"`). Cases: accepts and returns both; malformed `"23/10/2026"` and
   `"2026-02-30"` → message contains `ONCHAIN_START_DATE_CS2`; CS2 date before `startDate` → message contains
   both names; `loadOnchainSettings` with an env lacking `ONCHAIN_START_DATE_CS2` (and one lacking
   `ARCHIVE_ROOT_CS2`) → `ConfigurationError` naming the variable.
8. `src/onchain-plan.test.ts` (no RPC; real dates allowed):
   - P1 "a ready Dota/LoL day before the CS2 start stays done": Dota/LoL sidecars + manifests for `2026-10-22`,
     a CS2 root (start `2026-10-23`) with an old CS2 sidecar; `listUnfinishedDays(startDate 2026-10-22, today 2026-10-23)` → `[]`.
   - P2 "2026-10-23 needs CS2": same roots, `today 2026-10-24`, Dota/LoL manifests for `2026-10-23` too →
     one `work` plan for `2026-10-23` whose `uncoveredTokens` are the CS2 pair; after a CS2 manifest covering
     the pair → `[]`. With `today 2026-10-23` the current day is not listed.
   - P3 "a CS2 conflict blocks only from the CS2 start": two disagreeing CS2 sidecars; before the start the day is
     `work`/absent, not `blocked`; on the start date it is `blocked`.
   - P4 "an unfinished day before the start lists only Dota/LoL tokens": no Dota manifest on `2026-10-22` →
     `uncoveredTokens` hold Dota tokens and no CS2 token.
9. `src/onchain-day.test.ts` (fixture date `DATE = 2026-08-08`; set the CS2 start relative to it):
   - D1 "before the CS2 start only Dota/LoL scan and publish": `setup()` roots + `withCs2Root(NEXT)` with an old
     CS2 sidecar; `runPass` publishes Dota/LoL manifests; CS2 root has no `manifests/onchain/2026-08-08.json`,
     no `parquet/onchain_fills`, no `.onchain/chunks/2026-08-08`. Assert no CS2 token is in the artifacts or
     the published parquet asset dirs.
   - D2 "a ready pre-start day with old CS2 tokens makes no RPC": after D1, a second `runPass` → `getLogsSpans`
     of the new calls is `[]` and the CS2 root is still empty.
   - D3 "on the CS2 start date CS2 publishes": `withCs2Root(DATE)` with a CS2 sidecar → CS2 manifest and
     parquet exist; a rerun after publish makes zero `eth_getLogs` calls (published chunks are not read again).
10. `src/onchain-app.test.ts`:
   - `settingsOf` (`:108-140`) takes `cs2ArchiveRoot` and `cs2StartDate`.
   - A1 `sweepOnchainWork`: Dota/LoL manifests for a date before the CS2 start, stale chunk dir, no commits →
     the dir is removed although the CS2 root has no manifest. With a CS2 start on that date the dir is kept.
   - A2 `runOnchainTick`: Dota/LoL manifests ready for `yesterday`, CS2 start = today → no RPC calls,
     `lastFullDay = yesterday`, `lagDays = 1`, `fills` equals `{ dota: 0, lol: 0, cs2: 0 }` shape, the
     Telegram text contains `fillsCs2=`. Update the existing `report.fills` expectation (`:332`).
   - A3 `onchainMain` runOnce (`:536`): three roots, CS2 start = `yesterday`, CS2 sidecar → CS2 manifest
     published, CS2 layout created.
11. `src/onchain-import.test.ts`: `inventory(f, game)` accepts `"cs2"`; fixture gets a CS2 root with
   `startDate`; a staged CS2 file dated before it → `status: "rejected"`, reason contains
   `onchain start date`, target file absent; a staged CS2 file on/after it → copied like Dota.
   `loadOnchainImportSettings` fails without `ONCHAIN_START_DATE_CS2` and on a malformed value.
12. `src/ship-config.test.ts` (`:7, :44`): `cs2ArchiveRoot`; a missing `ARCHIVE_ROOT_CS2` fails.
13. `src/ship-app.test.ts` `buildShipTargets` (`:59-68`): three targets, last `"/data-cs2 -> ship@100.67.62.33:cs2/"`.
14. `src/ship.test.ts`: one three-target `runShipPass` case where the LoL target's `manifests` is a file
   (scan fails): Dota and CS2 days ship and get receipts; `failures` names only `lol`. It sits in the
   `skipIf(!hasRrsync)` block, so it skips locally (see risk 8). `formatShipped` case (`:143`) adds a CS2 day.
15. `src/ship-alerts.test.ts` (`:66-74`): add a `cs2` scan failure next to `lol`; the alert names both.
16. `src/onchain-repair.test.ts`: no new case. `listManifestDates` returns `[]` for a root without
   `manifests/onchain` (`src/onchain-repair.ts:266-268`), unchanged code.

## 5. Verification

Run in `../polymarket-collector`:

1. `yarn typecheck`
2. `yarn vitest run src/selection.test.ts src/telegram.test.ts src/docker.test.ts src/config.test.ts src/app.test.ts src/compaction-scheduler.test.ts`
3. `yarn vitest run src/onchain-config.test.ts src/onchain-plan.test.ts src/onchain-day.test.ts src/onchain-app.test.ts src/onchain-import.test.ts src/onchain-repair.test.ts src/onchain-reconcile.test.ts`
4. `yarn vitest run src/ship-config.test.ts src/ship-app.test.ts src/ship.test.ts src/ship-alerts.test.ts`
   — report skipped `hasRrsync` tests as skipped, not passed.
5. `yarn check` (typecheck + Effect diagnostics + oxlint + all tests). It must end green.
6. `rg -n 'const GAMES' src` → no output.
7. `rg -n '"dota", "lol"\]|dotaArchiveRoot, settings.lolArchiveRoot\]' src --glob '!*.test.ts'` → no output.
8. If Docker and a local `.env` exist: `docker compose config --quiet` (renders only, starts nothing).
   If not, write "not run" in progress.
9. `git diff --stat` in each of the three repos touches only the files in section 3.

No `docker compose up`, no `ssh`, no RPC scan, no write under `/var/lib/*`.

## 6. Edge cases and risks

1. **Deploy order for 2026-10-23 (STEP-002 owner).** If `onchain` runs day 2026-10-23 before `archive-cs2`
   wrote CS2 sidecars, it publishes an empty CS2 manifest. Later sidecars with an earlier `startAt` reopen the
   day and rescan all its chunks once. If the old `onchain` already published Dota/LoL for 2026-10-23, the new
   code rescans that day once under the new catalog fingerprint. Both self-heal at the cost of one day of RPC.
   The runbook therefore starts `archive-cs2` first and recreates `onchain` after sidecars appear.
2. **CS2 markets that closed before `archive-cs2` started** get no sidecar, so their 2026-10-23 fills are not
   collected. No backfill is in scope.
3. **Moving `ONCHAIN_START_DATE_CS2` back** reopens and rescans every earlier day. A day committed while CS2
   sidecars were already in the catalog then fails with "committed chunk missing artifact" on every pass, because
   snapshot credit counts CS2 tokens the scan did not write. Out of scope; the learning says do not do it.
4. **`ONCHAIN_START_DATE_CS2 >= ONCHAIN_START_DATE` rule.** If the Dota/LoL start ever moves past 2026-10-23,
   `onchain` refuses to start until the CS2 date moves too. The error names both variables.
5. **Load.** The first Gamma page alone had 99 CS2 series and 182 map markets (100 events). `archive-cs2` adds a
   subscription set of that size, more journal and parquet bytes, and a third compaction pass at 03:00 UTC.
   STEP-002 checks RSS and disk. Odd/even, totals and handicap pairs are journaled once each as unknown pairs.
6. **Map 3.** The sample showed only `Map 1`/`Map 2 Winner`; the regex accepts any `N ≥ 1`.
7. **NAS `cs2/` ownership.** `ship` and `s08_fetch_telonex_books.py` both write `cs2/parquet` on the NAS, and the
   ship key can overwrite. Only the documented rule separates them (section 3.8).
8. **Ship tests skip locally** without `rrsync`. The three-target ship case is proven only where `rrsync` exists.
   `buildShipTargets`, config and alert tests still run locally.
9. **`takesPart` on `undefined` start.** Compaction and import Dota/LoL roots have no start date and keep today's
   behavior. A typo that drops `startDate` from the CS2 onchain root would make CS2 take part on every day; test
   A3 and D1 catch that.
10. **`docker.test.ts` service-block parser** splits on `\n  [a-z0-9-]+:`; `archive-cs2` matches it. Keep
    `archive-cs2` between `archive-lol` and `compact` so `serviceBlock(compose, "archive-lol")` stays short.

## 7. Commits

One commit per repo, on `main`, no push:

1. `../polymarket-collector`: code, tests, `compose.yaml`, contract, runbook, `docs/learnings.md`.
   Example subject: `feat: archive CS2 and start its onchain days on 2026-10-23`.
2. `../esports-trader`: `AGENTS.md` s08/ship rule only. Example: `docs: ship owns new CS2 days on the NAS`.
3. `betting_workspace`: `.learnings/polymarket-collector.md` and the task progress files the implement skill writes.
   `tasks/cs2-live/orchestrate/` is gitignored; do not `git add -f` it.

## 8. Out of scope

STEP-002 rollout, any VPS command, CS2 onchain before 2026-10-23, empty CS2 manifests for earlier dates,
backfill of closed CS2 markets, trader/dashboard code, `poly-maker`.
