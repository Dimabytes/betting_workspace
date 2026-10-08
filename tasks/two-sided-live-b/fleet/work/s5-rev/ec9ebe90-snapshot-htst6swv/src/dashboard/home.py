import math
from datetime import datetime
from urllib.parse import quote

from dashboard import summarize
from dashboard.catalog import ArchiveEntry, MapView
from dashboard.hub_types import HubSnapshot


def fmt_money(value: float | None, *, signed: bool = False) -> str:
    if value is None or not math.isfinite(value):
        return "—"
    if signed:
        return f"{value:+.2f}"
    return f"{value:.2f}"


def fmt_usd(value: float | None, *, signed: bool = False) -> str:
    text = fmt_money(value, signed=signed)
    return text if text == "—" else f"${text}"


def fmt_age(age_s: float | None) -> str:
    if age_s is None:
        return "возраст ?"
    seconds = int(age_s)
    if seconds < 60:
        return f"{seconds}с"
    minutes, sec = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}м{sec:02d}с"
    hours, minute = divmod(minutes, 60)
    return f"{hours}ч{minute:02d}м"


_MONTHS = (
    "янв",
    "фев",
    "мар",
    "апр",
    "мая",
    "июн",
    "июл",
    "авг",
    "сен",
    "окт",
    "ноя",
    "дек",
)


def fmt_clock(ts: float) -> str:
    local = datetime.fromtimestamp(ts, summarize.BERLIN)
    return f"{local.day} {_MONTHS[local.month - 1]} {local.hour:02d}:{local.minute:02d}"


def fmt_pnl_since(now_s: float) -> str:
    day = datetime.fromtimestamp(now_s, summarize.BERLIN).date()
    start, _end = summarize.berlin_day_bounds(day)
    return fmt_clock(start)


def fmt_second(second: float | None) -> str:
    if second is None:
        return "—"
    total = int(second)
    sign = "-" if total < 0 else ""
    minutes, sec = divmod(abs(total), 60)
    return f"{sign}{minutes}:{sec:02d}"


def match_url(match_id: str) -> str:
    return f"?match={quote(match_id, safe='')}"


def polymarket_url(event_slug: str | None, market_slug: str | None) -> str | None:
    slug = event_slug or market_slug
    if slug is None:
        return None
    return f"https://polymarket.com/event/{quote(slug, safe='')}"


def map_title(entry: ArchiveEntry) -> str:
    teams = f"{entry.radiant} vs {entry.dire}" if entry.radiant and entry.dire else None
    if teams is None:
        base = entry.slug or entry.match_id
    elif entry.map_number is not None:
        base = f"{teams} · карта {entry.map_number}"
    else:
        base = teams
    return base


def berlin_day(now_s: float) -> str:
    return datetime.fromtimestamp(now_s, summarize.BERLIN).date().isoformat()


def find_match(snap: HubSnapshot, match_id: str) -> tuple[MapView, ...]:
    return tuple(
        view for view in (*snap.maps, *snap.legacy_maps) if view.entry.match_id == match_id
    )
