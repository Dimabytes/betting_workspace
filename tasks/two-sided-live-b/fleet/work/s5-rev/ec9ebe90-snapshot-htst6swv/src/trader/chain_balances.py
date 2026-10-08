"""On-chain conditional-token balances at an explicit Polygon block."""

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import cast

import httpx
from eth_abi.abi import decode, encode
from eth_abi.exceptions import DecodingError, EncodingError
from eth_utils.abi import function_signature_to_4byte_selector

from shared.utils.log import get_logger
from trader.strict_json import StrictJsonError, require_object

logger = get_logger(__name__)

CHAIN_BLOCK_MAX_AGE_S = 30.0
# A validator timestamp a fraction of a second ahead of the VPS is normal.
# Far-future timestamps are not: a public node could pair one with a stale balance.
CHAIN_BLOCK_MAX_FUTURE_S = 5.0
CTF_ADDRESS = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"
_BALANCE_OF_BATCH = function_signature_to_4byte_selector("balanceOfBatch(address[],uint256[])")
_SHARE_SCALE = 1_000_000
_PUBLIC_RPCS = (
    "https://polygon-bor-rpc.publicnode.com",
    "https://polygon.llamarpc.com",
    "https://rpc.ankr.com/polygon",
)


@dataclass(frozen=True, slots=True)
class ChainSnapshot:
    """Balances of one funder, all read at the same block."""

    block_number: int
    block_ts: float
    balances: dict[str, float]


def polygon_rpcs(configured: str | None) -> list[str]:
    """Configured RPC first, then the public nodes poly-maker already falls back to."""
    ordered: list[str] = []
    for rpc in (configured, *_PUBLIC_RPCS):
        if rpc and rpc not in ordered:
            ordered.append(rpc)
    return ordered


def snapshot_covers(snapshot: ChainSnapshot, floor_ts: float, now: float) -> bool:
    """True when this block can contain the last settled fill and is not a stale head.

    `floor_ts` is wall time of the last CONFIRMED/FAILED fill, or the block time of
    a later write-down, and never earlier than process start. A block slightly
    ahead of the VPS clock still counts.
    """
    if snapshot.block_ts < floor_ts:
        return False
    age = now - snapshot.block_ts
    return -CHAIN_BLOCK_MAX_FUTURE_S <= age <= CHAIN_BLOCK_MAX_AGE_S


def fresh_token_balances(
    snapshot: ChainSnapshot,
    token_ids: list[str],
    floor_of: Callable[[str], float],
    now: float,
) -> dict[str, float] | None:
    """Balances when this one block is fresh for every token. Otherwise None."""
    out: dict[str, float] = {}
    for token_id in token_ids:
        size = snapshot.balances.get(token_id)
        if size is None or not snapshot_covers(snapshot, floor_of(token_id), now):
            return None
        out[token_id] = size
    return out


async def read_chain_snapshot(
    client: httpx.AsyncClient,
    rpcs: list[str],
    funder: str,
    token_ids: list[str],
    *,
    floor_ts: float,
    clock: Callable[[], float] = time.time,
) -> ChainSnapshot | None:
    """Read `balanceOfBatch` at the block `latest` just returned.

    One error, HTTP 429, or a head older than `floor_ts` / 30s fails that node.
    The next node starts over, so a stale head is never kept in place of a fresh
    fallback. Freshness uses the clock after that node's answer: a slow timeout
    must not make the next block look far in the future. None when every node
    fails or is stale.
    """
    if not token_ids:
        return None
    data = _encode_balance_of_batch(funder, token_ids)
    if data is None:
        logger.warning("chain balance encode failed tokens=%d", len(token_ids))
        return None
    saw_stale = False
    for rpc in rpcs:
        snapshot = await _read_rpc(client, rpc, data, token_ids)
        if snapshot is None:
            continue
        if snapshot_covers(snapshot, floor_ts, clock()):
            return snapshot
        saw_stale = True
    if saw_stale:
        logger.warning("chain balance stale block tokens=%d", len(token_ids))
    else:
        logger.warning("chain balance read failed tokens=%d", len(token_ids))
    return None


async def _read_rpc(
    client: httpx.AsyncClient,
    rpc: str,
    data: str,
    token_ids: list[str],
) -> ChainSnapshot | None:
    block = await _rpc(client, rpc, "eth_getBlockByNumber", ["latest", False])
    parsed = _block_head(block)
    if parsed is None:
        return None
    raw = await _rpc(
        client,
        rpc,
        "eth_call",
        [{"to": CTF_ADDRESS, "data": data}, hex(parsed.number)],
    )
    if not isinstance(raw, str):
        return None
    amounts = _decode_uint256_array(raw)
    if amounts is None or len(amounts) != len(token_ids):
        return None
    balances = {
        token_id: amount / _SHARE_SCALE for token_id, amount in zip(token_ids, amounts, strict=True)
    }
    return ChainSnapshot(
        block_number=parsed.number,
        block_ts=float(parsed.timestamp),
        balances=balances,
    )


async def _rpc(
    client: httpx.AsyncClient,
    rpc: str,
    method: str,
    params: list[object],
) -> object | None:
    payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    try:
        response = await client.post(rpc, json=payload)
    except httpx.HTTPError:
        return None
    if response.status_code == 429 or response.status_code >= 400:
        return None
    try:
        parsed: object = response.json()
        body = require_object(parsed, "rpc")
    except (ValueError, StrictJsonError):
        return None
    if "error" in body:
        return None
    return body.get("result")


@dataclass(frozen=True, slots=True)
class _BlockHead:
    """Number and timestamp of one `eth_getBlockByNumber` head."""

    number: int
    timestamp: int


def _block_head(block: object) -> _BlockHead | None:
    try:
        fields = require_object(block, "block")
    except StrictJsonError:
        return None
    number = _hex_int(fields.get("number"))
    timestamp = _hex_int(fields.get("timestamp"))
    if number is None or timestamp is None:
        return None
    return _BlockHead(number=number, timestamp=timestamp)


def _hex_int(value: object) -> int | None:
    if not isinstance(value, str):
        return None
    try:
        return int(value, 16)
    except ValueError:
        return None


def _encode_balance_of_batch(funder: str, token_ids: list[str]) -> str | None:
    try:
        token_ints = [int(token_id) for token_id in token_ids]
        body = encode(["address[]", "uint256[]"], [[funder] * len(token_ints), token_ints])
    except (EncodingError, ValueError, TypeError):
        return None
    return "0x" + (_BALANCE_OF_BATCH + body).hex()


def _decode_uint256_array(data: str) -> list[int] | None:
    raw = data.removeprefix("0x")
    try:
        decoded = decode(["uint256[]"], bytes.fromhex(raw))
    except (DecodingError, ValueError):
        return None
    amounts = cast(tuple[object, ...], decoded[0])
    shares: list[int] = []
    for amount in amounts:
        if isinstance(amount, bool) or not isinstance(amount, int):
            return None
        shares.append(amount)
    return shares
