"""Select the process-wide transport used by the CLOB SDK."""

# pyright: reportPrivateUsage=false, reportMissingTypeStubs=false

from typing import cast

import httpx
from py_clob_client_v2.http_helpers import helpers

from shared.utils.log import get_logger

logger = get_logger(__name__)


def use_http1_clob_transport() -> None:
    """Use separate connections for concurrent CLOB requests.

    Call only at process startup, before any CLOB requests. HTTP/2 shares
    a socket between concurrent requests, including their reads and writes;
    HTTP/1.1 gives each concurrent request its own connection.
    """
    old = helpers._http_client
    transport = cast(httpx.HTTPTransport, old._transport)
    if transport._pool._http2 is False:
        return
    helpers._http_client = httpx.Client(http2=False)
    old.close()
    logger.info("trader CLOB transport: HTTP/1.1")
