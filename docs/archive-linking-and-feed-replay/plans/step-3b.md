# Step 3B — обычный запуск без live-флагов, когорта «с этого матча» по времени каталога, mixed-model отчёт

Design: `esports-trader/docs/superpowers/specs/2026-09-20-archive-linking-and-feed-replay-design.md` §3B.
Product repo: `../esports-trader` (branch `main`, HEAD `21274237`). No commits from this plan.

## Цель и инварианты

Обычный `python -m backtest.run` становится единственным путём: без `--policy`,
без `--live-since`, без чтения старых `SignalUpdate` из core_trace. Каждый
выбранный матч проходит через `resolve_feed_plans` (Step 3A) безусловно:
admitted-архив → `SchedulePlan`, нет архива → `GridV1Plan`, плохой архив →
явное исключение.

- Из обычного запуска убраны `--policy archive|current` и `--live-since`:
  implicit-режим «старые сигналы из core_trace» удалён целиком.
- `make parity` остаётся отдельной диагностикой: `trader.replay_core_trace`
  читает core_trace через `trader.core_trace_codec.decode_header_row` и не
  зависит от `backtest/live_archives.py` — рецепт и модуль не трогаем.
- Фильтр «с этого матча» остаётся, но как **когортный фильтр по стабильному
  времени матча из каталога**, а не по `mtime` архивных директорий
  (rsync меняет mtime; каталог — нет). Новый флаг `--since-match <match_id>`.
- `--lag-seconds` / `--cadence-mean-interval` / `--validation-dataset` —
  не source-specific knobs: отклоняем, если в выборке есть хоть один
  `SchedulePlan`. На чисто-synthetic когорте работают как раньше.
- LoL едет тем же механизмом (`resolve_lol_feed_plans`, GRID LoL profile);
  whitelist остаётся на выборе матчей бэктеста, не на train.
- Отчёт показывает источник расписания и модель каждого матча, причины
  исключений и раздельные числа по группам.
- Манифест держит fingerprints расписаний отдельно от fingerprints
  датасета и моделей, версии правил допуска/извлечения, политику, seed
  синтетики, когорту и причины исключения.
- Пересборка датасета / смена модели **не** инвалидирует кэш расписаний
  и не меняет `schedule_map_sha256`; при этом resume корректно отказывает
  через dataset/model-ключи манифеста.

## Non-goals

- Не запускаем `make prepare` / `make train` / `make backtest` /
  `make lol-*` — train и бэктесты идут после этого шага.
- Не трогаем `trader/replay_core_trace.py`, `trader/core_trace_codec.py`,
  `Makefile::parity` — это диагностический путь, не бэктест.
- Не трогаем `src/archive_index/` — правила извлечения/допуска и fingerprint
  уже версионируются (`EXTRACTION_RULES_VERSION`, `ADMISSION_RULES_VERSION`).
- Не меняем train lag (10s), не вводим 15s-эксперимент, не меняем
  `LOL_EXECUTION_PRIORITY` и whitelist.
- Не трогаем `poly-maker`, `polymarket-collector`, on-chain скрипты
  (`telonex_onchain.py`, `sync_onchain_fills.py`, `parity_onchain_rpc.py`,
  `sync_collector_parquet.py`).
- Не используем `betting_workspace/current-task/`.

## Текущее состояние после 3A

`src/backtest/run.py::main()` уже решает feed-планы в auto-режиме
(`auto_mode = args.live_since is None and policy_mode != "archive"`), но
рядом живёт старый путь:

- `run.py:1211` — `--live-since GRID_DIR`; `run.py:1217` — `--policy`.
- `run.py:1666` — `_resolve_run_signals` ветка `use_live_signals` читает
  `SignalUpdate` из core_trace (`build_live_archive_signals_by_match`).
- `run.py:419` — `build_archive_kernels` (kernel config из хедера архива);
  `run.py:474` — `build_run_kernels` dispatch по `policy_mode`.
- `run.py:390` — `resolve_run_policies` берёт `Follow300Policy` из
  `ArchiveHeader.trace.policy` при `policy_mode == "archive"`.
- `run.py:301` (`live_archives.py`) — `read_run_archive_headers`.
- `run.py:260` — `build_order_latency(archive_parity=...)`: `cancel=0` для
  live-режима. `run.py:1925` — `archive_parity=(live_since or policy=="archive")`.
- `run.py:1373-1429` — `load_run_selection` ветки `live_since` через
  `resolve_live_since_match_ids` (mtime-фильтр по `list_grid_archive_dirs`).
- `run.py:1780` — `live_indexes` (`build_live_indexes`/`_lol_live_indexes`)
  нужны старому пути и `create_telonex_source_tree`-fallback'у.
- `Makefile:106` — help `backtest:` рекламирует `--policy` и `--live-since`.
- `manifest` пишет `backtest_policy`, `archive_policy_sha`,
  `archive_model_sha256`, `signal_source` (`"live_archive"|"auto"`),
  `schedule_map_sha256` (с `plan.model_dir` внутри digest'а).
- `results.py:65` — `signal_provenance` имеет ветку `live_archive` через
  `ArchiveHeader`; `MakerMatchResult` уже несёт `signal_mode`/`model_name`.

## Семантика запуска

### Целевые селекторы (mutually exclusive, required)

| Флаг | Когорта | Feed |
|---|---|---|
| `--match-id N` | один матч | feed-plan на матч; исключён → `ValueError` с причиной |
| `--validation` | eligible split (dota) / eligible audit + whitelist (lol) | per-match auto |
| `--since-match N` | replayable-матчи с временем каталога `>=` времени матча-якоря | per-match auto |

`--since-match` — когортный селектор уровня `--validation`: требует `--name`
(кроме `--warm-cache`), допускает `--limit`, `--shard`, `--merge-shards`,
`--resume`, `--signal-cadence-seed`, `--model-dir`. Старый `--live-since`
запрещал shard/limit/merge; новый флаг их разрешает — когорта обычная,
`plan_replay_ids`/`build_report_dir` уже умеют bulk-режим при
`args.match_id is None`.

### Когортный фильтр «с этого матча»

Якорь — только точка отсчёта времени; он не обязан быть eligible и не
обязан иметь архив. Отсчёт **включительно** (`>=`).

- **Dota**: якорное время = `sources.catalog[since_match_id].start_time`
  (`CatalogEntry.start_time`: unix-секунды, `spawn_at` иначе `horn_at` —
  то же поле, по которому сортируется split). Когорта = матчи
  `catalog ∩ usable_signal_match_ids`, не в `book_gap_excluded_match_ids`,
  с локальными telonex-данными (`has_local_telonex_days` по
  `calculate_availability_window(anchor_at, ended_at)` — тот же фильтр, что
  в `select_validation_matches`), у которых `start_time >= anchor`,
  отсортированные `(start_time, match_id)`. Якорь вне каталога →
  `parser.error`/`ValueError`.
- **LoL**: якорное время = horn матча = `signal_rows.groupby("match_id")
  ["start_time"].first()`. Когорта = `audit[eligible]` (аудит уже отфильтрован
  whitelist'ом внутри `load_lol_selection`) ∩ `signal_match_ids`, у которых
  `horn >= anchor`, отсортированные `(horn, match_id)`. Якоря нет в
  signal_rows → `ValueError`. Whitelist применяется как обычно — он фильтрует
  выбор матчей бэктеста, train его не видит (`src/lol/06_train_model.py`
  whitelist не импортирует — проверить грепом при реализации).
- Пустая когорта → `ValueError("no matches at/after <anchor>")`.
- После фильтра применяется `--limit` (обрезка упорядоченного списка).
- `coverage=None` для since-когорты (как у `--match-id` сейчас); причины
  исключений архивов всё равно логируются и попадают в манифест.

### Чего не делает фильтр

Не выбирает источник сигнала и не смотрит на архивы: матчи без архива
получают `GridV1Plan`, матчи с не-admitted архивом исключаются с причиной —
тем же кодом, что `--validation`. Никакого чтения core_trace.

## Файлы

### Изменяемые

- `src/backtest/run.py` — parser, `load_run_selection`, `main`, kernels,
  policies, signals-dispatch, manifest-call, удаление live-веток.
- `src/backtest/live_archives.py` — сжимается до inspector-хелперов.
- `src/backtest/feed_schedules.py` — `reject_schedule_flags` +
  `validation_dataset`; `schedule_map_sha256` без `model_dir`.
- `src/backtest/selection.py` — `select_dota_since_match_ids`.
- `src/backtest/lol_inputs.py` — `select_lol_since_match_ids`.
- `src/backtest/results.py` — `SignalProvenance.feed_source`, без
  `ArchiveHeader`/live_archive-ветки; `MakerMatchResult.feed_source`.
- `src/backtest/telonex_local.py` — удаление `_archive_for_context` и
  `indexes`-параметра.
- `src/backtest/report_types.py` — `ArmSummary.signal_groups`,
  `ArmSummary.model_groups`.
- `src/backtest/postprocess.py` — `summarize_arm` считает группы.
- `src/backtest/report.py` — секция `SIGNALS` в terminal report.
- `scripts/compare_backtests.py` — `EXPECTED_MANIFEST_DIFFS` += новые ключи.
- `Makefile` — help-строки `backtest:` и `lol-backtest:`.

### Тесты

- `tests/test_backtest.py`, `tests/test_live_archives.py`,
  `tests/test_backtest_validation.py`, `tests/test_extraction_oracle.py`,
  `tests/test_lol_backtest.py`, `tests/test_feed_schedules.py` —
  обновления сигнатур и удаление тестов мёртвого пути.
- Новые тесты — секция G.

### Не трогаем

`src/backtest/signals.py`, `src/backtest/strategy.py`,
`src/archive_index/`, `src/trader/replay_core_trace.py`,
`src/trader/core_trace_codec.py`, `scripts/run_seeds.sh`,
`scripts/report_seeds.py` (новые ключи seed-инвариантны),
`scripts/promote_backtest.py`, `src/backtest/inspect/`.

## A. `src/backtest/run.py` — CLI и main

### Parser (`build_parser` / `parse_args`)

- Удалить `target.add_argument("--live-since", ...)` и
  `parser.add_argument("--policy", ...)`.
- Добавить в target-группу:
  ```python
  target.add_argument(
      "--since-match",
      type=int,
      metavar="MATCH_ID",
      help="replay the replayable cohort with catalog match time "
      "at/after this match (chronological)",
  )
  ```
- `parse_args`:
  - `--name` обязателен при `args.validation or args.since_match is not None`
    (тот же `parser.error`, текст обновить).
  - Удалить проверку «--limit/--shard/--merge-shards do not apply to
    --live-since» — `--since-match` их допускает.
  - `--match-id`-запреты (`--limit/--resume/--shard/--merge-shards`) —
    без изменений.
  - `_pin_dota_experiment_flags` (`--lag-seconds`, `--validation-dataset`,
    `--cadence-mean-interval` только dota) — без изменений.
- Модульный docstring (= description парсера) обновить: три селектора,
  per-match feed resolution, никакого `--policy`.

### `load_run_selection`

- Параметр `live_since: str | None` → `since_match: int | None`
  (required-стиль: явный keyword-аргумент без дефолта, как `match_id`).
- LoL-ветка `live_since` заменяется:
  ```python
  probe = load_lol_selection(None, None, LOL_RESEARCH_MODEL_DIR, allowed_event_ids)
  since_ids = select_lol_since_match_ids(probe.audit, probe.signal_rows, since_match)
  if limit is not None:
      since_ids = since_ids[:limit]
  lol = replace(probe, selected_ids=since_ids, coverage=None)
  ```
- Dota-ветка `live_since` заменяется:
  ```python
  signal_rows = load_usable_signal_rows(validation_dataset)
  sources = load_market_sources(signal_rows)
  since_ids = select_dota_since_match_ids(sources, since_match)
  if limit is not None:
      since_ids = since_ids[:limit]
  dota = load_dota_selection(None, None, match_ids=since_ids,
                             validation_dataset=validation_dataset)
  ```

### `main()`

- Удалить `policy_mode`, `live_indexes`, `auto_mode`, `archive_headers`,
  `use_live_signals`-логику.
- `resolve_feed_plans` + `_apply_feed_exclusions` + `reject_schedule_flags`
  вызываются безусловно (сейчас под `if auto_mode:`); `feed` всегда
  заполнен.
- `reject_schedule_flags(feed.plans, lag_seconds=..., cadence_mean_interval=...,
  validation_dataset=args.validation_dataset)` — новый параметр.
- `schedule_archives = schedule_archive_dirs(feed.plans)` — без изменений.
- `run_policies = resolve_run_policies(game=game, match_ids=...,
  min_abs_delta=..., exit_abs_delta=...)` — без `policy_mode`/`archive_headers`.
- `default_level = "INFO" if args.match_id is not None else "WARNING"`.
- `engine_skip_ids = frozenset() if args.match_id is not None
  else NAUTILUS_ZERO_FILL_SKIP_IDS` (since-когорта — bulk, skip-лист
  остаётся).
- `kernels = build_current_kernels(contexts=..., policies=..., plans=feed.plans)`
  — `build_run_kernels`/`build_archive_kernels` удалить.
- `provenance = signal_provenance_map(feed.plans, plan.match_ids)`.
- `replay_matches(...)` — без `archive_parity`/`indexes`.
- `warm_replay_cache(...)` — без `indexes`.
- Манифест — секция D.

### Функции run.py

- `resolve_run_policies`: убрать `policy_mode` и `archive_headers`; тело —
  всегда `follow300_policy(level, debounce, fallback)` + overrides.
- `build_order_latency`: убрать `archive_parity`; `cancel_latency_ns` всегда
  `round(NETWORK_LATENCY_MS * 1e6)` (auto-режим уже работал с
  `archive_parity=False`; live-архивный `cancel=0` умирает вместе с путём).
- `run_batch`/`replay_matches`: убрать `archive_parity`, `indexes`.
- `warm_replay_cache`: убрать `indexes`.
- `_resolve_run_signals`: убрать параметр `args` и ветку `use_live_signals`
  (вместе с `build_live_indexes(game)`/`TRADER_DIR`); принимать
  `signal_cadence_seed: int`, `cadence_mean_interval: int | None` явно.
  Оставшийся код — split `SchedulePlan`/`GridV1Plan` — без изменений.
- `_lol_live_indexes`, `build_live_indexes` — удалить.
- Импорты `live_archives` (`PolicyMode`, `LiveMatchIndexes`,
  `build_dota_live_indexes`, `resolve_live_since_match_ids`,
  `build_live_archive_signals_by_match`, `read_run_archive_headers`,
  `ArchiveHeader`) и `TRADER_DIR` — удалить, если не осталось пользователей.

## B. Когортный фильтр

### `src/backtest/selection.py`

```python
def select_dota_since_match_ids(
    sources: MarketSources, since_match_id: int
) -> tuple[int, ...]:
    """Replayable catalog matches with start_time >= the anchor match's."""
```

- `anchor_entry = sources.catalog.get(since_match_id)`; `None` →
  `ValueError("--since-match <id> is not in the match catalog")`.
- Гейты на матч — те же, что в `select_validation_matches`:
  `mid in usable_signal_match_ids`, `mid not in book_gap_excluded_match_ids`,
  `has_local_telonex_days(gamma.token_ids,
  calculate_availability_window(anchor_at, ended_at))`.
- Отбор `entry.start_time >= anchor_entry.start_time`, сортировка
  `(start_time, match_id)`, пустой результат → `ValueError`.
- Не вызывать `select_validation_matches` целиком: его вселенная —
  `validation_match_ids`, а since-когорта включает любые replayable
  catalog-матчи (в т.ч. свежие archive-linked вне текущего split —
  у них есть signal/feature строки после следующего `make prepare`).

### `src/backtest/lol_inputs.py`

```python
def select_lol_since_match_ids(
    audit: pd.DataFrame, signal_rows: pd.DataFrame, since_match_id: int
) -> tuple[int, ...]:
    """Eligible audited maps with horn >= the anchor match's horn."""
```

- `horns = signal_rows.groupby("match_id")["start_time"].first()`;
  якоря нет → `ValueError`.
- Когорта = `match_id` из `audit[audit["eligible"]]`, у которых horn
  `>=` якорного; сортировка `(horn, match_id)`. `audit` приходит уже
  whitelist-отфильтрованным (probe из `load_lol_selection`).

## C. Удаление старого live-пути

### `src/backtest/live_archives.py` — оставить только inspector-хелперы

Удалить: `PolicyMode`, `LiveArchivePick`, `list_grid_archive_dirs`,
`ArchiveHeader`, `_decode_archive_header_line`, `read_archive_header`,
`read_archive_headers_by_match`, `read_run_archive_headers`,
`_try_pick_archive`, `_raise_live_since_gaps`,
`resolve_live_since_match_ids`, `_wall_ns_from_trace`,
`_append_live_signal_tick`, `build_match_signals_from_live_archive`,
`build_live_archive_signals_by_match`, `find_archive_for_match_id`.

Оставить (нужны `inspect/tail.py`): `LiveMatchIndexes`,
`build_dota_live_indexes`, `load_match_json`, `resolve_archive_match_id`,
`read_session_live_equity`, `live_equity_by_match_ids` и приватные
`_market_slug`/`_condition_id`/`_as_dict`, которые они используют.
Docstring модуля → «live-archive lookups for the backtest inspector».

Проверка независимости parity: `src/trader/replay_core_trace.py` импортирует
только `trader.core_trace_codec` — `make parity` продолжает работать.

### `src/backtest/results.py`

- `SignalProvenance` += `feed_source: str`
  (`binding.feed_source` → `"grid"|"oddin"`; `GridV1Plan` → `"grid_v1"`).
- `signal_provenance(plan: MatchFeedPlan) -> SignalProvenance` — без
  `header`-параметра и `live_archive`-ветки; аргумент required (план всегда
  есть — `plan=None` больше невозможен).
- `signal_provenance_map(plans, match_ids)` — без `archive_headers`.
- `MakerMatchResult` += `feed_source: str` (рядом с `signal_mode`,
  `model_name`); оба builder'а (`maker_match_result`,
  `engine_fault_match_result`) пишут `provenance.feed_source`.
- Импорт `ArchiveHeader` удалить.

### `src/backtest/telonex_local.py`

- Удалить `_archive_for_context` и параметр `indexes` у
  `create_telonex_source_tree`: после исключений каждый replayable матч
  с архивом — `SchedulePlan` (есть в `schedule_archives`), у `GridV1Plan`
  архива нет по построению — fallback мёртв. Strip own-book покрыт
  `schedule_archives` полностью.
- Импорты `LiveMatchIndexes`, `find_archive_for_match_id` удалить;
  docstring «indexes resolve…» переписать под schedule_archives-only.
- Вызывающие `run.py` (2 места) и тесты обновить сигнатуру.

## D. Манифест (`build_run_manifest`)

### Сигнатура

- Удалить параметры `archive_header`, `policy`, `signal_source`.
- `schedule_map_sha256: str | None` → `plans: Mapping[int, MatchFeedPlan]`
  (required): digest, `model_dirs` и `has_schedule` выводятся из plans —
  манифест всегда несёт schedule-ключи.
- += `exclusions: Mapping[int, str]`, `since_match: int | None`.

### Ключи

- `"backtest_policy": "current"` — константа (старые `"archive"`-манифесты
  resume откажут — желаемо).
- `"signal_source": "auto"` — константа (старые `"grid_v1"`/`"live_archive"`
  resume откажут).
- Удалить `archive_policy_sha`, `archive_model_sha256` — они уже в
  `EXPECTED_MANIFEST_DIFFS`, старые манифесты сравниваемы.
- `"schedule_map_sha256"`, `"feed_schedule_rules_version"`,
  `"admission_rules_version"` — всегда (сейчас условно).
- `"archive_exclusions"`: `{str(match_id): reason}` отсортировано по id;
  `{}` когда пусто — эмитим всегда для стабильной схемы.
- `"since_match": <int>` — только при использовании флага.
- `"model_sha256_by_dir"`: `{str(dir): model_identity_sha256(dir)}` по всем
  уникальным `plan.model_dir` — resume откажет при переобучении любого из
  использованных каталогов моделей (текущий `model_sha256` покрывает только
  `selection.model_dir` и пропустил бы noxp-переобучение).
- `"game_features_sha256"` — для dota при наличии хотя бы одного
  `SchedulePlan`: schedule-матчи читают `game_features.parquet`, а не
  validation parquet; sha256_file упадёт с понятной ошибкой, если артефакта
  ещё нет (та же ситуация, что и в `build_schedule_match_signals`).
- `model_name`/`model_path`/`model_sha256` остаются = `selection.model_dir`
  (primary/override dir); per-match истина — `model_sha256_by_dir` +
  колонки results.parquet.
- `signal_cadence_seed`, `selected_matches`, `selected_matches_sha256`,
  `validation_dataset_sha256` — без изменений; seeds-ключей исполнения нет
  (исполнение детерминировано, записывать нечего — spec это допускает).

### `feed_schedules.schedule_map_sha256`

Digest считается по `{match_id}|{mode}|{schedule_fingerprint}` —
`plan.model_dir` убрать из строк: fingerprint расписания не должен
включать идентичность модели (spec: «fingerprints расписаний отдельно от
fingerprints датасета и моделей»). Смена модели всё равно ломает resume —
через `model_sha256_by_dir`, а не через schedule-ключ. Назначение
mode→model_dir детерминировано из `binding.feed_source` (fingerprint его
покрывает через `feed_sha256`), так что потери покрытия нет.

## E. Mixed-model отчёт

### Данные

- `MakerMatchResult`: `signal_mode` (`"schedule"|"grid_v1"`),
  `feed_source` (`"grid"|"oddin"|"grid_v1"`), `model_name` — уже пишутся
  в `results.parquet` (два поля есть, добавляем третье).
- `manifest["archive_exclusions"]` — per-match причины (автоматически
  попадают в `summary.json` через `payload["manifest"]`).

### `report_types.py` / `postprocess.py`

- `ArmSummary` += `signal_groups: dict[str, int]`,
  `model_groups: dict[str, int]`.
- `summarize_arm` считает по `results` (все строки, включая terminated —
  это счёт когорты, не PnL):
  - `signal_groups[label]`: `"schedule:grid"`, `"schedule:oddin"`,
    `"grid_v1"` (label = `feed_source` для grid_v1,
    `f"{signal_mode}:{feed_source}"` иначе);
  - `model_groups[model_name]`.

### `report.py::format_terminal_report`

Секция `SIGNALS` после `RUN`-блока:

```
SIGNALS
  schedule:grid         87
  schedule:oddin         5
  grid_v1              120
  excluded               3   (schedule_stale 2, archive_unlinked 1)
  model research       207
  model research-noxp    5
```

- Группы из `arm["signal_groups"]`/`arm["model_groups"]`; `excluded` —
  Counter причин из `payload["manifest"]["archive_exclusions"]`
  (нет ключа/пусто → строка `excluded 0` или пропускаем — решить по
  краткости при реализации).
- Только terminal + summary; `summary.json` не дампим в stdout
  (правило backtest-report).

## F. Скрипты и Makefile

- `Makefile:106` `backtest:` help →
  `ARGS="--match-id N" | "--validation --name <s> …" | "--since-match N --name <s> […]"`.
- `Makefile:132` `lol-backtest:` help — добавить `--since-match N --name <s>`
  в список форм запуска.
- `Makefile:100` `parity:` — без изменений.
- `scripts/compare_backtests.py::EXPECTED_MANIFEST_DIFFS` += `signal_source`,
  `schedule_map_sha256`, `archive_exclusions`, `since_match`,
  `model_sha256_by_dir`, `game_features_sha256` (парные эксперименты могут
  различаться когортой/архивами/моделями — как `selected_matches_sha256`).
  `archive_policy_sha`, `archive_model_sha256` оставить в set — старые
  манифесты их несут.
- `scripts/report_seeds.py` — без изменений: `SEED_MANIFEST_KEY` единственный
  seed-variant ключ; новые ключи идентичны между seed'ами (одна когорта,
  одни планы, одни исключения).
- `scripts/run_seeds.sh` — без изменений: шлёт `--validation --name
  --signal-cadence-seed`, что и требуется.
- `src/backtest/inspect/` — без изменений (читает свои колонки;
  `feed_source` — аддитивная колонка parquet).

## G. Тесты

### Обновить

- `test_backtest.py`
  - `test_backtest_cli_exposes_only_operational_options`: kept +=
    `--since-match`; removed += `--policy`, `--live-since`.
  - `test_parse_args_rejects_every_removed_policy_flag`: parametrize +=
    `--policy`, `--live-since`.
  - `build_run_manifest` вызовы (~4 места): новая сигнатура
    (`plans`, `exclusions`, `since_match`; без `archive_header`).
  - `resolve_run_policies` вызовы: без `policy_mode`/`archive_headers`.
  - `build_order_latency`: без параметра.
  - `LiveMatchIndexes` import + фейки `create_telonex_source_tree` —
    новая сигнатура без `indexes`.
- `test_live_archives.py` — удалить тесты мёртвого пути
  (`test_resolve_live_since_*`, `test_list_grid_archive_dirs_missing_since`,
  `test_read_archive_header(s)_*`, `test_build_match_signals_from_live_archive`,
  `test_resolve_via_market_slug_when_steam_null` если ходит через
  `resolve_live_since_match_ids`). Оставить
  `test_build_dota_live_indexes_fills_slug_and_condition`.
  `test_match_kernel_config_survives_msgspec` переписать на
  `decode_header_row` из `trader.core_trace_codec` (та же фикстура
  header-row; цель теста — msgspec roundtrip `MatchKernelConfig`).
- `test_backtest_validation.py`: `run_batch`/`replay_matches` вызовы —
  без `archive_parity`/`indexes`; `EMPTY_LIVE_INDEXES` и import
  `LiveMatchIndexes` удалить.
- `test_extraction_oracle.py`: `run_batch` без `archive_parity`;
  `create_telonex_source_tree` без `build_live_indexes(game)`; import
  `build_live_indexes` удалить.
- `test_lol_backtest.py`: `build_run_manifest` — новая сигнатура.
- `test_feed_schedules.py`:
  `test_schedule_map_sha256_tracks_mode_fingerprint_and_model` → digest
  больше не зависит от `model_dir` (переименовать, убрать
  `changed_model != base`; добавить `== base` для другой модели —
  это же регрессия-тест инварианта «модель не трогает расписание»).

### Новые

- `parse_args`: `--since-match` принимается как target; `--name` обязателен;
  `--limit/--shard/--resume/--merge-shards` допустимы; mutual exclusion
  с `--match-id`/`--validation` через argparse.
- `selection`: `select_dota_since_match_ids` — якорь по
  `CatalogEntry.start_time` (не по файловому mtime): фикстура с двумя
  матчами, якорь = поздний → когорта = суффикс; фильтры
  catalog/signal/book-gap/telonex; якорь вне каталога → `ValueError`;
  сортировка `(start_time, match_id)`.
- `lol_inputs`: `select_lol_since_match_ids` — horn-якорь, eligible+whitelist
  когорта, сортировка по `(horn, match_id)`, якорь без signal_rows →
  `ValueError`.
- `feed_schedules`: `reject_schedule_flags` отклоняет
  `validation_dataset` на schedule-когорте и пропускает на grid_v1-only.
- `run` (manifest): `signal_source=="auto"`, `backtest_policy=="current"`,
  нет `archive_policy_sha`/`archive_model_sha256`; `archive_exclusions`
  сериализуется `{mid: reason}`; `schedule_map_sha256` + rules versions
  всегда присутствуют; `model_sha256_by_dir` покрывает оба каталога на
  смешанной когорте; `since_match` эмитится только при флаге;
  `game_features_sha256` для dota при schedule-планах.
- `results`: `signal_provenance` отдаёт `feed_source` по типу плана.
- `postprocess`/`report`: `signal_groups`/`model_groups` в arm; terminal
  текст содержит `SIGNALS` и `excluded N (reason …)`.
- `load_run_selection` since-match: когорта пробрасывается в
  `selected_ids`, исключения снимаются через `_apply_feed_exclusions`.

## H. Порядок реализации

1. `results.py` (provenance + `feed_source`) и `live_archives.py` slim-down.
2. `selection.py` / `lol_inputs.py` — since-селекторы.
3. `run.py`: parser → `load_run_selection` → `main` rewiring → kernels /
   policies / signals / latency / manifest-call.
4. `feed_schedules.py`: `reject_schedule_flags` + `schedule_map_sha256`.
5. `telonex_local.py`: strip-fallback удаление.
6. `report_types.py` / `postprocess.py` / `report.py`: группы + SIGNALS.
7. `compare_backtests.py`, `Makefile` help.
8. Тесты по списку.
9. `ruff` + `basedpyright`.

## I. Проверка

```bash
cd ../esports-trader
uv run pytest tests/test_backtest.py tests/test_feed_schedules.py \
  tests/test_backtest_signals.py tests/test_backtest_maker.py \
  tests/test_backtest_validation.py tests/test_live_archives.py \
  tests/test_extraction_oracle.py -x -q
uv run pytest tests/test_lol_backtest.py tests/test_lol_leagues.py \
  tests/test_archive_index.py tests/test_match_catalog.py \
  tests/test_backtest_postprocess.py tests/test_core_trace.py -x -q
uv run ruff check src/ tests/
uv run python -m basedpyright            # whole-project, как в pre-commit
```

- Полный suite не гонять во время live-карты.
- `python -m backtest.run --help`: `--since-match` есть, `--policy` и
  `--live-since` нет.
- `make parity` не проверять запуском (нужны реальные архивы); проверить,
  что рецепт и импорты `replay_core_trace` не зависят от удаляемых
  символов — это статический факт.
- `make prepare`/`train`/`backtest` не запускать. Если бэктест запускается
  позже — two-column terminal report, никакого dump `summary.json`.

## J. Инварианты cache/resume (проверить при реализации)

- `compute_fingerprint` (`archive_index/schedule.py`) не читает dataset/
  model-полей — пересборка датасета или модели не меняет ни per-schedule
  fingerprint, ни файлы `data/archive_index/schedules/`.
- `schedule_map_sha256` не содержит model_dir/dataset — дайджест расписаний
  стабилен при `--model-dir` и пересборке датасета; resume отказывает через
  `model_sha256_by_dir` / `validation_dataset_sha256` /
  `game_features_sha256`, а не через schedule-ключ.
- Смена расписания (новый fingerprint в index) меняет
  `schedule_map_sha256` → resume отказ.
- `index.py` re-extracts по `schedule_rules_version`/fingerprint —
  `make archive-index` инвалидирует кэш только по feed/rules-изменениям.

## K. Явные упрощения (зафиксировать)

- `--since-match` принимает `match_id`, не archive dirname: стабильный
  ключ; архивную директорию привязать к матчу можно через
  `index.parquet` при необходимости.
- Якорь — только точка отсчёта: не обязан быть eligible/admitted.
- Report dir для since-когорты остаётся `validation_<policy>_<name>/seedN`
  (`build_report_dir` keyed on `match_id is None`) — имя несёт `--name`.
- `cancel_latency_ns` всегда реальный: live-архивный `cancel=0` умирал
  вместе с implicit-режимом; schedule-replay — новые решения, latency
  уместен (auto-режим 3A уже так работал).
- `reject_schedule_flags` расширен на `--validation-dataset`: schedule-
  матчи читают canonical `game_features.parquet`, кастомный parquet
  применился бы только к synthetic-подмножеству — молчаливая полу-эксперимент.
  На grid_v1-only когорте флаг работает.
- `archive_exclusions` живёт только в manifest (и через него в
  summary.json); per-match причины в results.parquet не дублируются —
  исключённые матчи там отсутствуют по построению.
- `signal_groups`/`model_groups` — счёт по всем result-строкам (terminated
  включительно): это состав когорты, не PnL-метрика.
- `model_name`/`model_path`/`model_sha256` в манифесте — primary dir;
  смешанная когорта видна по `model_sha256_by_dir` и колонкам parquet.

## L. Запрещено этим планом

- `poly-maker`, `polymarket-collector`, on-chain скрипты — не трогаем.
- `betting_workspace/current-task/` — не используем.
- Не читать `SignalUpdate`/feature-значения/политики из core_trace в
  обычном пути — `make parity` единственный потребитель ленты.
- Не подменять excluded-архив синтетическим cadence; не вводить fallback.
- Не менять whitelist LoL, train-код, lag, `LOL_EXECUTION_PRIORITY`.
- Не трогать `src/archive_index/` правила и fingerprint-функции.
- Не запускать prepare/train/backtest/full-suite-во-время-live-карты.
