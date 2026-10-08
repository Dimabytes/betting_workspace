import gzip
import json
import sys
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, Literal, cast

import httpx
import pandas as pd
import typer

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from collect.common.paths import STRATZ_MATCH_INDEX_PATH
from collect.common.stratz_client import (
    StratzError,
    StratzForbiddenError,
    StratzRateLimitError,
    StratzServerError,
    stratz_client,
    stratz_graphql,
)
from collect.common.window_ids import load_admitted_match_ids
from shared.types.stratz import StratzMatch
from shared.utils.json_io import read_gzip_json
from shared.utils.parquet_io import write_parquet
from shared.utils.stratz import (
    stratz_match_cache_path,
    stratz_match_unusable_reason,
)

STRATZ_CACHE_PROFILE = "stratz_rich_v3"
DEFAULT_INTERVAL_SECONDS = 2.5
SERVER_ERROR_MAX_RETRIES = 4
SERVER_ERROR_BASE_BACKOFF = 2.0
RATE_LIMIT_MAX_RETRIES = 1
RATE_LIMIT_DEFAULT_WAIT = 60.0

CacheStatus = Literal["usable", "unusable"]

RICH_QUERY = """
query StratzRichMatch($id: Long!) {
  match(id: $id) {
    id
    startDateTime
    endDateTime
    durationSeconds
    didRadiantWin
    gameMode
    lobbyType
    leagueId
    seriesId
    radiantTeamId
    direTeamId
    radiantTeam { id name }
    direTeam { id name }
    towerStatusRadiant
    towerStatusDire
    barracksStatusRadiant
    barracksStatusDire
    radiantNetworthLeads
    radiantExperienceLeads
    firstBloodTime
    towerDeaths { time isRadiant npcId }
    players {
      matchId
      playerSlot
      heroId
      isRadiant
      isVictory
      steamAccountId
      lane
      role
      roleBasic
      position
      level
      kills
      deaths
      assists
      networth
      gold
      goldSpent
      goldPerMinute
      experiencePerMinute
      numLastHits
      numDenies
      heroDamage
      towerDamage
      heroHealing
      imp
      award
      leaverStatus
      item0Id
      item1Id
      item2Id
      item3Id
      item4Id
      item5Id
      backpack0Id
      backpack1Id
      backpack2Id
      neutral0Id
      stats {
        networthPerMinute
        deathEvents { time timeDead }
        level
        itemPurchases { time itemId }
      }
      playbackData {
        playerUpdateGoldEvents { time gold networth networthDifference unreliableGold }
        goldEvents { time amount npcId isValidForStats }
        experienceEvents { time amount positionX positionY }
        killEvents { time attacker target assist gold xp isGank isSolo isSmoke isInvisible positionX positionY }
        deathEvents { time attacker target assist goldLost goldFed xpFed reliableGold unreliableGold timeDead positionX positionY }
        assistEvents { time attacker target gold xp subTime positionX positionY }
        buyBackEvents { time cost deathTimeRemaining heroId }
        purchaseEvents { time itemId }
        itemUsedEvents { time itemId attacker target }
        inventoryEvents { time item0 { itemId } item1 { itemId } item2 { itemId } item3 { itemId } item4 { itemId } item5 { itemId } backPack0 { itemId } backPack1 { itemId } backPack2 { itemId } neutral0 { itemId } teleport0 { itemId } }
        playerUpdatePositionEvents { time x y }
        playerUpdateHealthEvents { time hp maxHp mp maxMp }
        playerUpdateLevelEvents { time level }
        playerUpdateAttributeEvents { time str agi int }
        playerUpdateBattleEvents { time damageBonus damageMinMax hpRegen mpRegen }
        abilityLearnEvents { time abilityId level levelObtained isUltimate isTalent isMaxLevel }
        abilityUsedEvents { time abilityId attacker target }
        abilityActiveLists { time ability0 ability1 ability2 ability3 ability4 ability5 ability6 ability7 }
        heroDamageEvents { time attacker target value damageType byAbility byItem isPhysicalAttack }
        healEvents { time attacker target value byAbility byItem }
        csEvents { time attacker npcId gold xp isNeutral isAncient isCreep positionX positionY }
        runeEvents { time rune action gold positionX positionY }
        towerDamageEvents { time attacker damage byAbility byItem fromNpc npcId }
      }
    }
    playbackData {
      towerDeathEvents { time radiant dire }
      buildingEvents { time indexId type hp maxHp positionX positionY isRadiant npcId didShrineActivate }
      roshanEvents { time hp maxHp createTime x y totalDamageTaken item0 item1 item2 item3 item4 item5 }
      courierEvents { id ownerHero isRadiant }
      runeEvents { time rune action indexId location positionX positionY }
      wardEvents { time action fromPlayer indexId playerDestroyed positionX positionY wardType }
    }
  }
}
"""


@dataclass(frozen=True)
class StratzCacheEntry:
    match_id: int
    cache_profile: str
    fetched_at: str
    source: str
    graphql_errors: list[Any]
    response_bytes: int
    data: dict[str, Any]


@dataclass(frozen=True)
class CacheSummary:
    match_id: int
    status: CacheStatus
    reason: str | None
    playback_available: bool
    start_time: int | None
    duration: int | None


@dataclass(frozen=True)
class MatchFetch:
    match: StratzMatch | None
    errors: list[Any]


def start_to_start_sleep(request_started: float, interval: float, now: float) -> float:
    return max(0.0, (request_started + interval) - now)


def format_utc_now() -> str:
    return datetime.now(tz=UTC).isoformat().replace("+00:00", "Z")


def summarize_match(match_id: int, match: StratzMatch | None) -> CacheSummary:
    start_time = match.get("startDateTime") if match else None
    duration = match.get("durationSeconds") if match else None
    reason = stratz_match_unusable_reason(match)
    return CacheSummary(
        match_id=match_id,
        status="usable" if reason is None else "unusable",
        reason=reason,
        playback_available=bool(match and match.get("playbackData")),
        start_time=int(start_time) if start_time is not None else None,
        duration=int(duration) if duration is not None else None,
    )


def read_cache_summary(match_id: int) -> CacheSummary:
    entry = read_gzip_json(stratz_match_cache_path(match_id))
    data = cast(dict[str, Any], entry.get("data") or {})
    return summarize_match(match_id, cast(StratzMatch | None, data.get("match")))


def scan_cache(match_ids: list[int]) -> dict[int, CacheSummary]:
    summaries: dict[int, CacheSummary] = {}
    for match_id in match_ids:
        if stratz_match_cache_path(match_id).exists():
            summaries[match_id] = read_cache_summary(match_id)
    return summaries


def select_pending_match_ids(match_ids: list[int], force: bool, limit: int) -> list[int]:
    pending = [
        match_id
        for match_id in match_ids
        if force or not stratz_match_cache_path(match_id).exists()
    ]
    return pending[:limit] if limit > 0 else pending


def build_cache_entry(
    match_id: int, match: StratzMatch | None, *, source: str, graphql_errors: list[Any]
) -> StratzCacheEntry:
    data = {"match": match}
    serialized = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return StratzCacheEntry(
        match_id=match_id,
        cache_profile=STRATZ_CACHE_PROFILE,
        fetched_at=format_utc_now(),
        source=source,
        graphql_errors=graphql_errors,
        response_bytes=len(serialized.encode("utf-8")),
        data=data,
    )


def write_cache_atomic(match_id: int, entry: StratzCacheEntry) -> None:
    out = stratz_match_cache_path(match_id)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(out.suffix + ".tmp")
    with gzip.open(tmp, "wt", encoding="utf-8") as f:
        json.dump(asdict(entry), f, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    tmp.replace(out)


def fetch_match(client: httpx.Client, match_id: int) -> MatchFetch:
    response = stratz_graphql(client, RICH_QUERY, {"id": int(match_id)})
    data = response.data or {}
    match = cast(StratzMatch | None, data.get("match"))
    return MatchFetch(match=match, errors=response.errors)


def fetch_match_with_retry(client: httpx.Client, match_id: int) -> MatchFetch:
    server_errors = 0
    rate_limits = 0
    while True:
        try:
            return fetch_match(client, match_id)
        except (StratzServerError, httpx.TransportError) as exc:
            server_errors += 1
            if server_errors > SERVER_ERROR_MAX_RETRIES:
                raise
            backoff = SERVER_ERROR_BASE_BACKOFF * (2 ** (server_errors - 1))
            print(
                f"  {type(exc).__name__}: {exc}; backoff {backoff:.0f}s "
                f"(retry {server_errors}/{SERVER_ERROR_MAX_RETRIES})",
                flush=True,
            )
            time.sleep(backoff)
        except StratzRateLimitError as exc:
            rate_limits += 1
            if rate_limits > RATE_LIMIT_MAX_RETRIES:
                raise
            wait = exc.retry_after if exc.retry_after is not None else RATE_LIMIT_DEFAULT_WAIT
            print(f"  429 rate limited; sleeping Retry-After={wait:.0f}s", flush=True)
            time.sleep(wait)


def fetch_and_cache_match(client: httpx.Client, match_id: int) -> CacheSummary:
    fetched = fetch_match_with_retry(client, match_id)
    entry = build_cache_entry(
        match_id, fetched.match, source="network", graphql_errors=fetched.errors
    )
    write_cache_atomic(match_id, entry)
    return summarize_match(match_id, fetched.match)


def write_index(summaries: dict[int, CacheSummary]) -> int:
    rows = [asdict(summary) for summary in summaries.values()]
    df = pd.DataFrame(rows, columns=list(CacheSummary.__dataclass_fields__))
    write_parquet(df, STRATZ_MATCH_INDEX_PATH)
    return sum(1 for summary in summaries.values() if summary.status == "usable")


def fetch_pending_matches(
    pending: list[int],
    summaries: dict[int, CacheSummary],
    interval_seconds: float,
) -> None:
    with stratz_client(timeout=90) as client:
        for done, match_id in enumerate(pending, start=1):
            request_started = time.monotonic()
            progress = f"[{done}/{len(pending)}] match={match_id}"
            try:
                summary = fetch_and_cache_match(client, match_id)
                summaries[match_id] = summary
                print(
                    f"{progress} status={summary.status} reason={summary.reason} "
                    f"playback={summary.playback_available}",
                    flush=True,
                )
            except StratzForbiddenError as exc:
                raise SystemExit(
                    f"aborting on STRATZ 403: {exc} "
                    "(check User-Agent/token; see docs/STRATZ_API.md)"
                ) from exc
            except (StratzError, httpx.TransportError, OSError) as exc:
                print(f"{progress} status=error error={exc}", flush=True)

            sleep_seconds = start_to_start_sleep(
                request_started, interval_seconds, time.monotonic()
            )
            time.sleep(sleep_seconds)


def main(
    limit: Annotated[int, typer.Option(help="Max matches to fetch this run (0 = all).")] = 0,
    force: Annotated[
        bool, typer.Option("--force", help="Re-fetch even matches with a cache file.")
    ] = False,
    interval: Annotated[
        float, typer.Option(help="Start-to-start seconds between requests.")
    ] = DEFAULT_INTERVAL_SECONDS,
) -> None:
    match_ids = load_admitted_match_ids()
    total_matches = len(match_ids)
    summaries = scan_cache(match_ids)
    pending = select_pending_match_ids(match_ids, force, limit)
    interval_seconds = max(0.0, interval)
    print(
        f"total_linked={total_matches} cached={len(summaries)} "
        f"network_pending={len(pending)} interval={interval_seconds:.2f}s",
        flush=True,
    )

    if not pending:
        write_index(summaries)
        print(f"nothing to fetch; index at {STRATZ_MATCH_INDEX_PATH}", flush=True)
        return

    fetch_pending_matches(pending, summaries, interval_seconds)
    usable_total = write_index(summaries)
    print(
        f"done: index_usable={usable_total}/{total_matches} saved={STRATZ_MATCH_INDEX_PATH}",
        flush=True,
    )


if __name__ == "__main__":
    app = typer.Typer()
    app.command(help="Fetch the STRATZ rich match cache for linked PM matches.")(main)
    app()
