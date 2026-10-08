import pandas as pd

from collect.common.paths import GRID_GAME_WINDOWS_PATH, MATCH_LINKS_PATH


def admitted_link_mask(links: pd.DataFrame, windows: pd.DataFrame) -> pd.Series:
    """The dataset admission gate: GRID-windowed link OR archive-attached."""
    return (
        links["map_condition_id"].isin(set(windows["condition_id"])) | links["archive_id"].notna()
    )


def load_admitted_match_ids() -> list[int]:
    """Match ids admitted to the dataset, ordered by sort_ts."""
    windows = pd.read_parquet(GRID_GAME_WINDOWS_PATH)
    links = pd.read_parquet(MATCH_LINKS_PATH)
    admitted = links[admitted_link_mask(links, windows)]
    ordered = admitted.sort_values(["sort_ts", "match_id"])
    return ordered["match_id"].astype("int64").tolist()
