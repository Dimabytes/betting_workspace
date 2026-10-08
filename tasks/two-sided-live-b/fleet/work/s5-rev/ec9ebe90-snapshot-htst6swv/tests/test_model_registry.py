import json
from pathlib import Path

from shared.types.model import ModelMeta, ModelMetrics
from shared.utils.model_registry import (
    publish_model_dir,
    read_model_meta,
    staging_model_dir,
    write_model_meta,
)


def build_model_meta(name: str) -> ModelMeta:
    """Build the metadata fields needed by directory rotation."""
    return ModelMeta(
        name=name,
        trained_at="2026-08-13T23:37:31Z",
        train_dataset_sha256="0" * 64,
        validation_dataset_sha256="1" * 64,
        features=[],
        source_lag_seconds=8,
        train_matches=1,
        metrics=ModelMetrics(
            trees=1,
            rows=1,
            future_300_n=1,
            no_move_mae_300_cents=0.0,
            model_mae_300_cents=0.0,
            mae_gain_300_cents=0.0,
            mae_gain_300_ci_low_cents=0.0,
            mae_gain_300_ci_high_cents=0.0,
            model_bias_300_cents=0.0,
            dir_300_cents=0.0,
        ),
        members=["member_00.txt"],
        member_trees=[1],
        ensemble_arm="default_boot",
        ensemble_k=1,
        ensemble_sampling="boot",
    )


def build_live_dir(models_dir: Path, name: str, model_text: str) -> Path:
    """Create a live model dir holding one metadata, model, and split file."""
    live_dir = models_dir / "production"
    live_dir.mkdir(parents=True)
    write_model_meta(build_model_meta(name), live_dir / "model.json")
    (live_dir / "member_00.txt").write_text(model_text)
    (live_dir / "split.parquet").write_bytes(b"split")
    return live_dir


def write_staging(live_dir: Path, model_text: str) -> Path:
    """Fill a staging dir for live_dir with a complete model triple."""
    staging_dir = staging_model_dir(live_dir)
    write_model_meta(build_model_meta("20260901T000000Z"), staging_dir / "model.json")
    (staging_dir / "member_00.txt").write_text(model_text)
    (staging_dir / "split.parquet").write_bytes(b"next-split")
    return staging_dir


def test_publish_model_dir_archives_the_replaced_model(tmp_path: Path) -> None:
    """The replaced live dir moves into the archive; staging becomes the live dir."""
    models_dir = tmp_path / "models"
    archive_dir = models_dir / "archive" / "production"
    live_dir = build_live_dir(models_dir, "20260813T233731Z", "old-model")
    staging_dir = write_staging(live_dir, "new-model")

    archived_name = publish_model_dir(staging_dir, live_dir, archive_dir)

    assert archived_name == "20260813T233731Z"
    archived_dir = archive_dir / archived_name
    assert (archived_dir / "member_00.txt").read_text() == "old-model"
    assert (archived_dir / "split.parquet").read_bytes() == b"split"
    assert read_model_meta(archived_dir / "model.json")["name"] == archived_name
    assert (live_dir / "member_00.txt").read_text() == "new-model"
    assert read_model_meta(live_dir / "model.json")["name"] == "20260901T000000Z"
    assert not staging_dir.exists()


def test_publish_model_dir_suffixes_an_existing_archive(tmp_path: Path) -> None:
    """A second publish of the same named snapshot archives beside it, deleting nothing."""
    models_dir = tmp_path / "models"
    archive_dir = models_dir / "archive" / "production"
    taken_dir = archive_dir / "20260813T233731Z"
    taken_dir.mkdir(parents=True)
    (taken_dir / "member_00.txt").write_text("archived")
    live_dir = build_live_dir(models_dir, "20260813T233731Z", "second-model")

    archived_name = publish_model_dir(write_staging(live_dir, "new"), live_dir, archive_dir)

    assert archived_name == "20260813T233731Z_2"
    assert (taken_dir / "member_00.txt").read_text() == "archived"
    assert (archive_dir / "20260813T233731Z_2" / "member_00.txt").read_text() == "second-model"


def test_publish_model_dir_counts_up_past_a_taken_suffix(tmp_path: Path) -> None:
    """A third publish of the same name lands on _3, not back on _2."""
    models_dir = tmp_path / "models"
    archive_dir = models_dir / "archive" / "production"
    for name in ("20260813T233731Z", "20260813T233731Z_2"):
        (archive_dir / name).mkdir(parents=True)
    live_dir = build_live_dir(models_dir, "20260813T233731Z", "third-model")

    archived_name = publish_model_dir(write_staging(live_dir, "new"), live_dir, archive_dir)

    assert archived_name == "20260813T233731Z_3"
    assert (archive_dir / "20260813T233731Z_3" / "member_00.txt").read_text() == "third-model"


def test_staging_model_dir_drops_a_leftover_staging_dir(tmp_path: Path) -> None:
    """A staging dir left by a failed run is replaced by an empty one."""
    live_dir = tmp_path / "production"
    live_dir.mkdir()
    leftover = tmp_path / ".next-production"
    leftover.mkdir()
    (leftover / "member_00.txt").write_text("half-written")

    staging_dir = staging_model_dir(live_dir)

    assert staging_dir == leftover
    assert list(staging_dir.iterdir()) == []


def test_publish_model_dir_creates_the_first_live_dir(tmp_path: Path) -> None:
    """A missing live dir is a rename of staging; nothing is archived."""
    models_dir = tmp_path / "models"
    archive_dir = models_dir / "archive" / "production-noxp"
    live_dir = models_dir / "production-noxp"
    staging_dir = models_dir / ".next-production-noxp"
    staging_dir.mkdir(parents=True)
    write_model_meta(build_model_meta("20260919T000000Z"), staging_dir / "model.json")
    (staging_dir / "member_00.txt").write_text("first")

    archived_name = publish_model_dir(staging_dir, live_dir, archive_dir)

    assert archived_name is None
    assert (live_dir / "member_00.txt").read_text() == "first"
    assert read_model_meta(live_dir / "model.json")["name"] == "20260919T000000Z"
    assert not staging_dir.exists()
    assert not archive_dir.exists()


def test_a_failure_before_publish_leaves_the_live_dir_intact(tmp_path: Path) -> None:
    """Writing staging never touches the live pair, so a crash keeps the old model loadable."""
    models_dir = tmp_path / "models"
    live_dir = build_live_dir(models_dir, "20260813T233731Z", "old-model")

    staging_dir = staging_model_dir(live_dir)
    (staging_dir / "member_00.txt").write_text("half-written")

    assert (live_dir / "member_00.txt").read_text() == "old-model"
    assert read_model_meta(live_dir / "model.json")["name"] == "20260813T233731Z"


def test_model_meta_round_trips_through_json(tmp_path: Path) -> None:
    """Read and write metadata through the shared JSON helpers."""
    path = tmp_path / "model.json"
    meta = build_model_meta("test")

    write_model_meta(meta, path)

    assert json.loads(path.read_text()) == meta
    assert read_model_meta(path)["name"] == "test"
