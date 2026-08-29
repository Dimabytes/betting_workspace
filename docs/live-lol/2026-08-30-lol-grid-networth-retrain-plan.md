# LoL GRID Net Worth Retrain Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild LoL historical net worth as `totalGold - consumed item value`, retrain the research model, run the full validation backtest, and publish the production model only after the user accepts the backtest.

**Architecture:** Stage 05 joins each livestats window frame to the details frame with the same `rfc460Timestamp`. A small shared module tracks cumulative consumed-item gold per participant. Stage 05 keeps raw `totalGold` for feed invariants, then computes all gold and top-player features from corrected per-player net worth. The research comparison script uses the same module.

**Tech Stack:** Python 3.13, standard library, pandas, LightGBM, pytest, Typer, Make, and the existing LoL maker backtest.

**Spec:** `docs/live-lol/2026-08-29-live-lol-implementation-plan.md`

## Global constraints

- Work in `../dota_2_model` on `main`. Read `../dota_2_model/AGENTS.md` before starting.
- Do not edit `../poly-maker`.
- Use `uv run python` or the existing Make targets. Do not activate the virtual environment by hand.
- After every Python change, run `make lint-all`.
- Add no dependency. The reconstruction uses the standard library and a checked-in Data Dragon snapshot.
- Load the item table once in the Stage 05 parent process and pass it to workers.
- Join window and details frames only by exact `rfc460Timestamp`. Do not apply the GRID clock offset to historical frames.
- Keep all existing pause, spawn, age, XP, death, and raw-`totalGold` invariant rules.
- Never fall back to raw `totalGold` when details data is missing, corrupt, or incompatible.
- Train only on seconds `0..540`. Keep the full validation tail for the backtest.
- Launch all ten backtest shards at the same time. Merge only after every shard exits with status 0.
- Do not run `make prepare` or `make train`. Those targets build and train Dota. Use the LoL commands in Tasks 5, 6, and 8.
- Do not publish the production model until the user accepts the research backtest in Task 7.

---

## File map

Create these files in `../dota_2_model`:

- `src/lol/networth.py`: load the pinned item table and reconstruct cumulative consumed gold per participant.
- `src/lol/ddragon_items_16_17_1.json`: compact Data Dragon 16.17.1 item IDs and consumed-item prices.
- `tests/test_lol_networth.py`: focused unit tests for inventory drops and input validation.

Modify these files in `../dota_2_model`:

- `src/lol/livestats_frames.py:124-573`: retain participant IDs, join exact details frames, and build corrected gold features.
- `src/lol/05_prepare_dataset.py:107-941`: accept details and item-table paths, preflight the archives, and pass the table to workers.
- `scripts/reconstruct_lol_networth.py:49-296`: remove the duplicate inventory algorithm and call `lol.networth`.
- `src/lol/06_train_model.py:54-128`: record the corrected state source in `model.json`.
- `tests/test_lol_prepare_dataset.py:50-961`: write matching details fixtures and test Stage 05 integration.
- `tests/test_lol_train_model.py:240-300`: update the model provenance assertion.
- `AGENTS.md`: state that LoL Stage 05 requires details and reconstructs GRID-style net worth.
- `docs/domain.md:32-35`: record the exact formula, join rule, and train-versus-backtest time ranges.

---

### Task 1: Add the reusable consumed-gold reconstruction

**Files:**

- Create: `../dota_2_model/src/lol/networth.py`
- Create: `../dota_2_model/src/lol/ddragon_items_16_17_1.json`
- Create: `../dota_2_model/tests/test_lol_networth.py`

**Interfaces:**

- Consumes: raw details frames with `rfc460Timestamp`, ten `participantId` values, and item ID lists.
- Produces: `load_item_table(path: Path) -> ItemTable` and `build_consumed_timeline(frames: Sequence[Mapping[str, object]], item_table: ItemTable) -> ConsumedTimeline`.

- [ ] **Step 1: Write the failing reconstruction tests**

Add tests that prove five rules:

1. The first inventory is a baseline and consumes nothing.
2. Removing item `2003` adds 50 gold to that participant's cumulative consumed value.
3. Removing non-consumed item `1001` adds nothing.
4. An item ID absent from the pinned table raises `ValueError`.
5. The checked-in table has version `16.17.1`, 868 known IDs, 100 consumed IDs, and a 50-gold price for item `2003`.

Use these fixtures and assertions:

```python
from collections.abc import Mapping

import pytest

from lol.networth import (
    DEFAULT_ITEM_TABLE_PATH,
    ItemTable,
    build_consumed_timeline,
    load_item_table,
)


def details_frame(stamp: str, participant_one_items: list[int]) -> Mapping[str, object]:
    """Build one ten-participant details frame."""
    participants: list[dict[str, object]] = []
    for participant_id in range(1, 11):
        items = participant_one_items if participant_id == 1 else []
        participants.append({"participantId": participant_id, "items": items})
    return {"rfc460Timestamp": stamp, "participants": participants}


def test_consumed_items_accumulate_by_participant() -> None:
    """Only a consumed-item count drop lowers reconstructed net worth."""
    table = ItemTable(
        version="test",
        known_item_ids=frozenset({1001, 2003}),
        consumed_item_gold={2003: 50},
    )
    timeline = build_consumed_timeline(
        [
            details_frame("2026-01-01T00:00:00.000Z", [1001, 2003]),
            details_frame("2026-01-01T00:00:01.000Z", [1001]),
            details_frame("2026-01-01T00:00:02.000Z", []),
        ],
        table,
    )
    assert timeline.by_stamp["2026-01-01T00:00:00.000Z"].by_participant[1] == 0
    assert timeline.by_stamp["2026-01-01T00:00:01.000Z"].by_participant[1] == 50
    assert timeline.by_stamp["2026-01-01T00:00:02.000Z"].by_participant[1] == 50


def test_unknown_item_id_is_fatal() -> None:
    """An unknown item cannot be treated as non-consumed."""
    table = ItemTable("test", frozenset({2003}), {2003: 50})
    frames = [details_frame("2026-01-01T00:00:00.000Z", [999999])]
    with pytest.raises(ValueError, match="unknown item id 999999"):
        build_consumed_timeline(frames, table)


def test_pinned_item_table_identity() -> None:
    """The reconstruction uses the Data Dragon table verified against GRID."""
    table = load_item_table(DEFAULT_ITEM_TABLE_PATH)
    assert table.version == "16.17.1"
    assert len(table.known_item_ids) == 868
    assert len(table.consumed_item_gold) == 100
    assert table.consumed_item_gold[2003] == 50
```

- [ ] **Step 2: Run the tests and verify that the module is missing**

Run:

```bash
cd ../dota_2_model
PYTHONPATH=src uv run python -m pytest tests/test_lol_networth.py -q
```

Expected: collection fails with `ModuleNotFoundError: No module named 'lol.networth'`.

- [ ] **Step 3: Generate the compact pinned item table**

Generate the file from the exact Data Dragon response used by the 2026-08-29 CBLOL reconstruction:

```bash
cd ../dota_2_model
jq '{version, known_item_ids: (.data | keys | map(tonumber) | sort), consumed_item_gold: (.data | to_entries | map(select(.value.consumed == true) | {key: .key, value: .value.gold.total}) | from_entries)}' \
  ../betting_workspace/docs/live-lol/recordings/lol-fxw7-los-2026-08-29-20260829T180601Z/ddragon_items.json \
  > src/lol/ddragon_items_16_17_1.json
jq '{version, known_count: (.known_item_ids | length), consumed_count: (.consumed_item_gold | length)}' \
  src/lol/ddragon_items_16_17_1.json
```

Expected:

```json
{
  "version": "16.17.1",
  "known_count": 868,
  "consumed_count": 100
}
```

- [ ] **Step 4: Implement the pure reconstruction module**

Define these frozen values in `src/lol/networth.py`:

```python
@dataclass(frozen=True)
class ItemTable:
    """Pinned item IDs and consumed-item total-gold prices."""

    version: str
    known_item_ids: frozenset[int]
    consumed_item_gold: Mapping[int, int]


@dataclass(frozen=True)
class ConsumedFrame:
    """Cumulative consumed gold by participant at one details timestamp."""

    by_participant: Mapping[int, int]


@dataclass(frozen=True)
class ConsumedTimeline:
    """Exact details timestamp to cumulative consumed gold."""

    by_stamp: Mapping[str, ConsumedFrame]


DEFAULT_ITEM_TABLE_PATH = Path(__file__).with_name("ddragon_items_16_17_1.json")
```

`load_item_table` must reject a missing version, duplicate IDs, boolean prices, negative prices, and consumed IDs absent from `known_item_ids`.

Read the compact JSON with this shape and convert every object key to `int`:

```python
def load_item_table(path: Path) -> ItemTable:
    """Load and validate the pinned compact Data Dragon item table."""
    payload: object = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"item table is not an object: {path}")
    body = cast(dict[str, object], payload)
    version = body.get("version")
    raw_known = body.get("known_item_ids")
    raw_consumed = body.get("consumed_item_gold")
    if not isinstance(version, str) or not version:
        raise ValueError(f"item table has no version: {path}")
    if not isinstance(raw_known, list) or not isinstance(raw_consumed, dict):
        raise ValueError(f"item table has invalid collections: {path}")

    known_values: list[int] = []
    for value in raw_known:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"item table has invalid item id: {value!r}")
        known_values.append(value)
    known_item_ids = frozenset(known_values)
    if len(known_item_ids) != len(known_values):
        raise ValueError("item table has duplicate item ids")

    consumed_item_gold: dict[int, int] = {}
    for raw_item_id, raw_price in cast(dict[str, object], raw_consumed).items():
        item_id = int(raw_item_id)
        if isinstance(raw_price, bool) or not isinstance(raw_price, int) or raw_price < 0:
            raise ValueError(f"item table has invalid price for {item_id}")
        if item_id not in known_item_ids:
            raise ValueError(f"consumed item {item_id} is not a known item")
        consumed_item_gold[item_id] = raw_price
    return ItemTable(version, known_item_ids, consumed_item_gold)
```

Implement the inventory fold with one `Counter[int]` per participant. Require participant IDs `1..10` exactly once in every frame. Reject duplicate or empty timestamps. For each item count drop, add its price only when the item ID exists in `consumed_item_gold`:

```python
def build_consumed_timeline(
    frames: Sequence[Mapping[str, object]], item_table: ItemTable
) -> ConsumedTimeline:
    """Track cumulative consumed-item gold for ten participants."""
    participant_ids = frozenset(range(1, 11))
    previous: dict[int, Counter[int]] = {}
    cumulative = {participant_id: 0 for participant_id in participant_ids}
    by_stamp: dict[str, ConsumedFrame] = {}

    for frame in frames:
        stamp = frame.get("rfc460Timestamp")
        if not isinstance(stamp, str) or not stamp or stamp in by_stamp:
            raise ValueError(f"invalid or duplicate details timestamp: {stamp!r}")
        raw_participants = frame.get("participants")
        if not isinstance(raw_participants, list):
            raise ValueError(f"details frame {stamp} has no participants")

        seen: set[int] = set()
        for raw_participant in raw_participants:
            if not isinstance(raw_participant, dict):
                raise ValueError(f"details frame {stamp} has a non-object participant")
            participant = cast(dict[str, object], raw_participant)
            participant_id = participant.get("participantId")
            if isinstance(participant_id, bool) or not isinstance(participant_id, int):
                raise ValueError(f"details frame {stamp} has an invalid participantId")
            if participant_id not in participant_ids or participant_id in seen:
                raise ValueError(f"details frame {stamp} has participantId {participant_id}")
            seen.add(participant_id)

            raw_items = participant.get("items")
            if not isinstance(raw_items, list):
                raise ValueError(f"details frame {stamp} participant {participant_id} has no items")
            counts: Counter[int] = Counter()
            for raw_item_id in raw_items:
                if isinstance(raw_item_id, bool) or not isinstance(raw_item_id, int):
                    raise ValueError(f"details frame {stamp} has an invalid item id")
                if raw_item_id not in item_table.known_item_ids:
                    raise ValueError(f"unknown item id {raw_item_id} at {stamp}")
                counts[raw_item_id] += 1

            before = previous.get(participant_id)
            if before is not None:
                for item_id, old_count in before.items():
                    drop = old_count - counts.get(item_id, 0)
                    if drop > 0 and item_id in item_table.consumed_item_gold:
                        cumulative[participant_id] += drop * item_table.consumed_item_gold[item_id]
            previous[participant_id] = counts

        if seen != participant_ids:
            raise ValueError(f"details frame {stamp} does not contain participants 1..10")
        by_stamp[stamp] = ConsumedFrame(dict(cumulative))

    return ConsumedTimeline(by_stamp)
```

- [ ] **Step 5: Run the focused tests**

Run:

```bash
PYTHONPATH=src uv run python -m pytest tests/test_lol_networth.py -q
```

Expected: both tests pass.

- [ ] **Step 6: Commit the reusable reconstruction**

```bash
git add src/lol/networth.py src/lol/ddragon_items_16_17_1.json tests/test_lol_networth.py
git commit -m "Reconstruct LoL consumed gold from pinned item data."
```

Commit driver: Stage 05 and the GRID comparison must use one inventory algorithm and one pinned price table.

---

### Task 2: Build Stage 05 features from corrected per-player net worth

**Files:**

- Modify: `../dota_2_model/src/lol/livestats_frames.py:124-573`
- Modify: `../dota_2_model/tests/test_lol_prepare_dataset.py:104-590`

**Interfaces:**

- Consumes: `ItemTable`, `ConsumedFrame`, `ConsumedTimeline`, and exact details archives from Task 1.
- Produces: `prepare_map_livestats_until(link, windows_dir, details_dir, item_table, end_second)` with corrected `FrameFeatures`.

- [ ] **Step 1: Extend the synthetic archives with matching details frames**

In `tests/test_lol_prepare_dataset.py`, make `write_archive` also write a sibling `details/<game_id>.jsonl.gz`. The default details frame must contain participant IDs `1..10` with empty `items` lists and the same timestamp as each window frame.

Update `map_livestats` to pass:

```python
item_table = ItemTable("test", frozenset({1001, 2003}), {2003: 50})
return prepare_map_livestats_until(
    link,
    windows,
    windows.parent / "details",
    item_table,
    LOL_GRID_END_SECOND,
)
```

- [ ] **Step 2: Write the failing feature test**

Add one frame at spawn with participant 1 holding a potion. Add a second frame one second later with the potion removed. Keep the player's raw `totalGold` at 500 in both frames. Assert that raw non-decreasing-gold validation still passes and the corrected features decrease by 50:

```python
assert feat.radiant_nw == 2450
assert feat.dire_nw == 2500
assert feat.radiant_nw_adv == -50
assert feat.top1_nw_adv == 0
assert feat.radiant_top1_nw_ratio == pytest.approx(500 / 2450)
assert feat.dire_top1_nw_ratio == pytest.approx(500 / 2500)
```

Use participant 1 as the consumed player. Another Blue player remains at 500 and therefore becomes Blue top 1.

- [ ] **Step 3: Run the focused test and verify the old totalGold result**

Run:

```bash
PYTHONPATH=src uv run python -m pytest \
  tests/test_lol_prepare_dataset.py::test_consumed_item_corrects_team_and_top1_features -q
```

Expected before implementation: `radiant_nw` is 2500, not 2450.

- [ ] **Step 4: Retain numeric participant IDs in parsed window frames**

Replace `ParsedPlayer.key` with `participant_id: int`. Require unique integer participant IDs `1..5` for Blue and `6..10` for Red in `parse_team`. Index `players_by_key` by `participant_id`.

Keep `stats_non_decreasing` on raw player `totalGold`. Do not run this invariant on reconstructed net worth because consuming an item correctly lowers net worth.

- [ ] **Step 5: Read and require the exact details timeline**

Change the public signature to:

```python
def prepare_map_livestats_until(
    link: LolLinkRow,
    windows_dir: Path,
    details_dir: Path,
    item_table: ItemTable,
    end_second: int,
) -> LivestatsOk | LivestatsDrop:
```

After a readable, non-empty window archive exists, read the matching details archive with the existing `FETCH.archive_path`, `FETCH.read_gzip_jsonl`, and `dedup_sort_frames` helpers. Build `ConsumedTimeline` once per map.

Require equal timestamp sets:

```python
window_stamps = {frame.stamp for frame in frames}
details_stamps = set(consumed.by_stamp)
if window_stamps != details_stamps:
    missing = len(window_stamps - details_stamps)
    extra = len(details_stamps - window_stamps)
    raise RuntimeError(
        f"match {link['esports_game_id']}: window/details timestamp mismatch "
        f"missing={missing} extra={extra}"
    )
```

Do not use nearest-frame matching, forward fill, interpolation, or the GRID-versus-livestats offset.

- [ ] **Step 6: Correct every player before feature aggregation**

Pass the `ConsumedFrame` for the window timestamp into `features_from_sides`. Compute each player's net worth as raw player gold minus cumulative consumed gold. Reject a negative result.

Build all six gold-derived fields from corrected per-player values:

```python
blue_net_worth = [
    player.gold - consumed.by_participant[player.participant_id]
    for player in sides.blue.players
]
red_net_worth = [
    player.gold - consumed.by_participant[player.participant_id]
    for player in sides.red.players
]
radiant_nw = sum(blue_net_worth)
dire_nw = sum(red_net_worth)
```

Use these lists for `radiant_nw`, `dire_nw`, `radiant_nw_adv`, `top1_nw_adv`, `radiant_top1_nw_ratio`, and `dire_top1_nw_ratio`. Leave XP and deaths unchanged.

- [ ] **Step 7: Run the Stage 05 frame tests**

Run:

```bash
PYTHONPATH=src uv run python -m pytest tests/test_lol_networth.py tests/test_lol_prepare_dataset.py -q
```

Expected: all tests pass, including the seven existing livestats invariants.

- [ ] **Step 8: Commit corrected feature construction**

```bash
git add src/lol/livestats_frames.py tests/test_lol_prepare_dataset.py
git commit -m "Build LoL gold features from reconstructed net worth."
```

Commit driver: correcting only team totals would leave top-player features trained on the wrong quantity.

---

### Task 3: Make details mandatory in the Stage 05 process pool

**Files:**

- Modify: `../dota_2_model/src/lol/05_prepare_dataset.py:107-941`
- Modify: `../dota_2_model/tests/test_lol_prepare_dataset.py:50-961`

**Interfaces:**

- Consumes: the new `prepare_map_livestats_until` signature from Task 2.
- Produces: Stage 05 CLI options `--details-dir` and `--item-table-path`; every worker receives one loaded `ItemTable`.

- [ ] **Step 1: Write the failing no-fallback test**

Create one valid window archive and then remove its details archive. Run `prepare_dataset` against an empty output directory.

Assert both conditions:

```python
with pytest.raises(RuntimeError, match="missing details archive.*1001"):
    load_prepare().prepare_dataset(
        links_path,
        windows_dir,
        details_dir,
        item_table_path,
        catalog_path,
        telonex_root,
        output_dir,
        LOL_PREPARE_WORKERS,
    )
assert not any(path.exists() for path in output_files(output_dir))
```

- [ ] **Step 2: Run the test and verify the signature mismatch**

Run:

```bash
PYTHONPATH=src uv run python -m pytest \
  tests/test_lol_prepare_dataset.py::test_missing_details_aborts_before_publish -q
```

Expected: the old `prepare_dataset` signature rejects the new arguments.

- [ ] **Step 3: Add the details and item-table inputs**

Use these required internal signatures:

```python
def build_one_map(
    link: LolLinkRow,
    windows_dir: Path,
    details_dir: Path,
    item_table: ItemTable,
    telonex_root: Path,
    horizons: Sequence[int] = (LOL_TARGET_HORIZON_SECONDS,),
) -> MapBuild:


def build_maps_parallel(
    links: Sequence[LolLinkRow],
    windows_dir: Path,
    details_dir: Path,
    item_table: ItemTable,
    telonex_root: Path,
    workers: int,
    horizons: Sequence[int],
) -> list[MapBuild]:


def prepare_dataset(
    links_path: Path,
    windows_dir: Path,
    details_dir: Path,
    item_table_path: Path,
    catalog_path: Path,
    telonex_root: Path,
    output_dir: Path,
    workers: int,
    label_horizons: Sequence[int] = (LOL_TARGET_HORIZON_SECONDS,),
) -> None:
```

Load `ItemTable` once in `prepare_dataset`. Pass the frozen value through `build_maps_parallel` to `build_one_map`.

- [ ] **Step 4: Fail before worker startup when an archive path is missing**

Add:

```python
def require_details_archives(links: Sequence[LolLinkRow], details_dir: Path) -> None:
    """Abort before map work when any accepted link lacks a details archive."""
    for link in links:
        game_id = str(link["esports_game_id"])
        if not FETCH.archive_path(details_dir, game_id).is_file():
            raise RuntimeError(f"missing details archive for {game_id}")
```

Extend the local `FetchStage` protocol with the existing `archive_path(details_dir, game_id) -> Path` interface. Call `require_details_archives` after loading and validating links but before `build_maps_parallel`. Corrupt JSON, unknown items, or timestamp mismatches can still fail inside a worker. `publish_datasets` runs only after every worker returns, so the previous parquet set remains intact.

- [ ] **Step 5: Add CLI defaults**

Add the Typer options:

```python
details_dir: Annotated[Path, typer.Option("--details-dir")] = LOL_DETAILS_DIR
item_table_path: Annotated[Path, typer.Option("--item-table-path")] = DEFAULT_ITEM_TABLE_PATH
```

Pass both values to `prepare_dataset`.

- [ ] **Step 6: Update every Stage 05 test caller**

Update the typed `PrepareModule` protocol, `run_prepare`, the preflight tests, the invariant-cap test, and the multi-horizon test. Pass the sibling details directory and `DEFAULT_ITEM_TABLE_PATH` or the synthetic test-table path explicitly.

- [ ] **Step 7: Run the Stage 05 test files**

Run:

```bash
PYTHONPATH=src:scripts:../prediction-market-backtesting uv run --group backtest \
  python -m pytest tests/test_lol_networth.py tests/test_lol_prepare_dataset.py \
  tests/test_lol_prepare_backtest.py -q
```

Expected: all tests pass.

- [ ] **Step 8: Commit mandatory details input**

```bash
git add src/lol/05_prepare_dataset.py tests/test_lol_prepare_dataset.py
git commit -m "Require LoL details before Stage 05 publishes datasets."
```

Commit driver: a missing shard must stop the rebuild instead of mixing corrected and raw gold.

---

### Task 4: Reuse the formula and record model provenance

**Files:**

- Modify: `../dota_2_model/scripts/reconstruct_lol_networth.py:49-296`
- Modify: `../dota_2_model/src/lol/06_train_model.py:54-128`
- Modify: `../dota_2_model/tests/test_lol_train_model.py:240-300`
- Modify: `../dota_2_model/AGENTS.md`
- Modify: `../dota_2_model/docs/domain.md:32-35`

**Interfaces:**

- Consumes: `ItemTable`, `ConsumedTimeline`, and `build_consumed_timeline` from Task 1.
- Produces: unchanged comparison output and `model.json` with `state_source="lolesports_window_details_grid_networth"`.

- [ ] **Step 1: Remove the duplicate inventory fold from the comparison script**

Delete the script-local `PriceTable` and the body of `track_consumed`. Keep HTTP caching and report formatting.

Make `fetch_price_table` return the shared `ItemTable`. Convert the raw Data Dragon response into:

```python
ItemTable(
    version=str(body["version"]),
    known_item_ids=frozenset(int(item_id) for item_id in data),
    consumed_item_gold={
        int(item_id): int(cast(dict[str, object], item["gold"])["total"])
        for item_id, item in data.items()
        if item.get("consumed") is True
    },
)
```

Read recorded details lines into mappings and call `build_consumed_timeline`. In `build_pairs`, sum participant IDs `1..5` for Blue and `6..10` for Red. Keep exact stamp lookup.

- [ ] **Step 2: Re-run the recorded CBLOL comparison**

Run:

```bash
make run F=scripts/reconstruct_lol_networth.py \
  ARGS="../betting_workspace/docs/live-lol/recordings/lol-fxw7-los-2026-08-29-20260829T180601Z --offset 7"
```

Expected stable evidence:

```text
final consumed gold: blue 850, red 1275
paired 1235 window frames at offset +7s (0 without details)
rec blue: median|x-grid|=44
rec red : median|x-grid|=69
raw blue: median|x-grid|=545
raw red : median|x-grid|=715
```

The exact offset is only for this comparison. Stage 05 still joins window and details frames at the same timestamp.

- [ ] **Step 3: Change model provenance**

Change:

```python
LOL_STATE_SOURCE = "lolesports_window_details_grid_networth"
```

Update both `state_source` assertions in `tests/test_lol_train_model.py`. Do not change the 12-feature list, `xp_source`, horizon, lag, or train window.

- [ ] **Step 4: Update the product documentation**

In `AGENTS.md`, state that Stage 05 requires `data/lol/raw/lolesports/details/` and computes each player's net worth as `totalGold - cumulative consumed-item value`.

In `docs/domain.md`, record these invariants:

- Window and details frames join by exact `rfc460Timestamp`.
- Raw window `totalGold` remains the monotonic feed invariant.
- Gold and top-1 model features use corrected per-player net worth.
- Training and production parquets keep seconds `0..540`.
- Validation signals keep the full map tail for the backtest.

- [ ] **Step 5: Run focused tests and full lint**

Run:

```bash
PYTHONPATH=src:scripts:../prediction-market-backtesting uv run --group backtest \
  python -m pytest tests/test_lol_networth.py tests/test_lol_prepare_dataset.py \
  tests/test_lol_prepare_backtest.py tests/test_lol_train_model.py -q
make lint-all
```

Expected: tests and every pre-commit hook pass.

- [ ] **Step 6: Commit the shared formula and provenance**

```bash
git add scripts/reconstruct_lol_networth.py src/lol/06_train_model.py \
  tests/test_lol_train_model.py AGENTS.md docs/domain.md
git commit -m "Record reconstructed GRID net worth in the LoL model contract."
```

Commit driver: the research probe, dataset builder, and model metadata must describe the same gold quantity.

---

### Task 5: Rebuild the LoL datasets after all details shards finish

**Files:**

- Read: `../dota_2_model/data/lol/processed/lolesports_links/links.parquet`
- Read: `../dota_2_model/data/lol/raw/lolesports/windows/`
- Read: `../dota_2_model/data/lol/raw/lolesports/details/`
- Replace atomically: `../dota_2_model/data/lol/processed/datasets/*.parquet`

**Interfaces:**

- Consumes: all ten completed Stage 04 shards and the code from Tasks 1-4.
- Produces: corrected training, validation, production-training, split, audit, market-second, and backtest-audit parquets.

- [ ] **Step 1: Confirm the implementation branch is clean**

Run:

```bash
cd ../dota_2_model
git status --short
git log -4 --oneline
```

Expected: status is clean and the four commits from Tasks 1-4 are at `HEAD`.

- [ ] **Step 2: Confirm the details download has stopped**

Do not stop or restart the existing downloader. Start Stage 05 only after all ten Stage 04 shard commands have exited with status 0.

The Stage 05 path preflight checks every ID in `links.parquet`. If one details archive is still missing, Stage 05 exits before starting the process pool and keeps the previous datasets.

- [ ] **Step 3: Run corrected LoL Stage 05**

Run:

```bash
make run F=src/lol/05_prepare_dataset.py
```

Expected:

- The command reaches `prepared: 4992/4992` unless the accepted-link count changed in a later Stage 03 run.
- No error contains `missing details archive`, `unknown item id`, or `window/details timestamp mismatch`.
- The command publishes all seven parquet files only after every worker returns.

- [ ] **Step 4: Check the published dataset contract**

Run:

```bash
uv run python -c 'from pathlib import Path; import numpy as np; import pandas as pd; root=Path("data/lol/processed/datasets"); names=("training.parquet","validation.parquet","production_training.parquet"); frames={name:pd.read_parquet(root/name) for name in names}; assert not frames["training.parquet"].empty; assert not frames["validation.parquet"].empty; assert not frames["production_training.parquet"].empty; assert int(frames["training.parquet"]["second"].max()) <= 540; assert int(frames["production_training.parquet"]["second"].max()) <= 540; assert int(frames["validation.parquet"]["second"].max()) > 540; cols=("radiant_nw","dire_nw","radiant_nw_adv","top1_nw_adv","radiant_top1_nw_ratio","dire_top1_nw_ratio"); assert all(np.isfinite(frame[list(cols)].to_numpy()).all() for frame in frames.values()); assert all((frame["radiant_nw"] >= 0).all() and (frame["dire_nw"] >= 0).all() for frame in frames.values()); assert all(frame["radiant_top1_nw_ratio"].between(0,1).all() and frame["dire_top1_nw_ratio"].between(0,1).all() for frame in frames.values()); print({name:{"maps":int(frame["match_id"].nunique()),"rows":len(frame),"max_second":int(frame["second"].max())} for name,frame in frames.items()})'
shasum -a 256 data/lol/processed/datasets/training.parquet \
  data/lol/processed/datasets/validation.parquet \
  data/lol/processed/datasets/production_training.parquet
```

Expected: every assertion passes. Save the printed counts and hashes with the run notes. Do not require the counts to equal the old 20-minute fetch because validation now keeps the full map tail.

---

### Task 6: Train the corrected research model

**Files:**

- Read: `../dota_2_model/data/lol/processed/datasets/training.parquet`
- Read: `../dota_2_model/data/lol/processed/datasets/validation.parquet`
- Read: `../dota_2_model/data/lol/processed/datasets/split.parquet`
- Replace and archive through the trainer: `../dota_2_model/data/lol/models/research/`

**Interfaces:**

- Consumes: corrected Stage 05 parquets.
- Produces: a research model with base features, horizon 300, seconds `0..540`, row stride 1, and corrected state-source metadata.

- [ ] **Step 1: Train the unchanged research configuration**

Run:

```bash
make run F=src/lol/06_train_model.py
```

Do not pass sweep flags. The retained configuration is base features, target horizon 300, train second max 540, and row stride 1.

- [ ] **Step 2: Verify model metadata and dataset hashes**

Run:

```bash
LOL_GRID_TRAIN_SHA=$(shasum -a 256 data/lol/processed/datasets/training.parquet | awk '{print $1}')
LOL_GRID_VALID_SHA=$(shasum -a 256 data/lol/processed/datasets/validation.parquet | awk '{print $1}')
jq -e --arg train "$LOL_GRID_TRAIN_SHA" --arg valid "$LOL_GRID_VALID_SHA" '
  .state_source == "lolesports_window_details_grid_networth" and
  .train_dataset_sha256 == $train and
  .validation_dataset_sha256 == $valid and
  .feature_set == "base" and
  .target_horizon_seconds == 300 and
  .train_second_max == 540 and
  .row_stride == 1 and
  .metrics.trees > 0
' data/lol/models/research/model.json
jq '{name,state_source,train_matches,validation_matches,target_horizon_seconds,train_second_max,row_stride,metrics}' \
  data/lol/models/research/model.json
```

Expected: `jq -e` exits with status 0. Record the new model name. Do not train production yet.

---

### Task 7: Run and review the ten-shard research backtest

**Files:**

- Read: `../dota_2_model/data/lol/models/research/`
- Create: one named run under `../dota_2_model/data/backtests/lol_maker/`

**Interfaces:**

- Consumes: the corrected research model and the corrected validation signals.
- Produces: one merged S2 run with the established anchor maker settings.

- [ ] **Step 1: Launch all ten shards together**

Run this block in one `zsh` session:

```bash
cd ../dota_2_model
LOL_GRID_MODEL_NAME=$(jq -r .name data/lol/models/research/model.json)
LOL_GRID_RUN_NAME="grid-nw-${LOL_GRID_MODEL_NAME}"
LOL_GRID_BACKTEST_PIDS=()

for LOL_GRID_SHARD in {0..9}; do
  make lol-backtest ARGS="--validation --profile s2-join --model-dir data/lol/models/research --buy-cutoff-second 540 --min-abs-delta 0.01 --unwind-after-seconds 300 --min-entry-price 0.35 --max-abs-nw-delta-30 999999 --shard ${LOL_GRID_SHARD}/10 --name ${LOL_GRID_RUN_NAME}" &
  LOL_GRID_BACKTEST_PIDS+=($!)
done

LOL_GRID_BACKTEST_FAILED=0
for LOL_GRID_PID in $LOL_GRID_BACKTEST_PIDS; do
  wait $LOL_GRID_PID || LOL_GRID_BACKTEST_FAILED=1
done
test "$LOL_GRID_BACKTEST_FAILED" -eq 0
```

Expected: the final `test` exits with status 0. Do not merge if it fails.

- [ ] **Step 2: Merge the ten shards with identical settings**

Run:

```bash
LOL_GRID_MODEL_NAME=$(jq -r .name data/lol/models/research/model.json)
LOL_GRID_RUN_NAME="grid-nw-${LOL_GRID_MODEL_NAME}"
make lol-backtest ARGS="--validation --profile s2-join --model-dir data/lol/models/research --buy-cutoff-second 540 --min-abs-delta 0.01 --unwind-after-seconds 300 --min-entry-price 0.35 --max-abs-nw-delta-30 999999 --merge-shards 10 --name ${LOL_GRID_RUN_NAME}"
```

Expected: merge exits with status 0 and writes `results.parquet`, `fills.parquet`, `quote_events.parquet`, `manifest.json`, and `summary.json`.

- [ ] **Step 3: Verify the merged manifest**

Run:

```bash
LOL_GRID_MODEL_NAME=$(jq -r .name data/lol/models/research/model.json)
LOL_GRID_RUN_NAME="grid-nw-${LOL_GRID_MODEL_NAME}"
LOL_GRID_RUN_DIR="data/backtests/lol_maker/validation_s2_delta01_cut540_nw999999_p35_${LOL_GRID_RUN_NAME}"
LOL_GRID_SIGNALS_SHA=$(shasum -a 256 data/lol/processed/datasets/validation.parquet | awk '{print $1}')
jq -e --arg model "$LOL_GRID_MODEL_NAME" --arg signals "$LOL_GRID_SIGNALS_SHA" '
  .game == "lol" and
  .model_name == $model and
  .signals_sha256 == $signals and
  .profiles == ["s2-join"] and
  .buy_cutoff_second == 540 and
  .min_abs_delta == 0.01 and
  .unwind_after_seconds == 300 and
  .min_entry_price == 0.35 and
  .max_abs_nw_delta_30 == 999999
' "${LOL_GRID_RUN_DIR}/manifest.json"
jq '{selected,coverage,arms,wall_seconds}' "${LOL_GRID_RUN_DIR}/summary.json"
```

Expected: `jq -e` exits with status 0.

- [ ] **Step 4: Compare against the old raw-totalGold anchor on shared maps**

Run:

```bash
LOL_GRID_MODEL_NAME=$(jq -r .name data/lol/models/research/model.json)
LOL_GRID_RUN_NAME="grid-nw-${LOL_GRID_MODEL_NAME}"
LOL_GRID_RUN_DIR="data/backtests/lol_maker/validation_s2_delta01_cut540_nw999999_p35_${LOL_GRID_RUN_NAME}"
make run F=scripts/compare_backtests.py \
  ARGS="data/backtests/lol_maker/validation_s2_delta01_cut540_nw999999_p35_20260828T201023Z ${LOL_GRID_RUN_DIR} --fair-source s2"
```

Treat this comparison as a diagnostic. The candidate does not need to beat the old run because the old run trained and replayed the wrong gold. Record total PnL, shared-map PnL, traded maps, buy markout at 300 seconds, terminated rows, and the paired difference.

- [ ] **Step 5: Stop for the user decision**

Present the research `model.json`, `summary.json`, and paired comparison. Ask the user to accept or reject production publication.

Do not continue to Task 8 without an explicit acceptance.

---

### Task 8: Publish the corrected production model

**Files:**

- Read: `../dota_2_model/data/lol/processed/datasets/production_training.parquet`
- Read: `../dota_2_model/data/lol/models/research/model.json`
- Replace and archive through the trainer: `../dota_2_model/data/lol/models/production/`

**Interfaces:**

- Consumes: the accepted research tree count and corrected production-training parquet.
- Produces: the production model required before Stage 0 of the live LoL implementation plan.

- [ ] **Step 1: Train production only after acceptance**

Run:

```bash
make run F=src/lol/06_train_model.py ARGS="--production"
```

- [ ] **Step 2: Verify the production contract**

Run:

```bash
LOL_GRID_PRODUCTION_SHA=$(shasum -a 256 data/lol/processed/datasets/production_training.parquet | awk '{print $1}')
jq -e --arg train "$LOL_GRID_PRODUCTION_SHA" '
  .state_source == "lolesports_window_details_grid_networth" and
  .train_dataset_sha256 == $train and
  .validation_dataset_sha256 == null and
  .feature_set == "base" and
  .target_horizon_seconds == 300 and
  .train_second_max == 540 and
  .row_stride == 1 and
  .train_matches > 0
' data/lol/models/production/model.json
jq '{name,state_source,train_matches,features,target_horizon_seconds,train_second_max,row_stride}' \
  data/lol/models/production/model.json
```

Expected: `jq -e` exits with status 0. `data/lol/models/production/model.txt` and `model.json` now satisfy the precondition for the live LoL plan.

- [ ] **Step 3: Hand off to the live LoL plan**

Return to `docs/live-lol/2026-08-29-live-lol-implementation-plan.md`. Start Stage 0 only after Task 8 passes. Do not rerun Stage 05, research training, or the backtest as part of Stages 0-7.

---

## Completion gates

The plan is complete only when every statement below is true:

- Stage 05 has no path that substitutes raw `totalGold` for missing details.
- Window and details timestamps match exactly for every processed map.
- Every observed item ID exists in the pinned Data Dragon 16.17.1 table.
- Team and top-1 gold features use corrected per-player net worth.
- The recorded CBLOL reconstruction still reduces median team error from 545/715 gold to 44/69 gold.
- Training and production datasets stop at second 540.
- Validation keeps frames after second 540 for the full-map backtest.
- The research model records the corrected state source and the new dataset hashes.
- All ten backtest shards and the merge exit with status 0.
- The merged manifest points to the corrected model and validation parquet.
- The user accepts the research backtest before production training starts.
- The production model records the corrected state source and production-training hash.
