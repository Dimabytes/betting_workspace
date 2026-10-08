"""Explicit-block conditional-token reads and the freshness rule."""

import asyncio
import json
from collections.abc import Callable

import httpx
from eth_abi.abi import decode, encode
from eth_utils.abi import function_signature_to_4byte_selector

from trader.chain_balances import (
    CTF_ADDRESS,
    ChainSnapshot,
    fresh_token_balances,
    polygon_rpcs,
    read_chain_snapshot,
    snapshot_covers,
)
from trader.strict_json import require_list, require_object, require_str

FUNDER = "0x" + "ab" * 20
TOKEN_A = "1"
TOKEN_B = "2"
_BLOCK = 94_718_191
_BLOCK_TS = 1_758_000_000


def test_polygon_rpcs_puts_the_configured_node_first() -> None:
    alchemy = "https://polygon-mainnet.g.alchemy.com/v2/key"
    assert polygon_rpcs(alchemy) == [
        alchemy,
        "https://polygon-bor-rpc.publicnode.com",
        "https://polygon.llamarpc.com",
        "https://rpc.ankr.com/polygon",
    ]
    deduped = polygon_rpcs("https://polygon.llamarpc.com")
    assert deduped[0] == "https://polygon.llamarpc.com"
    assert deduped.count("https://polygon.llamarpc.com") == 1
    assert polygon_rpcs(None)[0] == "https://polygon-bor-rpc.publicnode.com"
    assert polygon_rpcs("")[0] == "https://polygon-bor-rpc.publicnode.com"


def test_snapshot_covers_rejects_an_old_floor_an_old_age_and_accepts_a_fresh_block() -> None:
    now = 1_000.0
    assert snapshot_covers(ChainSnapshot(1, 990.0, {}), 991.0, now) is False
    assert snapshot_covers(ChainSnapshot(1, 960.0, {}), 900.0, now) is False
    assert snapshot_covers(ChainSnapshot(1, 980.0, {}), 970.0, now) is True
    assert snapshot_covers(ChainSnapshot(1, 1_000.4, {}), 1_000.0, now) is True
    assert snapshot_covers(ChainSnapshot(1, 1_060.0, {}), 1_000.0, now) is False


def test_read_uses_the_block_number_and_decodes_batch_balances() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = _body(request)
        method = require_str(body, "method", "rpc")
        seen.append(method)
        if method == "eth_getBlockByNumber":
            return _ok({"number": hex(_BLOCK), "timestamp": hex(_BLOCK_TS)})
        to, data, block = _eth_call(body)
        assert to == CTF_ADDRESS
        assert block == hex(_BLOCK)
        assert _decoded_ids(data) == [1, 2]
        assert _decoded_accounts(data) == [FUNDER, FUNDER]
        return _ok(_encode_return([5556, 0]))

    snapshot = _read(handler, [TOKEN_A, TOKEN_B])
    assert snapshot is not None
    assert snapshot.block_number == _BLOCK
    assert snapshot.block_ts == float(_BLOCK_TS)
    assert snapshot.balances == {TOKEN_A: 0.005556, TOKEN_B: 0.0}
    assert seen == ["eth_getBlockByNumber", "eth_call"]


def test_balance_of_batch_round_trips_twelve_token_ids() -> None:
    token_ids = [str(1000 + index) for index in range(12)]

    def handler(request: httpx.Request) -> httpx.Response:
        body = _body(request)
        if require_str(body, "method", "rpc") == "eth_getBlockByNumber":
            return _ok({"number": hex(_BLOCK), "timestamp": hex(_BLOCK_TS)})
        _to, data, _block = _eth_call(body)
        assert _decoded_ids(data) == [1000 + index for index in range(12)]
        return _ok(_encode_return(list(range(12))))

    snapshot = _read(handler, token_ids)
    assert snapshot is not None
    assert snapshot.balances == {
        token_id: index / 1_000_000 for index, token_id in enumerate(token_ids)
    }


def test_header_not_found_and_429_fall_through_to_the_next_node() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = _body(request)
        host = request.url.host
        if host == "rpc-a.example":
            if body["method"] == "eth_getBlockByNumber":
                return _ok({"number": hex(1), "timestamp": hex(_BLOCK_TS)})
            return _error(-32000, "header not found")
        if host == "rpc-b.example":
            return httpx.Response(429)
        if body["method"] == "eth_getBlockByNumber":
            return _ok({"number": hex(_BLOCK), "timestamp": hex(_BLOCK_TS)})
        return _ok(_encode_return([1_000_000]))

    snapshot = _read(
        handler,
        [TOKEN_A],
        rpcs=["https://rpc-a.example", "https://rpc-b.example", "https://rpc-c.example"],
    )
    assert snapshot is not None
    assert snapshot.block_number == _BLOCK
    assert snapshot.balances == {TOKEN_A: 1.0}


def test_stale_first_node_does_not_block_a_fresh_fallback() -> None:
    """A successful but old head must not stop the next RPC from being read."""
    now = 1_000_000.0

    def handler(request: httpx.Request) -> httpx.Response:
        body = _body(request)
        host = request.url.host
        if require_str(body, "method", "rpc") == "eth_getBlockByNumber":
            timestamp = now - 40.0 if host == "rpc-a.example" else now - 1.0
            number = 1 if host == "rpc-a.example" else _BLOCK
            return _ok({"number": hex(number), "timestamp": hex(int(timestamp))})
        return _ok(_encode_return([1_000_000]))

    snapshot = _read(
        handler,
        [TOKEN_A],
        rpcs=["https://rpc-a.example", "https://rpc-b.example"],
        floor_ts=now - 10.0,
        now=now,
    )
    assert snapshot is not None
    assert snapshot.block_number == _BLOCK
    assert snapshot.block_ts == now - 1.0


def test_timeout_on_the_first_node_still_accepts_a_fresh_fallback() -> None:
    """A 10s timeout must not make the next block look too far in the future."""
    started = 1_000_000.0
    timed_out = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal timed_out
        body = _body(request)
        if request.url.host == "rpc-a.example":
            timed_out = True
            raise httpx.ReadTimeout("slow")
        if require_str(body, "method", "rpc") == "eth_getBlockByNumber":
            return _ok({"number": hex(_BLOCK), "timestamp": hex(int(started + 10.0))})
        return _ok(_encode_return([1_000_000]))

    def clock() -> float:
        return started + 10.0 if timed_out else started

    snapshot = _read(
        handler,
        [TOKEN_A],
        rpcs=["https://rpc-a.example", "https://rpc-b.example"],
        floor_ts=started - 10.0,
        clock=clock,
    )
    assert snapshot is not None
    assert snapshot.block_number == _BLOCK
    assert snapshot.block_ts == started + 10.0


def test_every_fresh_node_missing_returns_none() -> None:
    """A successful old head is not a usable balance."""
    now = 1_000_000.0

    def handler(request: httpx.Request) -> httpx.Response:
        body = _body(request)
        if require_str(body, "method", "rpc") == "eth_getBlockByNumber":
            return _ok({"number": hex(1), "timestamp": hex(int(now - 40.0))})
        return _ok(_encode_return([1_000_000]))

    assert _read(handler, [TOKEN_A], floor_ts=now - 10.0, now=now) is None


def test_invalid_token_id_does_not_call_rpc() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        del request
        raise AssertionError("rpc")

    assert _read(handler, ["nope"]) is None


def test_every_node_failing_returns_none() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(429)

    assert _read(handler, [TOKEN_A], rpcs=["https://rpc-a.example"]) is None


def test_fresh_token_balances_requires_every_token() -> None:
    now = 1_000.0
    fresh = ChainSnapshot(2, 990.0, {TOKEN_A: 1.0, TOKEN_B: 2.0})
    assert fresh_token_balances(fresh, [TOKEN_A, TOKEN_B], lambda _token: 980.0, now) == {
        TOKEN_A: 1.0,
        TOKEN_B: 2.0,
    }
    stale = ChainSnapshot(1, 900.0, {TOKEN_A: 0.0, TOKEN_B: 0.0})
    assert fresh_token_balances(stale, [TOKEN_A, TOKEN_B], lambda _token: 980.0, now) is None


def _read(
    handler: Callable[[httpx.Request], httpx.Response],
    token_ids: list[str],
    rpcs: list[str] | None = None,
    *,
    floor_ts: float = 0.0,
    now: float | None = None,
    clock: Callable[[], float] | None = None,
) -> ChainSnapshot | None:
    moment = float(_BLOCK_TS) if now is None else now

    def fixed_clock() -> float:
        return moment

    transport = httpx.MockTransport(handler)

    async def run() -> ChainSnapshot | None:
        async with httpx.AsyncClient(transport=transport) as client:
            return await read_chain_snapshot(
                client,
                rpcs or ["https://rpc.example"],
                FUNDER,
                token_ids,
                floor_ts=floor_ts,
                clock=fixed_clock if clock is None else clock,
            )

    return asyncio.run(run())


def _body(request: httpx.Request) -> dict[str, object]:
    parsed: object = json.loads(request.content)
    return require_object(parsed, "rpc")


def _eth_call(body: dict[str, object]) -> tuple[str, str, str]:
    params = require_list(body, "params", "rpc")
    call = require_object(params[0], "call")
    block = params[1]
    assert isinstance(block, str)
    return require_str(call, "to", "call"), require_str(call, "data", "call"), block


def _ok(result: object) -> httpx.Response:
    return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": result})


def _error(code: int, message: str) -> httpx.Response:
    return httpx.Response(
        200,
        json={"jsonrpc": "2.0", "id": 1, "error": {"code": code, "message": message}},
    )


def _encode_return(raws: list[int]) -> str:
    return "0x" + encode(["uint256[]"], [raws]).hex()


def _decoded_ids(data: str) -> list[int]:
    _accounts, ids = _decoded_call(data)
    return ids


def _decoded_accounts(data: str) -> list[str]:
    accounts, _ids = _decoded_call(data)
    return [account.lower() for account in accounts]


def _decoded_call(data: str) -> tuple[tuple[str, ...], list[int]]:
    blob = bytes.fromhex(data.removeprefix("0x"))
    selector = function_signature_to_4byte_selector("balanceOfBatch(address[],uint256[])")
    assert blob[:4] == selector
    accounts, ids = decode(["address[]", "uint256[]"], blob[4:])
    return accounts, [int(item) for item in ids]
