"""External API endpoints and the HTTP knobs shared by every caller."""

import os

POLYMARKET_DATA_API = "https://data-api.polymarket.com"
POLYMARKET_GAMMA_API = "https://gamma-api.polymarket.com"
POLYMARKET_CLOB_API = "https://clob.polymarket.com"
OPENDOTA_API = "https://api.opendota.com/api"

HTTP_TIMEOUT_SECONDS = float(os.getenv("HTTP_TIMEOUT_SECONDS", "30"))
HTTP_LIMIT = int(os.getenv("HTTP_LIMIT", "500"))

# How far back from an anchor (game start) to pull Polymarket minute quotes.
QUOTE_TRAILING_SECONDS = 6 * 3600
