"""Catalog loader: ensemble mean, identity, and rejection rules."""

from pathlib import Path
from typing import cast

import numpy as np
import pandas as pd
import pytest

import shared.utils.gbm as gbm_module
from shared.types.model import ModelMeta
from shared.utils.dota_features import DOTA_XP_FEATURE_COLUMNS
from shared.utils.gbm import (
    GbmPredictor,
    ModelCatalogError,
    catalog_member_names,
    catalog_member_trees,
    load_predictor,
    member_filename,
    model_identity_sha256,
)
from shared.utils.hashing import sha256_file
from shared.utils.model_registry import write_model_meta


def build_meta(**overrides: object) -> ModelMeta:
    """Minimal model.json payload; overrides replace fields."""
    values: dict[str, object] = {
        "name": "test",
        "trained_at": "2026-09-11T00:00:00Z",
        "train_dataset_sha256": "0" * 64,
        "validation_dataset_sha256": "1" * 64,
        "features": list(DOTA_XP_FEATURE_COLUMNS),
        "source_lag_seconds": 10,
        "train_matches": 1,
        "metrics": None,
        "members": ["member_00.txt"],
        "member_trees": [3],
        "ensemble_arm": "default_boot",
        "ensemble_k": 1,
        "ensemble_sampling": "boot",
    }
    values.update(overrides)
    return cast(ModelMeta, values)


class FakeBooster:
    """LightGBM stand-in that returns a fixed delta per row."""

    def __init__(self, model_file: str, delta: float) -> None:
        self.model_file = model_file
        self.delta = delta

    def feature_name(self) -> list[str]:
        return list(DOTA_XP_FEATURE_COLUMNS)

    def num_trees(self) -> int:
        return 3

    def predict(self, features: object) -> np.ndarray:
        n_rows = len(features) if isinstance(features, pd.DataFrame) else 1
        return np.full(n_rows, self.delta, dtype=np.float64)


def test_catalog_member_names_requires_members() -> None:
    """A model.json without members is a load error, not a singleton model.txt."""
    payload = dict(build_meta())
    del payload["members"]
    with pytest.raises(ModelCatalogError, match="empty"):
        catalog_member_names(cast(ModelMeta, payload))


def test_catalog_member_names_rejects_empty_duplicate_and_paths() -> None:
    """Empty list, a repeated file, and a path component are load errors."""
    with pytest.raises(ModelCatalogError, match="empty"):
        catalog_member_names(build_meta(members=[]))
    with pytest.raises(ModelCatalogError, match="repeats"):
        catalog_member_names(build_meta(members=["member_00.txt", "member_00.txt"]))
    with pytest.raises(ModelCatalogError, match="plain filename"):
        catalog_member_names(build_meta(members=["../member_00.txt"]))


def test_catalog_member_trees_requires_member_trees() -> None:
    """A model.json without member_trees is a load error, not a silent skip."""
    payload = dict(build_meta())
    del payload["member_trees"]
    with pytest.raises(ModelCatalogError, match="member_trees"):
        catalog_member_trees(cast(ModelMeta, payload))
    with pytest.raises(ModelCatalogError, match="member_trees"):
        catalog_member_trees(build_meta(member_trees=None))
    with pytest.raises(ModelCatalogError, match="member_trees"):
        catalog_member_trees(build_meta(member_trees=[]))


def test_member_filename_is_zero_padded() -> None:
    """K=10 members are member_00.txt through member_09.txt."""
    assert member_filename(0) == "member_00.txt"
    assert member_filename(9) == "member_09.txt"


def test_ensemble_identity_changes_when_a_member_is_replaced(tmp_path: Path) -> None:
    """Swapping one member file changes the catalog identity."""
    members = ["member_00.txt", "member_01.txt"]
    (tmp_path / "member_00.txt").write_text("a")
    (tmp_path / "member_01.txt").write_text("b")
    write_model_meta(build_meta(members=members), tmp_path / "model.json")
    first = model_identity_sha256(tmp_path)
    (tmp_path / "member_01.txt").write_text("c")
    second = model_identity_sha256(tmp_path)
    assert first != second
    assert first != sha256_file(tmp_path / "member_00.txt")


def test_ensemble_identity_changes_when_features_change(tmp_path: Path) -> None:
    """Predict metadata is part of ensemble identity, not only the member bytes."""
    members = ["member_00.txt"]
    (tmp_path / "member_00.txt").write_text("a")
    write_model_meta(build_meta(members=members), tmp_path / "model.json")
    first = model_identity_sha256(tmp_path)
    rotated = [*DOTA_XP_FEATURE_COLUMNS[1:], DOTA_XP_FEATURE_COLUMNS[0]]
    write_model_meta(build_meta(members=members, features=rotated), tmp_path / "model.json")
    assert model_identity_sha256(tmp_path) != first


def test_load_predictor_rejects_missing_member_trees(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A catalog without member_trees does not load."""

    def build_booster(model_file: str) -> FakeBooster:
        return FakeBooster(model_file, 0.01)

    monkeypatch.setattr(gbm_module.lgb, "Booster", build_booster)
    payload = dict(build_meta(members=["member_00.txt"]))
    del payload["member_trees"]
    write_model_meta(cast(ModelMeta, payload), tmp_path / "model.json")
    (tmp_path / "member_00.txt").write_text("a")
    with pytest.raises(ModelCatalogError, match="member_trees"):
        load_predictor(tmp_path)


def test_load_predictor_rejects_member_trees_count_mismatch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """members and member_trees must be the same length before any booster is opened."""

    def build_booster(model_file: str) -> FakeBooster:
        return FakeBooster(model_file, 0.01)

    monkeypatch.setattr(gbm_module.lgb, "Booster", build_booster)
    write_model_meta(
        build_meta(members=["member_00.txt", "member_01.txt"], member_trees=[3]),
        tmp_path / "model.json",
    )
    (tmp_path / "member_00.txt").write_text("a")
    (tmp_path / "member_01.txt").write_text("b")
    with pytest.raises(ModelCatalogError, match="member_trees do not match members"):
        load_predictor(tmp_path)


def test_load_predictor_rejects_member_trees_mismatch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Recorded member_trees must match the boosters on disk."""

    def build_booster(model_file: str) -> FakeBooster:
        return FakeBooster(model_file, 0.01)

    monkeypatch.setattr(gbm_module.lgb, "Booster", build_booster)
    write_model_meta(
        build_meta(members=["member_00.txt"], member_trees=[99]),
        tmp_path / "model.json",
    )
    (tmp_path / "member_00.txt").write_text("a")
    with pytest.raises(ModelCatalogError, match="member_trees"):
        load_predictor(tmp_path)


def test_load_predictor_rejects_missing_member(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A listed member that is not on disk fails closed; no partial mean."""

    def build_booster(model_file: str) -> FakeBooster:
        return FakeBooster(model_file, 0.01)

    monkeypatch.setattr(gbm_module.lgb, "Booster", build_booster)
    write_model_meta(
        build_meta(
            members=["member_00.txt", "member_01.txt"],
            member_trees=[3, 3],
        ),
        tmp_path / "model.json",
    )
    (tmp_path / "member_00.txt").write_text("a")
    with pytest.raises(ModelCatalogError, match="ensemble member is missing"):
        load_predictor(tmp_path)


def test_load_predictor_does_not_invent_model_txt_for_ensemble(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An ensemble catalog is valid without model.txt."""
    seen: list[str] = []

    def build_booster(model_file: str) -> FakeBooster:
        seen.append(model_file)
        return FakeBooster(model_file, 0.01)

    monkeypatch.setattr(gbm_module.lgb, "Booster", build_booster)
    write_model_meta(build_meta(members=["member_00.txt"]), tmp_path / "model.json")
    (tmp_path / "member_00.txt").write_text("member")
    (tmp_path / "model.txt").write_text("should-not-load")
    predictor = load_predictor(tmp_path)
    assert seen == [str(tmp_path / "member_00.txt")]
    assert predictor.member_names == ("member_00.txt",)


def test_ensemble_predict_is_the_mean(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Two members average their deltas; a bank.json sibling is ignored."""
    deltas = {str(tmp_path / "member_00.txt"): 0.10, str(tmp_path / "member_01.txt"): 0.20}

    def build_booster(model_file: str) -> FakeBooster:
        return FakeBooster(model_file, deltas[model_file])

    monkeypatch.setattr(gbm_module.lgb, "Booster", build_booster)
    write_model_meta(
        build_meta(members=["member_00.txt", "member_01.txt"], member_trees=[3, 3]),
        tmp_path / "model.json",
    )
    (tmp_path / "member_00.txt").write_text("a")
    (tmp_path / "member_01.txt").write_text("b")
    (tmp_path / "model.txt").write_text("ignored")
    (tmp_path / "bank.json").write_text("{}")
    predictor = load_predictor(tmp_path)
    features = pd.DataFrame([{column: 0.0 for column in DOTA_XP_FEATURE_COLUMNS}])
    assert predictor.predict(features).tolist() == pytest.approx([0.15])
    assert isinstance(predictor, GbmPredictor)
