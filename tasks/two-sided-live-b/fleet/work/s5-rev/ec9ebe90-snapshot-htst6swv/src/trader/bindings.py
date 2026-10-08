"""Immutable trader bindings: discovery output and match.json start context."""

from dataclasses import dataclass, field

from trader.collector_sidecars import MarketKind


@dataclass(frozen=True)
class TeamSides:
    """The two team names: Radiant and Dire."""

    radiant: str
    dire: str


@dataclass(frozen=True)
class MarketReference:
    """The Polymarket market this match is traded on; decimals stay strings.

    Sidecar tick/min size are compare=False: they move mid-map.
    """

    condition_id: str
    market_slug: str
    event_slug: str
    yes_token_id: str
    no_token_id: str
    yes_is_radiant: bool
    outcome_0_name: str
    outcome_1_name: str
    tick_size: str | None = field(compare=False)
    min_order_size: str | None = field(compare=False)
    neg_risk: bool
    grid_series_id: str | None


@dataclass(frozen=True)
class ModelReference:
    """The pinned model identity from model.json, resolved by the session owner."""

    name: str
    trained_at: str


@dataclass(frozen=True)
class DiscoveredMatch:
    """One linked market. `game` is the GameProfile key and is required.

    Dynamic fields are compare=False so match.json resume stays valid.
    """

    match_id: str
    game: str
    steam_match_id: str | None
    league_id: int | None
    tournament: str | None
    sides: TeamSides
    map_number: int
    market: MarketReference
    market_kind: MarketKind | None = field(default=None, compare=False, kw_only=True)
    oddin_match_id: str | None = field(default=None, compare=False, kw_only=True)
    oddin_delay_s: int | None = field(default=None, compare=False, kw_only=True)
    record_only: bool = field(default=False, compare=False, kw_only=True)

    def with_model(self, model: ModelReference) -> "MatchStart":
        """Pin the model identity onto this discovery result."""
        return MatchStart(
            match_id=self.match_id,
            game=self.game,
            steam_match_id=self.steam_match_id,
            league_id=self.league_id,
            tournament=self.tournament,
            sides=self.sides,
            map_number=self.map_number,
            market=self.market,
            market_kind=self.market_kind,
            oddin_match_id=self.oddin_match_id,
            oddin_delay_s=self.oddin_delay_s,
            record_only=self.record_only,
            model=model,
        )


@dataclass(frozen=True)
class MatchStart(DiscoveredMatch):
    """Immutable discovery/session context for one tracked match."""

    model: ModelReference


@dataclass(frozen=True)
class SessionPnl:
    """The session's engine PnL handoff at match end, in USDC."""

    realized_pnl_usdc: float
    unrealized_pnl_usdc: float
