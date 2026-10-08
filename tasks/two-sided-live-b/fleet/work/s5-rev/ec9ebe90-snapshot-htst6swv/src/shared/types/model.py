from typing import TypedDict


class ModelMetrics(TypedDict):
    """Validation metrics stored with one trained model."""

    trees: int
    rows: int
    future_300_n: int
    no_move_mae_300_cents: float
    model_mae_300_cents: float
    mae_gain_300_cents: float
    mae_gain_300_ci_low_cents: float
    mae_gain_300_ci_high_cents: float
    model_bias_300_cents: float
    dir_300_cents: float


class ModelMeta(TypedDict):
    """JSON metadata stored beside one trained ensemble catalog."""

    name: str
    trained_at: str
    train_dataset_sha256: str
    validation_dataset_sha256: str | None
    features: list[str]
    source_lag_seconds: int
    train_matches: int
    # None for a production fit: it trains on every match, so there is no holdout.
    metrics: ModelMetrics | None
    members: list[str]
    member_trees: list[int]
    ensemble_arm: str
    ensemble_k: int
    ensemble_sampling: str
