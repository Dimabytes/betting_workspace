"""Scoreboard features shared by training, replay and live inference."""

from dataclasses import dataclass

from shared.utils.match_time import NS_PER_SECOND

BOARD_LEAD_SECONDS = 8
BOARD_DEATH_AGE_CAP_SECONDS = 30
BOARD_REACTION_SECONDS = 0.11
BOARD_FEATURE_COLUMNS = (
    "pending_deaths_radiant",
    "pending_deaths_dire",
    "board_death_age_radiant_s",
    "board_death_age_dire_s",
)


@dataclass(frozen=True)
class BoardFeatures:
    deaths_radiant: int
    deaths_dire: int
    pending_deaths_radiant: int
    pending_deaths_dire: int
    board_death_age_radiant_s: float
    board_death_age_dire_s: float


def board_model_values(board: BoardFeatures) -> dict[str, float]:
    """Model deaths_* are scoreboard deaths; table deaths stay on the strategy signal."""
    return {
        "deaths_radiant": float(board.deaths_radiant),
        "deaths_dire": float(board.deaths_dire),
        "pending_deaths_radiant": float(board.pending_deaths_radiant),
        "pending_deaths_dire": float(board.pending_deaths_dire),
        "board_death_age_radiant_s": float(board.board_death_age_radiant_s),
        "board_death_age_dire_s": float(board.board_death_age_dire_s),
    }


@dataclass
class _SideDeaths:
    count: int = 0
    table_count: int | None = None
    origin_ns: int | None = None

    def record_board(self, count: int, now_ns: int) -> None:
        if count > self.count:
            self.count = count
            self.origin_ns = now_ns

    def record_table(self, count: int, now_ns: int) -> None:
        if self.table_count is not None and count > self.table_count and count > self.count:
            origin = now_ns - BOARD_LEAD_SECONDS * NS_PER_SECOND
            if self.origin_ns is None or origin > self.origin_ns:
                self.origin_ns = origin
        self.table_count = count
        self.count = max(self.count, count)

    def age_seconds(self, now_ns: int) -> float:
        if self.origin_ns is None:
            return float(BOARD_DEATH_AGE_CAP_SECONDS)
        return min((now_ns - self.origin_ns) / NS_PER_SECOND, BOARD_DEATH_AGE_CAP_SECONDS)


class BoardHistory:
    def __init__(self) -> None:
        self._radiant = _SideDeaths()
        self._dire = _SideDeaths()

    def record_board(self, now_ns: int, radiant: int, dire: int) -> None:
        self._radiant.record_board(radiant, now_ns)
        self._dire.record_board(dire, now_ns)

    def record_table(self, now_ns: int, radiant: int, dire: int) -> None:
        self._radiant.record_table(radiant, now_ns)
        self._dire.record_table(dire, now_ns)

    def derive(self, now_ns: int, table_radiant: int, table_dire: int) -> BoardFeatures:
        radiant = max(self._radiant.count, table_radiant)
        dire = max(self._dire.count, table_dire)
        return BoardFeatures(
            deaths_radiant=radiant,
            deaths_dire=dire,
            pending_deaths_radiant=radiant - table_radiant,
            pending_deaths_dire=dire - table_dire,
            board_death_age_radiant_s=self._radiant.age_seconds(now_ns),
            board_death_age_dire_s=self._dire.age_seconds(now_ns),
        )
