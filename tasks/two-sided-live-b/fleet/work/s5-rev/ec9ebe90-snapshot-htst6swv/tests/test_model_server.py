"""Tests for the live model server: load contract, lifecycle and inference.

Everything runs offline: metadata comes from a patched reader, boosters are a
typed fake recording construction paths and received rows, the canonical
paths are temporary placeholder files, and Telegram delivery is a synchronous
recorder. No real model.txt, no HTTP, Steam, collector, engine or Docker.
"""

# The stderr-suppression seam _SilencedStderr is exercised directly by the
# concurrency tests below; its name is intentionally private to the module.
# pyright: reportPrivateUsage=false

import json
import logging
import math
import os
import threading
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
from typing import cast

import lightgbm as lgb
import numpy as np
import numpy.typing as npt
import pytest

import shared.utils.gbm as gbm_module
from shared.constants.dataset import BACKTEST_LAG_SECONDS, TRAIN_LAG_SECONDS
from shared.constants.lol import LOL_SOURCE_LAG_SECONDS
from shared.types.model import ModelMeta
from shared.utils.board_features import BoardFeatures
from shared.utils.dota_features import (
    DOTA_HISTORY_COLUMN_NAMES,
    DOTA_NOXP_FEATURE_COLUMNS,
    DOTA_XP_FEATURE_COLUMNS,
    GRID_HISTORY_POLICY,
    SnapshotHistory,
    market_derived_values,
)
from shared.utils.top_players import TopPlayerFeatures
from trader import model_server
from trader.bindings import ModelReference
from trader.game_profile import GAME_PROFILES, GameProfile
from trader.live_feed import GameSnapshot, MatchPhase
from trader.model_server import (
    ModelContractError,
    ModelLoadError,
    ModelPredictionError,
    ModelServer,
    load_model,
    yes_fair_from_model,
)

MODEL_FILENAME = "member_00.txt"
META_FILENAME = "model.json"
# The fake model name embeds the marker: leaking any metadata into a log or
# alert trips the safety assertions below.
SENSITIVE_MARKER = "SECRET-MARKER-7f3a"
FAKE_MODEL_NAME = f"model-{SENSITIVE_MARKER}"
FAKE_TRAINED_AT = "2026-08-14T16:52:39Z"
BLOCKED_PREFIX = "trader model startup blocked: "
# Unique per-feature values: each row column must trace back to its field name.
SECOND_VALUE = 117
NW_VALUE = 511
RADIANT_NW_VALUE = 2200
DIRE_NW_VALUE = 1689
XP_VALUE = 317
DEATHS_RADIANT_VALUE = 3
DEATHS_DIRE_VALUE = 5
TOP1_NW_ADV_VALUE = 23
RAD_RATIO_VALUE = 1.4
DIRE_RATIO_VALUE = 0.8
MARKET_VALUE = 0.61
PRIOR_VALUE = 0.37
LIVE_FEATURE_NAMES = list(DOTA_XP_FEATURE_COLUMNS)
TOP3_NW_ADV_VALUE = 47
RAD_TOP3_RATIO_VALUE = 2.1
DIRE_TOP3_RATIO_VALUE = 1.6


def empty_tape() -> SnapshotHistory:
    """A never-recorded tape: every history pivot resolves to None (NaN)."""
    return SnapshotHistory(GRID_HISTORY_POLICY)


class AlertRecorder:
    """Synchronous stand-in for notify_in_background recording every alert."""

    def __init__(self) -> None:
        self.messages: list[str] = []

    def __call__(self, message: str) -> None:
        self.messages.append(message)


class MetaReader:
    """Stand-in for read_model_meta serving one document and recording calls."""

    def __init__(self, meta: ModelMeta) -> None:
        self.meta = meta
        self.calls: list[Path] = []

    def __call__(self, path: Path) -> ModelMeta:
        self.calls.append(path)
        return self.meta


class FakeBooster:
    """Typed lgb.Booster stand-in recording construction, names and predict rows."""

    def __init__(self, model_file: str) -> None:
        self.model_file = model_file
        self.feature_names: list[str] = list(LIVE_FEATURE_NAMES)
        self.rows: list[npt.NDArray[np.float64]] = []
        self.num_threads: list[int] = []
        self.outputs: list[np.ndarray] = []
        self.feature_name_error: Exception | None = None
        self.predict_error: Exception | None = None

    def feature_name(self) -> list[str]:
        if self.feature_name_error is not None:
            raise self.feature_name_error
        return self.feature_names

    def num_trees(self) -> int:
        return 1

    def predict(self, data: npt.NDArray[np.float64], num_threads: int) -> np.ndarray:
        if self.predict_error is not None:
            raise self.predict_error
        self.num_threads.append(num_threads)
        self.rows.append(np.asarray(data))
        if not self.outputs:
            return np.asarray([0.0], dtype=np.float64)
        return self.outputs.pop(0)


class BoosterFactory:
    """Stand-in for lgb.Booster constructing and remembering FakeBoosters."""

    def __init__(
        self,
        names: list[str] | None = None,
        feature_name_error: Exception | None = None,
        predict_error: Exception | None = None,
    ) -> None:
        self.names = names
        self.feature_name_error = feature_name_error
        self.predict_error = predict_error
        self.boosters: list[FakeBooster] = []

    def __call__(self, model_file: str) -> FakeBooster:
        booster = FakeBooster(model_file)
        names = self.names if self.names is not None else LIVE_FEATURE_NAMES
        booster.feature_names = list(names)
        booster.feature_name_error = self.feature_name_error
        booster.predict_error = self.predict_error
        self.boosters.append(booster)
        return booster


class RaisingBoosterFactory:
    """Stand-in for lgb.Booster raising one fixed constructor error per call."""

    def __init__(self, error: Exception) -> None:
        self.error = error
        self.calls: list[str] = []

    def __call__(self, model_file: str) -> FakeBooster:
        self.calls.append(model_file)
        raise self.error


def patch_catalog_booster(monkeypatch: pytest.MonkeyPatch, factory: object) -> None:
    """Point the shared catalog loader at a fake LightGBM constructor."""
    monkeypatch.setattr(gbm_module.lgb, "Booster", factory)


def profile_with_dir(game: str, model_dir: Path) -> GameProfile:
    """Return the game profile whose primary catalog points at `model_dir`."""
    profile = GAME_PROFILES[game]
    return replace(profile, primary=replace(profile.primary, model_dir=model_dir))


def load_production(profile: GameProfile) -> ModelServer:
    """Load the profile's primary production catalog."""
    return load_model(
        profile.primary.model_dir,
        profile.primary.features,
        profile.primary.source_lag_seconds,
    )


class LoadedHarness:
    """One patched load boundary: placeholder paths, fake reader/booster and alerts."""

    def __init__(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        meta: ModelMeta,
        booster_names: list[str] | None = None,
        feature_name_error: Exception | None = None,
        predict_error: Exception | None = None,
        game: str = "dota",
    ) -> None:
        self.model_path = tmp_path / MODEL_FILENAME
        self.meta_path = tmp_path / META_FILENAME
        self.model_path.write_bytes(b"")
        self.meta_path.write_bytes(b"{}")
        self.profile = profile_with_dir(game, tmp_path)
        self.reader = MetaReader(meta)
        monkeypatch.setattr(model_server, "read_model_meta", self.reader)
        self.booster_factory = BoosterFactory(booster_names, feature_name_error, predict_error)
        patch_catalog_booster(monkeypatch, self.booster_factory)
        self.alerts = AlertRecorder()
        monkeypatch.setattr(model_server, "notify_in_background", self.alerts)


def build_meta(**overrides: object) -> ModelMeta:
    """Build model.json metadata with the live contract; overrides replace fields."""
    values: dict[str, object] = {
        "name": FAKE_MODEL_NAME,
        "trained_at": FAKE_TRAINED_AT,
        "features": list(LIVE_FEATURE_NAMES),
        "source_lag_seconds": TRAIN_LAG_SECONDS,
        "members": ["member_00.txt"],
        "member_trees": [1],
    }
    values.update(overrides)
    return cast(ModelMeta, values)


def build_snapshot(**overrides: object) -> GameSnapshot:
    """Build one snapshot with unique per-field values; overrides replace fields."""
    values: dict[str, object] = {
        "second": SECOND_VALUE,
        "server_timestamp": 1_786_700_000,
        "phase": MatchPhase.PRE_MATCH,
        "radiant_nw_adv": NW_VALUE,
        "radiant_nw": RADIANT_NW_VALUE,
        "dire_nw": DIRE_NW_VALUE,
        "radiant_xp_adv": XP_VALUE,
        "deaths_radiant": DEATHS_RADIANT_VALUE,
        "deaths_dire": DEATHS_DIRE_VALUE,
        "top": TopPlayerFeatures(
            top1_nw_adv=TOP1_NW_ADV_VALUE,
            radiant_top1_nw_ratio=RAD_RATIO_VALUE,
            dire_top1_nw_ratio=DIRE_RATIO_VALUE,
            top3_nw_adv=TOP3_NW_ADV_VALUE,
            radiant_top3_nw_ratio=RAD_TOP3_RATIO_VALUE,
            dire_top3_nw_ratio=DIRE_TOP3_RATIO_VALUE,
        ),
        "paused": False,
    }
    values.update(overrides)
    return GameSnapshot(
        second=cast(int, values["second"]),
        server_timestamp=cast(int, values["server_timestamp"]),
        phase=cast(MatchPhase, values["phase"]),
        radiant_nw_adv=cast(int, values["radiant_nw_adv"]),
        radiant_nw=cast(int, values["radiant_nw"]),
        dire_nw=cast(int, values["dire_nw"]),
        radiant_xp_adv=cast(int, values["radiant_xp_adv"]),
        deaths_radiant=cast(int, values["deaths_radiant"]),
        deaths_dire=cast(int, values["deaths_dire"]),
        top=cast(TopPlayerFeatures, values["top"]),
        paused=cast(bool, values["paused"]),
    )


def expected_values() -> dict[str, float]:
    """The unique per-feature values one row column must carry; history is NaN."""
    derived = market_derived_values(MARKET_VALUE, PRIOR_VALUE)
    logit = derived["logit_market_p_radiant"]
    versus_prior = derived["market_vs_prior"]
    values = {
        "second": float(SECOND_VALUE),
        "radiant_nw_adv": float(NW_VALUE),
        "radiant_nw": float(RADIANT_NW_VALUE),
        "dire_nw": float(DIRE_NW_VALUE),
        "radiant_xp_adv": float(XP_VALUE),
        "deaths_radiant": float(DEATHS_RADIANT_VALUE),
        "deaths_dire": float(DEATHS_DIRE_VALUE),
        "top1_nw_adv": float(TOP1_NW_ADV_VALUE),
        "radiant_top1_nw_ratio": float(RAD_RATIO_VALUE),
        "dire_top1_nw_ratio": float(DIRE_RATIO_VALUE),
        "top3_nw_adv": float(TOP3_NW_ADV_VALUE),
        "radiant_top3_nw_ratio": float(RAD_TOP3_RATIO_VALUE),
        "dire_top3_nw_ratio": float(DIRE_TOP3_RATIO_VALUE),
        "market_radiant_prior": PRIOR_VALUE,
        "logit_market_p_radiant": logit,
        "market_vs_prior": versus_prior,
        "market_p_radiant": MARKET_VALUE,
    }
    values.update(
        {
            "pending_deaths_radiant": 0.0,
            "pending_deaths_dire": 0.0,
            "board_death_age_radiant_s": 30.0,
            "board_death_age_dire_s": 30.0,
        }
    )
    values.update({column: float("nan") for column in DOTA_HISTORY_COLUMN_NAMES})
    return values


def assert_row_matches(row: npt.NDArray[np.float64], features: Sequence[str]) -> None:
    """Each feature position equals its expected value; NaN matches NaN."""
    expected = expected_values()
    for index, column in enumerate(features):
        if math.isnan(expected[column]):
            assert math.isnan(row[0, index])
        else:
            assert row[0, index] == expected[column]


def assert_safe_texts(model_path: Path, meta_path: Path, *texts: str) -> None:
    """Assert the texts expose no artifact path and no fake sensitive marker."""
    for text in texts:
        assert str(model_path) not in text
        assert str(meta_path) not in text
        assert SENSITIVE_MARKER not in text


def assert_one_startup_log(caplog: pytest.LogCaptureFixture, reason: str) -> None:
    """Assert exactly one model-server warning carrying the blocked message."""
    warnings = [
        record
        for record in caplog.records
        if record.name == "trader.model_server" and record.levelno == logging.WARNING
    ]
    assert len(warnings) == 1
    assert warnings[0].getMessage() == f"{BLOCKED_PREFIX}{reason}"


# --- successful load and lifecycle ---------------------------------------------


def test_successful_load_pins_one_model_for_repeated_predictions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Metadata and booster load once; repeated predictions reload nothing."""
    harness = LoadedHarness(tmp_path, monkeypatch, build_meta())
    server = load_production(harness.profile)
    assert server.model_reference == ModelReference(
        name=FAKE_MODEL_NAME, trained_at=FAKE_TRAINED_AT
    )
    assert harness.reader.calls == [harness.meta_path]
    assert len(harness.booster_factory.boosters) == 1
    booster = harness.booster_factory.boosters[0]
    assert booster.model_file == str(harness.model_path)
    assert harness.alerts.messages == []
    server.predict_fair(build_snapshot(), MARKET_VALUE, PRIOR_VALUE, empty_tape(), neutral_board())
    server.predict_fair(build_snapshot(), MARKET_VALUE, PRIOR_VALUE, empty_tape(), neutral_board())
    assert harness.reader.calls == [harness.meta_path]
    assert len(harness.booster_factory.boosters) == 1
    assert len(booster.rows) == 2


def test_live_predict_uses_one_thread(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Live inference asks LightGBM for a single thread."""
    harness = LoadedHarness(tmp_path, monkeypatch, build_meta())
    server = load_production(harness.profile)
    server.predict_fair(build_snapshot(), MARKET_VALUE, PRIOR_VALUE, empty_tape(), neutral_board())
    assert harness.booster_factory.boosters[0].num_threads == [1]


def test_feature_row_carries_exact_order_and_raw_second(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The row is (1, 81) float64 in DOTA_XP order; second is not lagged."""
    harness = LoadedHarness(tmp_path, monkeypatch, build_meta())
    server = load_production(harness.profile)
    server.predict_fair(build_snapshot(), MARKET_VALUE, PRIOR_VALUE, empty_tape(), neutral_board())
    row = harness.booster_factory.boosters[0].rows[0]
    assert row.shape == (1, len(LIVE_FEATURE_NAMES))
    assert row.dtype == np.float64
    assert_row_matches(row, LIVE_FEATURE_NAMES)
    second_index = LIVE_FEATURE_NAMES.index("second")
    assert row[0, second_index] == float(SECOND_VALUE)
    assert row[0, second_index] != float(SECOND_VALUE - BACKTEST_LAG_SECONDS)


def test_feature_row_follows_patched_column_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A reordered live catalog reorders the row by field name, not position."""
    reordered = list(reversed(LIVE_FEATURE_NAMES))
    harness = LoadedHarness(
        tmp_path, monkeypatch, build_meta(features=reordered), booster_names=reordered
    )
    harness.profile = replace(
        harness.profile,
        primary=replace(harness.profile.primary, features=tuple(reordered)),
    )
    server = load_production(harness.profile)
    server.predict_fair(build_snapshot(), MARKET_VALUE, PRIOR_VALUE, empty_tape(), neutral_board())
    row = harness.booster_factory.boosters[0].rows[0]
    assert_row_matches(row, reordered)


def test_build_feature_values_maps_all_feature_columns() -> None:
    """Live maps every catalog name; history stays NaN on an empty tape."""
    values = model_server._build_feature_values(
        build_snapshot(), MARKET_VALUE, PRIOR_VALUE, empty_tape(), neutral_board()
    )
    assert set(DOTA_XP_FEATURE_COLUMNS).issubset(values)
    expected = expected_values()
    assert all(
        math.isnan(values[column])
        if math.isnan(expected[column])
        else values[column] == expected[column]
        for column in DOTA_XP_FEATURE_COLUMNS
    )


def test_negative_second_is_sent_unchanged(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A pre-horn negative second reaches the row as-is (float64, no lag shift)."""
    harness = LoadedHarness(tmp_path, monkeypatch, build_meta())
    server = load_production(harness.profile)
    server.predict_fair(
        build_snapshot(second=-7), MARKET_VALUE, PRIOR_VALUE, empty_tape(), neutral_board()
    )
    row = harness.booster_factory.boosters[0].rows[0]
    assert row[0, LIVE_FEATURE_NAMES.index("second")] == -7.0


def test_second_explicit_load_gets_new_artifacts_first_server_stays_pinned(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No global cache: a later load sees new production/ artifacts; the old server stays pinned."""
    harness = LoadedHarness(tmp_path, monkeypatch, build_meta())
    first = load_production(harness.profile)
    booster_a = harness.booster_factory.boosters[0]
    harness.reader.meta = build_meta(name="model-B")
    second = load_production(harness.profile)
    booster_b = harness.booster_factory.boosters[1]
    assert len(harness.booster_factory.boosters) == 2
    assert booster_b is not booster_a
    assert first.model_reference.name == FAKE_MODEL_NAME
    assert second.model_reference.name == "model-B"
    assert harness.reader.calls == [harness.meta_path, harness.meta_path]
    first.predict_fair(build_snapshot(), MARKET_VALUE, PRIOR_VALUE, empty_tape(), neutral_board())
    second.predict_fair(build_snapshot(), MARKET_VALUE, PRIOR_VALUE, empty_tape(), neutral_board())
    assert len(booster_a.rows) == 1
    assert len(booster_b.rows) == 1


def test_load_production_model_reads_profile_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Dota and LoL profiles load member_*.txt/model.json from their own temp dirs."""
    dota_dir = tmp_path / "dota"
    lol_dir = tmp_path / "lol"
    dota_dir.mkdir()
    lol_dir.mkdir()
    for directory in (dota_dir, lol_dir):
        (directory / MODEL_FILENAME).write_bytes(b"")
        (directory / META_FILENAME).write_bytes(b"{}")
    metas = {
        dota_dir / META_FILENAME: build_meta(name="dota-temp-name"),
        lol_dir / META_FILENAME: build_meta(
            name="lol-temp-name",
            features=list(LIVE_FEATURE_NAMES),
            source_lag_seconds=LOL_SOURCE_LAG_SECONDS,
        ),
    }

    def read_meta(path: Path) -> ModelMeta:
        return metas[path]

    monkeypatch.setattr(model_server, "read_model_meta", read_meta)
    names_by_file = {
        str(dota_dir / MODEL_FILENAME): list(LIVE_FEATURE_NAMES),
        str(lol_dir / MODEL_FILENAME): list(LIVE_FEATURE_NAMES),
    }

    class DirBoosterFactory:
        """Return the booster whose feature names match that dir's meta."""

        def __init__(self) -> None:
            self.boosters: list[FakeBooster] = []

        def __call__(self, model_file: str) -> FakeBooster:
            booster = FakeBooster(model_file)
            booster.feature_names = list(names_by_file[model_file])
            self.boosters.append(booster)
            return booster

    factory = DirBoosterFactory()
    patch_catalog_booster(monkeypatch, factory)
    monkeypatch.setattr(model_server, "notify_in_background", AlertRecorder())
    dota_server = load_production(profile_with_dir("dota", dota_dir))
    lol_server = load_production(profile_with_dir("lol", lol_dir))
    assert dota_server.model_reference.name == "dota-temp-name"
    assert lol_server.model_reference.name == "lol-temp-name"
    assert factory.boosters[0].model_file == str(dota_dir / MODEL_FILENAME)
    assert factory.boosters[1].model_file == str(lol_dir / MODEL_FILENAME)


def test_lol_profile_loads_the_81_column_catalog(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The lol-map production profile loads a DOTA_XP meta at the LoL source lag."""
    harness = LoadedHarness(
        tmp_path,
        monkeypatch,
        build_meta(
            features=list(LIVE_FEATURE_NAMES),
            source_lag_seconds=LOL_SOURCE_LAG_SECONDS,
        ),
        game="lol",
    )
    server = load_production(harness.profile)
    assert server.model_reference == ModelReference(
        name=FAKE_MODEL_NAME, trained_at=FAKE_TRAINED_AT
    )
    prediction = server.predict_fair(
        build_snapshot(), MARKET_VALUE, PRIOR_VALUE, empty_tape(), neutral_board()
    )
    assert prediction.fair == pytest.approx(MARKET_VALUE)
    row = harness.booster_factory.boosters[0].rows[0]
    assert row.shape == (1, len(LIVE_FEATURE_NAMES))
    assert_row_matches(row, LIVE_FEATURE_NAMES)
    assert harness.alerts.messages == []


def test_noxp_catalog_predicts_without_xp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """production-noxp builds a 70-column row that drops every XP field."""
    noxp = list(DOTA_NOXP_FEATURE_COLUMNS)
    harness = LoadedHarness(tmp_path, monkeypatch, build_meta(features=noxp), booster_names=noxp)
    server = load_model(tmp_path, noxp, TRAIN_LAG_SECONDS)
    server.predict_fair(build_snapshot(), MARKET_VALUE, PRIOR_VALUE, empty_tape(), neutral_board())
    row = harness.booster_factory.boosters[0].rows[0]
    assert row.shape == (1, 70)
    assert_row_matches(row, noxp)
    assert "radiant_xp_adv" not in noxp


def test_noxp_expected_features_reject_xp_catalog(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """An 81-feature catalog cannot load as production-noxp; 70-feature cannot load as production."""
    harness = LoadedHarness(tmp_path, monkeypatch, build_meta())
    with (
        caplog.at_level(logging.WARNING, logger="trader.model_server"),
        pytest.raises(ModelContractError),
    ):
        load_model(tmp_path, DOTA_NOXP_FEATURE_COLUMNS, TRAIN_LAG_SECONDS)
    noxp = list(DOTA_NOXP_FEATURE_COLUMNS)
    harness.reader.meta = build_meta(features=noxp)
    with pytest.raises(ModelContractError):
        load_production(harness.profile)


def test_ensemble_catalog_serves_the_mean(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Trader inference on an ensemble catalog is the mean of every member."""
    members = ["member_00.txt", "member_01.txt"]
    harness = LoadedHarness(tmp_path, monkeypatch, build_meta(members=members, member_trees=[1, 1]))
    (tmp_path / "member_00.txt").write_bytes(b"")
    (tmp_path / "member_01.txt").write_bytes(b"")
    deltas = {str(tmp_path / "member_00.txt"): 0.10, str(tmp_path / "member_01.txt"): 0.20}

    class EnsembleFactory:
        def __init__(self) -> None:
            self.boosters: list[FakeBooster] = []

        def __call__(self, model_file: str) -> FakeBooster:
            booster = FakeBooster(model_file)
            booster.outputs = [np.asarray([deltas[model_file]], dtype=np.float64)]
            self.boosters.append(booster)
            return booster

    factory = EnsembleFactory()
    patch_catalog_booster(monkeypatch, factory)
    server = load_production(harness.profile)
    prediction = server.predict_fair(
        build_snapshot(), MARKET_VALUE, PRIOR_VALUE, empty_tape(), neutral_board()
    )
    assert prediction.raw_delta == pytest.approx(0.15)
    assert [booster.model_file for booster in factory.boosters] == [
        str(tmp_path / "member_00.txt"),
        str(tmp_path / "member_01.txt"),
    ]


# --- contract failures ----------------------------------------------------------


@pytest.mark.parametrize(
    ("override", "reason"),
    [
        (
            {"features": list(reversed(LIVE_FEATURE_NAMES))},
            "model.json features do not match the live feature columns",
        ),
        (
            {"features": [*LIVE_FEATURE_NAMES[:-1], "other_price"]},
            "model.json features do not match the live feature columns",
        ),
        (
            {"features": LIVE_FEATURE_NAMES[:-1]},
            "model.json features do not match the live feature columns",
        ),
        ({"features": "second"}, "model.json 'features' must be a list"),
        (
            {"source_lag_seconds": TRAIN_LAG_SECONDS + 1},
            "model.json source_lag_seconds does not match the live contract",
        ),
        ({"source_lag_seconds": True}, "model.json 'source_lag_seconds' must be an integer"),
        ({"name": ""}, "model.json 'name' must be a nonempty string"),
        ({"trained_at": 42}, "model.json 'trained_at' must be a string"),
    ],
)
def test_metadata_contract_mismatch_blocks_booster_and_alerts_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    override: dict[str, object],
    reason: str,
) -> None:
    """Each metadata mismatch raises, skips the booster and alerts once, safely."""
    harness = LoadedHarness(tmp_path, monkeypatch, build_meta(**override))
    with (
        caplog.at_level(logging.WARNING, logger="trader.model_server"),
        pytest.raises(ModelContractError) as excinfo,
    ):
        load_production(harness.profile)
    assert excinfo.value.reason == reason
    assert harness.booster_factory.boosters == []
    assert harness.alerts.messages == [f"{BLOCKED_PREFIX}{reason}"]
    assert_one_startup_log(caplog, reason)
    assert_safe_texts(harness.model_path, harness.meta_path, caplog.text, *harness.alerts.messages)


def test_booster_feature_mismatch_raises_contract_error_with_one_alert(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A stale member file next to valid metadata is the same typed failure plus one alert."""
    wrong_names = [*LIVE_FEATURE_NAMES[:-1], "other_price"]
    harness = LoadedHarness(tmp_path, monkeypatch, build_meta(), booster_names=wrong_names)
    with (
        caplog.at_level(logging.WARNING, logger="trader.model_server"),
        pytest.raises(ModelContractError) as excinfo,
    ):
        load_production(harness.profile)
    assert excinfo.value.reason == "ensemble member features do not match model.json"
    assert len(harness.booster_factory.boosters) == 1
    reason = "ensemble member features do not match model.json"
    assert harness.alerts.messages == [f"{BLOCKED_PREFIX}{reason}"]
    assert_one_startup_log(caplog, reason)
    assert_safe_texts(harness.model_path, harness.meta_path, caplog.text, *harness.alerts.messages)


def test_malformed_model_file_leaks_no_path_and_blocks_startup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capfd: pytest.CaptureFixture[str],
) -> None:
    """A real malformed member file emits no native path to stderr and fails safely."""
    model_path = tmp_path / MODEL_FILENAME
    meta_path = tmp_path / META_FILENAME
    model_path.write_text("this is not a lightgbm model\n", encoding="utf-8")
    meta_path.write_text(json.dumps(build_meta()), encoding="utf-8")
    profile = profile_with_dir("dota", tmp_path)
    recorder = AlertRecorder()
    monkeypatch.setattr(model_server, "notify_in_background", recorder)
    with (
        caplog.at_level(logging.WARNING, logger="trader.model_server"),
        pytest.raises(ModelLoadError) as excinfo,
    ):
        load_production(profile)
    assert type(excinfo.value) is ModelLoadError
    reason = "ensemble member cannot be parsed by LightGBM"
    assert excinfo.value.reason == reason
    assert recorder.messages == [f"{BLOCKED_PREFIX}{reason}"]
    assert_one_startup_log(caplog, reason)
    captured = capfd.readouterr()
    assert str(model_path) not in captured.err
    assert str(model_path) not in captured.out
    assert_safe_texts(model_path, meta_path, caplog.text, *recorder.messages)


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (lgb.basic.LightGBMError("native boom"), "ensemble member cannot be parsed by LightGBM"),
        (OSError("vanished"), "ensemble member cannot be read"),
        (ValueError("bad path"), "ensemble member cannot be parsed by LightGBM"),
        (RuntimeError("bad state"), "ensemble member cannot be parsed by LightGBM"),
    ],
)
def test_booster_constructor_errors_normalize_to_one_load_error_and_alert(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    error: Exception,
    reason: str,
) -> None:
    """Expected constructor errors (incl. the is_file race) become one safe alert."""
    harness = LoadedHarness(tmp_path, monkeypatch, build_meta())
    raising = RaisingBoosterFactory(error)
    patch_catalog_booster(monkeypatch, raising)
    with (
        caplog.at_level(logging.WARNING, logger="trader.model_server"),
        pytest.raises(ModelLoadError) as excinfo,
    ):
        load_production(harness.profile)
    assert type(excinfo.value) is ModelLoadError
    assert excinfo.value.reason == reason
    assert len(raising.calls) == 1
    assert harness.alerts.messages == [f"{BLOCKED_PREFIX}{reason}"]
    assert_one_startup_log(caplog, reason)
    assert_safe_texts(harness.model_path, harness.meta_path, caplog.text, *harness.alerts.messages)


@pytest.mark.parametrize(
    "error",
    [
        lgb.basic.LightGBMError("native boom"),
        OSError("boom"),
        ValueError("boom"),
        RuntimeError("boom"),
    ],
)
def test_booster_feature_name_failure_is_contract_error_with_one_alert(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    error: Exception,
) -> None:
    """Booster interface errors at the feature check fail closed with one safe alert."""
    harness = LoadedHarness(tmp_path, monkeypatch, build_meta(), feature_name_error=error)
    with (
        caplog.at_level(logging.WARNING, logger="trader.model_server"),
        pytest.raises(ModelContractError) as excinfo,
    ):
        load_production(harness.profile)
    reason = "ensemble member features cannot be read"
    assert excinfo.value.reason == reason
    assert len(harness.booster_factory.boosters) == 1
    assert harness.alerts.messages == [f"{BLOCKED_PREFIX}{reason}"]
    assert_one_startup_log(caplog, reason)
    assert_safe_texts(harness.model_path, harness.meta_path, caplog.text, *harness.alerts.messages)


@pytest.mark.parametrize(
    ("missing", "reason"),
    [
        (MODEL_FILENAME, "ensemble member is missing or not a regular file"),
        (META_FILENAME, "model.json is missing or not a regular file"),
    ],
)
def test_missing_artifact_blocks_startup_with_one_alert(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    missing: str,
    reason: str,
) -> None:
    """A missing member file or model.json fails closed with one safe alert."""
    model_path = tmp_path / MODEL_FILENAME
    meta_path = tmp_path / META_FILENAME
    if missing == META_FILENAME:
        model_path.write_bytes(b"{}")
    else:
        meta_path.write_text(json.dumps(build_meta()), encoding="utf-8")
    profile = profile_with_dir("dota", tmp_path)
    recorder = AlertRecorder()
    monkeypatch.setattr(model_server, "notify_in_background", recorder)
    with (
        caplog.at_level(logging.WARNING, logger="trader.model_server"),
        pytest.raises(ModelLoadError) as excinfo,
    ):
        load_production(profile)
    assert type(excinfo.value) is ModelLoadError
    assert excinfo.value.reason == reason
    assert recorder.messages == [f"{BLOCKED_PREFIX}{reason}"]
    assert_one_startup_log(caplog, reason)
    assert_safe_texts(model_path, meta_path, caplog.text, *recorder.messages)


def test_unparseable_metadata_blocks_startup_with_one_alert(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """An unreadable model.json fails closed with the same safe one-alert behavior."""
    model_path = tmp_path / MODEL_FILENAME
    meta_path = tmp_path / META_FILENAME
    model_path.write_bytes(b"")
    meta_path.write_text("{ not json", encoding="utf-8")
    profile = profile_with_dir("dota", tmp_path)
    recorder = AlertRecorder()
    monkeypatch.setattr(model_server, "notify_in_background", recorder)
    with (
        caplog.at_level(logging.WARNING, logger="trader.model_server"),
        pytest.raises(ModelLoadError) as excinfo,
    ):
        load_production(profile)
    assert type(excinfo.value) is ModelLoadError
    reason = "model.json cannot be read or parsed"
    assert excinfo.value.reason == reason
    assert recorder.messages == [f"{BLOCKED_PREFIX}{reason}"]
    assert_one_startup_log(caplog, reason)
    assert_safe_texts(model_path, meta_path, caplog.text, *recorder.messages)


# --- inference ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("market", "delta", "expected"),
    [
        (0.6, -0.8, 0.0),
        (0.2, 0.9, 1.0),
    ],
)
def test_delta_clips_to_bounds(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    market: float,
    delta: float,
    expected: float,
) -> None:
    """Negative and positive deltas clip the fair price to exactly 0.0 and 1.0."""
    harness = LoadedHarness(tmp_path, monkeypatch, build_meta())
    server = load_production(harness.profile)
    harness.booster_factory.boosters[0].outputs = [np.asarray([delta])]
    prediction = server.predict_fair(
        build_snapshot(), market, PRIOR_VALUE, empty_tape(), neutral_board()
    )
    assert prediction.fair == expected
    assert type(prediction.fair) is float
    assert prediction.raw_delta == delta


def test_normal_delta_adds_to_market_price_without_squeeze(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A raw delta adds linearly: no sigmoid, raw-score or other transform."""
    harness = LoadedHarness(tmp_path, monkeypatch, build_meta())
    server = load_production(harness.profile)
    harness.booster_factory.boosters[0].outputs = [np.asarray([0.25])]
    prediction = server.predict_fair(
        build_snapshot(), 0.4, PRIOR_VALUE, empty_tape(), neutral_board()
    )
    assert prediction.fair == pytest.approx(0.65)
    assert prediction.raw_delta == pytest.approx(0.25)


@pytest.mark.parametrize(
    "market",
    [float("nan"), float("inf"), -float("inf"), 1.5, -0.1, True],
)
def test_invalid_market_price_raises_prediction_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, market: float
) -> None:
    """NaN, infinite, out-of-range and bool market prices never reach the booster."""
    harness = LoadedHarness(tmp_path, monkeypatch, build_meta())
    server = load_production(harness.profile)
    with pytest.raises(ModelPredictionError):
        server.predict_fair(build_snapshot(), market, PRIOR_VALUE, empty_tape(), neutral_board())
    assert harness.booster_factory.boosters[0].rows == []
    assert harness.alerts.messages == []


@pytest.mark.parametrize(
    "prior",
    [float("nan"), float("inf"), -float("inf"), 1.5, -0.1, True],
)
def test_invalid_prior_raises_prediction_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, prior: float
) -> None:
    """NaN, infinite, out-of-range and bool priors never reach the booster."""
    harness = LoadedHarness(tmp_path, monkeypatch, build_meta())
    server = load_production(harness.profile)
    with pytest.raises(ModelPredictionError):
        server.predict_fair(build_snapshot(), MARKET_VALUE, prior, empty_tape(), neutral_board())
    assert harness.booster_factory.boosters[0].rows == []
    assert harness.alerts.messages == []


@pytest.mark.parametrize(
    "field",
    [
        "second",
        "radiant_nw_adv",
        "radiant_nw",
        "dire_nw",
        "radiant_xp_adv",
        "deaths_radiant",
        "deaths_dire",
    ],
)
@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -float("inf"), True])
def test_non_finite_snapshot_feature_raises_prediction_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    bad: object,
) -> None:
    """A non-finite or bool snapshot feature fails before any inference."""
    harness = LoadedHarness(tmp_path, monkeypatch, build_meta())
    server = load_production(harness.profile)
    snapshot = build_snapshot(**{field: bad})
    with pytest.raises(ModelPredictionError):
        server.predict_fair(snapshot, MARKET_VALUE, PRIOR_VALUE, empty_tape(), neutral_board())
    assert harness.booster_factory.boosters[0].rows == []


@pytest.mark.parametrize(
    "output",
    [
        np.asarray([float("nan")]),
        np.asarray([float("inf")]),
        np.asarray([-float("inf")]),
        np.asarray([[0.1, 0.2]]),
        np.asarray([0.1, 0.2]),
        cast(np.ndarray, None),
    ],
)
def test_invalid_booster_output_raises_prediction_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, output: np.ndarray
) -> None:
    """Non-finite, multi-value and non-array booster outputs raise prediction errors."""
    harness = LoadedHarness(tmp_path, monkeypatch, build_meta())
    server = load_production(harness.profile)
    harness.booster_factory.boosters[0].outputs = [output]
    with pytest.raises(ModelPredictionError):
        server.predict_fair(
            build_snapshot(), MARKET_VALUE, PRIOR_VALUE, empty_tape(), neutral_board()
        )


@pytest.mark.parametrize(
    "error",
    [
        lgb.basic.LightGBMError("native boom"),
        OSError("boom"),
        ValueError("boom"),
        RuntimeError("boom"),
    ],
)
def test_booster_predict_failure_is_prediction_error_with_no_alert_or_log(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    error: Exception,
) -> None:
    """Booster interface errors per tick become silent prediction errors (no I/O)."""
    harness = LoadedHarness(tmp_path, monkeypatch, build_meta(), predict_error=error)
    server = load_production(harness.profile)
    with (
        caplog.at_level(logging.WARNING, logger="trader.model_server"),
        pytest.raises(ModelPredictionError),
    ):
        server.predict_fair(
            build_snapshot(), MARKET_VALUE, PRIOR_VALUE, empty_tape(), neutral_board()
        )
    assert harness.alerts.messages == []
    assert caplog.records == []
    assert harness.booster_factory.boosters[0].rows == []


def test_yes_fair_maps_polarity_without_touching_the_booster() -> None:
    """True preserves the Radiant fair; False returns the exact complement."""
    assert yes_fair_from_model(0.6, True) == pytest.approx(0.6)
    assert yes_fair_from_model(0.6, False) == pytest.approx(0.4)


@pytest.mark.parametrize(
    "fair",
    [float("nan"), float("inf"), -float("inf"), 1.5, -0.1, True],
)
def test_yes_fair_rejects_invalid_radiant_fair(fair: float) -> None:
    """Non-finite, out-of-range and bool Radiant prices raise prediction errors."""
    with pytest.raises(ModelPredictionError):
        yes_fair_from_model(fair, True)


@pytest.mark.parametrize("polarity", [1, 0, "yes", None])
def test_yes_fair_rejects_non_bool_polarity(polarity: object) -> None:
    """Only an exact bool polarizes; ints, strings and None raise."""
    with pytest.raises(ModelPredictionError):
        yes_fair_from_model(0.6, cast(bool, polarity))


# --- stderr suppression --------------------------------------------------------


def count_open_fds() -> int:
    """Count this process's open fds via /proc/self/fd, or /dev/fd on Darwin."""
    fd_dir = "/proc/self/fd" if os.path.exists("/proc/self/fd") else "/dev/fd"
    return len(os.listdir(fd_dir))


def assert_lock_is_free() -> None:
    """Assert no thread holds the suppression lock (probed from another thread).

    The probe must run on a different thread: RLock.acquire from the owner
    thread re-enters and would mask a leaked hold.
    """
    results: list[bool] = []

    def probe() -> None:
        results.append(model_server._STDERR_SILENCE_LOCK.acquire(blocking=False))
        if results[0]:
            model_server._STDERR_SILENCE_LOCK.release()

    thread = threading.Thread(target=probe)
    thread.start()
    thread.join(timeout=10)
    assert not thread.is_alive()
    assert results == [True]


def test_concurrent_silenced_stderr_never_leaks_and_restores_fd2(
    tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    """Overlapping suppression windows serialize; no native path reaches stderr.

    Thread A holds the devnull redirect while thread B enters and both wait
    for their own release events. Without the process-wide lock, B saves A's
    devnull fd, A's exit (before B's write) restores the real stderr, B's
    native write then leaks its path, and B's exit leaves fd 2 pointing at
    devnull. With the lock, B blocks at enter while A holds the window,
    enters only after A's release, both writes are suppressed, and stderr is
    valid afterwards.
    """
    ready: list[threading.Event] = [threading.Event(), threading.Event()]
    inside: list[threading.Event] = [threading.Event(), threading.Event()]
    releases = [threading.Event(), threading.Event()]
    go_b = threading.Event()
    attempting_b = threading.Event()
    errors: list[BaseException] = []
    model_path_a = tmp_path / "model-a.txt"
    model_path_b = tmp_path / "model-b.txt"

    def worker(index: int) -> None:
        try:
            path = str(model_path_a) if index == 0 else str(model_path_b)
            ready[index].set()
            if index == 1:
                go_b.wait(timeout=10)
                attempting_b.set()
            with model_server._SilencedStderr():
                inside[index].set()
                releases[index].wait(timeout=10)
                os.write(2, f"PATH-LEAK {path}".encode())
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(index,)) for index in range(2)]
    for thread in threads:
        thread.start()
    assert ready[0].wait(timeout=10)
    assert ready[1].wait(timeout=10)
    assert inside[0].wait(timeout=10)
    assert threads[0].is_alive()
    go_b.set()
    assert attempting_b.wait(timeout=10)
    # B is at the with statement while A holds the lock: it must not enter.
    assert not inside[1].wait(timeout=0.5)
    releases[0].set()
    threads[0].join(timeout=10)
    # B may enter only after A released the window.
    assert inside[1].wait(timeout=10)
    releases[1].set()
    threads[1].join(timeout=10)
    assert not any(thread.is_alive() for thread in threads)
    assert errors == []
    os.write(2, b"AFTER-SILENCE-MARKER")
    captured = capfd.readouterr()
    assert str(model_path_a) not in captured.err
    assert str(model_path_b) not in captured.err
    assert "AFTER-SILENCE-MARKER" in captured.err


def test_silenced_stderr_restores_on_body_exception_and_nesting(
    capfd: pytest.CaptureFixture[str],
) -> None:
    """The fd 2 redirect is restored when the body raises, and nesting is safe."""
    with (
        pytest.raises(RuntimeError),
        model_server._SilencedStderr(),
        model_server._SilencedStderr(),
    ):
        raise RuntimeError("body boom")
    os.write(2, b"AFTER-SILENCE-MARKER")
    captured = capfd.readouterr()
    assert "AFTER-SILENCE-MARKER" in captured.err


def test_silenced_stderr_enter_open_failure_cleans_up_and_releases(
    monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    """A failing devnull open in __enter__ closes the dup, releases, keeps stderr."""
    real_open = os.open
    failing = [True]

    def guarded_open(path: str | bytes, flags: int, mode: int = 0o777) -> int:
        if failing[0] and path == os.devnull:
            raise OSError("devnull unavailable")
        return real_open(path, flags, mode)

    before = count_open_fds()
    monkeypatch.setattr(model_server.os, "open", guarded_open)
    with pytest.raises(OSError), model_server._SilencedStderr():
        raise AssertionError("the body must not run")
    after = count_open_fds()
    assert after <= before
    os.write(2, b"AFTER-SILENCE-MARKER")
    captured = capfd.readouterr()
    assert "AFTER-SILENCE-MARKER" in captured.err
    # The lock was released: a later window still enters and exits normally.
    failing[0] = False
    with model_server._SilencedStderr():
        pass
    os.write(2, b"SECOND-AFTER-MARKER")
    captured = capfd.readouterr()
    assert "SECOND-AFTER-MARKER" in captured.err


def test_silenced_stderr_enter_dup2_failure_cleans_up_and_releases(
    monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    """A failing redirect dup2 in __enter__ closes both fds, releases, keeps stderr."""
    real_dup2 = os.dup2
    failed = False

    def flaky_dup2(src: int, dst: int) -> None:
        nonlocal failed
        if dst == 2 and not failed:
            failed = True
            raise OSError("dup2 failed")
        real_dup2(src, dst)

    before = count_open_fds()
    monkeypatch.setattr(model_server.os, "dup2", flaky_dup2)
    with pytest.raises(OSError), model_server._SilencedStderr():
        raise AssertionError("the body must not run")
    after = count_open_fds()
    assert after <= before
    os.write(2, b"AFTER-SILENCE-MARKER")
    captured = capfd.readouterr()
    assert "AFTER-SILENCE-MARKER" in captured.err
    with model_server._SilencedStderr():
        pass
    os.write(2, b"SECOND-AFTER-MARKER")
    captured = capfd.readouterr()
    assert "SECOND-AFTER-MARKER" in captured.err


def test_silenced_stderr_enter_dup_failure_releases_lock_and_keeps_stderr(
    monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    """A failing os.dup in __enter__ releases the lock and leaves stderr untouched."""
    real_dup = os.dup

    def failing_dup(fd: int) -> int:
        if fd == 2:
            raise OSError("dup failed")
        return real_dup(fd)

    before = count_open_fds()
    monkeypatch.setattr(model_server.os, "dup", failing_dup)
    try:
        with pytest.raises(OSError), model_server._SilencedStderr():
            raise AssertionError("the body must not run")
    finally:
        # Undo before fixture finalization: capfd teardown calls os.dup itself.
        monkeypatch.undo()
    after = count_open_fds()
    assert after <= before
    assert_lock_is_free()
    os.write(2, b"AFTER-SILENCE-MARKER")
    captured = capfd.readouterr()
    assert "AFTER-SILENCE-MARKER" in captured.err


def test_silenced_stderr_enter_open_failure_with_close_failure_still_releases(
    monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    """Close failures during open-failure cleanup cannot hold the lock or skip raises."""
    real_open = os.open
    real_close = os.close
    close_calls = [0]

    def guarded_open(path: str | bytes, flags: int, mode: int = 0o777) -> int:
        if path == os.devnull:
            raise OSError("devnull unavailable")
        return real_open(path, flags, mode)

    def flaky_close(fd: int) -> None:
        close_calls[0] += 1
        if close_calls[0] == 1:
            raise OSError("close failed")
        real_close(fd)

    before = count_open_fds()
    monkeypatch.setattr(model_server.os, "open", guarded_open)
    monkeypatch.setattr(model_server.os, "close", flaky_close)
    with pytest.raises(OSError) as excinfo, model_server._SilencedStderr():
        raise AssertionError("the body must not run")
    assert "devnull unavailable" in str(excinfo.value)
    after = count_open_fds()
    assert after == before + 1  # only the injected close failure leaks one fd
    assert_lock_is_free()
    os.write(2, b"AFTER-SILENCE-MARKER")
    captured = capfd.readouterr()
    assert "AFTER-SILENCE-MARKER" in captured.err


def test_silenced_stderr_enter_dup2_failure_with_close_failure_still_releases(
    monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    """A close failure on one fd still closes the other and releases the lock."""
    real_dup2 = os.dup2
    real_close = os.close
    redirect_attempts = [0]
    close_calls = [0]

    def flaky_dup2(src: int, dst: int) -> None:
        if dst == 2:
            redirect_attempts[0] += 1
            if redirect_attempts[0] == 1:
                raise OSError("redirect failed")
        real_dup2(src, dst)

    def flaky_close(fd: int) -> None:
        close_calls[0] += 1
        if close_calls[0] == 1:
            raise OSError("close failed")
        real_close(fd)

    before = count_open_fds()
    monkeypatch.setattr(model_server.os, "dup2", flaky_dup2)
    monkeypatch.setattr(model_server.os, "close", flaky_close)
    with pytest.raises(OSError) as excinfo, model_server._SilencedStderr():
        raise AssertionError("the body must not run")
    assert "redirect failed" in str(excinfo.value)
    after = count_open_fds()
    assert after == before + 1  # the devnull fd was still closed despite the failure
    assert_lock_is_free()
    os.write(2, b"AFTER-SILENCE-MARKER")
    captured = capfd.readouterr()
    assert "AFTER-SILENCE-MARKER" in captured.err


def test_silenced_stderr_restore_failure_is_loud_and_releases(
    monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    """An irrecoverable restore writes CRITICAL, raises, closes fds, releases the lock."""
    real_dup2 = os.dup2
    restore_attempts = [0]

    def flaky_dup2(src: int, dst: int) -> None:
        if dst == 2:
            restore_attempts[0] += 1
            if restore_attempts[0] == 2:
                raise OSError("restore failed")
        real_dup2(src, dst)

    before = count_open_fds()
    monkeypatch.setattr(model_server, "_stderr_restore_broken", False)
    monkeypatch.setattr(model_server.os, "dup2", flaky_dup2)
    with pytest.raises(OSError) as excinfo, model_server._SilencedStderr():
        pass
    assert "restore failed" in str(excinfo.value)
    after = count_open_fds()
    assert after <= before
    assert_lock_is_free()
    captured = capfd.readouterr()
    assert "CRITICAL" in captured.err
    # The failure was loud (CRITICAL + raise), but fd 2 itself stays on
    # devnull until process exit; the broken flag makes every later attempt
    # fail closed instead of silently running with poisoned stderr.
    os.write(2, b"AFTER-SILENCE-MARKER")
    captured = capfd.readouterr()
    assert "AFTER-SILENCE-MARKER" not in captured.err
    with pytest.raises(OSError), model_server._SilencedStderr():
        pass
    assert_lock_is_free()


def test_silenced_stderr_restore_failure_chains_the_body_exception(
    monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    """The raised restore error keeps the body exception as its cause."""
    real_dup2 = os.dup2
    restore_attempts = [0]

    def flaky_dup2(src: int, dst: int) -> None:
        if dst == 2:
            restore_attempts[0] += 1
            if restore_attempts[0] == 2:
                raise OSError("restore failed")
        real_dup2(src, dst)

    monkeypatch.setattr(model_server, "_stderr_restore_broken", False)
    monkeypatch.setattr(model_server.os, "dup2", flaky_dup2)
    with pytest.raises(OSError) as excinfo, model_server._SilencedStderr():
        raise RuntimeError("body boom")
    assert isinstance(excinfo.value.__cause__, RuntimeError)
    assert_lock_is_free()
    captured = capfd.readouterr()
    assert "CRITICAL" in captured.err


@pytest.mark.parametrize("failing_close", [1, 2])
def test_silenced_stderr_exit_close_failure_still_restores_and_releases(
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
    failing_close: int,
) -> None:
    """Either close failing in __exit__ still restores fd 2 and releases the lock."""
    real_close = os.close
    close_calls = [0]

    def flaky_close(fd: int) -> None:
        close_calls[0] += 1
        if close_calls[0] == failing_close:
            raise OSError("close failed")
        real_close(fd)

    before = count_open_fds()
    monkeypatch.setattr(model_server.os, "close", flaky_close)
    with model_server._SilencedStderr():
        pass
    after = count_open_fds()
    assert after == before + 1  # only the injected close failure leaks one fd
    assert_lock_is_free()
    os.write(2, b"AFTER-SILENCE-MARKER")
    captured = capfd.readouterr()
    assert "AFTER-SILENCE-MARKER" in captured.err


def neutral_board() -> BoardFeatures:
    snapshot = build_snapshot()
    return BoardFeatures(snapshot.deaths_radiant, snapshot.deaths_dire, 0, 0, 30.0, 30.0)
