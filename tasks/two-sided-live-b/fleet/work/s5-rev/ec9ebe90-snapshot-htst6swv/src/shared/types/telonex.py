"""Read schema for Telonex `book_snapshot_full` parquet files.

On disk every level stores price/size as strings, and a whole-day side can
come out as list<null>; reading with this schema casts strings to float64
and promotes null-typed columns to typed levels.
"""

import pyarrow as pa

TELONEX_BOOK_LEVELS = pa.list_(
    pa.struct([pa.field("price", pa.float64()), pa.field("size", pa.float64())])
)
TELONEX_BOOK_SCHEMA = pa.schema(
    [
        pa.field("timestamp_us", pa.int64()),
        pa.field("bids", TELONEX_BOOK_LEVELS),
        pa.field("asks", TELONEX_BOOK_LEVELS),
    ]
)
