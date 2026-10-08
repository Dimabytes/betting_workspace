# STEP-005: Merge through the pUSD adapter: `ctf_merge.py` and `PairMerger`

Task: `W/tasks/two-sided-live-b/feature.json`, step STEP-005.
Code repo: `E = /Users/dimabytes/work/polymarket/dota_2_bot/esports-trader`, branch `main`.
Base: STEP-001 `8dadf747`, STEP-002 `b86cdcdc`, then STEP-003 (`plans/STEP-003.md`) and STEP-004 (`plans/STEP-004.md`).
No Figma, no UI.

Read `W/AGENTS.md`, `E/AGENTS.md` and `W/.learnings/poly-maker.md` first. Rules that matter here:
- `../poly-maker` is frozen. Import from it, never edit it.
- Function names are verbs. Arguments are required. New public functions and the `PairMerger` constructor are keyword-only.
- No `dict[str, Any]` for our own data. No anonymous multi-field tuples.
- No branches for inputs that cannot occur.
- **New code gets no comments and no docstrings** (review config). `# pyright:` file pragmas are tool directives and are allowed. `dust_sweep.py` and `engine_seams.py` use the same pragmas.
- Never use the real keys. Tests use a throwaway key and `http://127.0.0.1:9` URLs. No test touches the network.

## Precondition

Start only after STEP-003 **and** STEP-004 are committed. In E:

```bash
git status --short   # only the two untracked data/backtests/dota_maker/validation_*ts-regress-* dirs
grep -n "def apply_merge" src/trader/wallet_store.py
grep -n "event == \"merged\"\|def _note_merge_leg" src/trader/core_session_io.py
grep -n "def note_merge" src/trader/session_core.py
```

All four greps must hit. The success test below needs STEP-004's `merged` branch in `consume_core_outbox`. `make lint` runs pre-commit, which stashes unstaged files and type-checks the whole project, so a dirty tree mixes another step's errors into this one.

## Goal

Two new modules. Nothing in production calls them yet. STEP-006 plugs `PairMerger` into `TwoSidedWorker`. STEP-007 calls `install_adapter_merge` for `two_sided` only. Service A (Follow300) is unchanged: no edit touches `match_worker.py`, `wallet_host.py`, `engine_seams.py`, `dust_sweep.py`, `config/**` or `compose.yaml`.

1. `src/trader/ctf_merge.py`: one deposit-wallet merge through the relayer, in three phases. Every way it can end maps to exactly one `MergeOutcome`: `merged`, `failed` or `unknown`. It never raises. It also holds `install_adapter_merge(engine)`.
2. `src/trader/pair_merge.py`: `PairMerger`, the DustSweeper-shaped slot. `schedule()` starts a threshold merge. `merge_all()` is the final merge. It also covers the in-flight, pause and disable rules, the chain clamp, booking, Telegram and the collateral refresh.
3. `scripts/merge_probe.py` imports the moved constants.

## Decisions (made without the owner, with reasons)

1. **Phase 1 signs the batch itself, and phase 2 posts it with `RelayClient._post_request`.** The public `execute_deposit_wallet_batch` does both in one call. Phase 1 calls the library's own `build_deposit_wallet_batch_request` with a `Signer`, which is exactly the code that method runs. Phase 2 calls `relay._post_request(POST, SUBMIT_TRANSACTION, body)`, the method's last line.
   - Reason: the feature puts signing in phase 1 ("calldata, nonce, подпись"). A local signing error then sends nothing and is `failed`. Inside the public method it would look like "another exception in phase 2", which is `unknown` and would turn merges off for the whole map.
   - Cost: one private call on a dependency pinned in `uv.lock` (`py-builder-relayer-client 0.0.2`).
2. **How a relayer HTTP error is classified.** A 4xx with a body (quota 429, signature 401/400) is `failed`. A 5xx or any other non-200 is `unknown`. A transport error is `unknown`: a `RelayerApiException` with `status_code is None`, which is how the client reports every `requests.RequestException`, timeouts included.
   - Reason: Resolved Questions say `failed` means "a parsed relayer answer with an error body". A 5xx can come from a proxy after the relayer already submitted the transaction.
   - `RelayerClientException` from `_post_request` ("could not generate builder headers") is raised before the POST, so it is `failed`.
   - `RelayerApiException` subclasses `RelayerClientException`, so it must be caught first.
3. **The RelayClient is built without `private_key`.** Phase 1 makes one `Signer(pk, chain_id)` and uses it to sign. `get_nonce` and `_post_request` do not need the client's signer. This avoids a second key object and the `Signer | None` attribute.
4. **The nonce passes through unchanged:** `cast(dict[str, str], relay.get_nonce(addr, "WALLET"))["nonce"]`. The fork does the same and it was verified live. A text answer or a missing key raises in phase 1, which is `failed`.
5. **No proxy code.** `cfg.proxy` is only `os.environ ALL_PROXY or HTTPS_PROXY`. `requests`, which both the relayer client and web3's `HTTPProvider` use, already reads those same variables, `all_proxy` included. So the fork's copy into `HTTP(S)_PROXY` changes nothing here, and writing `os.environ` from a worker thread would be a needless global side effect. This meets "the proxy from cfg.proxy is applied, as in the fork" with zero lines. Record this in `progress.txt`.
6. **The receipt is polled every 2 s (`RECEIPT_POLL_S`), not web3's default 0.1 s,** for up to 180 s. Reason: a 429 from the RPC while polling raises, which is `unknown` and turns merges off for the map. Ninety polls are far less likely to hit a rate limit than 1800. Polygon blocks are about 2 s, so the extra latency is at most one block.
7. **No POA middleware.** `eth_getTransactionReceipt` does not parse `extraData`. Only block reads need it.
8. **One outer bound per call, `MERGE_CALL_TIMEOUT_S = 190.0`.** `PairMerger` wraps the thread in `asyncio.wait_for`. A timeout is `unknown("no outcome in 190s")`.
   - Reason: the relayer client calls `requests` with **no timeout** (`http_helpers/helpers.py`), so a nonce GET or the POST can hang forever.
   - A hung merge would block `MatchWorker._quiesce`, which awaits `wait_inflight()` before it cancels orders.
   - 190 s is the 180 s receipt wait plus 10 s for the nonce and the POST. A timeout here is `unknown`, the same as a receipt timeout. The leaked thread finishes when its own receipt wait ends. It never books anything.
9. **In-flight means `self._task` is running.** There is no separate flag. The task ends after the collateral refresh, so `schedule()` also skips during that one CLOB call. Reason: one marker instead of two. Nothing can start a second merge on the same map in that second anyway. The sync booking block still runs with no await between `apply_merge`, consume, wake and Telegram, as the feature requires.
10. **The core is fed through `host._consume_core_outbox(yes_token)`.** It resolves the worker, its `SessionIdentity` and its token set at call time. `PairMerger` cannot build the identity at construction, because `yes_is_radiant` is pinned on the first feed tick. A consume error is logged and the merge stays booked (STEP-004 handoff). The next quote cycle's `_position_mismatch` triggers Recovery, which re-reads the ledger.
11. **Reasons carry exception type names.** The one exception is relayer HTTP errors, which carry status and body. Reason: `str()` of a web3 or requests HTTP error includes the RPC URL, and an Alchemy-style URL holds the API key. The reason goes to Telegram truncated to 200 characters.
12. **The collateral cache is written only when the read is > 0.** `gateway.collateral_balance()` returns `0.0` when its read fails. After a merge the real balance is always > 0. A 0 would block BUYs until the next REST positions read.
13. **`SHARE_BASE_UNITS` also moves into `ctf_merge.py`**, next to the five constants the feature names. `merge_probe.py` imports it. `pair_merge.py` needs it, and `amount_raw` is `ctf_merge`'s unit. Otherwise it would be a third copy.
14. **`PairMerger` takes `match_id`**, for the Telegram line. Its constructor is keyword-only because it takes four `str` arguments in a row.
15. **No `sweep()` on `PairMerger`.** The feature lists the interface as `attach_core, mark_quiesced, cancel, schedule, wait_inflight` + `merge_all`. `MatchWorker._quiesce` calls `self._dust.sweep(force=True)`. STEP-006 owns that (see the handoff).
16. **No negRisk branch.** B trades only BLAST Slam Dota map markets. Those are binary CTF v1, and `merge_probe` already rejects negRisk.
17. **`_skip_fork_merge` keeps the fork's parameter names `(cid, meta, p, yes_size, no_size)`.** basedpyright strict rejects the assignment to `engine._maybe_merge` on a name mismatch. I checked this: "Parameter name mismatch: p versus profile".
18. **The tests patch the network methods at class level:** `RelayClient.get_nonce`, `RelayClient._post_request` and `web3.eth.Eth.wait_for_transaction_receipt`. Production code gets no test hooks. Each test passes `Secrets` fields explicitly (throwaway PK, localhost URLs), so E's `.env` never supplies a real `PK` or `POLYGON_RPC`.

I ran the code and both test files below in a scratch copy, layered over E's `trader` namespace package. On the current tree: 36 passed, basedpyright strict 0 errors, ruff `C901` clean. The four STEP-004 assertions marked below were not in that run.

## Order of edits (all paths relative to E)

### 1. New `src/trader/ctf_merge.py`

```python
# pyright: reportMissingTypeStubs=false, reportUnknownVariableType=false, reportUnknownMemberType=false
# pyright: reportUnknownArgumentType=false, reportPrivateUsage=false

import time
from dataclasses import dataclass
from typing import Literal, cast

from eth_typing import HexStr
from polymaker.config import Config, StrategyProfile
from polymaker.domain import MarketMeta
from polymaker.engine import Engine
from polymaker.merge import _to_bytes32
from py_builder_relayer_client.builder.deposit_wallet import build_deposit_wallet_batch_request
from py_builder_relayer_client.client import RelayClient
from py_builder_relayer_client.endpoints import SUBMIT_TRANSACTION
from py_builder_relayer_client.exceptions import RelayerApiException, RelayerClientException
from py_builder_relayer_client.http_helpers.helpers import POST
from py_builder_relayer_client.models import DepositWalletCall, DepositWalletTransactionArgs
from py_builder_relayer_client.signer import Signer
from py_builder_signing_sdk.config import BuilderConfig
from py_builder_signing_sdk.sdk_types import BuilderApiKeyCreds
from web3 import Web3

CTF_COLLATERAL_ADAPTER = "0xAdA100Db00Ca00073811820692005400218FcE1f"
PUSD = "0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB"
ZERO32 = b"\x00" * 32
PARTITION = [1, 2]
SHARE_BASE_UNITS = 1_000_000
ADAPTER_ABI = [ ... copy the mergePositions entry from scripts/merge_probe.py unchanged ... ]
RECEIPT_TIMEOUT_S = 180.0
RECEIPT_POLL_S = 2.0
BATCH_DEADLINE_S = 3600

MergeStatus = Literal["merged", "failed", "unknown"]


@dataclass(frozen=True)
class MergeOutcome:
    status: MergeStatus
    tx_hash: str
    reason: str


def merge_pairs_via_adapter(*, cfg: Config, condition_id: str, amount_raw: int) -> MergeOutcome:
    try:
        relay = _open_relay(cfg)
        w3 = Web3(Web3.HTTPProvider(cfg.secrets.polygon_rpc or cfg.wallet.polygon_rpc))
        body = _sign_merge_batch(
            relay=relay,
            signer=Signer(cfg.secrets.pk, cfg.wallet.chain_id),
            w3=w3,
            wallet=cfg.secrets.browser_address,
            condition_id=condition_id,
            amount_raw=amount_raw,
        )
    except Exception as exc:
        return MergeOutcome(status="failed", tx_hash="", reason=f"prepare: {_describe_error(exc)}")
    return _submit_merge_batch(relay=relay, w3=w3, body=body)


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
    nonce_answer = cast(dict[str, str], relay.get_nonce(signer_address, "WALLET"))
    args = DepositWalletTransactionArgs(
        from_address=signer_address,
        chain_id=relay.chain_id,
        wallet_address=Web3.to_checksum_address(wallet),
        nonce=nonce_answer["nonce"],
        deadline=str(int(time.time()) + BATCH_DEADLINE_S),
        calls=[DepositWalletCall(target=adapter.address, value="0", data=data)],
    )
    request = build_deposit_wallet_batch_request(
        signer=signer, args=args, config=relay.contract_config
    )
    return request.to_dict()


def _submit_merge_batch(*, relay: RelayClient, w3: Web3, body: dict[str, str]) -> MergeOutcome:
    try:
        answer = relay._post_request(POST, SUBMIT_TRANSACTION, body)
    except RelayerApiException as exc:
        return _classify_relayer_error(exc)
    except RelayerClientException as exc:
        return MergeOutcome(status="failed", tx_hash="", reason=f"post: {exc.msg}")
    except Exception as exc:
        return MergeOutcome(status="unknown", tx_hash="", reason=f"post: {_describe_error(exc)}")
    tx_hash = str(answer.get("transactionHash") or "") if isinstance(answer, dict) else ""
    if not tx_hash:
        return MergeOutcome(status="unknown", tx_hash="", reason=f"no tx hash: {answer!r}")
    try:
        receipt = w3.eth.wait_for_transaction_receipt(
            HexStr(tx_hash), timeout=RECEIPT_TIMEOUT_S, poll_latency=RECEIPT_POLL_S
        )
        status = receipt["status"]
    except Exception as exc:
        return MergeOutcome(
            status="unknown", tx_hash=tx_hash, reason=f"receipt: {_describe_error(exc)}"
        )
    if status == 1:
        return MergeOutcome(status="merged", tx_hash=tx_hash, reason="")
    return MergeOutcome(status="failed", tx_hash=tx_hash, reason=f"receipt status {status}")


def _classify_relayer_error(exc: RelayerApiException) -> MergeOutcome:
    reason = f"post: {_describe_error(exc)}"
    if exc.status_code is not None and 400 <= exc.status_code < 500:
        return MergeOutcome(status="failed", tx_hash="", reason=reason)
    return MergeOutcome(status="unknown", tx_hash="", reason=reason)


def _describe_error(exc: Exception) -> str:
    if isinstance(exc, RelayerApiException):
        return f"relayer {exc.status_code}: {exc.error_msg}"
    return type(exc).__name__
```

Notes:
- PLC0415 bans nested imports, so all imports stay at the top. The fork imports inside the function.
- `encode_abi` is web3 7's public encoder. Do not copy the script's private `_encode_transaction_data`.
- `HexStr(...)` is needed because `wait_for_transaction_receipt` does not accept a plain `str` under strict mode.
- Let `ruff check --fix` sort the imports.

### 2. `scripts/merge_probe.py`

- Delete the definitions of `CTF_COLLATERAL_ADAPTER`, `PUSD`, `ZERO32`, `PARTITION`, `SHARE_BASE_UNITS` and `ADAPTER_ABI`.
- Add `from trader.ctf_merge import ADAPTER_ABI, CTF_COLLATERAL_ADAPTER, PARTITION, PUSD, SHARE_BASE_UNITS, ZERO32`. Let ruff place it in the first-party block.
- Keep `GAMMA_MARKETS`, `TAKER_PRICE_CUSHION`, `SETTLE_TIMEOUT_S`, `ERC20_ABI` and `CTF_ABI`.
- Do not change behavior. The script still sends through the fork's `Merger._merge_safe` / `_merge_deposit_wallet`. That path was verified live on 08.10.
- Do not touch its docstring.

### 3. New `src/trader/pair_merge.py`

```python
# pyright: reportPrivateUsage=false, reportMissingTypeStubs=false

import asyncio
import math
import time
from contextlib import suppress
from typing import TYPE_CHECKING, cast

from py_clob_client_v2.clob_types import AssetType, BalanceAllowanceParams

from shared.utils.log import get_logger
from trader.ctf_merge import SHARE_BASE_UNITS, MergeOutcome, merge_pairs_via_adapter
from trader.notify import notify_in_background
from trader.session_core import LiveCore
from trader.trading_mode import ExecutionMode

if TYPE_CHECKING:
    from trader.wallet_host import WalletHost

logger = get_logger(__name__)

MERGE_AT_HELD_USDC = 130.0
MERGE_MIN_PAIRS = 5.0
FINAL_MERGE_MIN_RAW = 10_000
MERGE_PAUSES_S = (30.0, 60.0, 120.0, 300.0)
MERGE_CALL_TIMEOUT_S = 190.0


def pick_merge_pause_s(failures: int) -> float:
    return MERGE_PAUSES_S[min(failures, len(MERGE_PAUSES_S)) - 1]


class PairMerger:
    def __init__(
        self,
        *,
        host: "WalletHost",
        cid: str,
        match_id: str,
        yes_token: str,
        no_token: str,
        mode: ExecutionMode,
    ) -> None:
        self._host = host
        self._cid = cid
        self._match_id = match_id
        self._yes = yes_token
        self._no = no_token
        self._mode = mode
        self._core: LiveCore | None = None
        self._quiesced = False
        self._disabled = False
        self._failures = 0
        self._paused_until_s = 0.0
        self._task: asyncio.Task[None] | None = None

    def attach_core(self, core: LiveCore) -> None:
        self._core = core

    def mark_quiesced(self) -> None:
        self._quiesced = True

    def cancel(self) -> None:
        if self._task is not None:
            self._task.cancel()

    def schedule(self) -> None:
        if self._task is not None and not self._task.done():
            return
        if not self._is_due():
            return
        self._task = asyncio.get_running_loop().create_task(self._merge_pairs(min_raw=1))

    async def wait_inflight(self) -> None:
        if self._task is not None and not self._task.done():
            with suppress(Exception, asyncio.CancelledError):
                await self._task

    async def merge_all(self) -> None:
        if self._disabled:
            return
        await self._merge_pairs(min_raw=FINAL_MERGE_MIN_RAW)

    def _is_due(self) -> bool:
        if self._disabled or self._mode != "live" or self._quiesced:
            return False
        if time.monotonic() < self._paused_until_s:
            return False
        core = self._core
        if core is None or core.state.recovery_pending:
            return False
        store = self._host.store
        yes = store.position(self._yes)
        no = store.position(self._no)
        held_usdc = yes.size * yes.avg_price + no.size * no.avg_price
        if held_usdc < MERGE_AT_HELD_USDC or min(yes.size, no.size) < MERGE_MIN_PAIRS:
            return False
        return not store.has_unacked_matched({self._yes, self._no})

    async def _merge_pairs(self, *, min_raw: int) -> None:
        async with self._host.engine._chain_lock:
            balances = await self._host.read_fresh_balances([self._yes, self._no])
            if balances is None:
                return
            store = self._host.store
            pairs = min(
                store.position(self._yes).size,
                store.position(self._no).size,
                balances[self._yes],
                balances[self._no],
            )
            amount_raw = math.floor(pairs * SHARE_BASE_UNITS)
            if amount_raw < min_raw:
                return
            outcome = await self._call_adapter(amount_raw)
            match outcome.status:
                case "merged":
                    self._book_merge(tx_hash=outcome.tx_hash, qty=amount_raw / SHARE_BASE_UNITS)
                case "failed":
                    self._pause_merges(outcome)
                case "unknown":
                    self._disable_merges(outcome)
        if outcome.status == "merged":
            await self._refresh_collateral()

    async def _call_adapter(self, amount_raw: int) -> MergeOutcome:
        call = asyncio.to_thread(
            merge_pairs_via_adapter,
            cfg=self._host.engine.cfg,
            condition_id=self._cid,
            amount_raw=amount_raw,
        )
        try:
            return await asyncio.wait_for(call, timeout=MERGE_CALL_TIMEOUT_S)
        except TimeoutError:
            return MergeOutcome(
                status="unknown", tx_hash="", reason=f"no outcome in {MERGE_CALL_TIMEOUT_S:.0f}s"
            )

    def _book_merge(self, *, tx_hash: str, qty: float) -> None:
        applied = self._host.store.apply_merge(
            tx_hash=tx_hash, token_ids=(self._yes, self._no), qty=qty
        )
        try:
            self._host._consume_core_outbox(self._yes)
        except Exception as exc:
            logger.warning(
                "merge core outbox failed match=%s err=%s", self._match_id, type(exc).__name__
            )
        self._failures = 0
        self._host.engine._wake_cid(self._cid)
        logger.info(
            "merge match=%s pairs=%.6f tx=%s applied=%s", self._match_id, qty, tx_hash, applied
        )
        if applied:
            notify_in_background(
                f"trader merge: match {self._match_id} pairs {qty:.2f} tx {tx_hash}"
            )

    def _pause_merges(self, outcome: MergeOutcome) -> None:
        self._failures += 1
        pause_s = pick_merge_pause_s(self._failures)
        self._paused_until_s = time.monotonic() + pause_s
        tx = outcome.tx_hash or "-"
        logger.warning(
            "merge failed match=%s pause=%.0fs tx=%s reason=%s",
            self._match_id,
            pause_s,
            tx,
            outcome.reason,
        )
        notify_in_background(
            f"trader merge failed: match {self._match_id} pause {pause_s:.0f}s tx {tx}: "
            f"{outcome.reason[:200]}"
        )

    def _disable_merges(self, outcome: MergeOutcome) -> None:
        self._disabled = True
        tx = outcome.tx_hash or "-"
        logger.error("merge unknown match=%s tx=%s reason=%s", self._match_id, tx, outcome.reason)
        notify_in_background(
            f"trader merge unknown: match {self._match_id} merges off for this map tx {tx}: "
            f"{outcome.reason[:200]}"
        )

    async def _refresh_collateral(self) -> None:
        gateway = self._host.engine.gateway
        params = BalanceAllowanceParams(asset_type=cast(AssetType, AssetType.COLLATERAL))
        try:
            await gateway._io(gateway._client.update_balance_allowance, params)
        except Exception as exc:
            logger.warning(
                "merge allowance refresh failed match=%s err=%s", self._match_id, type(exc).__name__
            )
        balance = await gateway.collateral_balance()
        if balance > 0.0:
            self._host._budget_cache.value = balance
```

Notes:
- `cast(AssetType, AssetType.COLLATERAL)` is the existing pattern in `src/dashboard/collateral.py`. `AssetType` is a plain class of `str` constants.
- `WalletHost.store` is narrowed to `WalletStateStore` in `WalletHost.__init__`, so `store.apply_merge` and `store.has_unacked_matched` type-check.
- Order inside `_is_due`: the cheap in-memory checks come first. The `has_unacked_matched` SQL runs only when the $130 / 5-pair threshold is met, because `schedule()` runs on every feed tick.
- The sync block after `await self._call_adapter(...)` has no await until `_refresh_collateral`, which runs after the chain lock is released.

### 4. New `tests/test_trader_ctf_merge.py`

Copy this verbatim. It ran green in the scratch copy.

```python
# pyright: reportPrivateUsage=false, reportMissingTypeStubs=false

import asyncio
import time
from pathlib import Path
from typing import cast

import merge_probe
import pytest
import requests
from eth_typing import HexStr
from polymaker.config import Config, PathsConfig, Secrets, StrategyProfile
from polymaker.engine import Engine
from py_builder_relayer_client.client import RelayClient
from py_builder_relayer_client.exceptions import RelayerApiException, RelayerClientException
from trader_session_fixtures import make_meta
from web3 import Web3
from web3.eth import Eth
from web3.exceptions import TimeExhausted

from trader.ctf_merge import (
    ADAPTER_ABI,
    CTF_COLLATERAL_ADAPTER,
    PARTITION,
    PUSD,
    RECEIPT_TIMEOUT_S,
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


def _secrets(*, builder_key: str) -> Secrets:
    return Secrets(
        PK=TEST_PK,
        BROWSER_ADDRESS=WALLET,
        POLYGON_RPC=UNREACHABLE,
        POLY_BUILDER_KEY=builder_key,
        POLY_BUILDER_SECRET="c2VjcmV0",
        POLY_BUILDER_PASSPHRASE="pass",
        POLY_RELAYER_URL=UNREACHABLE,
    )


def _cfg(*, builder_key: str) -> Config:
    return Config(secrets=_secrets(builder_key=builder_key))


class _FakeVenue:
    def __init__(self, *, answer: object, receipt: object) -> None:
        self.answer = answer
        self.receipt = receipt
        self.bodies: list[dict[str, object]] = []
        self.waited: list[str] = []
        self.timeouts: list[float] = []

    def post(self, method: str, path: str, body: dict[str, object]) -> object:
        del method, path
        self.bodies.append(body)
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


def _answer_nonce(client: RelayClient, signer_address: str, signer_type: str) -> dict[str, str]:
    del client, signer_address, signer_type
    return {"nonce": "7"}


def _fail_nonce(client: RelayClient, signer_address: str, signer_type: str) -> dict[str, str]:
    del client, signer_address, signer_type
    raise RelayerApiException(error_msg="Request exception!")


def _relayer_error(status: int, body: bytes) -> RelayerApiException:
    response = requests.Response()
    response.status_code = status
    response._content = body
    return RelayerApiException(response)


def _install_venue(
    monkeypatch: pytest.MonkeyPatch, *, answer: object, receipt: object
) -> _FakeVenue:
    venue = _FakeVenue(answer=answer, receipt=receipt)
    monkeypatch.setattr(RelayClient, "get_nonce", _answer_nonce)
    monkeypatch.setattr(RelayClient, "_post_request", venue.post)
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
        cfg=_cfg(builder_key="key"), condition_id=CONDITION, amount_raw=20_000_000
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
    monkeypatch.setattr(RelayClient, "get_nonce", _fail_nonce)
    outcome = merge_pairs_via_adapter(
        cfg=_cfg(builder_key=builder_key), condition_id=CONDITION, amount_raw=1
    )
    assert outcome.status == "failed"
    assert outcome.reason.startswith("prepare: ")
    assert venue.bodies == []


@pytest.mark.parametrize(
    ("answer", "status"),
    [
        (_relayer_error(429, b'{"error": "quota exceeded"}'), "failed"),
        (_relayer_error(401, b'{"error": "invalid signature"}'), "failed"),
        (RelayerClientException("could not generate builder headers"), "failed"),
        (RelayerApiException(error_msg="Request exception!"), "unknown"),
        (_relayer_error(502, b"<html>bad gateway</html>"), "unknown"),
        (RuntimeError("socket closed"), "unknown"),
        ({"transactionID": "id-1"}, "unknown"),
        ("<html>ok</html>", "unknown"),
    ],
    ids=[
        "quota",
        "signature",
        "headers",
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
        cfg=_cfg(builder_key="key"), condition_id=CONDITION, amount_raw=1
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
        cfg=_cfg(builder_key="key"), condition_id=CONDITION, amount_raw=1
    )
    assert outcome.status == status
    assert outcome.tx_hash == TX


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


def test_merge_probe_reads_the_adapter_constants_from_ctf_merge() -> None:
    assert merge_probe.CTF_COLLATERAL_ADAPTER is CTF_COLLATERAL_ADAPTER
    assert merge_probe.PUSD is PUSD
    assert merge_probe.ADAPTER_ABI is ADAPTER_ABI
    assert merge_probe.ZERO32 is ZERO32
    assert merge_probe.PARTITION is PARTITION
    assert merge_probe.SHARE_BASE_UNITS is SHARE_BASE_UNITS
```

Why these fakes:
- `_FakeVenue.post` and `.wait` are bound methods set as class attributes, so Python does not bind the `RelayClient` / `Eth` instance to them. That is why they have no `client` / `eth` parameter. This was the one failure I hit in the scratch run.
- `engine.paper = False` makes the fork's real `_maybe_merge` reachable (it returns early in paper). With the stub installed nothing is added to `_merging` and no task is created.
- `post_timeout` is `RelayerApiException(error_msg="Request exception!")`. That is exactly what `http_helpers.request` raises when `requests` times out or loses the connection.

### 5. New `tests/test_trader_pair_merge.py`

Copy this verbatim, plus the **four STEP-004 assertions** marked `# STEP-004` here. Delete the marker comments when you paste: new code has no comments. Without STEP-004, the merged rows stop the core cursor and those assertions fail. The rest ran green on the current tree.

```python
# pyright: reportPrivateUsage=false

import asyncio
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, cast

import pytest
from polymaker.config import Config
from polymaker.domain import Fill, Side
from trader_session_fixtures import AlertRecorder

from shared.constants.strategy import LIVE_MAX_POSITION_LEVELS
from strategy.policy import follow300_policy
from strategy.types import FreshnessLimits, MarketLimits, TokenInventory
from trader import pair_merge
from trader.core_persistence import CoreSessionKey
from trader.core_session_io import SessionIdentity, consume_core_outbox
from trader.ctf_merge import MergeOutcome
from trader.pair_merge import PairMerger, pick_merge_pause_s
from trader.session_core import CollateralCache, LiveCore
from trader.trading_mode import ExecutionMode
from trader.wallet_store import WalletStateStore

CID = "0xcond"
MATCH = "8944931337"
YES = "yes-token"
NO = "no-token"
TOKENS = frozenset({YES, NO})
TX = "0xmerge"
IDENTITY = SessionIdentity(
    session_id=CID,
    key=CoreSessionKey(
        condition_id=CID, game="dota", yes_token=YES, no_token=NO, yes_is_radiant=True
    ),
)


class _FakeClobClient:
    def __init__(self) -> None:
        self.allowance_updates = 0

    def update_balance_allowance(self, params: object) -> None:
        del params
        self.allowance_updates += 1


class _FakeGateway:
    def __init__(self) -> None:
        self._client = _FakeClobClient()

    async def _io(self, fn: Callable[[object], None], arg: object) -> None:
        fn(arg)

    async def collateral_balance(self) -> float:
        return 205.0


class _FakeEngine:
    def __init__(self) -> None:
        self._chain_lock = asyncio.Lock()
        self.cfg = object()
        self.gateway = _FakeGateway()
        self.woken: list[str] = []

    def _wake_cid(self, condition_id: str) -> None:
        self.woken.append(condition_id)


class _FakeHost:
    def __init__(self, store: WalletStateStore, core: LiveCore) -> None:
        self.store = store
        self.core = core
        self.engine = _FakeEngine()
        self._budget_cache = CollateralCache()
        self.chain: dict[str, float] | None = {YES: 500.0, NO: 500.0}
        self.consume_error: Exception | None = None

    async def read_fresh_balances(self, token_ids: list[str]) -> dict[str, float] | None:
        del token_ids
        return self.chain

    def _consume_core_outbox(self, token_id: str) -> None:
        del token_id
        if self.consume_error is not None:
            raise self.consume_error
        consume_core_outbox(store=self.store, core=self.core, identity=IDENTITY, tokens=TOKENS)


class _ScriptedMerge:
    def __init__(self, outcome: MergeOutcome) -> None:
        self.outcome = outcome
        self.amounts: list[int] = []

    def __call__(self, *, cfg: Config, condition_id: str, amount_raw: int) -> MergeOutcome:
        del cfg, condition_id
        self.amounts.append(amount_raw)
        return self.outcome


def _buy(store: WalletStateStore, *, token_id: str, size: float, price: float, key: str) -> None:
    fill = Fill(token_id, Side.BUY, price, size, key, 1.0, is_maker=True)
    store.apply_confirmed_fill(fill, key)


def _open_host(tmp_path: Path, *, yes: float, no: float, price: float) -> _FakeHost:
    store = WalletStateStore(tmp_path / "w.db")
    _buy(store, token_id=YES, size=yes, price=price, key="t1:v1")
    _buy(store, token_id=NO, size=no, price=price, key="t2:v2")
    core = LiveCore(
        policy=follow300_policy(level_usdc=65.0, debounce_ms=100, fallback_timer_s=2.0),
        limits=MarketLimits(
            min_order_size=5.0, tick_size=0.01, pair_sum_tolerance=0.05, radiant_token_index=0
        ),
        freshness=FreshnessLimits(book_stale_s=5.0, entry_stale_s=16.0, exit_stale_s=45.0),
        yes_token=YES,
        no_token=NO,
        drop_sell=lambda _quote: False,
        trace=None,
        max_position_levels=LIVE_MAX_POSITION_LEVELS["dota"],
    )
    core.last_outbox_seq = store.core_outbox_after(after_seq=0, tokens=TOKENS)[-1].seq
    core.note_recovery(now_ns=1)
    core.drain_apply()
    core.note_recovery_verified(
        now_ns=2,
        generation=core.state.recovery_generation,
        inventory=(
            TokenInventory(token_index=0, qty=yes, cost_basis=yes * price, last_buy_ns=None),
            TokenInventory(token_index=1, qty=no, cost_basis=no * price, last_buy_ns=None),
        ),
    )
    core.drain_apply()
    return _FakeHost(store, core)


def _merger_for(host: _FakeHost, *, mode: ExecutionMode) -> PairMerger:
    merger = PairMerger(
        host=cast(Any, host), cid=CID, match_id=MATCH, yes_token=YES, no_token=NO, mode=mode
    )
    merger.attach_core(host.core)
    return merger


def _script(monkeypatch: pytest.MonkeyPatch, outcome: MergeOutcome) -> _ScriptedMerge:
    scripted = _ScriptedMerge(outcome)
    monkeypatch.setattr(pair_merge, "merge_pairs_via_adapter", scripted)
    return scripted


def _record_alerts(monkeypatch: pytest.MonkeyPatch) -> AlertRecorder:
    alerts = AlertRecorder()
    monkeypatch.setattr(pair_merge, "notify_in_background", alerts)
    return alerts


async def _schedule_and_wait(merger: PairMerger) -> None:
    merger.schedule()
    await merger.wait_inflight()


MERGED = MergeOutcome(status="merged", tx_hash=TX, reason="")


@pytest.mark.parametrize(
    ("yes", "no", "price", "calls"),
    [(150.0, 120.0, 0.50, 1), (150.0, 109.0, 0.50, 0), (258.0, 4.0, 0.50, 0)],
    ids=["held_135_pairs_120", "held_129_5", "pairs_4"],
)
def test_schedule_merges_at_130_usdc_held_and_5_pairs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    yes: float,
    no: float,
    price: float,
    calls: int,
) -> None:
    _record_alerts(monkeypatch)
    scripted = _script(monkeypatch, MERGED)
    host = _open_host(tmp_path, yes=yes, no=no, price=price)
    asyncio.run(_schedule_and_wait(_merger_for(host, mode="live")))
    assert len(scripted.amounts) == calls


async def _block_in_flight(merger: PairMerger, host: _FakeHost) -> None:
    del host
    merger._task = asyncio.get_running_loop().create_task(asyncio.sleep(0.05))


async def _block_unacked_matched(merger: PairMerger, host: _FakeHost) -> None:
    del merger
    late = Fill(YES, Side.BUY, 0.50, 10.0, "t3:v3", 2.0, is_maker=True)
    host.store.apply_matched_fill(late, "t3:v3")


async def _block_recovery(merger: PairMerger, host: _FakeHost) -> None:
    del merger
    host.core.note_recovery(now_ns=3)
    host.core.drain_apply()


async def _block_pause(merger: PairMerger, host: _FakeHost) -> None:
    del host
    merger._paused_until_s = time.monotonic() + 30.0


async def _block_disabled(merger: PairMerger, host: _FakeHost) -> None:
    del host
    merger._disabled = True


async def _block_quiesce(merger: PairMerger, host: _FakeHost) -> None:
    del host
    merger.mark_quiesced()


@pytest.mark.parametrize(
    "block",
    [
        _block_in_flight,
        _block_unacked_matched,
        _block_recovery,
        _block_pause,
        _block_disabled,
        _block_quiesce,
    ],
    ids=["in_flight", "unacked_matched", "recovery", "pause", "disabled", "quiesce"],
)
def test_schedule_skips_each_block(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    block: Callable[[PairMerger, _FakeHost], Awaitable[None]],
) -> None:
    _record_alerts(monkeypatch)
    scripted = _script(monkeypatch, MERGED)
    host = _open_host(tmp_path, yes=150.0, no=120.0, price=0.50)
    merger = _merger_for(host, mode="live")

    async def run() -> None:
        await block(merger, host)
        await _schedule_and_wait(merger)

    asyncio.run(run())
    assert scripted.amounts == []


def test_paper_mode_never_merges(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    scripted = _script(monkeypatch, MERGED)
    host = _open_host(tmp_path, yes=150.0, no=120.0, price=0.50)
    asyncio.run(_schedule_and_wait(_merger_for(host, mode="paper")))
    assert scripted.amounts == []


@pytest.mark.parametrize(
    ("chain", "amounts"),
    [
        (None, []),
        ({YES: 0.0, NO: 500.0}, []),
        ({YES: 200.0, NO: 100.1234567}, [100_123_456]),
    ],
    ids=["stale_chain", "zero_on_chain", "chain_below_store"],
)
def test_merge_amount_is_clamped_to_fresh_chain_balance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    chain: dict[str, float] | None,
    amounts: list[int],
) -> None:
    _record_alerts(monkeypatch)
    scripted = _script(monkeypatch, MERGED)
    host = _open_host(tmp_path, yes=150.0, no=120.0, price=0.50)
    host.chain = chain
    asyncio.run(_schedule_and_wait(_merger_for(host, mode="live")))
    assert scripted.amounts == amounts


def test_successful_merge_moves_store_core_cash_and_telegram(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    alerts = _record_alerts(monkeypatch)
    scripted = _script(monkeypatch, MERGED)
    host = _open_host(tmp_path, yes=150.0, no=120.0, price=0.50)
    cash_before = host.store.running_net_cash
    asyncio.run(_schedule_and_wait(_merger_for(host, mode="live")))
    assert scripted.amounts == [120_000_000]
    assert host.store.position(YES).size == pytest.approx(30.0)
    assert host.store.position(NO).size == 0.0
    assert host.store.running_net_cash == pytest.approx(cash_before + 120.0)
    yes_inventory, no_inventory = host.core.state.inventory                      # STEP-004
    assert yes_inventory.qty == pytest.approx(30.0)                              # STEP-004
    assert no_inventory.qty == 0.0                                               # STEP-004
    assert host.core.last_outbox_seq == host.store.core_outbox_after(            # STEP-004
        after_seq=0, tokens=TOKENS
    )[-1].seq
    assert host.core.state.recovery_pending is False
    assert host.engine.woken == [CID]
    assert host.engine.gateway._client.allowance_updates == 1
    assert host._budget_cache.value == 205.0
    assert alerts.messages == [f"trader merge: match {MATCH} pairs 120.00 tx {TX}"]


def test_core_consume_error_keeps_the_merge_booked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    alerts = _record_alerts(monkeypatch)
    _script(monkeypatch, MERGED)
    host = _open_host(tmp_path, yes=150.0, no=120.0, price=0.50)
    host.consume_error = RuntimeError("core snapshot write failed")
    merger = _merger_for(host, mode="live")
    asyncio.run(_schedule_and_wait(merger))
    assert host.store.position(YES).size == pytest.approx(30.0)
    assert merger._disabled is False
    assert merger._failures == 0
    assert host.engine.woken == [CID]
    assert len(alerts.messages) == 1


def test_failed_merge_alerts_pauses_and_success_resets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    alerts = _record_alerts(monkeypatch)
    scripted = _script(
        monkeypatch, MergeOutcome(status="failed", tx_hash="", reason="post: relayer 429: quota")
    )
    host = _open_host(tmp_path, yes=150.0, no=120.0, price=0.50)
    merger = _merger_for(host, mode="live")

    async def run() -> None:
        await _schedule_and_wait(merger)
        assert merger._failures == 1
        assert merger._paused_until_s - time.monotonic() == pytest.approx(30.0, abs=1.0)
        assert alerts.messages == [
            f"trader merge failed: match {MATCH} pause 30s tx -: post: relayer 429: quota"
        ]
        await _schedule_and_wait(merger)
        assert len(scripted.amounts) == 1
        assert host.store.position(YES).size == 150.0
        merger._paused_until_s = 0.0
        scripted.outcome = MERGED
        await _schedule_and_wait(merger)
        assert merger._failures == 0

    asyncio.run(run())
    assert [pick_merge_pause_s(failures) for failures in range(1, 7)] == [
        30.0,
        60.0,
        120.0,
        300.0,
        300.0,
        300.0,
    ]


def test_unknown_merge_turns_off_merges_for_the_map(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    alerts = _record_alerts(monkeypatch)
    scripted = _script(
        monkeypatch,
        MergeOutcome(status="unknown", tx_hash="0xdead", reason="receipt: TimeExhausted"),
    )
    host = _open_host(tmp_path, yes=150.0, no=120.0, price=0.50)
    merger = _merger_for(host, mode="live")

    async def run() -> None:
        await _schedule_and_wait(merger)
        await _schedule_and_wait(merger)
        await merger.merge_all()

    asyncio.run(run())
    assert scripted.amounts == [120_000_000]
    assert merger._disabled is True
    assert host.store.position(YES).size == 150.0
    assert alerts.messages == [
        f"trader merge unknown: match {MATCH} merges off for this map tx 0xdead: receipt: TimeExhausted"
    ]


def test_merge_call_without_outcome_in_time_is_unknown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    alerts = _record_alerts(monkeypatch)

    def hang(*, cfg: Config, condition_id: str, amount_raw: int) -> MergeOutcome:
        del cfg, condition_id, amount_raw
        time.sleep(0.3)
        return MERGED

    monkeypatch.setattr(pair_merge, "merge_pairs_via_adapter", hang)
    monkeypatch.setattr(pair_merge, "MERGE_CALL_TIMEOUT_S", 0.05)
    host = _open_host(tmp_path, yes=150.0, no=120.0, price=0.50)
    merger = _merger_for(host, mode="live")
    asyncio.run(_schedule_and_wait(merger))
    assert merger._disabled is True
    assert host.store.position(YES).size == 150.0
    assert "merges off for this map tx -" in alerts.messages[0]


def test_merge_all_ignores_quiesce_and_pause_and_needs_one_cent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _record_alerts(monkeypatch)
    scripted = _script(monkeypatch, MERGED)
    host = _open_host(tmp_path, yes=0.5, no=0.005, price=0.50)
    host.chain = {YES: 0.5, NO: 0.5}
    merger = _merger_for(host, mode="live")
    merger.mark_quiesced()
    merger._paused_until_s = time.monotonic() + 300.0

    async def run() -> None:
        await merger.merge_all()
        assert scripted.amounts == []
        _buy(host.store, token_id=NO, size=0.015, price=0.50, key="t4:v4")
        await merger.merge_all()

    asyncio.run(run())
    assert scripted.amounts == [20_000]
```

Notes on the tests:
- The core is a Follow300 `LiveCore`, as in STEP-004's consume test. `consume_core_outbox` persists a snapshot through `export_checkpoint`, which needs `policy.sell_min_life_s`. STEP-006 widens `LiveCore`.
- `_open_host` moves the core cursor past the two `confirmed` rows. Then it runs Recovery → RecoveryVerified with the store sizes, which is B's restart path. Only the merged rows reach the core, and it ends with `recovery_pending=False`.
- `self.cfg = object()` in the fake engine: the scripted merge ignores `cfg`. `Config()` would read E's `.env`, which holds real keys.
- The success test covers every part of the feature's success rule: store, core, cursor, wake, allowance, cache and Telegram. The other tests cover each block, the clamp, the pause ladder, the reset on success, the unknown rule including `merge_all`, the 190 s bound and the `merge_all` flags.
- `trader_session_fixtures` and `merge_probe` resolve because `make test` puts `tests` and `scripts` on the path, and so do the pytest commands below.

## Edge cases: every failure phase and its outcome

| Phase | What happens | Outcome | Effect |
|---|---|---|---|
| prepare | builder creds missing/blank → `BuilderConfig` raises `ValueError` | failed | alert, pause |
| prepare | calldata encode fails (malformed condition id) | failed | alert, pause |
| prepare | nonce GET: transport error, non-200, text body, no `nonce` key | failed | alert, pause. A GET has no side effects |
| prepare | EIP-712 signing fails | failed | alert, pause |
| post | `RelayerClientException` (builder headers not generated, before the request) | failed | alert, pause |
| post | 4xx with body (quota 429, signature 400/401) | failed | alert, pause 30/60/120/300 |
| post | 5xx / other non-200 | unknown | merges off for the map, alert |
| post | transport error or timeout (`RelayerApiException`, `status_code None`) | unknown | merges off, alert |
| post | any other exception | unknown | merges off, alert |
| post | 200 without `transactionHash`, or non-JSON 200 | unknown | merges off, alert |
| receipt | `TimeExhausted` after 180 s | unknown (tx kept) | merges off, alert with tx |
| receipt | RPC error while polling | unknown (tx kept) | merges off, alert with tx |
| receipt | receipt without `status` | unknown (tx kept) | merges off, alert with tx |
| receipt | `status == 0` (revert) | failed (tx kept) | alert with tx, pause |
| receipt | `status == 1` | merged | book, see below |
| whole call | no outcome in 190 s (hung `requests`, which has no timeout) | unknown | merges off. The leaked thread books nothing |
| booking | `apply_merge` returns False (tx already in the ledger) | merged | consume + wake, no second Telegram line |
| booking | `consume_core_outbox` raises | merged | logged. Core corrected by the next Recovery (STEP-004) |
| booking | allowance refresh raises / balance read returns 0 | merged | logged / cache untouched until the next REST positions read |

Other cases:
- **Chain read not fresh** (`read_fresh_balances` → None, e.g. a block older than the last fill's chain floor): no attempt, no pause. The next feed tick tries again.
- **Chain below store** (MATCHED not mined yet): merge `min(store, chain)`. **Chain above store** (fill not booked yet): `min()` uses the store, so the ledger stays consistent.
- **Store below `qty` at booking** (a MATCHED that failed during the call): STEP-003 clamps at 0 and credits the full cash.
- **BUY fill during the call**: the store grows. `apply_merge` subtracts only `qty`.
- **Two B maps at once**: `engine._chain_lock` serializes merges on the wallet (relayer nonce). A's engine and wallet are separate.
- **Cancel mid-call** (`cancel()` from `MatchWorker.run`'s finally): only reachable if the shielded quiesce did not wait. The thread may still merge, and the result is unbooked like `unknown`. Chain reconcile fixes sizes. The pUSD balance is the truth.
- **Unknown, then the tx lands**: store sizes stay above chain until chain reconcile. Merges stay off for the map. Searching the chain for the outcome is a Non-Goal.
- **Paper**: `schedule()` returns on mode. `merge_all()` gets None from `read_fresh_balances` (it returns None for `engine.paper`).
- **Quota exhausted for the day**: each attempt returns 429 → failed → pauses grow to 300 s. Counting quota is a Non-Goal.

## What later steps need from this step

**STEP-006 (TwoSidedWorker):**
- Build the slot with `PairMerger(host=host, cid=cid, match_id=discovered.match_id, yes_token=yes, no_token=no, mode=mode)` and call `attach_core` in `open_core`, as `DustSweeper` does.
- `MatchWorker.__init__` types `self._dust` as `DustSweeper`. `_quiesce` calls `await self._dust.sweep(force=True)`, and `PairMerger` has no `sweep`. Pick one:
  - (a) widen the slot type to `DustSweeper | PairMerger` and add a no-op `async def sweep(self, *, force: bool) -> None` to `PairMerger` then;
  - (b) or override the parts of `_quiesce` that touch the slot.
  Option (a) is the smaller diff.
- `schedule()` is driven by `MatchWorker.run` on every feed tick while `_quoting` (plus `note_fill` on SELL, which B never has). That is enough for the threshold merge. Nothing else needs to call it.
- `_finish_final`:
  - Call `await self._dust.merge_all()` **after** the proven fence.
  - It must run **before** `end_snapshot()`, so the Telegram net includes merge cash.
  - It must run **before** `zero_token_sizes`, which would wipe the sizes it merges.
  - It must run **before** `unregister_worker`, because `PairMerger` feeds the core through `host._consume_core_outbox`, which needs the registered worker.
- `_quiesce` awaits `self._dust.wait_inflight()` **before** `stop_quoter` and `cancel_market`. With `PairMerger` that wait can last up to `MERGE_CALL_TIMEOUT_S` (190 s) in the pathological case.
  - The two-sided core pulls both bids on game end and pause by itself (`two_sided_pull_reason`), and the quoter keeps running during the wait.
  - If STEP-006 wants a hard guarantee, it should cancel before waiting.
- Worker tests through `build_attached_worker` use `FakeWalletHost`, which lacks `read_fresh_balances`, `_consume_core_outbox`, `_budget_cache` and a `WalletStateStore`. Replace `worker._dust.merge_all` with a recorder to check ordering, or extend the fake.
- From STEP-004: widen `LiveCore` to `Policy` so that `export_checkpoint` works for `TwoSidedPolicy`. Every merge runs `consume_core_outbox`, which persists a snapshot.

**STEP-007:** call `install_adapter_merge(self.engine)` in `WalletHost._install_runtime_seams` only for `two_sided`. Startup must require `has_builder_creds`. Without it every merge is `failed: prepare: ValueError` and pauses forever.

**STEP-008:** `stop_grace_period: 200s` covers one in-flight merge (190 s). It does not also cover the cancel and the 20 s fence after it. 240 s covers both. Leave the call to STEP-008.

**STEP-009 (runbook):**
- Telegram lines are `trader merge: match … pairs … tx …`, `trader merge failed: match … pause …s tx …: …` and `trader merge unknown: match … merges off for this map tx …: …`.
- The adapter must be approved on CTF for wallet B (`merge_probe.py --signature-type 3` prints `adapter approved`). Otherwise every merge reverts (`receipt status 0`) and pauses.

## Do not touch

- `src/trader/match_worker.py`, `src/trader/wallet_host.py`, `src/trader/engine_seams.py`, `src/trader/dust_sweep.py`.
- `src/trader/wallet_store.py`, `src/trader/core_session_io.py`, `src/strategy/**`.
- `config/**`, `compose.yaml`.
- Every existing test file, `../poly-maker`, and the untracked `data/backtests/dota_maker/validation_*ts-regress-*` dirs (do not stage them).

## Verification (in E)

```bash
cd /Users/dimabytes/work/polymarket/dota_2_bot/esports-trader

# step tests + dust sweep (feature list)
PYTHONPATH=src:scripts:../prediction-market-backtesting uv run --group backtest python -m pytest \
  tests/test_trader_ctf_merge.py tests/test_trader_pair_merge.py tests/test_trader_dust_sweep.py -q

# neighbours: ledger, outbox consume, host, seams, worker lifecycle (A regression)
PYTHONPATH=src:scripts:../prediction-market-backtesting uv run --group backtest python -m pytest \
  tests/test_trader_wallet_store.py tests/test_trader_core_persistence.py \
  tests/test_trader_session_core.py tests/test_trader_wallet_host.py \
  tests/test_trader_engine_seams.py tests/test_trader_match_lifecycle.py \
  tests/test_paper_gateway.py tests/test_strategy_core.py tests/test_follow300_replay.py -q

# the probe still starts (PYTHONPATH=src is how the script is run; the Docker image sets it too)
PYTHONPATH=src uv run python scripts/merge_probe.py --help

# no comments/docstrings in new code; McCabe stays under 10
grep -nE '^\s*#[^ ]|^\s*# |"""' src/trader/ctf_merge.py src/trader/pair_merge.py | grep -v '# pyright:'   # expect nothing
uv run ruff check --select C901 src/trader/ctf_merge.py src/trader/pair_merge.py

# only intended files changed
git status --short   # src/trader/ctf_merge.py, src/trader/pair_merge.py, scripts/merge_probe.py, two new tests + untracked backtest dirs

# typecheck (strict, whole project) and lint on staged files
uv run python -m basedpyright
git add src/trader/ctf_merge.py src/trader/pair_merge.py scripts/merge_probe.py \
  tests/test_trader_ctf_merge.py tests/test_trader_pair_merge.py
make lint
```

Expected:
- Everything is green.
- `--help` prints the usage.
- basedpyright reports 0 errors.
- If `make lint` reformats or re-sorts imports, re-stage and run it again.
- Leave `PYTEST_N` unset. A full serial suite is STEP-010's job. Run `make test` here too if time allows.
- No command in this step touches the network. If a test hangs, a network patch is missing. Check that `_install_venue` ran before the call.

Then, per the implement skill:
- One commit in E on `main`. Suggested message: `Merge YES+NO pairs through the pUSD adapter with phase-classified outcomes.`
- Set `passes: true` for STEP-005 in `W/tasks/two-sided-live-b/feature.json`.
- Append to `W/tasks/two-sided-live-b/progress.txt`: decisions 1, 2, 5, 8 and 9, plus the STEP-006 handoff (slot type and `sweep`; `merge_all` order; `wait_inflight` before cancel).
- No push.
