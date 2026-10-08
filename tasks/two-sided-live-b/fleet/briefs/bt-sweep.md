# Brief: bt-sweep — two-sided param backtests (owner request, runs AFTER STEP-010)

Run after every feature.json step passes and STEP-010 is committed. Code = E main HEAD at that time.
Backtests are allowed for this brief (that overrides the "no backtests" default). Commits: none in code; results dirs stay on disk.

## Owner's request (verbatim, Russian)

Общее для обоих прогонов.
- Берём те же 307 архивных карт Dota (--validation --archives-only) и стратегию two-sided с настройками пилота: 3 тика от середины, 20 акций, сдвиг цены g0 2e-4, merge с 20 пар.
- Каждый прогон идёт в двух режимах очереди. Строгая: мы стоим за всеми, кто уже стоит на нашей цене. Свободная: перед нами никого. Правда где-то между ними.
- 5 шардов на каждый режим. Прогоны идут один за другим, ~20 минут каждый, всего ~40 минут.
- Сравниваем с n50 (ts-full-n50 / ts-full-n50-nq). Меняется ровно одна вещь.

Прогон 1 — лимит перекоса 30 (--net-max-shares 30, папки ts-full-n30, ts-full-n30-nq)
- Отличие от n50: ручка 30 вместо 50.
- Реальная стена — 22.5 акции, максимум без пары — 27.5 акции. У n50 было 37.5 и 42.5.
- Вопрос: худшая карта и p5 снова уменьшатся, а прибыль останется прежней? Так было при переходе 100 → 50.
- Возможный минус: бот реже покупает перегруженную сторону. Пар становится меньше, прибыль может упасть.

Прогон 2 — сдвиг цены вдвое сильнее при лимите 50 (--net-max-shares 50 --skew-per-share 0.0004, папки ts-full-n50-g4e-4, ts-full-n50-g4e-4-nq)
- Отличие от n50: при перекосе 50 биды сдвигаются на 2¢, а не на 1¢.
- Вопрос: пары закрываются быстрее, и хвост без пары становится меньше?
- Возможный минус: каждая пара, закрытая сдвигом, даёт на 1¢ меньше.

Что смотрим и как решаем
- Метрики: PnL, худшая карта, p5, убыток хвоста (leftover) и доход пар.
- Изменение берём, только если оно идёт в одну сторону на обеих очередях.
- Разница меньше ~$400 на 307 картах (парный t < 1.5) — это шум.
- Цель для пилота та же, что у n50: меньше худшая карта без потери прибыли.

Прогон 3 — только если ОБА прогона 1 и 2 выиграли по правилу выше: n30 + g4e-4 вместе
(--net-max-shares 30 --skew-per-share 0.0004, папки ts-full-n30-g4e-4, ts-full-n30-g4e-4-nq). Иначе не запускать.

## How

1. Find the exact ts-full-n50 / ts-full-n50-nq commands (W/reports/2026-10-07-two-sided-merge/RUNBOOK-wave2.md, the n50 commit `01d33aca` in E, run dirs under E/data/backtests/dota_maker/validation_join_delta02_x015_cut480_p45_ts-full-n50*). Copy every flag; change only the one knob. Check the run config/manifest of n50 vs the new run to prove exactly one difference.
2. Run sequentially (one mode at a time, 5 shards), from E main. No worktree.
3. Compare each run to n50 per queue mode, paired by map: total PnL (with rebate), worst map, p5, leftover settlement loss, merge/pair income, paired t on per-map PnL diff. Write the comparison script to work/bt-sweep/ and the numbers table in the report.
4. Verdict per run with the owner's rule. Then run 3 only if both win.

Report `R/reports/bt-sweep.md`: commands, run dirs, one-difference proof, tables (strict and free queue), verdicts, the decision on run 3.
