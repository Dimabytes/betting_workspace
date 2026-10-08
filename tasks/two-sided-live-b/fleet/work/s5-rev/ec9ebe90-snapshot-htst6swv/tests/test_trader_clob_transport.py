"""CLOB transport replacement and lifetime without network requests.

HTTPX has no public protocol configuration getter. The private path
_transport._pool._http2 is checked against httpx 0.28.1 / httpcore 1.0.9;
dependency upgrades may require updating this inspection.
"""

# pyright: reportPrivateUsage=false, reportMissingTypeStubs=false

from typing import cast

import httpx
from py_clob_client_v2.http_helpers import helpers

from trader.clob_transport import use_http1_clob_transport


def test_use_http1_clob_transport_replaces_client_once() -> None:
    """Switch protocol, preserve defaults, and close only the replaced client."""
    original = helpers._http_client
    old = httpx.Client(http2=True)
    try:
        helpers._http_client = old
        use_http1_clob_transport()
        replacement = helpers._http_client
        transport = cast(httpx.HTTPTransport, replacement._transport)
        assert replacement is not old
        assert transport._pool._http2 is False
        assert old.is_closed is True
        assert replacement.is_closed is False
        assert replacement.timeout == old.timeout
        assert replacement.headers == old.headers

        use_http1_clob_transport()
        assert helpers._http_client is replacement
        assert replacement.is_closed is False
    finally:
        helpers._http_client.close()
        old.close()
        helpers._http_client = original
