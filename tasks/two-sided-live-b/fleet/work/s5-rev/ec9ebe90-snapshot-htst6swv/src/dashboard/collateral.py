# pyright: reportMissingTypeStubs=false

import math
from collections.abc import Callable, Mapping
from typing import Protocol, cast

from py_clob_client_v2.client import ClobClient
from py_clob_client_v2.clob_types import AssetType, BalanceAllowanceParams

from dashboard.balance import BalanceAccount, BalanceRead, parse_collateral_usdc


class BalanceClient(Protocol):
    def derive_api_key(self) -> object: ...
    def set_api_creds(self, creds: object) -> None: ...
    def get_balance_allowance(self, params: object) -> object: ...


def build_balance_client(account: BalanceAccount) -> BalanceClient:
    client = cast(
        BalanceClient,
        ClobClient(
            host=account.host,
            chain_id=account.chain_id,
            key=account.pk,
            signature_type=account.signature_type,
            funder=account.funder,
        ),
    )
    creds = client.derive_api_key()
    client.set_api_creds(creds)
    return client


def _status_code(exc: Exception) -> int | None:
    code = getattr(exc, "status_code", None)
    if isinstance(code, bool) or not isinstance(code, int):
        return None
    return code


def _retry_after_s(exc: Exception) -> float | None:
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    if headers is None:
        headers = getattr(exc, "headers", None)
    if not isinstance(headers, Mapping):
        return None
    header_map = cast(Mapping[str, object], headers)
    raw = header_map.get("Retry-After") or header_map.get("retry-after")
    if raw is None:
        return None
    if not isinstance(raw, (int, float, str)) or isinstance(raw, bool):
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) and value >= 0.0 else None


class CollateralReader:
    def __init__(
        self,
        account: BalanceAccount,
        *,
        client_factory: Callable[[BalanceAccount], BalanceClient] | None = None,
    ) -> None:
        self._account = account
        self._factory = client_factory if client_factory is not None else build_balance_client
        self._client: BalanceClient | None = None

    def read(self) -> BalanceRead:
        try:
            if self._client is None:
                self._client = self._factory(self._account)
            response = self._client.get_balance_allowance(
                BalanceAllowanceParams(asset_type=cast(AssetType, AssetType.COLLATERAL))
            )
            amount = parse_collateral_usdc(response)
        except Exception as exc:
            return BalanceRead(
                ok=False,
                collateral_usdc=None,
                error=type(exc).__name__,
                status_code=_status_code(exc),
                retry_after_s=_retry_after_s(exc),
            )
        return BalanceRead(
            ok=True,
            collateral_usdc=amount,
            error=None,
            status_code=None,
            retry_after_s=None,
        )
