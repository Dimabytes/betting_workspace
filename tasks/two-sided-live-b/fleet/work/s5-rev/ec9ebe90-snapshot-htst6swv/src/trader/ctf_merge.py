# pyright: reportMissingTypeStubs=false, reportUnknownVariableType=false, reportUnknownMemberType=false
# pyright: reportUnknownArgumentType=false, reportPrivateUsage=false

import json
import os
import socket
import threading
import time
from dataclasses import dataclass
from typing import Any, Literal, cast

import requests
from eth_typing import HexStr
from polymaker.config import Config, StrategyProfile
from polymaker.domain import MarketMeta
from polymaker.engine import Engine
from polymaker.merge import _to_bytes32
from py_builder_relayer_client.builder.deposit_wallet import build_deposit_wallet_batch_request
from py_builder_relayer_client.client import RelayClient
from py_builder_relayer_client.endpoints import GET_NONCE, SUBMIT_TRANSACTION
from py_builder_relayer_client.exceptions import RelayerApiException, RelayerClientException
from py_builder_relayer_client.http_helpers.helpers import GET, POST
from py_builder_relayer_client.models import DepositWalletCall, DepositWalletTransactionArgs
from py_builder_relayer_client.signer import Signer
from py_builder_signing_sdk.config import BuilderConfig
from py_builder_signing_sdk.sdk_types import BuilderApiKeyCreds
from web3 import Web3
from web3.types import RPCEndpoint, RPCResponse

CTF_COLLATERAL_ADAPTER = "0xAdA100Db00Ca00073811820692005400218FcE1f"
PUSD = "0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB"
ZERO32 = b"\x00" * 32
PARTITION = [1, 2]
SHARE_BASE_UNITS = 1_000_000
ADAPTER_ABI = [
    {
        "name": "mergePositions",
        "type": "function",
        "stateMutability": "nonpayable",
        "inputs": [
            {"name": "collateralToken", "type": "address"},
            {"name": "parentCollectionId", "type": "bytes32"},
            {"name": "conditionId", "type": "bytes32"},
            {"name": "partition", "type": "uint256[]"},
            {"name": "amount", "type": "uint256"},
        ],
        "outputs": [],
    }
]
RECEIPT_TIMEOUT_S = 180.0
RECEIPT_POLL_S = 2.0
RPC_REQUEST_TIMEOUT_S = 10.0
BATCH_DEADLINE_S = 3600

MergeStatus = Literal["merged", "failed", "unknown"]


@dataclass(frozen=True)
class MergeOutcome:
    status: MergeStatus
    tx_hash: str
    reason: str


@dataclass(frozen=True)
class _HttpBody:
    payload: bytes
    status: int


class _DeadlineHTTPProvider(Web3.HTTPProvider):
    def __init__(self, endpoint_uri: str, deadline_s: float) -> None:
        super().__init__(endpoint_uri, request_kwargs={"stream": True})
        self._exception_retry_configuration = None
        self._deadline_s = deadline_s

    def make_request(self, method: RPCEndpoint, params: Any) -> RPCResponse:
        remaining = self._deadline_s - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("deadline")
        self._request_kwargs = {
            "stream": True,
            "timeout": min(RPC_REQUEST_TIMEOUT_S, remaining),
        }
        return super().make_request(method, params)


def merge_pairs_via_adapter(
    *, cfg: Config, condition_id: str, amount_raw: int, deadline_s: float
) -> MergeOutcome:
    try:
        _require_time(deadline_s)
        relay = _open_relay(cfg)
        rpc = cfg.secrets.polygon_rpc or cfg.wallet.polygon_rpc
        w3 = Web3(_DeadlineHTTPProvider(rpc, deadline_s))
        body = _sign_merge_batch(
            relay=relay,
            signer=Signer(cfg.secrets.pk, cfg.wallet.chain_id),
            w3=w3,
            wallet=cfg.secrets.browser_address,
            condition_id=condition_id,
            amount_raw=amount_raw,
            deadline_s=deadline_s,
        )
        _require_time(deadline_s)
        headers = _builder_headers(relay, body)
        _require_time(deadline_s)
    except Exception as exc:
        return MergeOutcome(status="failed", tx_hash="", reason=f"prepare: {_describe_error(exc)}")
    return _submit_merge_batch(
        relay=relay, w3=w3, body=body, headers=headers, deadline_s=deadline_s
    )


def install_adapter_merge(engine: Engine) -> None:
    engine._maybe_merge = _skip_fork_merge


def _skip_fork_merge(
    cid: str, meta: MarketMeta, p: StrategyProfile, yes_size: float, no_size: float
) -> None:
    del cid, meta, p, yes_size, no_size


def _open_relay(cfg: Config) -> RelayClient:
    secrets = cfg.secrets
    creds = BuilderApiKeyCreds(
        key=secrets.builder_key or "",
        secret=secrets.builder_secret or "",
        passphrase=secrets.builder_passphrase or "",
    )
    return RelayClient(
        secrets.relayer_url,
        cfg.wallet.chain_id,
        builder_config=BuilderConfig(local_builder_creds=creds),
    )


def _sign_merge_batch(
    *,
    relay: RelayClient,
    signer: Signer,
    w3: Web3,
    wallet: str,
    condition_id: str,
    amount_raw: int,
    deadline_s: float,
) -> dict[str, str]:
    adapter = w3.eth.contract(
        address=Web3.to_checksum_address(CTF_COLLATERAL_ADAPTER), abi=ADAPTER_ABI
    )
    data = adapter.encode_abi(
        "mergePositions",
        args=[
            Web3.to_checksum_address(PUSD),
            ZERO32,
            _to_bytes32(condition_id),
            PARTITION,
            amount_raw,
        ],
    )
    signer_address = signer.address()
    nonce = _fetch_nonce(relay, signer_address, deadline_s)
    args = DepositWalletTransactionArgs(
        from_address=signer_address,
        chain_id=relay.chain_id,
        wallet_address=Web3.to_checksum_address(wallet),
        nonce=nonce,
        deadline=str(int(time.time()) + BATCH_DEADLINE_S),
        calls=[DepositWalletCall(target=adapter.address, value="0", data=data)],
    )
    request = build_deposit_wallet_batch_request(
        signer=signer, args=args, config=relay.contract_config
    )
    return request.to_dict()


def _fetch_nonce(relay: RelayClient, signer_address: str, deadline_s: float) -> str:
    timeout = _require_time(deadline_s)
    url = f"{relay.relayer_url}{GET_NONCE}?address={signer_address}&type=WALLET"
    answer = _relay_http(GET, url, headers=None, body=None, timeout=timeout)
    nonce_answer = cast(dict[str, str], answer)
    return nonce_answer["nonce"]


def _builder_headers(relay: RelayClient, body: dict[str, str]) -> dict[str, str]:
    headers = relay._generate_builder_headers(POST, SUBMIT_TRANSACTION, body)
    if not isinstance(headers, dict):
        raise RelayerClientException("could not generate builder headers")
    return cast(dict[str, str], headers)


def _submit_merge_batch(
    *,
    relay: RelayClient,
    w3: Web3,
    body: dict[str, str],
    headers: dict[str, str],
    deadline_s: float,
) -> MergeOutcome:
    try:
        timeout = _require_time(deadline_s)
    except TimeoutError:
        return MergeOutcome(status="failed", tx_hash="", reason="prepare: TimeoutError")
    try:
        answer = _relay_http(
            POST,
            f"{relay.relayer_url}{SUBMIT_TRANSACTION}",
            headers=headers,
            body=body,
            timeout=timeout,
        )
    except RelayerApiException as exc:
        return _classify_relayer_error(exc)
    except Exception as exc:
        return MergeOutcome(status="unknown", tx_hash="", reason=f"post: {_describe_error(exc)}")
    parsed = _outcome_from_answer(answer)
    if parsed is not None:
        return parsed
    tx_hash = str(cast(dict[str, object], answer)["transactionHash"])
    return _wait_receipt(w3, tx_hash, deadline_s)


def _wait_receipt(w3: Web3, tx_hash: str, deadline_s: float) -> MergeOutcome:
    try:
        remaining = _require_time(deadline_s)
    except TimeoutError:
        return MergeOutcome(status="unknown", tx_hash=tx_hash, reason="receipt: TimeoutError")
    try:
        receipt = w3.eth.wait_for_transaction_receipt(
            HexStr(tx_hash),
            timeout=min(RECEIPT_TIMEOUT_S, remaining),
            poll_latency=RECEIPT_POLL_S,
        )
        status = receipt["status"]
    except Exception as exc:
        return MergeOutcome(
            status="unknown", tx_hash=tx_hash, reason=f"receipt: {_describe_error(exc)}"
        )
    if status == 1:
        return MergeOutcome(status="merged", tx_hash=tx_hash, reason="")
    return MergeOutcome(status="failed", tx_hash=tx_hash, reason=f"receipt status {status}")


def _relay_http(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None,
    body: dict[str, str] | None,
    timeout: float,
) -> object:
    deadline_s = time.monotonic() + timeout
    received = _exchange(
        method, url, headers=headers, body=body, timeout=timeout, deadline_s=deadline_s
    )
    if received.status != 200:
        wrapped = requests.Response()
        wrapped.status_code = received.status
        wrapped._content = received.payload
        raise RelayerApiException(wrapped)
    return _decode_payload(received.payload)


def _exchange(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None,
    body: dict[str, str] | None,
    timeout: float,
    deadline_s: float,
) -> _HttpBody:
    box: list[requests.Response | None] = [None]
    stop = threading.Event()
    watcher = threading.Thread(
        target=_stop_reader, args=(box, stop, deadline_s), name="merge-http-deadline", daemon=True
    )
    watcher.start()
    response: requests.Response | None = None
    try:
        response = requests.request(
            method=method,
            url=url,
            headers=headers,
            json=body if body else None,
            timeout=(timeout, timeout),
            stream=True,
        )
        box[0] = response
        if time.monotonic() >= deadline_s:
            raise requests.Timeout("deadline")
        return _HttpBody(
            payload=b"".join(response.iter_content(chunk_size=65536)),
            status=response.status_code,
        )
    except requests.RequestException as exc:
        raise RelayerApiException(error_msg="Request exception!") from exc
    finally:
        stop.set()
        if response is not None:
            response.close()
        watcher.join(timeout=1)


def _stop_reader(
    box: list[requests.Response | None], stop: threading.Event, deadline_s: float
) -> None:
    remaining = deadline_s - time.monotonic()
    if remaining > 0 and stop.wait(remaining):
        return
    while not stop.is_set():
        response = box[0]
        if response is not None:
            _shutdown_response(response)
            return
        if stop.wait(0.01):
            return


def _shutdown_response(response: requests.Response) -> None:
    original = response.raw._original_response
    fp = None if original is None else original.fp
    if fp is None:
        response.close()
        return
    try:
        sock = socket.socket(fileno=os.dup(fp.fileno()))
    except (AttributeError, OSError, ValueError):
        response.close()
        return
    try:
        sock.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass
    finally:
        sock.detach()


def _decode_payload(payload: bytes) -> object:
    if not payload:
        return ""
    try:
        return cast(object, json.loads(payload))
    except json.JSONDecodeError:
        return payload.decode("utf-8", errors="replace")


def _outcome_from_answer(answer: object) -> MergeOutcome | None:
    if not isinstance(answer, dict):
        return MergeOutcome(status="unknown", tx_hash="", reason=f"no tx hash: {answer!r}")
    tx_hash = str(answer.get("transactionHash") or "")
    error = _explicit_error(answer)
    if error is not None and not tx_hash:
        return MergeOutcome(status="failed", tx_hash="", reason=f"post: relayer 200: {error}")
    if not tx_hash:
        return MergeOutcome(status="unknown", tx_hash="", reason=f"no tx hash: {answer!r}")
    return None


def _classify_relayer_error(exc: RelayerApiException) -> MergeOutcome:
    reason = f"post: {_describe_error(exc)}"
    if _explicit_error(exc.error_msg) is not None:
        return MergeOutcome(status="failed", tx_hash="", reason=reason)
    return MergeOutcome(status="unknown", tx_hash="", reason=reason)


def _explicit_error(payload: object) -> str | None:
    if not isinstance(payload, dict):
        return None
    error = payload.get("error")
    if isinstance(error, str) and error.strip():
        return error
    return None


def _require_time(deadline_s: float) -> float:
    remaining = deadline_s - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("deadline")
    return remaining


def _describe_error(exc: Exception) -> str:
    if isinstance(exc, RelayerApiException):
        return f"relayer {exc.status_code}: {exc.error_msg}"
    return type(exc).__name__
