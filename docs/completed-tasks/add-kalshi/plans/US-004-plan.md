# US-004 Implementation Plan

Story: **US-004** — «match.json схема 4: объект kalshi»

Repo: `/root/work/dota_2_model` on `main`. Persistence only. Do **not** implement anything in `/root/work/poly-maker` (frozen). Do **not** rebuild Docker (US-015). Do **not** start the resolve task, Telegram bind, IN_PROGRESS retry, or BUY_CUTOFF (US-005). Do **not** import `kalshi` / `kalshi_match` / `kalshi_client` from `match_meta.py` (keeps match.json parse off the SDK import graph).

---

## Binding decisions (feature.json / overlay / US-003 learnings)

Cross-ref `current-task/feature.json` FR-2, US-004 `changes`, Resolved Questions, Non-Goals, and `docs/plans/kalshi-overlay.md` § Journal and alerts.

1. **Writes are schema 4. Reads are schemas 3 and 4.** `MATCH_META_SCHEMA_VERSION = 4`. New files always have `kalshi`. Schema 3 on disk has **no** `kalshi` key and that is valid. Schema 1/2 stay foreign (resume raises; boot scan / pin skip).
2. **Do not rewrite an unfinished file.** `write_match_start` already returns when bindings match. Keep that. Schema-3 resume must not grow a `kalshi` key. Schema-4 resume must not replace `pending` with `off` (or the reverse). Kalshi-off for a legacy start is a **read** rule, not a rewrite.
3. **`update_kalshi_binding` is `pin_horn_from_event` + `_atomic_write_json`.** Read, require schema 4 and `final is null`, copy the document, replace only `kalshi`, atomically write the full document. Never assign `market`, `feed_source`, `model`, match identity, join/horn, delays, or `final`. Finalized files raise (unlike pin_horn, which no-ops). Schema 3 raises (do not introduce a venue mid-legacy-document).
4. **`final.pnl` stays Polymarket USDC.** `_build_pnl_block` / `MatchMetaPnl` unchanged. Finalize of schema 4 keeps the existing `kalshi` object and does not add Kalshi cash fields.
5. **`tick_size` is the proven $0.01 string, not `price_ranges`.** US-003 already proved the grid. Persist `tick_size: "0.01"` only on `reason="matched"`. `off_grid` keeps ticker / names / `price_level_structure` and leaves `tick_size` null. Do not persist `price_ranges`.
6. **`reason` includes `matched`.** Feature.json listed `pending/none/ambiguous/off_grid/cutoff/error/off` and omitted the success token. Overlay JSON, overlay state machine (`pending → matched | none | …`), and operator UX require `matched` so a bound ticker is not confused with `off_grid`. Implement the eight-value set below.
7. **`write_match_start` takes a required `kalshi_reason`, it does not call `load_kalshi_settings()`.** Load-once (AGENTS.md). Tests pass `"pending"` or `"off"` without env. The worker maps env → that argument (see Design). Resume ignores the argument because it does not rewrite.
8. **`match_meta.py` does not import `kalshi_match`.** Series strings `KXDOTA2MAP` / `KXDOTA2GAME` are inlined from `MatchStart.market_kind`. Importing the matcher would pull `kalshi-sdk` into every match.json parse.
9. **poly-maker is frozen.** Zero file changes there. No new dependency.

---

## Verified current code (2026-08-26)

| Item | Today | After this story |
|---|---|---|
| `MATCH_META_SCHEMA_VERSION` | `3` | `4` |
| `_MATCH_META_KEYS` | 19 keys, no `kalshi` | those 19 + `kalshi` |
| `_parse_meta_document` | `schema_version != 3` raises | accept `{3, 4}`; v3 exact keys without `kalshi`; v4 exact keys with `kalshi` |
| `read_finalized_match` | `schema_version != 3` → None + foreign log | accept `{3, 4}`; still skip 1/2; still does **not** parse `kalshi` (narrow boot-scan reader) |
| `write_match_start` | `(start, first_event)` | `(start, first_event, kalshi_reason)` |
| `pin_horn_from_event` / `finalize_match` | copy dict, assign horn or final | unchanged pattern; schema 3 stays 3; schema 4 keeps `kalshi` |
| `MatchMeta` TypedDict | schema 3 docstring | `kalshi: NotRequired[MatchMetaKalshi]` |
| `KalshiBinding` | `event_ticker`, `ticker`, `series_ticker`, `yes_outcome`, `no_outcome`, `yes_is_radiant`, `price_level_structure`, `price_ranges` | JSON uses those names minus `price_ranges`; adds `tick_size` + `reason` |

`pin_horn_from_event` is the copy-assign-atomic pattern to clone:

```python
document = _read_existing_meta(meta_path)
if document["final"] is not None or document["horn_at_utc"] == horn_iso:
    return
updated = cast(MatchMeta, dict(document))
updated["horn_at_utc"] = horn_iso
_atomic_write_json(archive_dir, updated)
```

`write_match_start` resume already refuses a different `feed_source` and a different match/market/model binding, then returns without writing. Schema-3 unfinished files go through that path once `_parse_meta_document` accepts 3.

`_atomic_write_json` is already temp + fsync + `os.replace` + dir sync. Reuse it. Do not add a second writer.

---

## kalshi-object JSON shape

Always all nine keys. Never omit a key inside `kalshi`. Insertion order (overlay example):

```json
"kalshi": {
  "event_ticker": "KXDOTA2MAP-26AUG281200PCKCPDYN-2",
  "ticker": "KXDOTA2MAP-26AUG281200PCKCPDYN-2-DYN",
  "series_ticker": "KXDOTA2MAP",
  "yes_outcome": "DYNASTY",
  "no_outcome": "PuckChamp",
  "yes_is_radiant": true,
  "price_level_structure": "linear_cent",
  "tick_size": "0.01",
  "reason": "matched"
}
```

Root document: insert `kalshi` **immediately after `market`** (overlay: “next to market”). `json.dumps(..., indent=2)` preserves dict insertion order.

### Null policy

| reason | event_ticker, ticker, yes_outcome, no_outcome, yes_is_radiant, price_level_structure | tick_size | series_ticker |
|---|---|---|---|
| `pending` | null | null | kind series or null |
| `off` | null | null | kind series or null |
| `none` / `ambiguous` / `error` / `cutoff` | null | null | series if kind/search picked one, else null |
| `off_grid` | binding values (structure kept) | **null** | binding.series_ticker |
| `matched` | binding values | **`"0.01"`** (string, not number) | binding.series_ticker |

`yes_is_radiant` on a miss is JSON **null**, not `false`.

`series_ticker` appears as soon as kind selects the series, including on the first `write_match_start` (no HTTP):

- `market_kind == "map_winner"` → `"KXDOTA2MAP"`
- `market_kind == "series_winner"` → `"KXDOTA2GAME"`
- `market_kind is None` (legacy handoff) → `null`

`price_level_structure` is the binding’s string (`"linear_cent"`, `"tapered"`, …). It is not an object. `price_ranges` never appear in match.json.

`tick_size` is only the proven cent grid. Constant in `match_meta.py`:

```python
KALSHI_CENT_TICK_SIZE = "0.01"
```

US-005 maps `KalshiBinding` → this object and uses that constant on `matched`. This story’s tests pass the TypedDict in by hand.

### Reason Literal

```python
KalshiMetaReason = Literal[
    "pending", "matched", "none", "ambiguous", "off_grid", "cutoff", "error", "off"
]
```

`pending` / `cutoff` / `off` are still not returned by `resolve_kalshi_match` (US-003). They are document reasons. `cutoff` is written later by US-005; the parser must accept it now so a future file is readable.

---

## Schema 3 / 4 read matrix

| Reader | schema 1/2 | schema 3 (no `kalshi`) | schema 4 (has `kalshi`) | schema 4 missing `kalshi` | schema 3 **with** `kalshi` |
|---|---|---|---|---|---|
| `_parse_meta_document` / `_read_existing_meta` | raise `ValueError` (schema) | accept; omit `kalshi` key in the returned dict | accept; parse `kalshi` | raise (exact keys) | raise (exact keys) |
| `write_match_start` on existing unfinished | raise, file untouched | resume if PM/feed/model bind; **no rewrite** | same; **no rewrite** | n/a (unreadable) | n/a |
| `pin_horn_from_event` | skip/raise via `_read_existing_meta` | may update `horn_at_utc` only; still schema 3, still no `kalshi` | may update horn; `kalshi` unchanged | — | — |
| `finalize_match` | raise | write `final` + delays; still schema 3; `pnl` USDC | same plus preserved `kalshi`; `pnl` still USDC only | — | — |
| `pinned_feed_source` | None (warning) | return `steam`/`grid` | return `steam`/`grid` | None | None |
| `read_finalized_match` / `match_has_final` / boot scan | None + foreign log | **accept** if `final` non-null and ids match; does not read `kalshi` | **accept** the same way | accept if market tokens parse (narrow reader; `kalshi` not required) | accept if market tokens parse |
| `update_kalshi_binding` | raise | **raise**, file untouched | replace only `kalshi` when `final is null` | raise | raise |
| `kalshi_reason_from_document` | n/a | `"off"` | persisted `kalshi.reason` | n/a | n/a |

`feed_source` must still be exactly `"steam"` or `"grid"`. Any other string fails `_parse_meta_document` (existing `StrictJsonError`). That is the “чужой feed_source” reject: `update_kalshi_binding` never sees it as a successful parse.

---

## Current code to reuse (do not reinvent)

| Existing | Reuse how |
|---|---|
| `pin_horn_from_event` + `_atomic_write_json` | Pattern and writer for `update_kalshi_binding`. |
| `_parse_meta_document` + `require_exact_keys` | Versioned key sets, same as `session_journal.first_start` (`ACCEPTED_SESSION_SCHEMA_VERSIONS` + `_SESSION_START_KEYS` vs `_SESSION_START_KEYS_V3`). |
| `require_nullable_str` / `require_nullable_nonempty_str` / `require_nullable_bool` | kalshi nullables. Bool-or-null already exists; do not add a new strict_json helper. |
| `write_match_start` resume | Schema-3 unfinished reuse. Do not add a second resume function. |
| `read_finalized_match` | Keep fail-soft and market-token-only. Change the schema membership test, not the field set. |
| `tests/test_match_meta.py` | Extend this file. Helpers `build_match_start`, `write_archive`, `read_meta` stay. Add `_v3_start_document()`. |
| `session_journal.py` | Pattern only. Do not bump session schema (US-011). |

Do not call `resolve_kalshi_match` from `match_meta`. Do not import `KalshiBinding`.

---

## Design

### 1. TypedDict — `src/shared/types/live_paper.py`

```python
KalshiMetaReason = Literal[
    "pending", "matched", "none", "ambiguous", "off_grid", "cutoff", "error", "off"
]


class MatchMetaKalshi(TypedDict):
    """Kalshi bind on match.json schema 4. Schema 3 omits this object."""

    event_ticker: str | None
    ticker: str | None
    series_ticker: str | None
    yes_outcome: str | None
    no_outcome: str | None
    yes_is_radiant: bool | None
    price_level_structure: str | None
    tick_size: str | None
    reason: KalshiMetaReason


class MatchMeta(TypedDict):
    """Root of one match.json document (schema_version 3 or 4)."""

    # ...existing fields...
    market: MatchMetaMarket
    kalshi: NotRequired[MatchMetaKalshi]
    model: MatchMetaModel
    # ...
```

`NotRequired` matches `SessionFillRecord.fill_key`. Schema 3 in-memory dicts must **omit** `kalshi`, not store `None`. A pin_horn/finalize rewrite of schema 3 then cannot accidentally introduce the key.

### 2. `src/live_paper/match_meta.py`

```python
MATCH_META_SCHEMA_VERSION = 4
ACCEPTED_MATCH_META_SCHEMA_VERSIONS = frozenset({3, 4})
KALSHI_CENT_TICK_SIZE = "0.01"
_KALSHI_META_REASONS = frozenset({
    "pending", "matched", "none", "ambiguous", "off_grid", "cutoff", "error", "off"
})
_KALSHI_KEYS = frozenset({
    "event_ticker",
    "ticker",
    "series_ticker",
    "yes_outcome",
    "no_outcome",
    "yes_is_radiant",
    "price_level_structure",
    "tick_size",
    "reason",
})
_MATCH_META_KEYS_V3 = frozenset({ /* today's 19 keys */ })
_MATCH_META_KEYS = _MATCH_META_KEYS_V3 | {"kalshi"}
```

Story text says “add `kalshi` to `_MATCH_META_KEYS`”: `_MATCH_META_KEYS` is the **write / schema-4** set. Schema 3 parse uses `_MATCH_META_KEYS_V3`.

#### `_parse_meta_document`

Flat steps:

1. `require_object`, `require_int` `schema_version`.
2. If `schema_version not in ACCEPTED_MATCH_META_SCHEMA_VERSIONS`: raise the existing `ValueError` wording (`schema_version {n} is not {MATCH_META_SCHEMA_VERSION}` is now wrong — say it is not in `{3, 4}` / not accepted). Keep v1 leftover tests matching `schema_version`.
3. `expected = _MATCH_META_KEYS if schema_version == 4 else _MATCH_META_KEYS_V3`.
4. `require_exact_keys(fields, expected, _META_LABEL)`.
5. Parse today’s fields as now (including `feed_source` steam/grid).
6. If schema 4: `parsed["kalshi"] = _parse_kalshi(require_object(fields.get("kalshi"), f"{_META_LABEL} kalshi"))`. If schema 3: do not set `kalshi`.

#### `_parse_kalshi`

`require_exact_keys` on `_KALSHI_KEYS`. `reason` must be a string in `_KALSHI_META_REASONS` (else `StrictJsonError`). Tickers/names/structure/tick_size: `require_nullable_nonempty_str`. `yes_is_radiant`: `require_nullable_bool`. Do **not** add a pending-must-be-all-null checker in the parser (builder writes consistent objects; extra matrix is unrequested).

#### `_build_start_document`

Add after `market`:

```python
"kalshi": _start_kalshi_block(kalshi_reason, _series_ticker_from_kind(start.market_kind)),
```

```python
def _series_ticker_from_kind(kind: str | None) -> str | None:
    """Kind picks the Kalshi series before any HTTP. None kind → null."""
    if kind == "map_winner":
        return "KXDOTA2MAP"
    if kind == "series_winner":
        return "KXDOTA2GAME"
    return None


def _start_kalshi_block(
    reason: Literal["pending", "off"], series_ticker: str | None
) -> MatchMetaKalshi:
    """Schema-4 start: all bind fields null; series_ticker only if kind selected it."""
    return {
        "event_ticker": None,
        "ticker": None,
        "series_ticker": series_ticker,
        "yes_outcome": None,
        "no_outcome": None,
        "yes_is_radiant": None,
        "price_level_structure": None,
        "tick_size": None,
        "reason": reason,
    }
```

Two-branch kind, not a strategy table. Do not import `MarketKind` if `== "map_winner"` on `start.market_kind` type-checks (it is `MarketKind | None`).

#### `write_match_start`

```python
def write_match_start(
    start: MatchStart, first_event: FeedEvent, kalshi_reason: Literal["pending", "off"]
) -> None:
    """Persist the start document; reuse an unfinished file only when bindings match."""
```

Resume branch **unchanged** besides the signature: existing file → parse → refuse finalized / foreign feed_source / rebound match-market-model → `return`. `kalshi_reason` is used only in `_build_start_document` on the create path.

Do not call `load_kalshi_settings()` here (paper/live without keys would raise `TradingDisabled` on the first tick and skip the archive).

#### `update_kalshi_binding`

```python
def update_kalshi_binding(match_id: str, kalshi: MatchMetaKalshi) -> None:
    """Replace only the kalshi object on an unfinished schema-4 match.json."""
```

Steps (no optional args):

1. `archive_dir = match_archive_dir(LIVE_PAPER_DIR, match_id)` (id guard already in `match_archive_dir`).
2. `document = _read_existing_meta(archive_dir / MATCH_META_FILENAME)` — missing file raises like finalize.
3. If `document["schema_version"] != 4`: raise `ValueError` (schema 3: refuse to introduce a venue). File untouched.
4. If `document["final"] is not None`: raise `ValueError` (`already finalized` / refuse). File untouched.
5. `parsed = _parse_kalshi(require_object(kalshi, ...))` so a test dict with extra keys cannot be written. (`kalshi` is already a TypedDict; still run the same field readers on `dict(kalshi)` so runtime matches parse.)
6. If `document.get("kalshi") == parsed`: return (pin_horn’s equal-value no-op).
7. `updated = cast(MatchMeta, dict(document))`; `updated["kalshi"] = parsed`.
8. Immutability: every key except `kalshi` must equal the pre-image (`{k: document[k] for k in document if k != "kalshi"}` vs the same on `updated`). Raise if not. This is a copy-assign safety net, not a second writer.
9. `_atomic_write_json(archive_dir, updated)`.

`market`, `feed_source`, and `final` are never assigned. Foreign `feed_source` dies in step 2.

#### `kalshi_reason_from_document`

```python
def kalshi_reason_from_document(document: MatchMeta) -> KalshiMetaReason:
    """Schema 3 is off so a legacy start never grows a Kalshi venue. Schema 4 returns kalshi.reason."""
```

- schema 3 → `"off"`.
- schema 4 → `document["kalshi"]["reason"]` (present after parse).

US-005/US-013 call this on resume. This story only adds the function and tests it. Do not start/stop Kalshi HTTP here.

#### `read_finalized_match`

Change `if schema_version != MATCH_META_SCHEMA_VERSION` to `if schema_version not in ACCEPTED_MATCH_META_SCHEMA_VERSIONS`. Keep the foreign-schema log. Do not require `kalshi` (boot scan must still cancel leftover PM orders if an unrelated field is wrong).

### 3. How `KALSHI_TRADING=off` reaches a new file

**Parameter, not loader-inside-writer.**

`match_worker.py` first-event branch (one extra argument):

```python
from live_paper.kalshi_config import start_document_kalshi_reason

write_match_start(start, event, start_document_kalshi_reason())
```

New function on `kalshi_config.py` (SDK-free, already owns the mode parse):

```python
def start_document_kalshi_reason() -> Literal["pending", "off"]:
    """off when KALSHI_TRADING is unset, blank, or off; pending for observe/paper/live. Does not load keys."""
    trading = _parse_trading_mode(env_value("KALSHI_TRADING"))
    if trading == "off":
        return "off"
    return "pending"
```

Why this and not `load_kalshi_settings()`: paper/live without keys must not fail `write_match_start` (US-001: WalletHost does not yet fail the process on that). Why not inject `KalshiSettings` into `MatchWorker.__init__`: that constructor fan-out is US-005 (resolve task argument). ponytail: per-first-tick env read until US-005 loads settings once on WalletHost and passes `kalshi_reason` in.

conftest autouse `KALSHI_TRADING=off` ⇒ worker-written files are schema 4 with `reason=off`. Direct `write_match_start(..., "pending")` in meta tests does not touch env.

Call sites that must pass the third argument:

- `src/live_paper/match_worker.py` — `start_document_kalshi_reason()`
- `tests/test_match_meta.py` — `"pending"` on existing tests (so start-document assertions see the pending object)
- `tests/test_live_paper_wallet_host.py` — `"pending"` (two pin tests)
- `tests/test_steam_live_feed.py` — `"pending"`

No optional default (AGENTS.md).

### 4. Schema-3 resume ⇒ Kalshi off without rewrite

1. `_parse_meta_document` accepts the v3 key set.
2. `write_match_start` resume returns without `os.replace`.
3. `kalshi_reason_from_document` returns `"off"`.
4. `pin_horn_from_event` / `finalize_match` copy the v3 dict and never add `kalshi`.

US-005 will skip the resolve task when this helper returns `"off"` **or** when `KALSHI_TRADING=off`. This story does not skip HTTP (there is no HTTP yet).

### 5. Docs that become wrong

`dota_2_model/AGENTS.md`: “When a fact in these files becomes wrong, update that file in the same change.” Patch `docs/live-paper.md` **schema facts only**:

- `match.json` v3 → v4 with always-present `kalshi`; start/resume **accept 3 and 4**; refuse 1/2 as today.
- “Boot scan skips a folder whose schema is not 3” → not in `{3, 4}`.
- `schema_version == MATCH_META_SCHEMA_VERSION` for boot scan → membership in `ACCEPTED_MATCH_META_SCHEMA_VERSIONS`.

Do **not** write the operator Kalshi section (US-015). Do not bump `session.jsonl`.

### 6. What this story does not touch

- `wallet_host._pick_and_run` resolve task, Telegram, retry, cutoff (US-005)
- `kalshi_match.py`, `kalshi_client.py` (except the new `start_document_kalshi_reason` lives in `kalshi_config.py`)
- `session_journal.py` schema 6
- Docker / `docs/live-paper.md` Kalshi how-to

---

## Ordered implementation steps

Work in `/root/work/dota_2_model`. One story, smallest diffs, no new abstractions.

### Step 1 — Types

1. Add `KalshiMetaReason`, `MatchMetaKalshi`, and `kalshi: NotRequired[MatchMetaKalshi]` on `MatchMeta` in `src/shared/types/live_paper.py`.
2. Update the `MatchMeta` docstring to schema 3 or 4.

### Step 2 — Parser / version / start writer

1. In `match_meta.py`: bump version, add accepted set, v3 key set, `_KALSHI_KEYS`, `KALSHI_CENT_TICK_SIZE`.
2. Split `_parse_meta_document` schema gate as designed. Add `_parse_kalshi`.
3. Change `write_match_start` signature. Thread `kalshi_reason` into `_build_start_document`. Add `_start_kalshi_block` and `_series_ticker_from_kind`.
4. Change `read_finalized_match` membership test.

### Step 3 — `update_kalshi_binding` + resume helper

1. Add `update_kalshi_binding` next to `pin_horn_from_event`.
2. Add `kalshi_reason_from_document`.
3. Import `MatchMetaKalshi` / `KalshiMetaReason` in `match_meta.py`. Import `require_nullable_bool`.

### Step 4 — Worker + env helper

1. `start_document_kalshi_reason()` in `kalshi_config.py`. One-/two-line docstring. Do not read keys or PEM.
2. `match_worker.py` first-event `write_match_start(start, event, start_document_kalshi_reason())`.

### Step 5 — Call-site and assertion updates

1. Every existing `write_match_start(a, b)` becomes `write_match_start(a, b, "pending")`.
2. `test_start_document_has_exact_schema_and_derived_join_horn`: key set includes `kalshi`; `schema_version == 4`; assert the nine kalshi keys and pending nulls (`series_ticker is None` because `build_match_start()` has `market_kind=None`).
3. `test_grid_native_start_writes_v3_identity`: assert schema 4 (rename docstring to schema 4).
4. `test_finalize_computes_exact_summary_and_preserves_start_fields`: already preserves non-final fields, including new `kalshi`. Add `assert document["final"]["pnl"]` keys are still `realized_pnl_usdc` / `unrealized_pnl_usdc` only if not already implied.
5. `test_boot_scan_skips_foreign_schema`: keep parametrize `[1, 2]`. `_write_final_meta` may stay schema 3 (proves boot scan still accepts 3). Add one schema-4 finalized fixture that is also a boot-scan target.

### Step 6 — New tests

Add cases in `tests/test_match_meta.py` (table below). Hand-build `_v3_start_document()` from today’s 19-key start (copy `_v1_start_document` and add v3 fields: `steam_match_id`, `tournament`, `feed_source`, delays, `final: null`). Do not round-trip through the new writer.

Add two tests in `tests/test_live_paper_kalshi_config.py` for `start_document_kalshi_reason` (off vs observe). Patch `live_paper.kalshi_config.env_value`, not `os.environ`.

### Step 7 — Docs

Patch the now-wrong schema sentences in `docs/live-paper.md` (schema facts only).

### Step 8 — Quality gate

See Verification. `git add` new/edited files before `make lint-all` (pre-commit skips untracked).

### Step 9 — Bookkeeping (after green, implement-step skill)

- `dota_2_model` commit on `main`. Why: operators can open match.json and see the Kalshi bind (or why it is pending/off) without reading logs; schema 3 maps keep booting PM.
- Set US-004 `passes: true` in `betting_workspace/current-task/feature.json`.
- Append `current-task/progress.txt` and `learnings.txt` (NotRequired kalshi, schema-3 omit not null, tick_size string only on matched, write_match_start takes reason, resume does not rewrite).
- Do not commit `/root/work/poly-maker`. Do not docker compose.

---

## Test plan

### Existing file: `tests/test_match_meta.py`

| Test | Setup | Expect |
|---|---|---|
| start document schema 4 + kalshi (update existing) | `write_match_start(..., "pending")` | `schema_version==4`, `kalshi` in key set, nine kalshi keys, reason `pending`, bind fields null, `series_ticker is None` |
| start with kind picks series | `replace(build_match_start(), market_kind="map_winner")`, reason pending | `series_ticker=="KXDOTA2MAP"`, other bind fields null |
| start reason off | `write_match_start(..., "off")` | `reason=="off"`, bind fields null |
| schema 3 unfinished resume (no rewrite) | write `_v3_start_document()` bytes; `write_match_start(matching, event, "pending")` | file bytes **identical**; no `kalshi` key; `kalshi_reason_from_document` → `"off"` |
| schema 3 pin_horn does not grow kalshi | v3 start; `pin_horn_from_event` with a clock tick | `schema_version==3`, `"kalshi" not in document` |
| schema 3 finalized boots | v3 document with non-null `final` + market tokens | `read_finalized_match` returns ids; `match_has_final` True |
| schema 3 GRID pin boots | v3 `feed_source=grid` | `pinned_feed_source` → `"grid"` |
| new match writes 4 with kalshi | covered by updated start-document test | |
| `update_kalshi_binding` changes only kalshi | schema-4 pending start; update to matched object (ticker/names/structure/`tick_size=="0.01"`) | `kalshi` replaced; every other field equal; `market` and `feed_source` identical |
| update off_grid keeps structure, null tick | update with `reason=off_grid`, structure `"tapered"`, tick null | those values persist |
| update pending → none with series | series set, other fields null | parse-roundtrip |
| finalized rejected | finalize, then `update_kalshi_binding` | `ValueError`; file unchanged |
| foreign feed_source rejected | hand-write schema 4 with `feed_source="mds"` (or `"polymarket"`) | `update_kalshi_binding` raises; file unchanged |
| schema 3 update rejected | unfinished v3; `update_kalshi_binding` | raises; file unchanged (still no `kalshi`) |
| schema 1 still refused on start | existing `test_v1_on_disk_refuses_start` | still raises `schema_version`; file untouched |
| finalize schema 4 pnl is USDC | start pending, finalize with `SessionPnl` | `final.pnl` keys exactly `realized_pnl_usdc`, `unrealized_pnl_usdc`; `kalshi.reason` still pending |
| resume schema 4 does not rewrite pending | write pending; second `write_match_start(..., "off")` | document still `reason=="pending"` (unfinished not rewritten) |

### `tests/test_live_paper_wallet_host.py`

| Test | Setup | Expect |
|---|---|---|
| boot scan accepts schema 3 (keep `_write_final_meta`) | existing | still one target |
| boot scan accepts schema 4 | same helper with `"schema_version": 4` plus a dummy `kalshi` object **or** omit kalshi (narrow reader) | listed as target |
| foreign 1/2 still skipped | existing parametrize | unchanged |

`write_match_start` pin tests: pass `"pending"`.

### `tests/test_steam_live_feed.py`

Pass `"pending"` into the one `write_match_start`. Finalize still reads `final`.

### `tests/test_live_paper_kalshi_config.py`

| Test | Setup | Expect |
|---|---|---|
| unset/blank/off → `"off"` | patch `env_value` | `start_document_kalshi_reason()=="off"` |
| observe (no keys) → `"pending"` | `KALSHI_TRADING=observe` | `"pending"`; does not raise |

Lifecycle tests that run `MatchWorker.run()` write schema 4 `reason=off` via autouse env. They assert `match_has_final`, not the kalshi object — should stay green.

No browser. No live Kalshi HTTP. No `import kalshi` in `match_meta.py`.

---

## Verification

Run inside `/root/work/dota_2_model`:

```bash
make test
git add src/live_paper/match_meta.py src/live_paper/match_worker.py src/live_paper/kalshi_config.py \
  src/shared/types/live_paper.py docs/live-paper.md \
  tests/test_match_meta.py tests/test_live_paper_wallet_host.py tests/test_steam_live_feed.py \
  tests/test_live_paper_kalshi_config.py
make lint-all
```

Exact meaning:

- `make test` → `PYTHONPATH=src:scripts:../prediction-market-backtesting uv run --group backtest python -m pytest`
- `make lint-all` → `uv run python -m pre_commit run --all-files` (ruff check/format + basedpyright strict). New untracked files are skipped until `git add`.

Both must be clean. A local `pytest tests/test_match_meta.py tests/test_live_paper_kalshi_config.py` is the right first check, not the only gate.

Do **not** run `make install` as a bare `uv sync` (US-001: drops nautilus). No new dependency.

No Figma. No poly-maker patch.

---

## Risks / assumptions

1. **`matched` is a valid `reason`.** Feature.json’s reason set omitted it; overlay JSON and the resolve state machine include it. Without it, a bound ticker and `off_grid` cannot be told apart. Plan implements eight reasons including `matched`.
2. **Schema-3 in-memory omit, not null.** Injecting `kalshi: null` on read would make pin_horn/finalize write a hybrid document. Tests must assert `"kalshi" not in document` after a v3 pin/finalize.
3. **Resume does not rewrite.** A schema-4 `pending` file is not flipped to `off` if the process restarts with `KALSHI_TRADING=off`. US-005 should treat persisted `reason` as source of truth for an existing schema-4 file, and `kalshi_reason_from_document` for schema 3.
4. **`start_document_kalshi_reason` uses `_parse_trading_mode`.** An invalid `KALSHI_TRADING` token still raises `TradingDisabled` at first tick. conftest forces `off`. US-005 boot load will fail the process earlier for garbage tokens.
5. **Event-ticker series strings are duplicated** (`KXDOTA2MAP` / `KXDOTA2GAME`) so `match_meta` never imports the matcher/SDK. If Kalshi renames a series, two places change. Acceptable until US-005 maps `KalshiResolveResult.series_ticker` on update (that path uses the matcher’s value).
6. **`read_finalized_match` does not validate `kalshi` on schema 4.** Intentional: leftover PM cancel must not depend on the Kalshi object. A corrupt `kalshi` on a finalized file still boots the PM scan.
7. **MatchWorker still does not receive settings from WalletHost.** First-tick helper is a ceiling; US-005 replaces it with one boot load. Do not add a MatchWorker constructor argument in this story.
8. **Assumption:** leftover unfinished schema-3 folders may exist on the VPS when this ships. They must resume PM and never grow `kalshi`. New maps write 4.

No Figma. No poly-maker patch. No new dependency.
