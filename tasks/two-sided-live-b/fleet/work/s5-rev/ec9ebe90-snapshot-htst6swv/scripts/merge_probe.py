"""Probe the pUSD merge path for our wallet on one CTF market.

poly-maker's Merger calls ConditionalTokens.mergePositions with USDC.e as
collateral. Polymarket now collateralises with pUSD and merges through
CtfCollateralAdapter, which burns YES+NO and mints pUSD to the wallet in one tx
(docs.polymarket.com/trading/ctf/split). This probe keeps poly-maker's wallet
signing and only swaps the inner call, so poly-maker stays untouched:
Gnosis Safe (--signature-type 2) goes through Merger._merge_safe and the owner
EOA pays gas; deposit wallet (3) goes through Merger._merge_deposit_wallet and
the builder relayer pays gas (needs POLY_BUILDER_KEY/SECRET/PASSPHRASE).

Read-only by default: prints the wallet's pUSD balance, both token balances and
whether the adapter is approved on ConditionalTokens. Nothing is sent without
--send.

    PYTHONPATH=src uv run python scripts/merge_probe.py --slug cs2-...
    PYTHONPATH=src uv run python scripts/merge_probe.py --slug cs2-... --buy-pairs 5 --merge-shares 5 --send

--buy-pairs crosses the spread on both outcomes (taker, FAK) through
poly-maker's ExecutionGateway, waits for the shares on-chain, then merges.
PK and BROWSER_ADDRESS come from the environment or .env.
"""

# web3 contract calls are untyped; Merger internals are patched on purpose (poly-maker is frozen).
# pyright: reportUnknownVariableType=false, reportUnknownMemberType=false
# pyright: reportUnknownArgumentType=false, reportPrivateUsage=false

import argparse
import asyncio
import math
import time
from dataclasses import dataclass
from pathlib import Path

import httpx
from polymaker.catalog.gamma import parse_market
from polymaker.config import Config
from polymaker.domain import MarketMeta, Side
from polymaker.execution.gateway import ExecutionGateway
from polymaker.merge import CONDITIONAL_TOKENS, Merger, _to_bytes32
from web3.contract import Contract
from web3.contract.contract import ContractFunction

from trader.ctf_merge import (
    ADAPTER_ABI,
    CTF_COLLATERAL_ADAPTER,
    PARTITION,
    PUSD,
    SHARE_BASE_UNITS,
    ZERO32,
)

GAMMA_MARKETS = "https://gamma-api.polymarket.com/markets"
TAKER_PRICE_CUSHION = 1.08
SETTLE_TIMEOUT_S = 90.0

ERC20_ABI = [
    {
        "name": "balanceOf",
        "type": "function",
        "stateMutability": "view",
        "inputs": [{"name": "account", "type": "address"}],
        "outputs": [{"type": "uint256"}],
    }
]
CTF_ABI = [
    {
        "name": "isApprovedForAll",
        "type": "function",
        "stateMutability": "view",
        "inputs": [{"name": "owner", "type": "address"}, {"name": "operator", "type": "address"}],
        "outputs": [{"type": "bool"}],
    },
    {
        "name": "setApprovalForAll",
        "type": "function",
        "stateMutability": "nonpayable",
        "inputs": [{"name": "operator", "type": "address"}, {"name": "approved", "type": "bool"}],
        "outputs": [],
    },
    {
        "name": "balanceOf",
        "type": "function",
        "stateMutability": "view",
        "inputs": [{"name": "account", "type": "address"}, {"name": "id", "type": "uint256"}],
        "outputs": [{"type": "uint256"}],
    },
]


@dataclass(frozen=True)
class WalletCall:
    to: str
    data: str


def fetch_market(slug: str) -> MarketMeta:
    response = httpx.get(GAMMA_MARKETS, params={"slug": slug}, timeout=15.0)
    response.raise_for_status()
    rows = response.json()
    if not rows:
        raise SystemExit(f"no Gamma market for slug {slug!r}")
    version = rows[0].get("version")
    if version != "v1":
        raise SystemExit(f"market {slug!r} is CTF {version}; the adapter merge covers v1 only")
    meta = parse_market(rows[0])
    if meta is None:
        raise SystemExit(f"market {slug!r} is not accepting orders or is not binary")
    return meta


def encode_call(to: str, fn: ContractFunction) -> WalletCall:
    return WalletCall(to=to, data=fn._encode_transaction_data())


def send_call(merger: Merger, call: WalletCall, label: str) -> str:
    merger._inner_merge_call = lambda condition_id, amount_raw, neg_risk: (call.to, call.data)
    if merger._cfg.wallet.signature_type == 2:
        return merger._merge_safe(label, 0, False)
    return merger._merge_deposit_wallet(label, 0, False)


@dataclass(frozen=True)
class Chain:
    merger: Merger
    meta: MarketMeta
    wallet: str
    adapter_address: str
    pusd: Contract
    ctf: Contract
    adapter: Contract

    @classmethod
    def connect(cls, cfg: Config, meta: MarketMeta) -> "Chain":
        merger = Merger(cfg)
        merger._ensure_web3()
        w3 = merger._w3
        adapter_address = w3.to_checksum_address(CTF_COLLATERAL_ADAPTER)
        return cls(
            merger=merger,
            meta=meta,
            wallet=w3.to_checksum_address(cfg.secrets.browser_address),
            adapter_address=adapter_address,
            pusd=w3.eth.contract(address=w3.to_checksum_address(PUSD), abi=ERC20_ABI),
            ctf=w3.eth.contract(address=w3.to_checksum_address(CONDITIONAL_TOKENS), abi=CTF_ABI),
            adapter=w3.eth.contract(address=adapter_address, abi=ADAPTER_ABI),
        )

    def pusd_balance(self) -> float:
        return self.pusd.functions.balanceOf(self.wallet).call() / SHARE_BASE_UNITS

    def approved(self) -> bool:
        return self.ctf.functions.isApprovedForAll(self.wallet, self.adapter_address).call()

    def token_balances(self) -> tuple[float, float]:
        first, second = (
            self.ctf.functions.balanceOf(self.wallet, int(token.token_id)).call() / SHARE_BASE_UNITS
            for token in self.meta.tokens
        )
        return first, second


def ensure_approved(chain: Chain, *, send: bool) -> None:
    if chain.approved():
        return
    approval = encode_call(
        chain.ctf.address,
        chain.ctf.functions.setApprovalForAll(chain.adapter_address, True),
    )
    print(f"approval call -> {approval.to} data {approval.data[:18]}...")
    if send:
        tx_hash = send_call(chain.merger, approval, "setApprovalForAll")
        print(f"approval tx {tx_hash}  adapter approved now: {chain.approved()}")


def merge_pairs(
    chain: Chain, shares: float, held: tuple[float, float], pusd_before: float, *, send: bool
) -> None:
    if not chain.approved():
        raise SystemExit("adapter not approved; run with --approve --send first")
    if send and min(held) < shares:
        raise SystemExit(f"holding {held[0]:.2f} / {held[1]:.2f} shares, cannot merge {shares}")
    merge_call = encode_call(
        chain.adapter_address,
        chain.adapter.functions.mergePositions(
            chain.pusd.address,
            ZERO32,
            _to_bytes32(chain.meta.condition_id),
            PARTITION,
            math.floor(shares * SHARE_BASE_UNITS),
        ),
    )
    print(f"merge {shares:g} pairs -> {merge_call.to} data {merge_call.data[:18]}...")
    if send:
        tx_hash = send_call(chain.merger, merge_call, chain.meta.condition_id)
        pusd_after = chain.pusd_balance()
        held_after = chain.token_balances()
        print(
            f"merge tx {tx_hash}  pUSD {pusd_before:.6f} -> {pusd_after:.6f} ({pusd_after - pusd_before:+.6f})  "
            f"held {held_after[0]:.2f} / {held_after[1]:.2f}"
        )


async def buy_pairs(cfg: Config, meta: MarketMeta, pairs: float) -> None:
    gateway = ExecutionGateway(cfg)
    await gateway.connect()
    try:
        for token in meta.tokens:
            book = await gateway.get_book(token.token_id)
            best_ask = book.get("best_ask", 0.0)
            if not 0.0 < best_ask < 1.0:
                raise SystemExit(f"no ask on {token.outcome}: {book}")
            usd = round(pairs * best_ask * TAKER_PRICE_CUSHION + 0.05, 2)
            response = await gateway.market_order(token.token_id, Side.BUY, usd, meta, fak=True)
            status = str(response.get("status", ""))
            shares = float(response.get("takingAmount") or 0.0)
            spent = float(response.get("makingAmount") or 0.0)
            print(
                f"BUY {token.outcome}: ask {best_ask} sent ${usd} -> {shares:.2f} shares for ${spent:.2f} [{status}]"
            )
            if shares < pairs:
                raise SystemExit(
                    f"{token.outcome} filled {shares:.2f} < {pairs} shares; not merging"
                )
        deadline = time.monotonic() + SETTLE_TIMEOUT_S
        while True:
            balances = [await gateway.token_balance(token.token_id) for token in meta.tokens]
            if min(balances) >= pairs:
                print(f"on-chain balances: {balances[0]:.2f} / {balances[1]:.2f} shares")
                return
            if time.monotonic() > deadline:
                raise SystemExit(
                    f"shares not settled on-chain after {SETTLE_TIMEOUT_S:.0f}s: {balances}"
                )
            await asyncio.sleep(3.0)
    finally:
        gateway.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--config-dir", type=Path, default=Path("config"))
    parser.add_argument("--slug", required=True, help="Gamma market slug (binary, CTF v1)")
    parser.add_argument(
        "--signature-type",
        type=int,
        choices=(2, 3),
        default=2,
        help="2 Gnosis Safe (owner pays gas), 3 deposit wallet (relayer pays gas)",
    )
    parser.add_argument(
        "--buy-pairs",
        type=float,
        default=0.0,
        help="taker-buy this many shares of both outcomes first",
    )
    parser.add_argument(
        "--approve", action="store_true", help="setApprovalForAll(adapter) from the wallet"
    )
    parser.add_argument("--merge-shares", type=float, default=0.0, help="YES+NO pairs to merge")
    parser.add_argument(
        "--send", action="store_true", help="actually trade / sign and send; default is read-only"
    )
    args = parser.parse_args()

    cfg = Config.load(args.config_dir)
    cfg.wallet.signature_type = args.signature_type
    if not cfg.secrets.has_wallet:
        raise SystemExit("PK / BROWSER_ADDRESS missing in the environment")
    if args.signature_type == 3 and not cfg.secrets.has_builder_creds:
        raise SystemExit("deposit wallet merges need POLY_BUILDER_KEY/SECRET/PASSPHRASE")

    meta = fetch_market(args.slug)
    if meta.neg_risk:
        raise SystemExit(
            "negRisk market: that path uses NegRiskCtfCollateralAdapter, not covered here"
        )
    print(
        f"market {meta.slug}  condition {meta.condition_id}  tick {meta.tick_size:g}  min {meta.min_order_size:g}"
    )

    chain = Chain.connect(cfg, meta)
    pusd_before = chain.pusd_balance()
    held = chain.token_balances()
    print(
        f"wallet {chain.wallet}  pUSD {pusd_before:.6f}  held {held[0]:.2f} {meta.yes.outcome} / {held[1]:.2f} {meta.no.outcome}  adapter approved: {chain.approved()}"
    )

    if args.buy_pairs > 0 and args.send:
        asyncio.run(buy_pairs(cfg, meta, args.buy_pairs))
        held = chain.token_balances()
    if args.approve:
        ensure_approved(chain, send=args.send)
    if args.merge_shares > 0:
        merge_pairs(chain, args.merge_shares, held, pusd_before, send=args.send)
    if not args.send and (args.buy_pairs > 0 or args.approve or args.merge_shares > 0):
        print("dry run: nothing sent; add --send")


if __name__ == "__main__":
    main()
