from shared.constants.dataset import VALIDATION_START_TIME
from shared.constants.paths import MATCH_CATALOG_PATH
from shared.utils.match_catalog import load_match_catalog
from shared.utils.match_time import get_paused_seconds_before


catalog = load_match_catalog(MATCH_CATALOG_PATH)
rows = []
for entry in catalog.values():
    if entry.spawn_at is None:
        continue
    expected = 90 + get_paused_seconds_before(entry.pauses, 0)
    actual = (entry.horn_at - entry.spawn_at).total_seconds()
    rows.append(
        (
            abs(actual - expected),
            actual - expected,
            entry.match_id,
            entry.gamma.slug,
            entry.archive_id,
            entry.start_time,
            entry.horn_at.isoformat(),
            entry.spawn_at.isoformat(),
        )
    )
for row in sorted(rows, reverse=True)[:20]:
    distance = row[5] - VALIDATION_START_TIME
    print("OUTLIER", row, "split_distance_s", distance)
print(
    "WITHIN_900S_OF_SPLIT",
    sum(abs(row[5] - VALIDATION_START_TIME) <= 900 for row in rows),
    "MAX_SPLIT_DISTANCE_WITH_HORN_ERROR_GT_1S",
    min(abs(row[5] - VALIDATION_START_TIME) for row in rows if row[0] > 1),
    "GT_1S_BY_ARCHIVE_ID",
    {
        "archive": sum(row[0] > 1 and row[4] is not None for row in rows),
        "grid_only": sum(row[0] > 1 and row[4] is None for row in rows),
    },
)
