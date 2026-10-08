"""Fingerprints of what feeds a smoke golden, taken from the resolved feed plan.

The goldens pin a result. The plan already names the model directory, and for an
archive match the schedule file. `resolve_plan_dataset_path` names the parquet
`resolve_run_signals` reads. Hashing those is the fingerprint: a new map does
not add a field. Book captures under `data/raw/telonex` stay unhashed.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from backtest.feed_schedules import (
    MatchFeedPlan,
    SchedulePlan,
    assert_archive_binding,
    resolve_feed_plans,
)
from backtest.run import load_run_selection
from backtest.signals import BacktestGame, resolve_plan_dataset_path
from shared.constants import strategy as strategy_constants
from shared.utils.gbm import model_identity_sha256
from shared.utils.hashing import sha256_file
from shared.utils.json_io import read_json
from shared.utils.model_registry import MODEL_META_FILENAME, read_model_meta
from trader.strict_json import (
    StrictJsonError,
    require_int,
    require_list,
    require_object,
    require_str,
)

SMOKE_INPUTS_FILENAME = "inputs.json"
_SMOKE_KEYS = frozenset({"feeds", "strategy_constants_sha256"})
_MODEL_KEYS = frozenset({"name", "sha256"})
_GRID_FEED_KEYS = frozenset({"game", "kind", "match_id", "model", "signals_sha256"})
_SCHEDULE_FEED_KEYS = frozenset(
    {"features_sha256", "game", "kind", "match_id", "model", "schedule_sha256"}
)


@dataclass(frozen=True)
class SmokeMap:
    """One golden map and the archive its feed plan must bind. Empty means grid-v1."""

    game: BacktestGame
    match_id: int
    archive_id: str


# Three Dota grid-v1 maps, three Dota Oddin archives (schedule ticks, no-XP
# model — a losing and a winning map on purpose), two LoL grid-v1 maps; the
# second LoL map settles a leftover position.
SMOKE_MAPS = (
    SmokeMap(game="dota", match_id=8837869969, archive_id=""),
    SmokeMap(game="dota", match_id=8911784562, archive_id=""),
    SmokeMap(game="dota", match_id=8933879286, archive_id=""),
    SmokeMap(game="dota", match_id=9007208887, archive_id="9007208887"),
    SmokeMap(game="dota", match_id=9007700576, archive_id="9007700576"),
    SmokeMap(game="dota", match_id=9015175653, archive_id="9015175653"),
    SmokeMap(game="lol", match_id=115564793879469302, archive_id=""),
    SmokeMap(game="lol", match_id=116634566264113530, archive_id=""),
)


@dataclass(frozen=True)
class ModelInputs:
    """One model catalog a replay predicts with."""

    name: str
    sha256: str


@dataclass(frozen=True)
class GridFeedInputs:
    """Fingerprints a grid-v1 plan: its model and the validation parquet."""

    kind: Literal["grid_v1"]
    match_id: int
    game: BacktestGame
    model: ModelInputs
    signals_sha256: str


@dataclass(frozen=True)
class ScheduleFeedInputs:
    """Fingerprints a schedule plan: its model, feature parquet, and schedule file."""

    kind: Literal["schedule"]
    match_id: int
    game: BacktestGame
    model: ModelInputs
    features_sha256: str
    schedule_sha256: str


FeedInputs = GridFeedInputs | ScheduleFeedInputs


@dataclass(frozen=True)
class SmokeInputs:
    """Strategy knobs plus one fingerprint per smoke map, in `SMOKE_MAPS` order."""

    strategy_constants_sha256: str
    feeds: tuple[FeedInputs, ...]


def _require_key_set(fields: Mapping[str, object], keys: frozenset[str], label: str) -> None:
    missing = sorted(keys.difference(fields))
    extra = sorted(set(fields).difference(keys))
    if missing:
        raise StrictJsonError(f"{label}: missing {', '.join(missing)}")
    if extra:
        raise StrictJsonError(f"{label}: unexpected {', '.join(extra)}")


def _decode_game(fields: Mapping[str, object], label: str) -> BacktestGame:
    game = require_str(fields, "game", label)
    if game == "dota":
        return "dota"
    if game == "lol":
        return "lol"
    raise StrictJsonError(f"{label} 'game' must be dota or lol")


def _decode_model(value: object, label: str) -> ModelInputs:
    fields = require_object(value, label)
    _require_key_set(fields, _MODEL_KEYS, label)
    return ModelInputs(
        name=require_str(fields, "name", label),
        sha256=require_str(fields, "sha256", label),
    )


def _decode_grid(fields: Mapping[str, object], label: str) -> GridFeedInputs:
    return GridFeedInputs(
        kind="grid_v1",
        match_id=require_int(fields, "match_id", label),
        game=_decode_game(fields, label),
        model=_decode_model(fields["model"], f"{label}.model"),
        signals_sha256=require_str(fields, "signals_sha256", label),
    )


def _decode_schedule(fields: Mapping[str, object], label: str) -> ScheduleFeedInputs:
    return ScheduleFeedInputs(
        kind="schedule",
        match_id=require_int(fields, "match_id", label),
        game=_decode_game(fields, label),
        model=_decode_model(fields["model"], f"{label}.model"),
        features_sha256=require_str(fields, "features_sha256", label),
        schedule_sha256=require_str(fields, "schedule_sha256", label),
    )


def _decode_feed(value: object, index: int) -> FeedInputs:
    label = f"feeds[{index}]"
    fields = require_object(value, label)
    kind = require_str(fields, "kind", label)
    if kind == "grid_v1":
        _require_key_set(fields, _GRID_FEED_KEYS, label)
        return _decode_grid(fields, label)
    if kind == "schedule":
        _require_key_set(fields, _SCHEDULE_FEED_KEYS, label)
        return _decode_schedule(fields, label)
    raise StrictJsonError(f"{label} 'kind' must be grid_v1 or schedule")


def load_smoke_inputs(path: Path) -> SmokeInputs:
    """Read a captured smoke fingerprint. A missing or mistyped field names itself."""
    payload = require_object(read_json(path), str(path))
    _require_key_set(payload, _SMOKE_KEYS, "smoke inputs")
    feeds = require_list(payload, "feeds", "smoke inputs")
    return SmokeInputs(
        strategy_constants_sha256=require_str(payload, "strategy_constants_sha256", "smoke inputs"),
        feeds=tuple(_decode_feed(item, index) for index, item in enumerate(feeds)),
    )


def _model_inputs(model_dir: Path, cache: dict[Path, ModelInputs]) -> ModelInputs:
    cached = cache.get(model_dir)
    if cached is not None:
        return cached
    meta = read_model_meta(model_dir / MODEL_META_FILENAME)
    loaded = ModelInputs(name=meta["name"], sha256=model_identity_sha256(model_dir))
    cache[model_dir] = loaded
    return loaded


def _file_sha256(path: Path, cache: dict[Path, str]) -> str:
    cached = cache.get(path)
    if cached is not None:
        return cached
    digest = sha256_file(path)
    cache[path] = digest
    return digest


def _feed_inputs(
    smoke: SmokeMap,
    plan: MatchFeedPlan,
    models: dict[Path, ModelInputs],
    files: dict[Path, str],
) -> FeedInputs:
    model = _model_inputs(plan.model_dir, models)
    dataset_sha256 = _file_sha256(resolve_plan_dataset_path(smoke.game, plan), files)
    if isinstance(plan, SchedulePlan):
        return ScheduleFeedInputs(
            kind="schedule",
            match_id=smoke.match_id,
            game=smoke.game,
            model=model,
            features_sha256=dataset_sha256,
            schedule_sha256=_file_sha256(plan.binding.schedule_path, files),
        )
    return GridFeedInputs(
        kind="grid_v1",
        match_id=smoke.match_id,
        game=smoke.game,
        model=model,
        signals_sha256=dataset_sha256,
    )


def _smoke_plans() -> tuple[MatchFeedPlan, ...]:
    """Resolve each smoke map the way a real run does, and pin the archive it names."""
    games: list[BacktestGame] = []
    for smoke in SMOKE_MAPS:
        if smoke.game not in games:
            games.append(smoke.game)
    by_match: dict[int, MatchFeedPlan] = {}
    for game in games:
        maps = tuple(smoke for smoke in SMOKE_MAPS if smoke.game == game)
        selection = load_run_selection(
            game=game,
            match_id=maps[0].match_id,
            since_match=None,
            limit=None,
            allowed_event_ids=None,
        )
        feed = resolve_feed_plans(
            selection.archive_join,
            tuple(smoke.match_id for smoke in maps),
            None,
            None,
            model_override_noxp=None,
        )
        for smoke in maps:
            if smoke.match_id in feed.exclusions:
                raise ValueError(f"match {smoke.match_id}: {feed.exclusions[smoke.match_id]}")
            plan = feed.plans[smoke.match_id]
            assert_archive_binding(plan, archive_id=smoke.archive_id)
            by_match[smoke.match_id] = plan
    return tuple(by_match[smoke.match_id] for smoke in SMOKE_MAPS)


def collect_smoke_inputs() -> SmokeInputs:
    """Fingerprint the strategy file and each smoke map's resolved plan."""
    plans = _smoke_plans()
    models: dict[Path, ModelInputs] = {}
    files: dict[Path, str] = {}
    feeds = tuple(
        _feed_inputs(smoke, plan, models, files)
        for smoke, plan in zip(SMOKE_MAPS, plans, strict=True)
    )
    return SmokeInputs(
        strategy_constants_sha256=sha256_file(Path(strategy_constants.__file__)),
        feeds=feeds,
    )


def _model_drift(current: ModelInputs, recorded: ModelInputs, prefix: str) -> tuple[str, ...]:
    names: list[str] = []
    if current.name != recorded.name:
        names.append(f"{prefix}.model.name")
    if current.sha256 != recorded.sha256:
        names.append(f"{prefix}.model.sha256")
    return tuple(names)


def _grid_drift(current: GridFeedInputs, recorded: GridFeedInputs, prefix: str) -> tuple[str, ...]:
    names = list(_model_drift(current.model, recorded.model, prefix))
    if current.game != recorded.game:
        names.append(f"{prefix}.game")
    if current.signals_sha256 != recorded.signals_sha256:
        names.append(f"{prefix}.signals_sha256")
    return tuple(names)


def _schedule_drift(
    current: ScheduleFeedInputs, recorded: ScheduleFeedInputs, prefix: str
) -> tuple[str, ...]:
    names = list(_model_drift(current.model, recorded.model, prefix))
    if current.features_sha256 != recorded.features_sha256:
        names.append(f"{prefix}.features_sha256")
    if current.game != recorded.game:
        names.append(f"{prefix}.game")
    if current.schedule_sha256 != recorded.schedule_sha256:
        names.append(f"{prefix}.schedule_sha256")
    return tuple(names)


def _feed_drift(current: FeedInputs, recorded: FeedInputs) -> tuple[str, ...]:
    prefix = f"{current.game}.{current.match_id}"
    if isinstance(current, GridFeedInputs) and isinstance(recorded, GridFeedInputs):
        return _grid_drift(current, recorded, prefix)
    if isinstance(current, ScheduleFeedInputs) and isinstance(recorded, ScheduleFeedInputs):
        return _schedule_drift(current, recorded, prefix)
    return (f"{prefix}.kind",)


def inputs_drift(current: SmokeInputs, recorded: SmokeInputs) -> tuple[str, ...]:
    """Dotted names of the fingerprint fields where the two captures differ."""
    names: list[str] = []
    if current.strategy_constants_sha256 != recorded.strategy_constants_sha256:
        names.append("strategy_constants_sha256")
    current_by_id = {feed.match_id: feed for feed in current.feeds}
    recorded_by_id = {feed.match_id: feed for feed in recorded.feeds}
    for match_id in sorted(set(current_by_id) | set(recorded_by_id)):
        left = current_by_id.get(match_id)
        right = recorded_by_id.get(match_id)
        if left is None:
            names.append(f"{recorded_by_id[match_id].game}.{match_id}")
            continue
        if right is None:
            names.append(f"{left.game}.{match_id}")
            continue
        names.extend(_feed_drift(left, right))
    return tuple(sorted(names))
