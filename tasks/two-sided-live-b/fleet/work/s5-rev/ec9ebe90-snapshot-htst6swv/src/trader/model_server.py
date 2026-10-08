"""Load the canonical LightGBM model and serve clipped Radiant fair prices."""

import math
import os
import threading
from collections.abc import Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from types import TracebackType
from typing import NoReturn, cast

import lightgbm as lgb
import numpy as np
import numpy.typing as npt

from shared.types.model import ModelMeta
from shared.utils.board_features import BoardFeatures, board_model_values
from shared.utils.dota_features import (
    DOTA_HISTORY_FEATURE_NAMES,
    SnapshotHistory,
    market_derived_values,
    snapshot_history_levels,
)
from shared.utils.gbm import (
    MODEL_META_FILENAME,
    GbmPredictor,
    ModelCatalogError,
    load_predictor_from_meta,
)
from shared.utils.log import get_logger
from shared.utils.model_registry import read_model_meta
from shared.utils.top_players import top_player_feature_values
from trader.bindings import ModelReference
from trader.live_feed import GameSnapshot
from trader.notify import notify_in_background
from trader.strict_json import (
    StrictJsonError,
    require_int,
    require_nonempty_str,
    require_str_list,
)

logger = get_logger(__name__)

STARTUP_BLOCKED_PREFIX = "trader model startup blocked: "
META_LABEL = "model.json"

# fd 2 is process-global: overlapping suppression windows from other threads
# must be serialized, or one thread can save another thread's devnull fd,
# restore the real stderr mid-window, and finally leave fd 2 redirected.
_STDERR_SILENCE_LOCK = threading.RLock()
# Set once the fd-2 restore is known to have failed: every later suppression
# attempt fails closed instead of running with a poisoned global stderr.
_stderr_restore_broken = False


class _SilencedStderr:
    """Redirect process stderr to devnull for one `with` block.

    Hold the process lock across the whole window. A failed restore raises and
    fails later attempts closed; recovery is a process restart.
    """

    def __enter__(self) -> None:
        _STDERR_SILENCE_LOCK.acquire()
        saved_fd: int | None = None
        devnull_fd: int | None = None
        try:
            if _stderr_restore_broken:
                raise OSError(
                    "process stderr suppression is broken after a failed restore; restart required"
                )
            saved_fd = os.dup(2)
            devnull_fd = os.open(os.devnull, os.O_WRONLY)
            os.dup2(devnull_fd, 2)
        except BaseException:
            try:
                if saved_fd is not None:
                    _close_fd(saved_fd)
                if devnull_fd is not None:
                    _close_fd(devnull_fd)
            finally:
                _STDERR_SILENCE_LOCK.release()
            raise
        assert saved_fd is not None
        assert devnull_fd is not None
        self._saved_fd = saved_fd
        self._devnull_fd = devnull_fd

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        restore_error: BaseException | None = None
        try:
            os.dup2(self._saved_fd, 2)
        except BaseException as exc:
            global _stderr_restore_broken
            restore_error = exc
            _stderr_restore_broken = True
        if restore_error is not None:
            _report_stderr_restore_failure(self._saved_fd, restore_error)
        try:
            _close_fd(self._saved_fd)
            _close_fd(self._devnull_fd)
        finally:
            _STDERR_SILENCE_LOCK.release()
        if restore_error is not None:
            if exc_value is not None:
                raise restore_error from exc_value
            raise restore_error


def _close_fd(fd: int) -> None:
    """Close one descriptor best-effort; a close failure is ignored."""
    with suppress(OSError):
        os.close(fd)


def _report_stderr_restore_failure(saved_fd: int, error: BaseException) -> None:
    """Write one fixed CRITICAL line to the still-open original stderr channel."""
    message = f"CRITICAL: trader failed to restore process stderr ({type(error).__name__})\n"
    with suppress(OSError):
        os.write(saved_fd, message.encode())


class ModelLoadError(RuntimeError):
    """Model artifacts are missing, unreadable or unparseable.

    `reason` is the fixed safe label used for the startup log and alert.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class ModelContractError(ModelLoadError):
    """A readable model pair cannot satisfy the live feature/timing contract."""


class ModelPredictionError(ValueError):
    """An invalid numeric inference input or booster output."""


@dataclass(frozen=True)
class _ValidatedMeta:
    """The validated live metadata: model identity plus the verified feature list."""

    reference: ModelReference
    features: list[str]


@dataclass(frozen=True)
class ModelPrediction:
    """Raw model delta plus the clipped Radiant fair."""

    raw_delta: float
    fair: float


@dataclass(frozen=True)
class ModelServer:
    """One pinned model: identity plus the loaded catalog; inference never reloads."""

    model_reference: ModelReference
    _features: tuple[str, ...]
    _predictor: GbmPredictor = field(repr=False, compare=False)

    def predict_fair(
        self,
        snapshot: GameSnapshot,
        market_p_radiant: float,
        market_radiant_prior: float,
        history: SnapshotHistory,
        board: BoardFeatures,
    ) -> ModelPrediction:
        """Return the raw delta and the clipped Radiant fair."""
        values = _build_feature_values(
            snapshot,
            market_p_radiant,
            market_radiant_prior,
            history,
            board,
        )
        row = np.asarray(
            [[_catalog_value(values, column) for column in self._features]], dtype=np.float64
        )
        delta = _predict_delta(self._predictor, row)
        fair = values["market_p_radiant"] + delta
        if not math.isfinite(fair):
            raise ModelPredictionError("fair price is not finite")
        return ModelPrediction(raw_delta=float(delta), fair=float(np.clip(fair, 0.0, 1.0)))


def load_model(
    model_dir: Path, expected_features: Sequence[str], expected_lag_seconds: int
) -> ModelServer:
    """Load one catalog against `expected_features` and its source lag."""
    try:
        return _load_model_from(model_dir, expected_features, expected_lag_seconds)
    except ModelLoadError as exc:
        logger.warning("%s%s", STARTUP_BLOCKED_PREFIX, exc.reason)
        notify_in_background(f"{STARTUP_BLOCKED_PREFIX}{exc.reason}")
        raise


def yes_fair_from_model(radiant_fair: float, yes_is_radiant: bool) -> float:
    """Map a Radiant fair price to the YES fair price via the market polarity."""
    fair = _require_finite_number("radiant_fair", radiant_fair)
    if not 0.0 <= fair <= 1.0:
        raise ModelPredictionError("radiant_fair must be within [0, 1]")
    if type(yes_is_radiant) is not bool:
        raise ModelPredictionError("yes_is_radiant must be a boolean")
    return fair if yes_is_radiant else 1.0 - fair


_CONTRACT_CATALOG_REASONS = frozenset(
    {
        "ensemble member features do not match model.json",
        "ensemble member features cannot be read",
    }
)


def _load_model_from(
    model_dir: Path, expected_features: Sequence[str], expected_lag_seconds: int
) -> ModelServer:
    """Load and verify one catalog; the canonical loader adds the reporting."""
    meta_path = model_dir / MODEL_META_FILENAME
    _require_meta_file(meta_path)
    meta = _read_meta(meta_path)
    validated = _validate_meta(meta, expected_features, expected_lag_seconds)
    try:
        with _SilencedStderr():
            predictor = load_predictor_from_meta(model_dir, meta)
    except ModelCatalogError as exc:
        _reraise_catalog_error(exc)
    return ModelServer(
        model_reference=validated.reference,
        _features=tuple(validated.features),
        _predictor=predictor,
    )


def _require_meta_file(meta_path: Path) -> None:
    """Require model.json to be a regular file, or raise a safe load error."""
    try:
        meta_file = meta_path.is_file()
    except OSError as exc:
        logger.debug("model artifact check failed: %s", type(exc).__name__)
        raise ModelLoadError("model artifacts are unreadable") from None
    if not meta_file:
        raise ModelLoadError("model.json is missing or not a regular file")


def _reraise_catalog_error(exc: ModelCatalogError) -> NoReturn:
    """Map a catalog failure onto the trader load/contract errors without leaking paths."""
    if exc.reason in _CONTRACT_CATALOG_REASONS:
        raise ModelContractError(exc.reason) from None
    raise ModelLoadError(exc.reason) from None


def _read_meta(meta_path: Path) -> ModelMeta:
    """Read model.json once; file and JSON failures become safe load errors."""
    try:
        return read_model_meta(meta_path)
    except (OSError, ValueError) as exc:
        logger.debug("model metadata load failed: %s", type(exc).__name__)
        raise ModelLoadError("model.json cannot be read or parsed") from None


def _validate_meta(
    meta: ModelMeta, expected_features: Sequence[str], expected_lag_seconds: int
) -> _ValidatedMeta:
    """Validate the live-consumed metadata and return the pinned identity plus features."""
    fields = cast(Mapping[str, object], meta)
    try:
        name = require_nonempty_str(fields, "name", META_LABEL)
        trained_at = require_nonempty_str(fields, "trained_at", META_LABEL)
        features = require_str_list(fields, "features", META_LABEL)
        source_lag = require_int(fields, "source_lag_seconds", META_LABEL)
    except StrictJsonError as exc:
        raise ModelContractError(str(exc)) from None
    if features != list(expected_features):
        raise ModelContractError("model.json features do not match the live feature columns")
    if source_lag != expected_lag_seconds:
        raise ModelContractError("model.json source_lag_seconds does not match the live contract")
    reference = ModelReference(name=name, trained_at=trained_at)
    return _ValidatedMeta(reference=reference, features=features)


def _build_feature_values(
    snapshot: GameSnapshot,
    market_p_radiant: float,
    market_radiant_prior: float,
    history: SnapshotHistory,
    board: BoardFeatures,
) -> dict[str, float]:
    """Validate and convert the live model inputs under their feature names.

    Current levels and market values must be finite; tape-derived history
    columns may be NaN when a lag has no fresh-enough received snapshot.
    """
    market = _require_finite_number("market_p_radiant", market_p_radiant)
    if not 0.0 <= market <= 1.0:
        raise ModelPredictionError("market_p_radiant must be within [0, 1]")
    prior = _require_finite_number("market_radiant_prior", market_radiant_prior)
    if not 0.0 <= prior <= 1.0:
        raise ModelPredictionError("market_radiant_prior must be within [0, 1]")
    values = {
        "second": _require_finite_number("second", snapshot.second),
        "radiant_nw_adv": _require_finite_number("radiant_nw_adv", snapshot.radiant_nw_adv),
        "radiant_nw": _require_finite_number("radiant_nw", snapshot.radiant_nw),
        "dire_nw": _require_finite_number("dire_nw", snapshot.dire_nw),
        "radiant_xp_adv": _require_finite_number("radiant_xp_adv", snapshot.radiant_xp_adv),
        "deaths_radiant": _require_finite_number("deaths_radiant", snapshot.deaths_radiant),
        "deaths_dire": _require_finite_number("deaths_dire", snapshot.deaths_dire),
        "market_radiant_prior": prior,
        "market_p_radiant": market,
    }
    for name, raw in top_player_feature_values(snapshot.top).items():
        values[name] = _require_finite_number(name, raw)
    values.update(history.derived(snapshot.second, snapshot_history_levels(snapshot)))
    values.update(board_model_values(board))
    values.update(market_derived_values(market, prior))
    return values


def _catalog_value(values: Mapping[str, float], column: str) -> float:
    """One row value under the catalog contract: NaN only in tape-derived columns."""
    value = values.get(column)
    if value is None:
        raise ModelPredictionError("feature mapping does not match the live feature columns")
    if column not in DOTA_HISTORY_FEATURE_NAMES and not math.isfinite(value):
        raise ModelPredictionError(f"{column} must be a finite number")
    return value


def _require_finite_number(label: str, value: object) -> float:
    """Return value as a finite float, or raise on bool/non-numeric/non-finite input."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ModelPredictionError(f"{label} must be a finite number")
    try:
        number = float(value)
    except OverflowError:
        raise ModelPredictionError(f"{label} must be finite") from None
    if not math.isfinite(number):
        raise ModelPredictionError(f"{label} must be finite")
    return number


def _predict_delta(predictor: GbmPredictor, row: npt.NDArray[np.float64]) -> float:
    """Run one inference and require exactly one finite numeric scalar delta."""
    try:
        with _SilencedStderr():
            prediction = predictor.predict_one_thread(row)
    except (lgb.basic.LightGBMError, OSError, ValueError, RuntimeError):
        raise ModelPredictionError("model inference failed") from None
    deltas = _require_delta_array(prediction)
    delta = float(deltas[0])
    if not math.isfinite(delta):
        raise ModelPredictionError("model delta is not finite")
    return delta


def _require_delta_array(value: object) -> npt.NDArray[np.float64]:
    """Require a numeric (1,) ndarray delta; bool/string/object outputs are rejected."""
    if not isinstance(value, np.ndarray):
        raise ModelPredictionError("model returned no single scalar delta")
    array = cast(npt.NDArray[np.float64], value)
    if array.shape != (1,):
        raise ModelPredictionError("model returned no single scalar delta")
    if array.dtype.kind not in ("f", "i", "u"):
        raise ModelPredictionError("model delta is not a number")
    return array
