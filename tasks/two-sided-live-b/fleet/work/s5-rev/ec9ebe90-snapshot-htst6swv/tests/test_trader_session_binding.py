"""Tests for `trader.session_binding`: the collector sidecar projection.

One frozen sidecar decides both the fork MarketMeta a session trades on and
the immutable identity it is bound to. These tests pin the selection, the
decimal parsing, the meta build and the binding record roundtrip, including
every fail-closed path.
"""

# The session composes private engine seams by design; pinning them is the
# point of these tests. The production session modules are the only place
# that uses them.
# pyright: reportPrivateUsage=false, reportPrivateImportUsage=false

from pathlib import Path
from typing import Any, cast

import pytest
from polymaker.domain import TokenMeta
from trader_session_fixtures import (
    CONDITION_ID,
    NO_TOKEN,
    YES_TOKEN,
    build_discovered,
    make_binding,
    sidecar_body,
    write_sidecar,
)

from trader import (
    session_binding,
    session_types,
)


def test_sidecar_refresh_never_touches_the_fork_scanner_or_gamma(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The refresh reads only local sidecars; the fork scanner is never imported."""
    archive_root = tmp_path / "archive"
    write_sidecar(archive_root, sidecar_body())

    def scanner_boom(*args: object, **kwargs: object) -> object:
        raise AssertionError("fork scanner must never run")

    monkeypatch.setattr("polymaker.catalog.scanner.run_scan", scanner_boom)

    def gamma_boom(*args: object, **kwargs: object) -> object:
        raise AssertionError("GammaClient must never be constructed")

    monkeypatch.setattr("polymaker.catalog.gamma.GammaClient", gamma_boom)

    meta = session_binding.build_sidecar_meta(build_discovered(), archive_root, make_binding())
    assert meta.meta.condition_id == CONDITION_ID


def test_market_meta_projection_uses_question_or_slug_fallback(tmp_path: Path) -> None:
    """The nullable collector question is projected; the slug is the safe fallback."""
    archive_root = tmp_path / "archive"
    write_sidecar(archive_root, sidecar_body())
    meta = session_binding.build_sidecar_meta(build_discovered(), archive_root, make_binding())
    assert meta.meta.question == "Dota 2: Aurora vs Team Secret - Game 1 Winner"
    assert meta.meta.slug == "dota2-aurora-secret-game1"
    assert meta.meta.tokens == (
        TokenMeta(YES_TOKEN, "Aurora"),
        TokenMeta(NO_TOKEN, "Team Secret"),
    )
    assert meta.meta.event_id == "808454"
    assert meta.meta.rewards_min_size == 0.0
    assert meta.meta.rewards_max_spread == 0.0
    assert meta.meta.maker_fee_bps == 0
    assert meta.meta.taker_fee_bps == 0
    assert meta.meta.fees_enabled is False
    assert meta.meta.end_date_iso is None
    write_sidecar(archive_root, sidecar_body(question=None))
    meta_without_question = session_binding.build_sidecar_meta(
        build_discovered(), archive_root, make_binding()
    )
    assert meta_without_question.meta.question == "dota2-aurora-secret-game1"


@pytest.mark.parametrize(
    "overrides, reason",
    [
        ({}, "collector sidecar unavailable"),
        ({"acceptingOrders": False}, "market is not tradeable"),
        ({"closed": True}, "market is not tradeable"),
        ({"tickSize": None}, "no usable tick or min order size"),
        ({"negRisk": True}, "negRisk does not match"),
        ({"gridSeriesId": "999"}, "gridSeriesId does not match"),
        ({"minOrderSize": None}, "no usable tick or min order size"),
        (
            {
                "outcomes": [
                    {"index": 0, "name": "Aurora", "tokenId": "TOKEN9"},
                    {"index": 1, "name": "Team Secret", "tokenId": NO_TOKEN},
                ]
            },
            "collector sidecar unavailable",
        ),
    ],
)
def test_setup_sidecar_faults_disable_only_trading(
    tmp_path: Path, overrides: dict[str, object], reason: str
) -> None:
    """Missing/changed/non-tradeable/tick-less sidecars are typed setup faults."""
    archive_root = tmp_path / "archive"
    if overrides:
        write_sidecar(archive_root, sidecar_body(**overrides))
    with pytest.raises(session_types.TradingDisabled, match=reason):
        session_binding.build_sidecar_meta(build_discovered(), archive_root, make_binding())


@pytest.mark.parametrize(
    "discovered_kwargs, sidecar_overrides, reason",
    [
        (
            {"market_kind": "map_winner"},
            {"marketKind": "series_winner", "mapNumber": None},
            "market kind does not match",
        ),
        ({"market_kind": "map_winner"}, {"mapNumber": 2}, "map number does not match"),
        (
            {"market_kind": "series_winner"},
            {"marketKind": "map_winner", "mapNumber": 1},
            "market kind does not match",
        ),
        (
            {"market_kind": "series_winner", "map_number": 2},
            {"marketKind": "series_winner", "mapNumber": None},
            "series market does not match",
        ),
        ({"market_kind": None}, {}, "carries no market kind"),
    ],
)
def test_setup_sidecar_kind_and_map_mismatches_fail_closed(
    tmp_path: Path,
    discovered_kwargs: dict[str, object],
    sidecar_overrides: dict[str, object],
    reason: str,
) -> None:
    """A changed kind/map or a legacy kind-less handoff fails before any Engine."""
    archive_root = tmp_path / "archive"
    write_sidecar(archive_root, sidecar_body(**sidecar_overrides))
    discovered = build_discovered(**cast(Any, discovered_kwargs))
    with pytest.raises(session_types.TradingDisabled, match=reason):
        session_binding.build_sidecar_meta(discovered, archive_root, make_binding())


@pytest.mark.parametrize("map_number", [1, 3, 5])
def test_series_winner_decider_maps_are_accepted(tmp_path: Path, map_number: int) -> None:
    """Match Winner handoff is accepted on BO1/BO3/BO5 last maps with a null sidecar map."""
    archive_root = tmp_path / "archive"
    write_sidecar(archive_root, sidecar_body(marketKind="series_winner", mapNumber=None))
    discovered = build_discovered(market_kind="series_winner", map_number=map_number)
    series_binding = make_binding(market_kind="series_winner", map_number=None)
    meta = session_binding.build_sidecar_meta(discovered, archive_root, series_binding)
    assert meta.meta.condition_id == CONDITION_ID


@pytest.mark.parametrize(
    "mutation",
    [
        {"negRisk": True},
        {"gridSeriesId": "999999"},
        {"eventId": "999999"},
        {
            "outcomes": [
                {"index": 0, "name": "Renamed", "tokenId": YES_TOKEN},
                {"index": 1, "name": "Team Secret", "tokenId": NO_TOKEN},
            ]
        },
        {"marketKind": "series_winner", "mapNumber": None},
        {"mapNumber": 2},
    ],
)
def test_restart_sidecar_binding_mutation_rejects_without_engine(
    tmp_path: Path, mutation: dict[str, object]
) -> None:
    """On resume the persisted binding is the baseline: a changed sidecar fails closed.

    Mutations of kind/map/grid trip the earlier handoff checks first; every
    other static mutation trips the persisted-binding comparison. Either way
    the fault is a typed TradingDisabled before any Engine exists.
    """
    archive_root = tmp_path / "archive"
    write_sidecar(archive_root, sidecar_body(**mutation))
    with pytest.raises(session_types.TradingDisabled):
        session_binding.build_sidecar_meta(build_discovered(), archive_root, make_binding())


def test_restart_with_unchanged_sidecar_accepts_the_persisted_binding(
    tmp_path: Path,
) -> None:
    """A resume whose sidecar still matches the persisted binding trades."""
    archive_root = tmp_path / "archive"
    write_sidecar(archive_root, sidecar_body())
    meta = session_binding.build_sidecar_meta(build_discovered(), archive_root, make_binding())
    assert meta.binding == make_binding()


def test_missing_provenance_binding_fails_closed(tmp_path: Path) -> None:
    """No pinned binding in the provenance: no baseline, no trading."""
    archive_root = tmp_path / "archive"
    write_sidecar(archive_root, sidecar_body())
    with pytest.raises(session_types.TradingDisabled, match="no pinned sidecar binding"):
        session_binding.build_sidecar_meta(build_discovered(), archive_root, None)


def test_non_null_grid_restart_is_accepted(tmp_path: Path) -> None:
    """A restart with an unchanged non-null grid sidecar trades (no false fault)."""
    archive_root = tmp_path / "archive"
    write_sidecar(archive_root, sidecar_body(gridSeriesId="2974458"))
    discovered = build_discovered(grid_series_id="2974458")
    binding = make_binding(grid_series_id="2974458")
    meta = session_binding.build_sidecar_meta(discovered, archive_root, binding)
    assert meta.binding.grid_series_id == "2974458"
