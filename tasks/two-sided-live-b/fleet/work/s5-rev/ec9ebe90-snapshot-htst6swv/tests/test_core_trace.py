"""Live core_trace.jsonl codec, writer, and offline replay."""

# pyright: reportPrivateUsage=false

import json
from dataclasses import replace
from pathlib import Path
from typing import cast, get_args

import pytest
from adapter_contract_fixtures import (
    DEFAULT_CASH,
    PINNED_LEVEL_USDC,
    LiveDriver,
    TapeCancel,
    TapeEvent,
    TapeFill,
    TapePlaceMissing,
    TapePlaceOk,
    TapePlaceRejected,
    TapeRecovery,
    TapeRecoveryVerified,
    TapeWake,
    open_books,
    open_clock,
    open_signal,
    pinned_policy,
)
from trader_session_fixtures import NO_TOKEN, YES_TOKEN, build_attached_worker

from shared.constants.strategy import (
    LATCH_REANCHOR_OFF_S,
    LIVE_DOTA_MAX_POSITION_LEVELS,
    ORDER_CANCEL_LATENCY_MS,
)
from shared.utils.match_time import NS_PER_SECOND
from strategy.policy import Follow300Policy
from strategy.types import (
    BookPair,
    BookUpdate,
    Budget,
    BudgetUpdate,
    BuySettled,
    CancelAck,
    CancelOrder,
    CancelTimeout,
    CancelUnsettled,
    ClockUpdate,
    Fill,
    FreshnessLimits,
    GameClock,
    InboundEvent,
    KeepOrder,
    KillGate,
    KillGateUpdate,
    KillWait,
    LimitsUpdate,
    MarketLimits,
    Merged,
    MoveOrder,
    OrderAccepted,
    OrderRejected,
    OwnershipResolved,
    Permissions,
    PermissionsUpdate,
    PlaceOrder,
    Plan,
    RawDeltaSignal,
    Recovery,
    RecoveryVerified,
    SettlementStatus,
    SignalUpdate,
    SubmitTimeout,
    TokenBook,
    TokenInventory,
    Wake,
)
from trader.core_trace import CoreTrace, try_open_trace
from trader.core_trace_codec import (
    CORE_TRACE_SCHEMA_VERSION,
    DIGEST_GROUPS,
    EVENT_DECODERS,
    TraceHeader,
    decode_event,
    decode_plan,
    decode_policy,
    decode_signal,
    decode_token_book,
    digest_state,
    encode_event,
    encode_plan,
    jsonable,
    policy_sha,
)
from trader.paths import CORE_TRACE_FILENAME
from trader.replay_core_trace import replay_trace
from trader.session_core import LiveCore
from trader.strict_json import StrictJsonError

CANCEL_LATENCY_NS = round(ORDER_CANCEL_LATENCY_MS * 1_000_000)
SETTLE_NS = 10 * NS_PER_SECOND


def _limits() -> MarketLimits:
    return MarketLimits(
        min_order_size=5.0, tick_size=0.01, pair_sum_tolerance=0.05, radiant_token_index=0
    )


def _freshness() -> FreshnessLimits:
    return FreshnessLimits(book_stale_s=5.0, entry_stale_s=16.0, exit_stale_s=45.0)


def _books() -> BookPair:
    return BookPair(
        tokens=(
            TokenBook(0, 0.50, 0.52, 10.0, 10.0, 1),
            TokenBook(1, 0.48, 0.50, 10.0, 10.0, 1),
        )
    )


def _signal() -> RawDeltaSignal:
    return RawDeltaSignal(
        predicted_delta=0.05,
        source_received_ns=1,
        received_ns=1,
        anchor_p=0.51,
        deaths_radiant=3,
        deaths_dire=2,
    )


def _clock() -> GameClock:
    return GameClock(now_ns=1, game_second=0, paused=False, game_ended=False)


def _inventory() -> tuple[TokenInventory, TokenInventory]:
    return (
        TokenInventory(0, 10.0, 4.0, 1),
        TokenInventory(1, 0.0, 0.0, None),
    )


def _sample_events() -> tuple[InboundEvent, ...]:
    books = _books()
    signal = _signal()
    clock = _clock()
    limits = _limits()
    perms = Permissions(False, False, True, True, False)
    budget = Budget(cash_usdc=100.0, cap_room_usdc=float("inf"), account_cap_room_usdc=float("inf"))
    inventory = _inventory()
    return (
        BookUpdate(1, books),
        BookUpdate(1, None),
        SignalUpdate(1, signal),
        SignalUpdate(1, None),
        KillGateUpdate(
            1,
            KillGate(
                radiant=KillWait(awaited_deaths=4, until_ns=11 * NS_PER_SECOND),
                dire=KillWait(awaited_deaths=2, until_ns=1),
            ),
        ),
        ClockUpdate(1, clock),
        PermissionsUpdate(1, perms),
        BudgetUpdate(1, budget),
        LimitsUpdate(1, limits),
        Wake(1, True),
        Wake(1, False),
        Fill(1, "f1", "c0", 5.0, 0.50, 0, "BUY"),
        OrderAccepted(1, "c0"),
        OrderRejected(1, "c0", "post-only"),
        SubmitTimeout(1, "c0"),
        CancelAck(1, "c0"),
        CancelTimeout(1, "c0"),
        CancelUnsettled(1, "c0"),
        BuySettled(1, "c0", 0.0),
        BuySettled(1, "c0", 5.0),
        Recovery(1, ("c0",)),
        RecoveryVerified(1, 1, inventory),
        OwnershipResolved(1, "c0", 1, 0, "BUY", 0.50, 200.0, 5.0, 0, False),
        SettlementStatus(1, "f1", "matched"),
        Merged(1, 0, 20.0),
    )


def _sample_plan() -> Plan:
    return Plan(
        keep=(KeepOrder("c0", 0),),
        moves=(MoveOrder("c1", 1, 0),),
        cancels=(CancelOrder("c2", "stale_signal"),),
        places=(PlaceOrder("c3", 1, 0, "BUY", 0.49, 132.65, 1, False),),
        block_reason="no_cash",
    )


def _header(policy: Follow300Policy) -> TraceHeader:
    return TraceHeader(
        session_id="0xcond",
        match_id="1",
        game="dota",
        execution_mode="live",
        git_commit="test",
        policy=policy,
        policy_sha=policy_sha(policy, _limits(), _freshness()),
        limits=_limits(),
        freshness=_freshness(),
        model_name="tape",
        model_trained_at="test",
        model_sha256="0" * 64,
        yes_token=YES_TOKEN,
        no_token=NO_TOKEN,
    )


def _open(*, now_ns: int = 0) -> tuple[TapeEvent, ...]:
    return (
        open_books(now_ns=now_ns),
        open_signal(now_ns=now_ns),
        open_clock(now_ns=now_ns),
        TapeWake(now_ns=now_ns),
    )


def _contract_tapes() -> tuple[tuple[str, tuple[TapeEvent, ...], float], ...]:
    return (
        (
            "ladder_open",
            (*_open(), TapePlaceOk(now_ns=0, refs=(0, 1, 2)), TapeWake(now_ns=NS_PER_SECOND)),
            DEFAULT_CASH,
        ),
        (
            "partial_buy_fill_then_replace",
            (
                *_open(),
                TapePlaceOk(now_ns=0, refs=(0, 1, 2)),
                TapeFill(now_ns=0, ref=0, qty=40.0, price=0.50),
                open_books(now_ns=NS_PER_SECOND),
                open_signal(now_ns=NS_PER_SECOND),
                open_clock(now_ns=NS_PER_SECOND),
                TapeWake(now_ns=NS_PER_SECOND),
                TapeCancel(now_ns=NS_PER_SECOND + CANCEL_LATENCY_NS, ref=0, ok=True),
            ),
            DEFAULT_CASH,
        ),
        (
            "fill_then_settle_then_sell",
            (
                *_open(),
                TapePlaceOk(now_ns=0, refs=(0, 1, 2)),
                TapeFill(now_ns=0, ref=0, qty=200.0, price=0.50),
                open_books(now_ns=SETTLE_NS),
                open_signal(now_ns=SETTLE_NS),
                open_clock(now_ns=SETTLE_NS),
                TapeWake(now_ns=SETTLE_NS),
            ),
            DEFAULT_CASH,
        ),
        (
            "new_buy_fill_during_settle",
            (
                *_open(),
                TapePlaceOk(now_ns=0, refs=(0, 1, 2)),
                TapeFill(now_ns=0, ref=0, qty=200.0, price=0.50),
                open_books(now_ns=SETTLE_NS),
                open_signal(now_ns=SETTLE_NS),
                open_clock(now_ns=SETTLE_NS),
                TapeWake(now_ns=SETTLE_NS),
                TapePlaceOk(now_ns=SETTLE_NS, refs=(0,)),
                TapeFill(now_ns=SETTLE_NS + NS_PER_SECOND, ref=1, qty=50.0, price=0.49),
                TapeWake(now_ns=SETTLE_NS + NS_PER_SECOND),
            ),
            DEFAULT_CASH,
        ),
        (
            "fill_cancel_race",
            (
                *_open(),
                TapePlaceOk(now_ns=0, refs=(0, 1, 2)),
                open_books(now_ns=NS_PER_SECOND),
                open_signal(now_ns=NS_PER_SECOND),
                open_clock(now_ns=NS_PER_SECOND),
                TapeWake(now_ns=NS_PER_SECOND),
                TapeFill(now_ns=NS_PER_SECOND + 40_000_000, ref=0, qty=5.0, price=0.50),
                TapeCancel(now_ns=NS_PER_SECOND + CANCEL_LATENCY_NS, ref=0, ok=True),
            ),
            DEFAULT_CASH,
        ),
        (
            "rung_move_keeps_queue",
            (
                *_open(),
                TapePlaceOk(now_ns=0, refs=(0, 1, 2)),
                TapeFill(now_ns=0, ref=0, qty=200.0, price=0.50),
                open_books(now_ns=NS_PER_SECOND),
                open_signal(now_ns=NS_PER_SECOND),
                open_clock(now_ns=NS_PER_SECOND),
                TapeWake(now_ns=NS_PER_SECOND),
            ),
            DEFAULT_CASH,
        ),
        (
            "cancel_timeout",
            (
                *_open(),
                TapePlaceOk(now_ns=0, refs=(0, 1, 2)),
                open_books(now_ns=NS_PER_SECOND),
                open_signal(now_ns=NS_PER_SECOND),
                open_clock(now_ns=NS_PER_SECOND),
                TapeWake(now_ns=NS_PER_SECOND),
                TapeCancel(now_ns=NS_PER_SECOND + CANCEL_LATENCY_NS, ref=0, ok=False),
                TapeWake(now_ns=2 * NS_PER_SECOND),
            ),
            DEFAULT_CASH,
        ),
        (
            "submit_unknown",
            (*_open(), TapePlaceMissing(now_ns=0, refs=(1,)), TapePlaceOk(now_ns=0, refs=(0, 2))),
            DEFAULT_CASH,
        ),
        (
            "place_rejected",
            (
                *_open(),
                TapePlaceRejected(now_ns=0, refs=(1,)),
                TapePlaceOk(now_ns=0, refs=(0, 2)),
                TapeWake(now_ns=NS_PER_SECOND),
            ),
            DEFAULT_CASH,
        ),
        (
            "mixed_buy_sell",
            (
                *_open(),
                TapePlaceOk(now_ns=0, refs=(0, 1, 2)),
                TapeFill(now_ns=0, ref=0, qty=200.0, price=0.50),
                open_books(now_ns=SETTLE_NS),
                open_signal(now_ns=SETTLE_NS),
                open_clock(now_ns=SETTLE_NS),
                TapeWake(now_ns=SETTLE_NS),
            ),
            DEFAULT_CASH,
        ),
        (
            "matched_then_confirmed",
            (
                *_open(),
                TapePlaceOk(now_ns=0, refs=(0, 1, 2)),
                TapeFill(now_ns=0, ref=0, qty=40.0, price=0.50),
                TapeFill(now_ns=0, ref=0, qty=40.0, price=0.50),
            ),
            DEFAULT_CASH,
        ),
        (
            "recovery_unwinds",
            (
                *_open(),
                TapePlaceOk(now_ns=0, refs=(0, 1, 2)),
                TapeFill(now_ns=0, ref=0, qty=40.0, price=0.50),
                TapeRecovery(now_ns=NS_PER_SECOND),
            ),
            DEFAULT_CASH,
        ),
        (
            "recovery_verified_exit",
            (
                *_open(),
                TapePlaceOk(now_ns=0, refs=(0, 1, 2)),
                TapeCancel(now_ns=NS_PER_SECOND, ref=0, ok=False),
                TapeFill(now_ns=NS_PER_SECOND, ref=0, qty=40.0, price=0.50),
                TapeRecovery(now_ns=NS_PER_SECOND),
                TapeCancel(now_ns=2 * NS_PER_SECOND, ref=1, ok=True),
                TapeCancel(now_ns=2 * NS_PER_SECOND, ref=2, ok=True),
                TapeCancel(now_ns=2 * NS_PER_SECOND, ref=0, ok=True),
                TapeRecoveryVerified(
                    now_ns=3 * NS_PER_SECOND,
                    generation=1,
                    verified_qty=40.0,
                    verified_token_index=0,
                    last_buy_ns=NS_PER_SECOND,
                ),
                open_books(now_ns=11 * NS_PER_SECOND),
                TapeWake(now_ns=11 * NS_PER_SECOND),
                TapePlaceOk(now_ns=11 * NS_PER_SECOND, refs=(0,)),
                TapeFill(now_ns=11 * NS_PER_SECOND, ref=3, qty=40.0, price=0.52),
                TapeRecoveryVerified(
                    now_ns=11 * NS_PER_SECOND,
                    generation=2,
                    verified_qty=0.0,
                    verified_token_index=None,
                    last_buy_ns=None,
                ),
                TapeWake(now_ns=12 * NS_PER_SECOND),
            ),
            DEFAULT_CASH,
        ),
        (
            "short_budget",
            (*_open(), TapePlaceOk(now_ns=0, refs=(0, 1)), TapeWake(now_ns=NS_PER_SECOND)),
            220.0,
        ),
    )


def _record_tape(path: Path, tape: tuple[TapeEvent, ...], *, cash: float) -> LiveDriver:
    policy = pinned_policy()
    trace = CoreTrace.open(path)
    trace.write_header(_header(policy))
    live = LiveDriver(cash=cash, policy=policy, trace=trace)
    for event in tape:
        live.feed(event)
    live._core.detach_trace()
    return live


def test_codec_round_trip() -> None:
    for event in _sample_events():
        encoded = encode_event(event)
        assert decode_event(encoded) == event
    plan = _sample_plan()
    assert decode_plan(encode_plan(plan)) == plan


def test_decode_unsettled_events_reject_bad_fields() -> None:
    with pytest.raises(StrictJsonError, match="CancelUnsettled keys"):
        decode_event({"type": "CancelUnsettled", "now_ns": 1})
    with pytest.raises(StrictJsonError, match="CancelUnsettled keys"):
        decode_event({"type": "CancelUnsettled", "now_ns": 1, "order_id": "c0", "extra": 1})
    with pytest.raises(StrictJsonError, match="BuySettled keys"):
        decode_event({"type": "BuySettled", "now_ns": 1, "order_id": "c0"})
    with pytest.raises(StrictJsonError, match="BuySettled keys"):
        decode_event(
            {"type": "BuySettled", "now_ns": 1, "order_id": "c0", "matched_qty": 1.0, "extra": 1}
        )
    with pytest.raises(StrictJsonError, match="matched_qty"):
        decode_event({"type": "BuySettled", "now_ns": 1, "order_id": "c0", "matched_qty": "3"})


def test_decode_policy_defaults_missing_max_entry_price() -> None:
    policy = pinned_policy()
    encoded = jsonable(policy)
    assert isinstance(encoded, dict)
    fields = cast(dict[str, object], encoded)
    current = decode_policy(fields, "policy")
    assert current.max_entry_price == policy.max_entry_price
    archived = {key: value for key, value in fields.items() if key != "max_entry_price"}
    decoded = decode_policy(archived, "policy")
    assert decoded.max_entry_price == 1.0


def test_decode_policy_defaults_missing_exit_abs_delta() -> None:
    policy = pinned_policy()
    encoded = jsonable(policy)
    assert isinstance(encoded, dict)
    fields = cast(dict[str, object], encoded)
    archived = {key: value for key, value in fields.items() if key != "exit_abs_delta"}
    decoded = decode_policy(archived, "policy")
    assert decoded.exit_abs_delta == decoded.min_abs_delta


def test_decode_policy_ignores_retired_lonely_l0() -> None:
    policy = pinned_policy()
    encoded = jsonable(policy)
    assert isinstance(encoded, dict)
    fields = cast(dict[str, object], encoded)
    decoded = decode_policy({**fields, "lonely_l0_s": 3.0}, "policy")
    assert decoded == policy


def test_decode_policy_defaults_missing_latch_reanchor_s() -> None:
    policy = pinned_policy()
    encoded = jsonable(policy)
    assert isinstance(encoded, dict)
    fields = cast(dict[str, object], encoded)
    archived = {key: value for key, value in fields.items() if key != "latch_reanchor_s"}
    decoded = decode_policy(archived, "policy")
    assert decoded.latch_reanchor_s == LATCH_REANCHOR_OFF_S


def test_decode_token_book_ignores_retired_bid_levels() -> None:
    archived = {
        "token_index": 0,
        "bid": 0.5,
        "ask": 0.52,
        "bid_size": 10.0,
        "ask_size": 10.0,
        "ts_ns": 1,
    }
    plain = decode_token_book(archived, "book")
    with_ladder = decode_token_book(
        {**archived, "bid_levels": [{"price": 0.5, "size": 10.0}]}, "book"
    )
    assert with_ladder == plain


def test_decode_policy_drops_legacy_nw_keys() -> None:
    policy = pinned_policy()
    encoded = jsonable(policy)
    assert isinstance(encoded, dict)
    fields = cast(dict[str, object], encoded)
    fields["max_abs_nw_delta_30"] = 350.0
    fields["nw_window_s"] = 30
    fields["nw_asof_max_age_s"] = 25
    decoded = decode_policy(fields, "policy")
    assert decoded == policy
    encoded_out = jsonable(decoded)
    assert isinstance(encoded_out, dict)
    assert "max_abs_nw_delta_30" not in encoded_out


def test_decode_signal_defaults_missing_death_fields() -> None:
    """Traces written before the kill gate omit deaths; they decode as zeros."""
    decoded = decode_signal({"predicted_delta": 0.05, "received_ns": 1, "anchor_p": 0.51}, "signal")
    assert decoded == RawDeltaSignal(
        predicted_delta=0.05,
        source_received_ns=1,
        received_ns=1,
        anchor_p=0.51,
        deaths_radiant=0,
        deaths_dire=0,
    )


def test_decode_recovery_tolerates_archived_sell_ids() -> None:
    event = decode_event(
        {
            "type": "Recovery",
            "now_ns": 1,
            "restored_buy_ids": ["c0"],
            "restored_sell_ids": ["c1"],
        }
    )
    assert event == Recovery(now_ns=1, restored_buy_ids=("c0",))


def test_inbound_event_codec_is_complete() -> None:
    names = {cls.__name__ for cls in get_args(InboundEvent)}
    assert names == set(EVENT_DECODERS)
    for event in _sample_events():
        encode_event(event)
    state = LiveCore(
        policy=pinned_policy(),
        limits=_limits(),
        freshness=_freshness(),
        yes_token=YES_TOKEN,
        no_token=NO_TOKEN,
        drop_sell=lambda _quote: False,
        trace=None,
        max_position_levels=LIVE_DOTA_MAX_POSITION_LEVELS,
    ).state
    assert tuple(digest_state(state)) == DIGEST_GROUPS


@pytest.mark.parametrize("name,tape,cash", _contract_tapes())
def test_replay_adapter_contract_tape(
    tmp_path: Path, name: str, tape: tuple[TapeEvent, ...], cash: float
) -> None:
    del name
    path = tmp_path / CORE_TRACE_FILENAME
    _record_tape(path, tape, cash=cash)
    assert replay_trace(path, use_current_policy=False).plan_mismatches == 0


def test_replay_size_sample(tmp_path: Path) -> None:
    path = tmp_path / CORE_TRACE_FILENAME
    tape = _contract_tapes()[0][1]
    _record_tape(path, tape, cash=DEFAULT_CASH)
    payload = path.read_bytes()
    lines = payload.splitlines()
    bytes_per_row = len(payload) / len(lines)
    cycles_per_map = int(40 * 60 / 0.1)
    estimated = bytes_per_row * 6 * cycles_per_map
    (tmp_path / "size.txt").write_text(
        f"bytes_per_row={bytes_per_row:.1f} rows={len(lines)} estimated_map={estimated:.0f}\n",
        encoding="utf-8",
    )
    assert bytes_per_row > 0
    print(f"core_trace bytes_per_row={bytes_per_row:.1f} estimated_40min_map={estimated:.0f}")


def test_drift_names_first_placing_row(tmp_path: Path) -> None:
    path = tmp_path / CORE_TRACE_FILENAME
    tape = _contract_tapes()[0][1]
    _record_tape(path, tape, cash=DEFAULT_CASH)
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    rows[0]["policy"]["level_usdc"] = PINNED_LEVEL_USDC * 2
    path.write_text(
        "".join(json.dumps(row, separators=(",", ":")) + "\n" for row in rows), encoding="utf-8"
    )
    parity = replay_trace(path, use_current_policy=False)
    mismatch = parity.first_divergence
    assert mismatch is not None
    assert parity.plan_mismatches > 0
    assert mismatch.field == "plan.places"
    assert mismatch.input_state_differed is False
    assert mismatch.recorded != mismatch.replayed


def test_digest_mutation_reports_input_state(tmp_path: Path) -> None:
    path = tmp_path / CORE_TRACE_FILENAME
    tape = _contract_tapes()[0][1]
    _record_tape(path, tape, cash=DEFAULT_CASH)
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    event_row = next(row for row in rows if row["kind"] == "event")
    event_row["digests"]["orders"] = "deadbeefdead"
    path.write_text(
        "".join(json.dumps(row, separators=(",", ":")) + "\n" for row in rows), encoding="utf-8"
    )
    parity = replay_trace(path, use_current_policy=False)
    mismatch = parity.first_digest
    assert mismatch is not None
    assert parity.plan_mismatches == 0
    assert parity.digest_mismatches == 1
    assert mismatch.field == "digest.orders"
    assert mismatch.seq == event_row["seq"]
    assert mismatch.input_state_differed is True


def test_plan_mutation_counts_one_divergence(tmp_path: Path) -> None:
    path = tmp_path / CORE_TRACE_FILENAME
    tape = _contract_tapes()[0][1]
    _record_tape(path, tape, cash=DEFAULT_CASH)
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    event_row = next(
        row
        for row in rows
        if row["kind"] == "event" and row["plan"]["block_reason"] != "ladder_budget"
    )
    event_row["plan"]["block_reason"] = "ladder_budget"
    path.write_text(
        "".join(json.dumps(row, separators=(",", ":")) + "\n" for row in rows), encoding="utf-8"
    )
    parity = replay_trace(path, use_current_policy=False)
    assert parity.plan_mismatches == 1
    assert parity.digest_mismatches == 0
    assert parity.first_divergence is not None
    assert parity.first_divergence.field == "plan.block_reason"
    assert parity.first_divergence.seq == event_row["seq"]


def test_restart_checkpoint_replays(tmp_path: Path) -> None:
    path = tmp_path / CORE_TRACE_FILENAME
    policy = pinned_policy()
    trace = CoreTrace.open(path)
    trace.write_header(_header(policy))
    core = LiveCore(
        policy=policy,
        limits=_limits(),
        freshness=_freshness(),
        yes_token=YES_TOKEN,
        no_token=NO_TOKEN,
        drop_sell=lambda _quote: False,
        trace=trace,
        max_position_levels=LIVE_DOTA_MAX_POSITION_LEVELS,
    )
    core.apply(Wake(now_ns=0, forced=True))
    checkpoint = core.export_checkpoint(now_ns=1, now_wall_s=1.0)
    core.restore_checkpoint(checkpoint=checkpoint, now_ns=2, now_wall_s=2.0)
    core.apply(Wake(now_ns=3, forced=True))
    core.detach_trace()
    assert replay_trace(path, use_current_policy=False).plan_mismatches == 0


def test_revert_replays(tmp_path: Path) -> None:
    path = tmp_path / CORE_TRACE_FILENAME
    policy = pinned_policy()
    trace = CoreTrace.open(path)
    trace.write_header(_header(policy))
    core = LiveCore(
        policy=policy,
        limits=_limits(),
        freshness=_freshness(),
        yes_token=YES_TOKEN,
        no_token=NO_TOKEN,
        drop_sell=lambda _quote: False,
        trace=trace,
        max_position_levels=LIVE_DOTA_MAX_POSITION_LEVELS,
    )
    memory = core.capture()
    core.apply(ClockUpdate(now_ns=1, clock=_clock()))
    core.apply(BudgetUpdate(now_ns=2, budget=Budget(50.0, float("inf"), float("inf"))))
    core.revert(memory)
    core.apply(Wake(now_ns=3, forced=True))
    core.detach_trace()
    assert replay_trace(path, use_current_policy=False).plan_mismatches == 0


def test_writer_fault_does_not_stop_core(tmp_path: Path) -> None:
    path = tmp_path / CORE_TRACE_FILENAME
    policy = pinned_policy()
    trace = CoreTrace.open(path)
    writes = {"n": 0}
    inner = trace._handle
    assert inner is not None

    def boom(data: str) -> int:
        writes["n"] += 1
        if writes["n"] == 3:
            raise OSError("disk")
        return inner.write(data)

    inner.write = boom  # type: ignore[method-assign]
    trace.write_header(_header(policy))
    core = LiveCore(
        policy=policy,
        limits=_limits(),
        freshness=_freshness(),
        yes_token=YES_TOKEN,
        no_token=NO_TOKEN,
        drop_sell=lambda _quote: False,
        trace=trace,
        max_position_levels=LIVE_DOTA_MAX_POSITION_LEVELS,
    )
    core.apply(Wake(now_ns=1, forced=True))
    core.apply(Wake(now_ns=2, forced=True))
    assert core.state.schedule.last_eval_ns == 2
    assert trace.failed is True
    before = path.read_text(encoding="utf-8")
    core.apply(Wake(now_ns=3, forced=True))
    assert path.read_text(encoding="utf-8") == before


def test_both_games_write_headers(tmp_path: Path, request: pytest.FixtureRequest) -> None:
    dota_trace = CoreTrace.open(tmp_path / "dota" / CORE_TRACE_FILENAME)
    lol_trace = CoreTrace.open(tmp_path / "lol" / CORE_TRACE_FILENAME)
    dota, _engine = build_attached_worker(
        tmp_path / "dota_w", request, trace=dota_trace, level_usdc=65.0, game="dota"
    )
    lol, _lol_engine = build_attached_worker(
        tmp_path / "lol_w", request, trace=lol_trace, level_usdc=20.0, game="lol"
    )
    assert dota.core is not None
    assert lol.core is not None
    dota.core.detach_trace()
    lol.core.detach_trace()
    dota_header = json.loads((tmp_path / "dota" / CORE_TRACE_FILENAME).read_text().splitlines()[0])
    lol_header = json.loads((tmp_path / "lol" / CORE_TRACE_FILENAME).read_text().splitlines()[0])
    assert dota_header["kind"] == lol_header["kind"] == "header"
    assert (
        dota_header["schema_version"] == lol_header["schema_version"] == CORE_TRACE_SCHEMA_VERSION
    )
    assert dota_header["game"] == "dota"
    assert lol_header["game"] == "lol"
    assert dota_header["policy"]["level_usdc"] == 65.0
    assert lol_header["policy"]["level_usdc"] == 20.0
    assert dota_header["yes_token"] == YES_TOKEN
    assert lol_header["no_token"] == NO_TOKEN
    assert dota_header["model"]["name"] == "fake-model"
    assert lol_header["model"]["name"] == "fake-model"
    assert dota_header["model"]["sha256"] != lol_header["model"]["sha256"]
    assert dota_header["model"]["sha256"]
    assert lol_header["model"]["sha256"]


def test_nonfinite_event_writes_null_and_keeps_the_writer(tmp_path: Path) -> None:
    path = tmp_path / CORE_TRACE_FILENAME
    policy = pinned_policy()
    trace = CoreTrace.open(path)
    trace.write_header(_header(policy))
    core = LiveCore(
        policy=policy,
        limits=_limits(),
        freshness=_freshness(),
        yes_token=YES_TOKEN,
        no_token=NO_TOKEN,
        drop_sell=lambda _quote: False,
        trace=trace,
        max_position_levels=LIVE_DOTA_MAX_POSITION_LEVELS,
    )
    assigned = trace.write_event(
        now_ns=1,
        event=BookUpdate(
            now_ns=1,
            books=BookPair(
                tokens=(
                    TokenBook(0, float("nan"), 0.52, 10.0, 10.0, 1),
                    TokenBook(1, 0.48, 0.50, 10.0, 10.0, 1),
                )
            ),
        ),
        plan=_sample_plan(),
        next_wake_ns=0,
        state=core.state,
    )
    assert assigned == 1
    assert trace.failed is False
    trace.close()
    row = json.loads(path.read_text(encoding="utf-8").splitlines()[1])
    assert row["event"]["books"]["tokens"][0]["bid"] is None
    core.apply(Wake(now_ns=2, forced=True))
    assert core.state.schedule.last_eval_ns == 2


def test_decode_signal_drops_legacy_nw_delta_30() -> None:
    decoded = decode_signal(
        {
            "predicted_delta": 0.05,
            "nw_delta_30": None,
            "received_ns": 1,
            "anchor_p": 0.5,
        },
        "signal",
    )
    assert decoded == RawDeltaSignal(
        predicted_delta=0.05,
        source_received_ns=1,
        received_ns=1,
        anchor_p=0.5,
        deaths_radiant=0,
        deaths_dire=0,
    )
    encoded = jsonable(decoded)
    assert isinstance(encoded, dict)
    assert "nw_delta_30" not in encoded


def test_corrupt_trace_open_does_not_raise(tmp_path: Path) -> None:
    folder = tmp_path / "match"
    folder.mkdir()
    (folder / CORE_TRACE_FILENAME).write_text("{not-json\n", encoding="utf-8")
    assert try_open_trace(folder) is None


def test_later_header_policy_change_replays(tmp_path: Path) -> None:
    path = tmp_path / CORE_TRACE_FILENAME
    policy = pinned_policy()
    other = replace(policy, level_usdc=policy.level_usdc * 2)
    trace = CoreTrace.open(path)
    trace.write_header(_header(policy))
    trace.write_header(_header(other))
    live = LiveDriver(cash=DEFAULT_CASH, policy=other, trace=trace)
    for event in _open():
        live.feed(event)
    live._core.detach_trace()
    assert replay_trace(path, use_current_policy=False).plan_mismatches == 0
