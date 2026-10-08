"""Parallel member fits: order, reproducibility, and the boosters surviving the pool."""

from typing import cast

import lightgbm as lgb
import numpy as np
import pandas as pd
import pytest

import shared.utils.gbm as gbm
from shared.utils.dota_features import DOTA_XP_FEATURE_COLUMNS
from shared.utils.gbm import (
    ENSEMBLE_K,
    LGB_PARAMS,
    MEMBER_THREADS,
    ProductionMemberFit,
    fit_production_members,
    fit_research_members,
)


def build_train_frame() -> pd.DataFrame:
    """Tiny multi-match frame with the columns the member fit reads."""
    rng = np.random.default_rng(7)
    matches = 40
    rows = matches * 60
    frame = pd.DataFrame({column: rng.normal(size=rows) for column in DOTA_XP_FEATURE_COLUMNS})
    frame["match_id"] = np.repeat(np.arange(matches, dtype=np.int64), rows // matches)
    frame["market_p_radiant"] = 0.5
    frame["signal_market_p_radiant_300s"] = 0.5 + rng.normal(scale=0.01, size=rows)
    return frame


def test_production_members_are_ordered_and_reproducible() -> None:
    """Two pool runs give K members in the same order with identical bytes."""
    frame = build_train_frame()
    first = fit_production_members(frame, 3, DOTA_XP_FEATURE_COLUMNS)
    second = fit_production_members(frame, 3, DOTA_XP_FEATURE_COLUMNS)

    assert len(first) == ENSEMBLE_K
    assert [booster.num_trees() for booster in first] == [3] * ENSEMBLE_K
    first_bytes = [booster.model_to_string() for booster in first]
    assert first_bytes == [booster.model_to_string() for booster in second]
    assert len(set(first_bytes)) > 1, "per-member bootstrap seeds must differ"


def test_research_members_predict_after_the_pool_round_trip() -> None:
    """A booster returned by a worker still predicts in the parent process."""
    frame = build_train_frame()
    boosters = fit_research_members(
        frame,
        frame[DOTA_XP_FEATURE_COLUMNS],
        (frame["signal_market_p_radiant_300s"] - frame["market_p_radiant"]).to_numpy(
            dtype=np.float64
        ),
        DOTA_XP_FEATURE_COLUMNS,
    )

    assert len(boosters) == ENSEMBLE_K
    predictions = boosters[0].predict(frame[DOTA_XP_FEATURE_COLUMNS])
    assert np.asarray(predictions).shape == (len(frame),)


def test_member_fits_pass_one_thread_and_the_delta_label(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both member fits call LightGBM with the live params, one thread, and the 300s delta."""
    frame = build_train_frame()
    seen_params: list[dict[str, object]] = []
    seen_labels: list[np.ndarray] = []
    seen_kwargs: list[dict[str, object]] = []

    def record_dataset(_features: pd.DataFrame, label: np.ndarray, **_kw: object) -> lgb.Dataset:
        """Capture the label and return a stand-in dataset."""
        seen_labels.append(label)
        return cast(lgb.Dataset, object())

    def record_train(
        params: dict[str, object], _data: lgb.Dataset, **kwargs: object
    ) -> lgb.Booster:
        """Capture the params and boosting kwargs."""
        seen_params.append(params)
        seen_kwargs.append(kwargs)
        return cast(lgb.Booster, object())

    monkeypatch.setattr(gbm.lgb, "Dataset", record_dataset)
    monkeypatch.setattr(gbm.lgb, "train", record_train)

    ProductionMemberFit(7, tuple(DOTA_XP_FEATURE_COLUMNS))(frame)

    assert seen_params == [{**LGB_PARAMS, "num_threads": MEMBER_THREADS}]
    assert seen_kwargs == [{"num_boost_round": 7}]
    expected = frame["signal_market_p_radiant_300s"] - frame["market_p_radiant"]
    assert np.allclose(seen_labels[0], expected.to_numpy(dtype=np.float64))
