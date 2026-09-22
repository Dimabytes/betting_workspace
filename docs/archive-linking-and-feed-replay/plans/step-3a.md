# Step 3A — признаки по игровой секунде + архивное расписание с выбранной моделью

Design: `esports-trader/docs/superpowers/specs/2026-09-20-archive-linking-and-feed-replay-design.md` §3A.
Product repo: `../esports-trader` (branch `main`, HEAD `0d1ae767`). No commits from this plan.

## Цель и инварианты

Обычный `python -m backtest.run` (без `--policy`/`--live-since`) автоматически решает
per-match, каким расписанием подавать feed-события:

- у матча есть admitted-архив → feed-события идут по сохранённому `FeedSchedule`
  (Step 2), а решения модели считаются заново из выбранной research-модели и
  dataset-признаков по игровой секунде;
- архива нет → текущий seeded grid-v1 cadence (поведение без изменений);
- архив есть, но не admitted / не слинкован / битый → матч исключается с явной
  причиной. Никакого silent-fallback на синтетический cadence.

Инварианты:

- Признаки читаются по `(game, match_id, game_second)` — ключ `game_second`
  является значением колонки, а не `second - lag`.
- Архивный тик задаёт пару `(arrival_ns, game_second)` как есть. Никакого
  дополнительного 10/15/17-секундного сдвига: `feature.second == tick.game_second`.
- Модель выбирается по источнику фида: Dota GRID → `research` (12 признаков),
  Dota Oddin → `research-noxp` (11 признаков), LoL GRID → `lol/research`.
  Fallback grid-v1 → основная research-модель игры.
- Расписание не зависит от модели: смена модели/признаков меняет
  `predicted_deltas`, но не `fingerprint`, не порядок тиков, не timestamps.
- Цена рынка читается каузально: mid book на момент `received_ns` тика
  (as-of по market-seconds tape на тех же wall-часах), а не `market_p_radiant`
  строки датасета.
- Отсутствие строки признаков для тика — ошибка готовности датасета →
  исключение матча. Никакой интерполяции и никакой подмены live-значениями.
- Стратегия видит наблюдаемые часы: `game_second`, `phase`, `paused`,
  `terminal` из расписания — не синтетические `0/540`, `paused=False`.
- Исключения матчей явные и стабильные (`archive:<admission>`,
  `archive_unlinked`, `schedule_stale`, `schedule_missing`,
  `dataset_features_missing:<second>` …), попадают в coverage и лог.

## Non-goals (3B и далее)

- Не трогаем удаление `--policy archive|current`, `live_archives.py`,
  core_trace-replay и mixed-model отчёт — это Step 3B.
- Не запускаем `make train`, `make backtest`, `make prepare` в этой задаче —
  только unit-проверки и статика.
- Не меняем train lag (10s), не вводим 15s-эксперимент.
- Не трогаем `poly-maker`, `polymarket-collector`, on-chain скрипты.
- `--policy archive` / `--live-since` продолжают работать на старом пути
  (core_trace `SignalUpdate`), весь новый код срабатывает только когда эти
  флаги не переданы.

## Известные проблемы текущего кода, которые закрывает план

- `src/backtest/signals.py::build_match_signals` строит модельную строку как
  `second - lag_seconds`: для архивного пути это недопустимо.
- `src/backtest/strategy.py::_clock_at` отдаёт `GameClock(game_second=0|540,
  paused=False)` — синтетические часы.
- `src/backtest/live_archives.py` читает готовые `SignalUpdate` из core_trace —
  старый путь сохраняется только под флагами 3B.

## Файлы

### Изменяемые

| Файл | Что |
|---|---|
| `src/shared/types/dataset.py` | `GameFeatureRow(StateFeatures)` — typed row нового артефакта |
| `src/shared/constants/paths.py` | `GAME_FEATURES_DATASET_PATH = NEW_DATASET_DIR / "game_features.parquet"` |
| `src/prepare_dataset/prepare_dataset.py` | `build_game_feature_rows`, emission в validation-цикле, `write_datasets` пишет артефакт |
| `src/backtest/feed_schedules.py` | **новый модуль**: планирование feed-режима per-match, загрузка index/schedule, выбор модели, digest для манифеста |
| `src/backtest/signals.py` | `MatchSignals` += наблюдаемые часы; `build_schedule_match_signals`; `DatasetReadinessError`; `MarketAnchorTape` |
| `src/backtest/run.py` | resolve планов после selection, исключения, per-source freshness, dispatch сигналов, манифест, запреты флагов |
| `src/backtest/strategy.py` | `DotaMakerConfig` += clock-ленты; `_clock_at` по наблюдаемым тикам |
| `src/backtest/results.py` | `MakerMatchResult` += `signal_mode`, `model_name` |
| `src/backtest/selection.py` | `ValidationCoverage` += `archive_excluded` |
| `src/backtest/context.py` | `MarketContext` без изменений (catalog timing остаётся владельцем replay window) |
| `src/backtest/lol_inputs.py` | проброс audit/condition для LoL-планов (selection уже всё несёт) |
| `src/backtest/telonex_local.py` | strip own-book по архиву из плана (не только `grid-*` glob) |

### Тесты

| Файл | Что |
|---|---|
| `tests/test_feed_schedules.py` | новый: планирование, binding, исключения, выбор модели |
| `tests/test_backtest_signals.py` | archive-builder тесты + обновление `MatchSignals(...)` в 2 местах |
| `tests/test_backtest_maker.py` | `_clock_at` по ленте; обновление `build_maker_config` |
| `tests/test_backtest_validation.py` | обновление `ValidationCoverage` ctor |
| `tests/test_prepare_dataset.py` | `build_game_feature_rows` emission/колонки |
| `tests/test_live_archives.py` | `MatchSignals` ctor получил новые поля (пустые ленты) |

## A. Dataset: артефакт признаков по игровой секунде

Проблема: `validation_dataset.parquet` — это join market-second → lagged state;
игровая секунда состояния в нём не записана (восстановимая, но fragile), и в нём
нет последних ~10 секунд матча (state `g` живёт в строке `second = g+10`, а
market-second дальше duration отсутствует). Архивные тики в хвосте матча
(`duration-10 < g ≤ duration`) иначе дали бы ложный `dataset_row_missing` для
каждого матча. Поэтому источник признаков — отдельный артефакт, а не validation
parquet.

`src/shared/types/dataset.py`:

```python
class GameFeatureRow(StateFeatures):
    """Exact-second game state keyed by game second for archive-schedule backtests."""
    match_id: int
    game_second: int
    market_radiant_prior: float
```

`src/prepare_dataset/prepare_dataset.py`:

- `build_game_feature_rows(match_id, states, market_radiant_prior) -> list[GameFeatureRow]` —
  одна строка на каждый `ExactSecondState` (`state_features(state)` уже плоский),
  `market_radiant_prior` из `inputs.entry.radiant_prior` (источник prior не меняется).
- В `main()` validation-цикле рядом с `join_validation_rows` копим
  `game_feature_rows`; states уже построены тем же вызовом.
- `write_datasets(...)` += параметр `game_feature_rows`; пишет
  `GAME_FEATURES_DATASET_PATH` в обычном режиме и `output_dir/"game_features.parquet"`
  под `--output-dir`.
- `radiant_win` в артефакт не пишем — outcome-данные не являются признаком.
- `write_rows` расширить union-типом `Sequence[GameFeatureRow]`.

Покрытие: строки на все `second ∈ [-60, duration]` (то, что
`build_exact_second_states` уже отдаёт), включая хвост после последнего
market-second. ~1.4M строк на validation-когорту — приемлемо.

LoL ничего не готовит: `lol validation.parquet.second` уже является игровой
секундой (pause-adjusted из livestats), весь нужный набор колонок на месте —
reader для LoL делает `second → game_second` rename.

## B. `src/backtest/feed_schedules.py` (новый модуль)

Владелец связки match → feed-режим. Типы (frozen dataclass, не dict/tuple):

```python
FeedMode = Literal["schedule", "grid_v1"]

@dataclass(frozen=True)
class ScheduleBinding:
    archive_root: str
    archive_id: str
    feed_source: str           # "grid" | "oddin"
    schedule_path: Path
    schedule_fingerprint: str  # из index.parquet
    schedule: FeedSchedule

@dataclass(frozen=True)
class MatchFeedPlan:
    match_id: int
    mode: FeedMode
    binding: ScheduleBinding | None  # только mode="schedule"
    model_dir: Path                  # effective research model dir

@dataclass(frozen=True)
class FeedPlanResult:
    plans: dict[int, MatchFeedPlan]
    exclusions: dict[int, str]       # match_id -> reason
```

Публичные функции:

- `load_archive_index() -> pd.DataFrame` — читает
  `ARCHIVE_INDEX_DIR / "index.parquet"`; отсутствует → `ValueError`
  ("run make archive-index first"). ~525 строк, дёшево.
- `resolve_dota_feed_plans(selected_ids, catalog, index, model_override) -> FeedPlanResult`
- `resolve_lol_feed_plans(selected_ids, audit, index, model_override) -> FeedPlanResult`
- `schedule_map_sha256(plans) -> str` — sha256 по отсортированным строкам
  `"match_id|mode|fingerprint|model_dir"` — в манифест, для resume-assert.
- `entry_stale_seconds(binding) -> float` — `ODDIN_FEED_STALE_SECONDS` (15.0)
  для oddin, `GRID_FEED_STALE_SECONDS` (16.0) иначе. Import из
  `trader.oddin_feed`/`shared.constants.strategy` — те же константы, что у live.

### Правила разрешения (Dota)

Для каждого `match_id` из `selected_ids`, `entry = catalog[match_id]`:

1. `entry.archive_id` непустой → binding по `(entry.archive_root, entry.archive_id)`:
   - строка index с этим ключом обязана иметь `admission == "admitted"`;
     иначе exclusion `archive:<admission>`;
   - `index.schedule_fingerprint == entry.schedule_fingerprint`, иначе
     `schedule_stale` (catalog/link построен до смены правил — нужен rerun
     collect+catalog, не молчаливый accept);
   - `schedule_path_for(ARCHIVE_INDEX_DIR, root, "dota", archive_id)` существует
     и `read_schedule(path).fingerprint` совпадает с обоими, иначе
     `schedule_missing`/`schedule_fingerprint_mismatch`;
   - cross-check `schedule.identity.steam_match_id == str(match_id)` —
     строковое сравнение (урок `0e40b87c`: колонка строковая, не int-parse).
2. `entry.archive_id` пустой → кандидаты в index: `meta_ok` строки,
   `game == "dota"`, `steam_match_id == str(match_id)` (строкой) **или**
   `condition_id.lower() == entry.condition_id.lower()`:
   - кандидат с `admission == "admitted"` → exclusion `archive_unlinked`
     (admitted-расписание существует, но Step-2 linker отказал — каталог устарел);
   - только не-admitted → exclusion `archive:<admission>` первого кандидата
     по детерминированной сортировке `archive_id`;
   - кандидатов нет → `mode="grid_v1"`, `binding=None`.
3. `model_dir`: `feed_source == "oddin"` → `RESEARCH_NOXP_MODEL_DIR`;
   `"grid"`/fallback → `RESEARCH_MODEL_DIR`. `model_override` (`--model-dir`)
   побеждает и записывается в план — явный эксперимент.
4. Модельная директория без `model.json` → понятная ошибка (`load_model_feature_columns`
   уже бросает) — не подмена.

### Правила разрешения (LoL)

Audit row матча (`LolBacktestAuditRow.condition_id`) → index rows
`game == "lol"`, `condition_id.lower()` совпадает, `meta_ok`, не `duplicate_of:*`:

- admitted → binding, `model_dir = LOL_RESEARCH_MODEL_DIR`;
- только не-admitted → exclusion `archive:<admission>`;
- нет → `grid_v1`.

Cross-check для LoL: `schedule.identity.condition_id.lower() == audit condition_id.lower()`.

Общие замечания:

- `duplicate_of:*` строки в кандидатах сравниваем по admission как есть —
  `admission` уже содержит причину (`duplicate_of:winner` → reason
  `archive:duplicate_of:winner`).
- `meta_ok=False` строки пропускаем (не идентифицированы, matchey быть не могут).
- План детерминирован: сортировки по `archive_id`, никаких dict-order effects.

## C. `src/backtest/signals.py` — schedule-driven сигналы

### `MatchSignals` расширение

```python
@dataclass(frozen=True)
class MatchSignals:
    feed_timestamps_ns: tuple[int, ...]
    timestamps_ns: tuple[int, ...]
    predicted_deltas: tuple[float, ...]
    dataset_market_ps: tuple[float, ...]
    feed_game_seconds: tuple[int, ...]   # aligned с feed_timestamps_ns; () у grid-v1
    feed_paused: tuple[bool, ...]
    feed_terminal: tuple[bool, ...]
```

Поля обязательные (правило «required args»): `build_match_signals` и
`build_match_signals_from_live_archive` заполняют `()` — stub-clock поведение
старого пути сохраняется. Все `MatchSignals(...)` конструкторы в коде/тестах
обновить.

### `MarketAnchorTape` — каузальный рынок

```python
@dataclass(frozen=True)
class MarketAnchorTape:
    timestamps_ns: tuple[int, ...]
    p_radiant: tuple[float, ...]
```

- `market_anchor_tape(rows) -> MarketAnchorTape` — из `market_status == "ok"`
  строк: `timestamps_ns = state_ts_us * 1000`, `p_radiant = market_p_radiant`,
  отсортировано по ts.
- `anchor_at(tape, now_ns) -> float | None` — `bisect_right(tape.timestamps_ns,
  now_ns) - 1`; `None` если нет строки ≤ now.

Источники tape:

- Dota: `load_market_second_rows(market_seconds_cache_path(match_id))` —
  per-match market-seconds cache (то же, что читает `load_match_mid_series`,
  покрытие `[-60, duration]`).
- LoL: `lol.market_seconds` frame, `market_status == "ok"` (уже загружен в
  `LolSelection`).

### `DatasetReadinessError`

```python
class DatasetReadinessError(ValueError):
    """Archive tick has no dataset feature row at its game second."""
```

Сообщение: `match {id}: no feature row at game_second {g}` — первая недостающая
секунда в порядке тиков.

### `build_schedule_match_signals(...) -> dict[int, MatchSignals]`

Аргументы: `match_ids`, `plans`, `schedules: Mapping[int, FeedSchedule]`,
`feature_rows: pd.DataFrame` (колонки `match_id, game_second, <StateFeatures>,
market_radiant_prior`), `anchor_tapes: Mapping[int, MarketAnchorTape]`.

Per match (порядок = порядок тиков, `received_ns` обязан быть non-decreasing —
иначе `ValueError` corrupt schedule):

1. `feed_ts = [t.received_ns]`, `feed_seconds/paused/terminal` из тиков.
2. `stale = recovery_stale_mask(feed_ts, grid_exit_age_seconds(entry_stale_seconds(binding)))` —
   exit-budget 45s для обоих источников → пост-gap тик остаётся feed-тиком без решения.
3. `features_by_second = {g: row}` из slice `feature_rows` по match_id.
4. Для каждого не-stale тика:
   - `row = features_by_second.get(tick.game_second)` → `None` →
     `DatasetReadinessError` (причина ловится в run.py → exclusion
     `dataset_features_missing:{g}`);
   - `anchor = anchor_at(tape, tick.received_ns)` → `None` → тик остаётся
     feed-only (решения нет, как у grid-v1 рынок-пропуск);
   - решение = `{second: g, **state features, market_radiant_prior: prior,
     market_p_radiant: anchor}` — `second` именно `g`, никакого сдвига.
5. Группировка решений по `plan.model_dir` → `load_model_feature_columns(dir)`
   → `predict_model_deltas(dir, feature_columns, frame)` — batch per model
   (не per match): Oddin-матчи группируются под noxp, grid под research.
6. `MatchSignals(feed_ts, decision_ts, deltas, anchor_ps, feed_seconds,
   feed_paused, feed_terminal)`; `decision_ts` = `received_ns` решивших тиков.

Замечания:

- Несколько тиков с одной `game_second` (частый oddin sub-second) → оба
  решают (одни признаки, разные anchors) — не дедуплицируем.
- Равные `received_ns` внутри одного фида сохраняют `seq`-порядок; cross-feed
  упорядочивание не возникает (один фид на матч) — зафиксировать в docstring.
- `find_signal_asof`/`passes_anchor_gate`/fair-value helpers не меняются —
  kernel-side gate продолжает проверять `book_p` vs anchor.
- `market_status`-фильтр: tape только ok-строки; missing/stale секунды рынка
  дают `anchor=None` → tick без решения (не exclusion — рынок может честно
  отсутствовать).

## D. `src/backtest/run.py` — wiring

### Порядок в `main()`

После `selection = load_run_selection(...)` и до `resolve_run_policies`:

```python
auto_mode = args.live_since is None and policy_mode != "archive"
if auto_mode:
    index = load_archive_index()
    if game == "lol":
        feed = resolve_lol_feed_plans(selection.selected_ids, lol_audit, index, args.model_dir)
    else:
        feed = resolve_dota_feed_plans(selection.selected_ids, selection.sources.catalog, index, args.model_dir)
    for mid, reason in sorted(feed.exclusions.items()):
        logger.warning("archive exclusion: match %s — %s", mid, reason)
    if args.match_id is not None and args.match_id in feed.exclusions:
        raise ValueError(f"match {args.match_id}: {feed.exclusions[args.match_id]}")
    selection = replace(selection,
        selected_ids=tuple(m for m in selection.selected_ids if m not in feed.exclusions))
else:
    feed = FeedPlanResult(plans={}, exclusions={})
```

LoL audit должен дойти: `LolSelection` уже несёт `audit`; `RunSelection`
получает новое поле `feed_plan_input` — лучше хранить готовый
`FeedPlanResult`? Нет: resolution должен видеть `--model-dir`, который резолвится
позже. Чистый вариант: `RunSelection` += поле `archive_join` — маленький frozen
dataclass `ArchiveJoinInput` с `catalog` (dota) или `audit` (lol) ссылкой;
resolution вызывается в `main()` после `_pin_cli_model_dir` (model override
уже resolved). Для `--live-since`/`--policy archive` — `None`.

Исключения: `coverage` (если есть) получает `archive_excluded =
len(feed.exclusions)`; `ValidationCoverage` — frozen dataclass, конструкторы в
`selection.py` и `lol_inputs.py` обновить, `log_coverage` добавить число в строку.
`asdict` в `build_summary_payload` подхватит поле сам.

### `_resolve_run_signals` — dispatch

```python
if use_live_signals:  # старый путь, без изменений
    ...
schedule_ids = [m for m in plan_match_ids if feed.plans[m].mode == "schedule"]
fallback_ids = [m for m in plan_match_ids if feed.plans[m].mode == "grid_v1"]
signals = {}
if fallback_ids:
    signals.update(build_match_signals(fallback_ids, signal_rows, model_dir,
                                       selection_lag_seconds, game, timing))
if schedule_ids:
    feature_rows = load_game_feature_rows(game, schedule_ids, selection)
    tapes = build_anchor_tapes(game, schedule_ids, selection)
    try:
        signals.update(build_schedule_match_signals(schedule_ids, feed.plans, ...))
    except DatasetReadinessError as exc:
        raise ValueError(f"dataset not ready for archive replay: {exc}") from exc
```

`load_game_feature_rows`:

- `game == "dota"`: читает `GAME_FEATURES_DATASET_PATH` (фильтр по
  schedule_ids); файла нет → `ValueError`("run make prepare — game_features
  artifact missing"). Лениво: только когда `schedule_ids` непуст.
- `game == "lol"`: `selection.signal_rows` (уже unfiltered validation frame)
  с rename `second → game_second`.

`build_anchor_tapes`:

- dota: per-match `load_market_second_rows(market_seconds_cache_path(m))` →
  `market_anchor_tape`;
- lol: slice `selection.market_seconds` по match_id, ok-строки.

### `build_run_kernels` / `build_current_kernels`

`build_current_kernels` += `plans: Mapping[int, MatchFeedPlan]`;
`freshness.entry_stale_s` = `entry_stale_seconds(plan.binding)` для
schedule-матчей (oddin 15s / grid 16s), fallback — `GRID_FEED_STALE_SECONDS`
как сейчас. `exit_stale_s`/`book_stale_s` неизменны. `build_run_kernels`
пробрасывает plans (archive-mode ветка не трогается).

### `build_strategy_configs`

Для `match_signals.feed_game_seconds` непустого:

- `buy_cutoff_ns` = первый `feed_timestamps_ns[i]`, где
  `feed_game_seconds[i] >= kernels[m].policy.buy_cutoff_second`; если такого
  нет — последний feed ts (игра кончилась до cutoff).
- `game_end_ns` = последний `feed_timestamps_ns[i]` с `feed_terminal[i]`;
  нет terminal → старый `datetime_to_ns(context.game_ended_at)` (admitted
  schedules всегда имеют terminal — fallback чисто defensive).
- config payload += `feed_game_seconds`, `feed_paused`, `feed_terminal`.
- `pauses` для cutoff больше не нужны у schedule-матчей (observed crossing).

Для пустых лент — ровно текущее поведение.

### Флаги

- `--lag-seconds`/`--cadence-mean-interval`: если в `feed.plans` есть хотя бы
  один `mode == "schedule"` → `parser.error` с именем первого schedule-матча
  (спек: применять к архивному расписанию — отклонять). Чисто-синтетическая
  когорта — разрешено.
- `--signal-cadence-seed` действует только на fallback-матчи (манифест хранит
  как раньше).
- `--model-dir` → `model_override` во всех планах (эксперимент); в results
  попадёт его `model_dir.name`.

### Манифест

`build_run_manifest` +=

- `signal_source`: `"auto"` при auto_mode (вместо `"grid_v1"` — resume-assert
  старых runs отвалится корректно, что и нужно),
- `schedule_map_sha256: schedule_map_sha256(feed.plans)` (при auto_mode),
- `feed_schedule_rules_version: EXTRACTION_RULES_VERSION` и
  `admission_rules_version: ADMISSION_RULES_VERSION` — читаемость, fingerprint
  уже их включает.

### Results

`MakerMatchResult` += `signal_mode: str` (`"schedule"|"grid_v1"|"live_archive"`)
и `model_name: str` (`plan.model_dir.name`). `build_maker_match_result`,
`build_maker_match_results` (через новый параметр `plans`/map),
`engine_fault_match_result` (поля из плана; fault до engine). Old path:
`signal_mode="live_archive"`, `model_name` из archive header или `""` —
старый путь не знает model identity → `""`.
`read_results_checkpoint` читает `frame[field_names]` — старые parquet без новых
колонок упадут с KeyError; приемлемо, т.к. manifest-assert (signal_source
сменился) останавливает resume раньше.

### `telonex_local.py` — strip own-book

`create_telonex_source_tree(contexts, capture_root, indexes)` получает
required-параметр `schedule_archives: Mapping[int, Path]`; внутри:

```python
archive = schedule_archives.get(context.match_id) or _archive_for_context(context, indexes=indexes)
```

Run-цепочка: `replay_matches(..., schedule_archives={m: archive_dir for plan...})`,
`warm_replay_cache` — то же. Старый путь передаёт `{}`. Это чинит strip для
Oddin-архивов (их директории не `grid-*` и `find_archive_for_match_id` их не
видит), не ломая live-резолвер для остальных. `archive_dir` для binding =
`Path(ARCHIVE_INDEX_DIR).parent` … нет — archive root из плана:
`roots` map — index хранит `archive_root` label; реальную директорию даёт
`scan_root`/`TRADER_DIR`-known roots: binding несёт `archive_dir: Path`
(= `root_path / archive_id`, root_path из того же `roots` что писал index —
`archive_index.__main__` строит roots из `TRADER_DIR`; resolver возвращает её в
`ScheduleBinding`). Держать в `ScheduleBinding.archive_dir`.

## E. `src/backtest/strategy.py` — наблюдаемые часы

`DotaMakerConfig` +=

```python
feed_game_seconds: tuple[int, ...]
feed_paused: tuple[bool, ...]
feed_terminal: tuple[bool, ...]
```

`_clock_at`:

```python
def _clock_at(self, now_ns: int) -> GameClock:
    if not self._config.feed_game_seconds:
        return GameClock(now_ns=now_ns,
            game_second=540 if now_ns >= self._config.buy_cutoff_ns else 0,
            paused=False,
            game_ended=now_ns >= self._config.game_end_ns)
    index = bisect_right(self._config.feed_timestamps_ns, now_ns) - 1
    if index < 0:
        return GameClock(now_ns=now_ns, game_second=0, paused=False, game_ended=False)
    return GameClock(
        now_ns=now_ns,
        game_second=self._config.feed_game_seconds[index],
        paused=self._config.feed_paused[index],
        game_ended=self._config.feed_terminal[index] or now_ns >= self._config.game_end_ns,
    )
```

- `phase` в `GameClock` нет (поле не существует) — `paused`/`terminal`/`game_second`
  покрывают kernel-семантику; `phase` хранится в ленте при необходимости позже —
  не добавляем мёртвое поле. (Проверить `GameClock` в `src/strategy` — если поле
  есть, пробросить.)
- `game_ended` = observed terminal ИЛИ `game_end_ns` (catalog fallback).
- Равные `received_ns` → `bisect_right` берёт последний тик — последнее
  наблюдаемое состояние на этот момент. Верно.
- Paused-тик → `GameClock.paused=True` → `_sync_inputs`/`ClockUpdate` → kernel
  ставит `paused`-блок и снимает quotes — поведение live.
- Buy-cutoff alert на `received_ns` crossing-тика совпадает с его SIGNAL alert —
  порядок alert'ов неважен: `_clock_at` уже отдаёт `game_second >= cutoff`, и
  `_on_buy_cutoff` снимает BUY'и; kernel `_cutoff` подстраховывает на следующем
  evaluate. До первого тика — stub `0/False/False` (late-join матч с первым
  тиком ≥ cutoff сразу видит секунду ≥ cutoff — BUY'и не ставятся).
- Interruption-маркеры (Oddin reconnect, GRID feed_gone) отдельных clock-событий
  не создают — gap виден через `recovery_stale_mask`/freshness, а следующий тик
  несёт новое состояние. Как в live: reconnect сам по себе не update.

`on_start`: `seen_signal_ts` dedup по feed ts сохранить (равные ts → один alert —
clock-лента всё равно читается по bisect). Никаких других правок: SIGNAL alerts
на каждый feed-тик уже ставятся.

## F. Модельная матрица (финальная)

| Матч | model_dir | features |
|---|---|---|
| Dota + GRID archive | `RESEARCH_MODEL_DIR` | 12 (с XP) |
| Dota + Oddin archive | `RESEARCH_NOXP_MODEL_DIR` | 11 (без `radiant_xp_adv`) |
| Dota без архива | `RESEARCH_MODEL_DIR` | 12 |
| LoL + GRID archive | `LOL_RESEARCH_MODEL_DIR` | LoL feature set |
| LoL без архива | `LOL_RESEARCH_MODEL_DIR` | LoL feature set |
| любой `--model-dir` | override | из его `model.json` |

`assert_lol_source_lag` для LoL не меняется (10s lag валидация остаётся).
`NO_XP`-модель отсутствует → `load_model_feature_columns` бросает понятную ошибку —
это и есть "понятная ошибка, а не подмена".

## G. Тесты (новые и обновлённые)

`tests/test_feed_schedules.py`:

- dota catalog `archive_id` → binding; schedule fingerprint проверен;
- catalog `archive_id` + index admission ≠ admitted → `archive:<reason>`;
- `schedule_fingerprint` расходится index↔catalog → `schedule_stale`;
- admitted index row без catalog link → `archive_unlinked`;
- только refused rows → `archive:record:no_terminal`-style reason;
- нет rows → `mode="grid_v1"`, `binding=None`;
- `duplicate_of:*` кандидат → `archive:duplicate_of:<id>`;
- LoL: join по `condition_id` (lowercase), admitted → binding,
  model=`LOL_RESEARCH_MODEL_DIR`;
- oddin binding → `model_dir=RESEARCH_NOXP_MODEL_DIR`, grid → `RESEARCH_MODEL_DIR`;
- `model_override` побеждает;
- `entry_stale_seconds`: oddin→15.0, grid→16.0.

`tests/test_backtest_signals.py` (новые + fixture `MatchSignals` update):

- тик `(arrival T, second g)` → решение с `second == g` и признаками строки `g`
  (нет `g - lag`);
- `market_p_radiant` решения == anchor as-of на `received_ns`, НЕ
  `market_p_radiant` dataset-строки;
- два тика с одинаковым `game_second`, разными arrival → два решения, разные anchors;
- gap > exit-age → пост-gap тик в `feed_timestamps_ns`, но не в `timestamps_ns`;
- отсутствие feature-строки → `DatasetReadinessError` с именем match+second;
- `feed_game_seconds/paused/terminal` выровнены с `feed_timestamps_ns`;
- смена модели → другие `predicted_deltas`, неизменные
  `feed_timestamps_ns`/`feed_game_seconds` (FakeBooster-вариант);
- `build_match_signals` отдаёт пустые ленты (старый путь не сломан).

`tests/test_backtest_maker.py`:

- `build_maker_config` fixture обновить новыми полями (`()` по умолчанию);
- `_clock_at` по ленте: до первого тика stub; paused-тик → `paused=True`;
  terminal-тик → `game_ended`; после тика значения держатся до следующего;
- cutoff: `buy_cutoff_ns` = ts crossing-тика; clock на нём ≥ cutoff;
- terminal + `game_end_ns` fallback.

`tests/test_prepare_dataset.py`: `build_game_feature_rows` — строки на все
states, `game_second == state.second`, `market_radiant_prior` проброшен,
колонки совпадают с `StateFeatures`.

`tests/test_backtest_validation.py`: `ValidationCoverage` ctor += `archive_excluded`.

`tests/test_live_archives.py`: `MatchSignals` ctor += `(), (), ()` (stub-ленты).

## H. Порядок реализации

1. `GameFeatureRow` + `GAME_FEATURES_DATASET_PATH` + emission в prepare_dataset.
2. `feed_schedules.py` (resolution + bindings + digest).
3. `signals.py` (`MatchSignals` fields, tape, `build_schedule_match_signals`,
   `DatasetReadinessError`).
4. `strategy.py` (`_clock_at` + config).
5. `run.py` wiring (plans → exclusions → kernels → signals → configs → manifest
   → results columns → telonex_local param).
6. `selection.py`/`lol_inputs.py` coverage/audit plumbing.
7. Тесты по списку выше.
8. `ruff` + `basedpyright`.

## I. Проверка

```bash
cd ../esports-trader
uv run pytest tests/test_feed_schedules.py tests/test_backtest_signals.py \
  tests/test_backtest_maker.py tests/test_prepare_dataset.py \
  tests/test_backtest_validation.py tests/test_live_archives.py -x -q
uv run pytest tests/test_lol_backtest.py tests/test_lol_prepare_dataset.py \
  tests/test_lol_prepare_backtest.py tests/test_archive_index.py \
  tests/test_archive_pauses.py tests/test_match_catalog.py -x -q   # регресс LoL/step-2 контрактов
uv run ruff check src/ tests/
uv run python -m basedpyright            # whole-project, как в pre-commit
```

- Не запускать полный suite во время live-карты.
- Не запускать `make prepare`/`make train`/`make backtest` — artifact
  game_features появится при следующем реальном `make prepare` (до первого
  archive-run); `build_schedule_match_signals` и так бросает понятную ошибку
  при отсутствующем файле.
- Бэктесты не запускать; если позже запускается — two-column terminal report,
  никакого dump `summary.json`.

## J. Явные упрощения (зафиксировать)

- Dota features — отдельный артефакт `game_features.parquet` (полное покрытие
  секунд), а не re-key validation parquet: validation-строки теряют хвост
  ~10 секунд матча и не имеют колонки `game_second`.
- LoL использует существующий `validation.parquet` напрямую (`second` — уже
  игровая секунда).
- `--model-dir` применяется ко всем матчам (эксперимент), не per-source.
- `phase` из расписания не пробрасывается в `GameClock`, если там нет поля —
  paused/terminal/game_second достаточно kernel'у.
- Exclusion reasons — строки вида `archive:<admission>` из index.parquet;
  новых кодов не вводим, кроме `archive_unlinked`, `schedule_stale`,
  `schedule_missing`, `schedule_fingerprint_mismatch`,
  `dataset_features_missing`.
- `archive_parity` latency для auto-режима: оставить текущее поведение
  (определяется старым `args.live_since or policy == "archive"` условием) —
  проверить при реализации: если archive-матчи должны replay'ить latency как
  live-архивы, флаг выставлять по наличию schedule-планов. Решение при
  имплементации, отметить в PR.

## K. Запрещено этим планом

- `poly-maker`, `polymarket-collector`, on-chain скрипты — не трогаем.
- `betting_workspace/current-task/` — не используем.
- Не менять train lag; не менять validation parquet schema.
- Не replay'ить сохранённые live predictions/feature-значения из core_trace —
  это старый путь, остаётся под `--live-since`/`--policy archive` до 3B.
- Не подменять incomplete-архив синтетическим cadence.
- Не вводить новый experimental-mode framework.
- Не запускать train/backtest/full-suite-live-map в этой задаче.
