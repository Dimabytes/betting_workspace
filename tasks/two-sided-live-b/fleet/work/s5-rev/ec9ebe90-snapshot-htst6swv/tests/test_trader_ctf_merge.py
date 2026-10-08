# pyright: reportPrivateUsage=false, reportMissingTypeStubs=false

import asyncio
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import cast

import merge_probe
import pytest
import requests
from eth_typing import HexStr
from polymaker.config import Config, PathsConfig, Secrets, StrategyProfile
from polymaker.engine import Engine
from py_builder_relayer_client.exceptions import RelayerApiException
from trader_session_fixtures import make_meta
from web3 import Web3
from web3._utils.http_session_manager import HTTPSessionManager
from web3.eth import Eth
from web3.exceptions import TimeExhausted

from trader import ctf_merge
from trader.ctf_merge import (
    ADAPTER_ABI,
    CTF_COLLATERAL_ADAPTER,
    PARTITION,
    PUSD,
    RECEIPT_TIMEOUT_S,
    RPC_REQUEST_TIMEOUT_S,
    SHARE_BASE_UNITS,
    ZERO32,
    MergeOutcome,
    install_adapter_merge,
    merge_pairs_via_adapter,
)
from trader.paper_gateway import PaperGateway

TEST_PK = "0x" + "11" * 32
WALLET = "0x" + "22" * 20
CONDITION = "0x" + "cd" * 32
TX = "0x" + "ab" * 32
UNREACHABLE = "http://127.0.0.1:9"


def _soon() -> float:
    return time.monotonic() + 30.0


def _secrets(
    *,
    builder_key: str,
    builder_secret: str = "c2VjcmV0",
    relayer_url: str = UNREACHABLE,
) -> Secrets:
    return Secrets(
        PK=TEST_PK,
        BROWSER_ADDRESS=WALLET,
        POLYGON_RPC=UNREACHABLE,
        POLY_BUILDER_KEY=builder_key,
        POLY_BUILDER_SECRET=builder_secret,
        POLY_BUILDER_PASSPHRASE="pass",
        POLY_RELAYER_URL=relayer_url,
    )


def _cfg(
    *,
    builder_key: str,
    builder_secret: str = "c2VjcmV0",
    relayer_url: str = UNREACHABLE,
) -> Config:
    return Config(
        secrets=_secrets(
            builder_key=builder_key, builder_secret=builder_secret, relayer_url=relayer_url
        )
    )


class _FakeVenue:
    def __init__(self, *, answer: object, receipt: object) -> None:
        self.answer = answer
        self.receipt = receipt
        self.bodies: list[dict[str, object]] = []
        self.waited: list[str] = []
        self.timeouts: list[float] = []
        self.fail_get = False
        self.gets = 0

    def http(
        self,
        method: str,
        url: str,
        *,
        headers: object,
        body: object,
        timeout: float,
    ) -> object:
        del url, headers, timeout
        if method == "GET":
            self.gets += 1
            if self.fail_get:
                raise RelayerApiException(error_msg="Request exception!")
            return {"nonce": "7"}
        if isinstance(body, dict):
            self.bodies.append(cast(dict[str, object], body))
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer

    def wait(self, transaction_hash: HexStr, timeout: float, poll_latency: float) -> object:
        del poll_latency
        self.waited.append(transaction_hash)
        self.timeouts.append(timeout)
        if isinstance(self.receipt, Exception):
            raise self.receipt
        return self.receipt


def _relayer_error(status: int, body: bytes) -> RelayerApiException:
    response = requests.Response()
    response.status_code = status
    response._content = body
    return RelayerApiException(response)


def _install_venue(
    monkeypatch: pytest.MonkeyPatch, *, answer: object, receipt: object
) -> _FakeVenue:
    venue = _FakeVenue(answer=answer, receipt=receipt)
    monkeypatch.setattr(ctf_merge, "_relay_http", venue.http)
    monkeypatch.setattr(Eth, "wait_for_transaction_receipt", venue.wait)
    return venue


def test_merge_posts_signed_adapter_batch_and_waits_for_receipt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    venue = _install_venue(
        monkeypatch,
        answer={"transactionID": "id-1", "transactionHash": TX},
        receipt={"status": 1},
    )
    outcome = merge_pairs_via_adapter(
        cfg=_cfg(builder_key="key"),
        condition_id=CONDITION,
        amount_raw=20_000_000,
        deadline_s=time.monotonic() + RECEIPT_TIMEOUT_S + 30,
    )
    assert outcome == MergeOutcome(status="merged", tx_hash=TX, reason="")
    assert venue.waited == [TX]
    assert venue.timeouts == [RECEIPT_TIMEOUT_S]
    body = venue.bodies[0]
    assert body["type"] == "WALLET"
    assert body["nonce"] == "7"
    params = cast(dict[str, object], body["depositWalletParams"])
    assert params["depositWallet"] == Web3.to_checksum_address(WALLET)
    assert abs(int(cast(str, params["deadline"])) - (time.time() + 3600)) < 60
    (call,) = cast(list[dict[str, str]], params["calls"])
    assert call["target"] == CTF_COLLATERAL_ADAPTER
    assert call["value"] == "0"
    adapter = Web3().eth.contract(
        address=Web3.to_checksum_address(CTF_COLLATERAL_ADAPTER), abi=ADAPTER_ABI
    )
    function, args = adapter.decode_function_input(call["data"])
    assert function.fn_name == "mergePositions"
    assert args == {
        "collateralToken": PUSD,
        "parentCollectionId": ZERO32,
        "conditionId": bytes.fromhex(CONDITION[2:]),
        "partition": PARTITION,
        "amount": 20_000_000,
    }


@pytest.mark.parametrize("builder_key", ["key", ""], ids=["nonce_error", "no_builder_creds"])
def test_failure_before_post_is_failed(monkeypatch: pytest.MonkeyPatch, builder_key: str) -> None:
    venue = _install_venue(monkeypatch, answer={"transactionHash": TX}, receipt={"status": 1})
    venue.fail_get = builder_key == "key"
    outcome = merge_pairs_via_adapter(
        cfg=_cfg(builder_key=builder_key),
        condition_id=CONDITION,
        amount_raw=1,
        deadline_s=_soon(),
    )
    assert outcome.status == "failed"
    assert outcome.reason.startswith("prepare: ")
    assert venue.bodies == []


@pytest.mark.parametrize(
    ("answer", "status"),
    [
        (_relayer_error(429, b'{"error": "quota exceeded"}'), "failed"),
        (_relayer_error(401, b'{"error": "invalid signature"}'), "failed"),
        (_relayer_error(500, b'{"error": "quota exceeded"}'), "failed"),
        ({"error": "invalid signature"}, "failed"),
        (_relayer_error(408, b""), "unknown"),
        (_relayer_error(429, b""), "unknown"),
        (_relayer_error(403, b"<html>gateway error</html>"), "unknown"),
        (RelayerApiException(error_msg="Request exception!"), "unknown"),
        (_relayer_error(502, b"<html>bad gateway</html>"), "unknown"),
        (RuntimeError("socket closed"), "unknown"),
        ({"transactionID": "id-1"}, "unknown"),
        ("<html>ok</html>", "unknown"),
    ],
    ids=[
        "quota",
        "signature",
        "quota_on_500",
        "parsed_error_without_tx",
        "empty_408",
        "empty_429",
        "html_403",
        "post_timeout",
        "gateway_5xx",
        "other_error",
        "no_tx_hash",
        "not_json",
    ],
)
def test_relayer_answer_sets_outcome(
    monkeypatch: pytest.MonkeyPatch, answer: object, status: str
) -> None:
    venue = _install_venue(monkeypatch, answer=answer, receipt={"status": 1})
    outcome = merge_pairs_via_adapter(
        cfg=_cfg(builder_key="key"),
        condition_id=CONDITION,
        amount_raw=1,
        deadline_s=_soon(),
    )
    assert outcome.status == status
    assert outcome.tx_hash == ""
    assert venue.waited == []


@pytest.mark.parametrize(
    ("receipt", "status"),
    [
        (TimeExhausted("not in the chain after 180 seconds"), "unknown"),
        (requests.HTTPError("429 Too Many Requests"), "unknown"),
        ({"blockNumber": 1}, "unknown"),
        ({"status": 0}, "failed"),
        ({"status": 1}, "merged"),
    ],
    ids=["receipt_timeout", "rpc_error", "no_status", "reverted", "mined"],
)
def test_receipt_sets_outcome_and_keeps_tx(
    monkeypatch: pytest.MonkeyPatch, receipt: object, status: str
) -> None:
    _install_venue(monkeypatch, answer={"transactionHash": TX}, receipt=receipt)
    outcome = merge_pairs_via_adapter(
        cfg=_cfg(builder_key="key"),
        condition_id=CONDITION,
        amount_raw=1,
        deadline_s=_soon(),
    )
    assert outcome.status == status
    assert outcome.tx_hash == TX


def test_expired_deadline_does_not_post(monkeypatch: pytest.MonkeyPatch) -> None:
    venue = _install_venue(monkeypatch, answer={"transactionHash": TX}, receipt={"status": 1})
    outcome = merge_pairs_via_adapter(
        cfg=_cfg(builder_key="key"),
        condition_id=CONDITION,
        amount_raw=1,
        deadline_s=time.monotonic() - 1,
    )
    assert outcome.status == "failed"
    assert outcome.reason.startswith("prepare:")
    assert venue.gets == 0
    assert venue.bodies == []


def test_late_prepare_does_not_post(monkeypatch: pytest.MonkeyPatch) -> None:
    venue = _install_venue(monkeypatch, answer={"transactionHash": TX}, receipt={"status": 1})

    def slow(
        method: str,
        url: str,
        *,
        headers: object,
        body: object,
        timeout: float,
    ) -> object:
        del url, headers, timeout
        if method == "GET":
            time.sleep(0.2)
            return {"nonce": "7"}
        if isinstance(body, dict):
            venue.bodies.append(cast(dict[str, object], body))
        return {"transactionHash": TX}

    monkeypatch.setattr(ctf_merge, "_relay_http", slow)
    outcome = merge_pairs_via_adapter(
        cfg=_cfg(builder_key="key"),
        condition_id=CONDITION,
        amount_raw=1,
        deadline_s=time.monotonic() + 0.05,
    )
    assert outcome.status == "failed"
    assert outcome.reason.startswith("prepare:")
    assert venue.bodies == []


def test_malformed_builder_secret_is_failed_before_post(monkeypatch: pytest.MonkeyPatch) -> None:
    venue = _install_venue(monkeypatch, answer={"transactionHash": TX}, receipt={"status": 1})
    outcome = merge_pairs_via_adapter(
        cfg=_cfg(builder_key="key", builder_secret="abcde"),
        condition_id=CONDITION,
        amount_raw=1,
        deadline_s=_soon(),
    )
    assert outcome.status == "failed"
    assert outcome.reason.startswith("prepare:")
    assert venue.bodies == []


def test_header_generation_exception_is_failed_before_post(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    venue = _install_venue(monkeypatch, answer={"transactionHash": TX}, receipt={"status": 1})

    def boom(relay: object, method: str, path: str, body: object = None) -> dict[str, str]:
        del relay, method, path, body
        raise RuntimeError("header")

    monkeypatch.setattr(ctf_merge.RelayClient, "_generate_builder_headers", boom)
    outcome = merge_pairs_via_adapter(
        cfg=_cfg(builder_key="key"),
        condition_id=CONDITION,
        amount_raw=1,
        deadline_s=_soon(),
    )
    assert outcome.status == "failed"
    assert outcome.reason.startswith("prepare:")
    assert venue.bodies == []


def test_install_adapter_merge_stubs_the_fork_merge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("polymaker.engine.ExecutionGateway", PaperGateway)
    cfg = Config(
        paths=PathsConfig(db=str(tmp_path / "engine.db"), journal_dir=str(tmp_path / "journal")),
        secrets=_secrets(builder_key="key"),
    )
    engine = Engine(cfg, paper=True)
    engine.paper = False
    meta = make_meta()

    async def run() -> None:
        install_adapter_merge(engine)
        engine._maybe_merge(
            meta.condition_id, meta, StrategyProfile(merge_min_size=1.0), 50.0, 50.0
        )

    try:
        asyncio.run(run())
        assert engine._merging == set()
        assert engine._aux_tasks == []
    finally:
        engine.state.close()
        engine.catalog.close()
        engine.journal.close()
        engine.gateway.close()


def test_receipt_wait_is_limited_to_the_remaining_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    venue = _install_venue(monkeypatch, answer={"transactionHash": TX}, receipt={"status": 1})
    deadline_s = time.monotonic() + 0.5
    outcome = merge_pairs_via_adapter(
        cfg=_cfg(builder_key="key"),
        condition_id=CONDITION,
        amount_raw=1,
        deadline_s=deadline_s,
    )
    assert outcome.status == "merged"
    assert outcome.tx_hash == TX
    (timeout,) = venue.timeouts
    assert timeout == pytest.approx(0.5, abs=0.2)
    assert timeout < RECEIPT_TIMEOUT_S


def test_late_post_keeps_the_hash_and_skips_receipt(monkeypatch: pytest.MonkeyPatch) -> None:
    venue = _install_venue(monkeypatch, answer={"transactionHash": TX}, receipt={"status": 1})

    def slow(
        method: str,
        url: str,
        *,
        headers: object,
        body: object,
        timeout: float,
    ) -> object:
        del url, headers, timeout
        if method == "GET":
            return {"nonce": "7"}
        time.sleep(0.2)
        if isinstance(body, dict):
            venue.bodies.append(cast(dict[str, object], body))
        return {"transactionHash": TX}

    monkeypatch.setattr(ctf_merge, "_relay_http", slow)
    outcome = merge_pairs_via_adapter(
        cfg=_cfg(builder_key="key"),
        condition_id=CONDITION,
        amount_raw=1,
        deadline_s=time.monotonic() + 0.05,
    )
    assert outcome == MergeOutcome(status="unknown", tx_hash=TX, reason="receipt: TimeoutError")
    assert venue.waited == []


def test_each_rpc_timeout_is_the_remaining_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[float] = []

    def post(self: object, endpoint_uri: str, data: object, **kwargs: object) -> bytes:
        del self, endpoint_uri, data
        timeout = kwargs["timeout"]
        assert isinstance(timeout, float)
        seen.append(timeout)
        assert kwargs.get("stream") is True
        raise requests.Timeout("stall")

    def relay(
        method: str,
        url: str,
        *,
        headers: object,
        body: object,
        timeout: float,
    ) -> dict[str, str]:
        del url, headers, body, timeout
        if method == "GET":
            return {"nonce": "7"}
        return {"transactionHash": TX}

    monkeypatch.setattr(HTTPSessionManager, "make_post_request", post)
    monkeypatch.setattr(ctf_merge, "_relay_http", relay)
    deadline_s = time.monotonic() + 0.4
    outcome = merge_pairs_via_adapter(
        cfg=_cfg(builder_key="key"),
        condition_id=CONDITION,
        amount_raw=1,
        deadline_s=deadline_s,
    )
    assert outcome.status == "unknown"
    assert outcome.tx_hash == TX
    assert seen
    assert all(0 < timeout <= RPC_REQUEST_TIMEOUT_S for timeout in seen)
    assert seen[0] == pytest.approx(0.4, abs=0.15)


class _Trickle(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        raw = b'{"nonce":"7"}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            self.rfile.read(length)
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", "100000")
        self.end_headers()
        for _ in range(1000):
            try:
                self.wfile.write(b"x")
                self.wfile.flush()
            except Exception:
                return
            time.sleep(0.02)

    def log_message(self, format: str, *args: object) -> None:
        del format, args


def test_trickled_relayer_body_stops_at_the_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
    server = HTTPServer(("127.0.0.1", 0), _Trickle)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    started = time.monotonic()
    try:
        outcome = merge_pairs_via_adapter(
            cfg=_cfg(builder_key="key", relayer_url=f"http://127.0.0.1:{port}"),
            condition_id=CONDITION,
            amount_raw=1,
            deadline_s=started + 0.25,
        )
        elapsed = time.monotonic() - started
    finally:
        server.shutdown()
        server.server_close()
    assert outcome.status == "unknown"
    assert outcome.tx_hash == ""
    assert outcome.reason.startswith("post:")
    assert elapsed < 1.5


def test_merge_probe_reads_the_adapter_constants_from_ctf_merge() -> None:
    assert merge_probe.CTF_COLLATERAL_ADAPTER is CTF_COLLATERAL_ADAPTER
    assert merge_probe.PUSD is PUSD
    assert merge_probe.ADAPTER_ABI is ADAPTER_ABI
    assert merge_probe.ZERO32 is ZERO32
    assert merge_probe.PARTITION is PARTITION
    assert merge_probe.SHARE_BASE_UNITS is SHARE_BASE_UNITS
