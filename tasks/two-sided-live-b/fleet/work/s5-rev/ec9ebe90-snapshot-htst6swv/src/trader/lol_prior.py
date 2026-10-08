"""LoL live prior from the CLOB book, same window as training."""

from collections.abc import Mapping

from lol.constants import LOL_PRIOR_WINDOW_SECONDS
from shared.utils.telonex_book import US_PER_SECOND, TokenBook, lookup_strict_prior


class LolPriorTape:
    """Two-sided book samples kept until the condition leaves the watch set."""

    def __init__(self) -> None:
        self._yes: dict[str, str] = {}
        self._no: dict[str, str] = {}
        self._open: set[str] = set()
        self._samples: dict[str, list[tuple[int, float, float]]] = {}

    def retain(self, markets: Mapping[str, tuple[str, str]]) -> None:
        """Keep samples for these conditions and drop every other condition."""
        keep = set(markets)
        for cid in list(self._yes):
            if cid not in keep:
                self._yes.pop(cid)
                self._no.pop(cid)
        for cid, (yes, no) in markets.items():
            self._yes.setdefault(cid, yes)
            self._no.setdefault(cid, no)
        self._open = set(self._yes.values()) | set(self._no.values())
        for token_id in list(self._samples):
            if token_id not in self._open:
                self._samples.pop(token_id)

    def note(self, token_id: str, ts_seconds: float, bid: float | None, ask: float | None) -> None:
        """Append one two-sided print. Out-of-order or one-sided prints are ignored."""
        if token_id not in self._open or bid is None or ask is None or ts_seconds <= 0:
            return
        ts_us = round(ts_seconds * US_PER_SECOND)
        samples = self._samples.setdefault(token_id, [])
        row = (ts_us, bid, ask)
        if samples and ts_us < samples[-1][0]:
            return
        if samples and ts_us == samples[-1][0]:
            samples[-1] = row
            return
        samples.append(row)

    def prior(
        self, yes_token: str, no_token: str, horn_unix: int, *, yes_is_radiant: bool
    ) -> float | None:
        """P(Radiant) from the last two-sided pair in [horn - 61s, horn)."""
        yes = self._book(yes_token)
        no = self._book(no_token)
        radiant, dire = (yes, no) if yes_is_radiant else (no, yes)
        return lookup_strict_prior(
            radiant, dire, horn_unix * US_PER_SECOND, LOL_PRIOR_WINDOW_SECONDS
        )

    def _book(self, token_id: str) -> TokenBook:
        samples = self._samples.get(token_id, ())
        return TokenBook(
            timestamps_us=tuple(item[0] for item in samples),
            bids=tuple(item[1] for item in samples),
            asks=tuple(item[2] for item in samples),
        )
