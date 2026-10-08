"""Execution timing shared by trader reconciliation and dust cleanup."""

# The CLOB refuses a SELL while the shares back a trade it already matched. Our
# MATCHED event lags that match by 8-10s, so a balance reject holds SELL for this
# long instead of waiting for a fill that may never come.
SELL_REJECT_HOLD_SECONDS = 12.0

# ponytail: fixed pause; exponential backoff if logs show a long crosses-book series
CROSS_REJECT_HOLD_SECONDS = 3.0
