# Codex conversation

## User

/Users/dimabytes/Desktop/\#\ Codex\ conversation.txt Подготовь план по тому, как я смотрю, у меня есть давно собранные матчи по Dota 2. Но они как-бы давно собраны. И потом я их не продолжал собирать. И как-бы я продолжаю сейчас собирать только эти матчи, которые у меня на Polymarket. И, короче, что я по ним, только там сматченный. Ну, короче, ты понял.

Этот мой pipeline collect. У меня там, конечно, что-то в кэше есть, но это не все. Не то, что не все. Я это не собираю там последний месяц уже или полтора.

Не обновляю эти вещи. Нам нужно, по идее, сделать как? Нам нужно просто обучить еще одну дополнительную модель. Точнее, ансамбль, да? Мы тоже будем делать ансамбль же. Или только одну. Не понимаю. Скорее всего, ансамбль. Значит, получается, мы можем... Тут видишь вопрос. Я так понимаю, у нас, нам, чтобы же обучить, нам нужно и данные из Strata, и данные из OpenDota. И правильно? Или нет? Или нам хватит только того, что у нас есть в Strata?

Не знаю. Нужно подумать. То есть, мы берем все матчи. И главное, чтобы у них последние... То есть, они закончились на моменте, когда у нас начинается валидация.

Да? При этом этим матчам тоже самим нужна валидация этой модели. Или нет? Или ей не нужна валидация? Наверное, нужна валидация. Валидация нужна всем, чтобы остановить деревья. Поэтому у них должна быть своя точка остановки валидации чуть пораньше, чем моя текущая.

И тогда мы обучим эту отдельную модель. У них будет своя валидация. Но эта их своя валидация должна заканчиваться там, где начинается моя текущая валидация. Чтобы они, не дай бог, не увидели мою актуальную валидацию.

И получается... Тут еще важно... То есть, как их взять. То есть, по идее, мы просто можем взять и посмотреть все матчи, на которых будет потом учиться моя обычная модель. И как-бы взять общий весь список всего, что у меня сейчас лежит в кэше. Там, да, всего того, что есть.

И исключить те, которые у меня идут в мою обычную модель. И получится такой список. Его тоже как-то сохранить и обучить. Так. Только нужно какое-то это делать.

Знаешь, я думаю, в PAUМ-де сделаю какую-то папку с экспериментом. В папке эксперимента это все делать. То есть, там должен быть скрипт, который, получается, у меня подготавливает какой-то сначала датасет. Получается отдельный под ту модель.

Тоже разделенный там отдельно. У него train, validation. Так. Про production пока мы не говорим. Просто пока train, validation разделенный.

Делает такой датасет. Потом запускает train. Тоже обучает. И потом как-то в нашу основную модель, в наш основной... Основной наш train, который в SRC лежит в папке. Там добавить какую-то вещь, чтобы можно было эту... Ну, чтобы эта штука заработала.

И в бэктесте, соответственно, тоже. Чтобы она ее вызывала. Из папки эксперимента, получается. Там у нас даже...

И все. Именно часть, которую пишем в SRC, можно писать без флагов. Просто писать, чтобы это всегда было включено. Потом, если что, просто мой код откатываем.

И не паримся. Идея такая у меня.

## Assistant

Прочитаю сохранённый разговор и устройство collect/train/backtest. Затем составлю план эксперимента: какие данные доступны, как разделить их без утечки и как подключить дополнительную модель. Использую skill brainstorming для проработки этого изменения.

## Activity

    $ cat '/Users/dimabytes/.agents/skills/brainstorming/SKILL.md'
    ---
    name: brainstorming
    description: "You MUST use this before any creative work - creating features, building components, adding functionality, or modifying behavior. Explores user intent, requirements and design before implementation."
    disable-model-invocation: true
    ---
    
    # Brainstorming Ideas Into Designs
    
    Help turn ideas into fully formed designs and specs through natural collaborative dialogue.
    
    Start by classifying how much process the request needs, then work
    through your path: understand the context, refine the idea, present a
    design, and get your human partner's approval.
    
    <HARD-GATE>
    Do NOT invoke any implementation skill, write any code, scaffold any
    project, or take any implementation action until you have told your
    human partner what you intend and they have approved it. This applies
    to EVERY task on EVERY path below — the ceremony scales with the task;
    the approval gate never does.
    </HARD-GATE>
    
    ## Three Paths
    
    Before your first question, classify the request and say the
    classification out loud — "this looks bounded, so I'll present a short
    design here rather than write a spec" — so your human partner can
    override it:
    
    - **Spike** — a feasibility question ("can we...", "is it possible...",
      "quick and dirty is fine") whose output is an answer, not code you
      keep. Present the question and what you'll try in 2-3 sentences, get
      a nod, then find out as cheaply as correctness allows. No design
      doc, no spec file. Report findings as a recommendation; anything you
      built stays labeled throwaway.
    - **Bounded** — a well-scoped change to code that already exists in
      this repo: a new flag, a small endpoint, a one-file fix.
      Understanding the kind of app is not enough — bounded means the flow
      you are changing is already here to read. If there is no existing
      flow to change, the task is not bounded. Ask the clarifying
      questions that matter, present a short design IN CHAT (a few
      sentences to a few short paragraphs), and STOP. Implementation
      starts only after your human partner says yes to that design — a
      bounded task's approval is as hard a gate as an architectural
      one. No spec file, no implementation plan document.
    - **Architectural** — new projects, new subsystems, changes that
      restructure how components fit together or alter interfaces others
      depend on. Follow the full process: questions, approaches, sectioned
      design, written spec.
    
    When in doubt between two paths, take the heavier one. The ratchet is
    one-way: hidden complexity discovered mid-task upgrades the path —
    stop, say so, and step up. Nothing downgrades mid-task.
    
    ## Anti-Pattern: "Too Simple To Need Approval"
    
    Every path ends with your human partner approving your intent before
    implementation. A todo list, a single-function utility, a config
    change — the design may be two sentences in chat, but you MUST present
    it and get approval. "Simple" tasks are where unexamined assumptions
    cause the most wasted work. What scales with simplicity is the
    artifact, never the approval.
    
    ## Red Flags
    
    | Thought | Reality |
    |---------|---------|
    | "This is too simple to need a design" | Simple means a short design, not no design. Two sentences in chat, then approval. |
    | "I'll call it bounded and skip the spec" | Reaching for a label to skip work IS the doubt — take the heavier path. |
    | "It's bounded and the design is obvious — I'll start while they read it" | The gate is the approval, not the design's length. Present, then stop until you hear yes. |
    | "I understand this kind of app, so it's bounded" | Bounded measures the repo, not your familiarity. A new project has no existing flow — it is architectural. |
    | "The spike works, so I'll keep the code" | A spike's output is an answer. Keeping the code is a new request — classify it. |
    | "It grew, but I'm almost done — no need to re-classify" | Hidden complexity upgrades the path mid-task. Stop and say so. |
    | "They approved the spike, so the follow-up change is approved too" | Each task gets its own classification and its own approval. |
    
    ## Checklist
    
    Classify first, announce the path, then create a task for each item on
    your path and complete them in order.
    
    **Spike:**
    1. **Explore project context** — enough to frame the probe
    2. **Present question + probe plan** — 2-3 sentences
    3. **Get approval** — a nod is enough
    4. **Investigate** — as cheaply as correctness allows
    5. **Report findings** — a recommendation; label anything built as throwaway
    
    **Bounded:**
    1. **Explore project context** — check files, docs, recent commits
    2. **Ask clarifying questions** — one at a time, the ones that matter
    3. **Present short design in chat** — approach, files touched, testing
    4. **Get approval** — STOP and wait for an explicit yes; presenting the design and starting in the same breath is skipping the gate
    5. **Implement** — proceed with the normal development workflow (TDD applies); no plan document
    
    **Architectural:**
    1. **Explore project context** — check files, docs, recent commits
    2. **Offer the visual companion just-in-time** — NOT upfront. The first time a question would genuinely be clearer shown than described, offer it then (its own message); on approval its browser tab opens for you. If no visual question ever arises, never offer it. See the Visual Companion section below.
    3. **Ask clarifying questions** — one at a time, understand purpose/constraints/success criteria
    4. **Propose 2-3 approaches** — with trade-offs and your recommendation
    5. **Present design** — in sections scaled to their complexity, get user approval after each section
    6. **Write design doc** — save to `docs/superpowers/specs/YYYY-MM-DD-<topic>-design.md` and commit
    7. **Spec self-review** — quick inline check for placeholders, contradictions, ambiguity, scope (see below)
    8. **User reviews written spec** — ask user to review the spec file before proceeding
    9. **Transition to implementation** — create implementation plan
    
    ## Process Flow
    
    ```dot
    digraph brainstorming {
        "Classify: spike / bounded / architectural" [shape=diamond];
        "Present question + probe (2-3 sentences)" [shape=box];
        "Ask clarifying questions (bounded)" [shape=box];
        "Present short design in chat" [shape=box];
        "Human approves?" [shape=diamond];
        "Investigate; report recommendation" [shape=doublecircle];
        "Implement via normal workflow (no plan doc)" [shape=doublecircle];
        "Explore project context" [shape=box];
        "Ask clarifying questions" [shape=box];
        "Propose 2-3 approaches" [shape=box];
        "Present design sections" [shape=box];
        "User approves design?" [shape=diamond];
        "Write design doc" [shape=box];
        "Spec self-review\n(fix inline)" [shape=box];
        "User reviews spec?" [shape=diamond];
        "Hidden complexity? Upgrade path" [shape=box];
    
        "Classify: spike / bounded / architectural" -> "Present question + probe (2-3 sentences)" [label="spike"];
        "Classify: spike / bounded / architectural" -> "Ask clarifying questions (bounded)" [label="bounded"];
        "Classify: spike / bounded / architectural" -> "Explore project context" [label="architectural"];
        "Present question + probe (2-3 sentences)" -> "Human approves?";
        "Ask clarifying questions (bounded)" -> "Present short design in chat";
        "Present short design in chat" -> "Human approves?";
        "Human approves?" -> "Investigate; report recommendation" [label="spike: yes"];
        "Human approves?" -> "Implement via normal workflow (no plan doc)" [label="bounded: yes"];
        "Hidden complexity? Upgrade path" -> "Classify: spike / bounded / architectural";
        "Explore project context" -> "Ask clarifying questions";
        "Ask clarifying questions" -> "Propose 2-3 approaches";
        "Propose 2-3 approaches" -> "Present design sections";
        "Present design sections" -> "User approves design?";
        "User approves design?" -> "Present design sections" [label="no, revise"];
        "User approves design?" -> "Write design doc" [label="yes"];
        "Write design doc" -> "Spec self-review\n(fix inline)";
        "Spec self-review\n(fix inline)" -> "User reviews spec?";
        "User reviews spec?" -> "Write design doc" [label="changes requested"];
    }
    ```
    
    ## The Process
    
    The subsections below serve the bounded and architectural paths (a
    spike stops at "present the probe, get a nod"). Sections from
    **Exploring approaches** onward are architectural-path depth — for
    bounded work, context plus a few questions plus a short in-chat design
    is the whole process.
    
    **Understanding the idea:**
    
    - Check out the current project state first (files, docs, recent commits)
    - Before asking detailed questions, assess scope: if the request describes multiple independent subsystems (e.g., "build a platform with chat, file storage, billing, and analytics"), flag this immediately. Don't spend questions refining details of a project that needs to be decomposed first.
    - If the project is too large for a single spec, help the user decompose into sub-projects: what are the independent pieces, how do they relate, what order should they be built? Then brainstorm the first sub-project through the normal design flow. Each sub-project gets its own spec → plan → implementation cycle.
    - For appropriately-scoped projects, ask questions one at a time to refine the idea
    - Prefer multiple choice questions when possible, but open-ended is fine too
    - Only one question per message - if a topic needs more exploration, break it into multiple questions
    - Focus on understanding: purpose, constraints, success criteria
    
    **Exploring approaches:**
    
    - Propose 2-3 different approaches with trade-offs
    - Present options conversationally with your recommendation and reasoning
    - Lead with your recommended option and explain why
    - YAGNI ruthlessly - remove unnecessary features from every approach and design
    
    **Presenting the design:**
    
    - Once you believe you understand what you're building, present the design
    - Scale each section to its complexity: a few sentences if straightforward, up to 200-300 words if nuanced
    - Ask after each section whether it looks right so far
    - Cover: architecture, components, data flow, error handling, testing
    - Be ready to go back and clarify if something doesn't make sense
    
    **Design for isolation and clarity:**
    
    - Break the system into smaller units that each have one clear purpose, communicate through well-defined interfaces, and can be understood and tested independently
    - For each unit, you should be able to answer: what does it do, how do you use it, and what does it depend on?
    - Can someone understand what a unit does without reading its internals? Can you change the internals without breaking consumers? If not, the boundaries need work.
    - Smaller, well-bounded units are also easier for you to work with - you reason better about code you can hold in context at once, and your edits are more reliable when files are focused. When a file grows large, that's often a signal that it's doing too much.
    
    **Working in existing codebases:**
    
    - Explore the current structure before proposing changes. Follow existing patterns.
    - Where existing code has problems that affect the work (e.g., a file that's grown too large, unclear boundaries, tangled responsibilities), include targeted improvements as part of the design - the way a good developer improves code they're working in.
    - Don't propose unrelated refactoring. Stay focused on what serves the current goal.
    
    ## After the Design (architectural path)
    
    **Documentation:**
    
    - Write the validated design (spec) to `docs/superpowers/specs/YYYY-MM-DD-<topic>-design.md`
      - (User preferences for spec location override this default)
    - Use elements-of-style:writing-clearly-and-concisely skill if available
    - Commit the design document to git
    
    **Spec Self-Review:**
    After writing the spec document, look at it with fresh eyes:
    
    1. **Placeholder scan:** Any "TBD", "TODO", incomplete sections, or vague requirements? Fix them.
    2. **Internal consistency:** Do any sections contradict each other? Does the architecture match the feature descriptions?
    3. **Scope check:** Is this focused enough for a single implementation plan, or does it need decomposition?
    4. **Ambiguity check:** Could any requirement be interpreted two different ways? If so, pick one and make it explicit.
    
    Fix any issues inline. No need to re-review — just fix and move on.
    
    **User Review Gate:**
    After the spec review loop passes, ask the user to review the written spec before proceeding:
    
    > "Spec written and committed to `<path>`. Please review it and let me know if you want to make any changes before we start writing out the implementation plan."
    
    Wait for the user's response. If they request changes, make them and re-run the spec review loop. Only proceed once the user approves.
    
    **Implementation:**
    
    - Create a detailed implementation plan
    
    ## Visual Companion
    
    A browser-based companion for showing mockups, diagrams, and visual options during brainstorming. Available as a tool — not a mode. Accepting the companion means it's available for questions that benefit from visual treatment; it does NOT mean every question goes through the browser.
    
    **Offering the companion (just-in-time):** Do NOT offer it upfront. Wait until a question would genuinely be clearer shown than told — a real mockup / layout / diagram question, not merely a UI *topic*. The first time that happens, offer it then, as its own message:
    > "This next part might be easier if I show you — I can put together mockups, diagrams, and comparisons in a browser tab as we go. It's still new and can be token-intensive. Want me to? I'll open it for you."
    
    **This offer MUST be its own message.** Only the offer — no clarifying question, summary, or other content. Wait for the user's response. If they accept, start the server with `--open` so their browser opens to the first screen automatically. If they decline, continue text-only and don't offer again unless they raise it.
    
    **Per-question decision:** Even after the user accepts, decide FOR EACH QUESTION whether to use the browser or the terminal. The test: **would the user understand this better by seeing it than reading it?**
    
    - **Use the browser** for content that IS visual — mockups, wireframes, layout comparisons, architecture diagrams, side-by-side visual designs
    - **Use the terminal** for content that is text — requirements questions, conceptual choices, tradeoff lists, A/B/C/D text options, scope decisions
    
    A question about a UI topic is not automatically a visual question. "What does personality mean in this context?" is a conceptual question — use the terminal. "Which wizard layout works better?" is a visual question — use the browser.
    
    If they agree to the companion, read the detailed guide before proceeding:
    `skills/brainstorming/visual-companion.md`
    ✓ • 0ms

## Activity

    $ cat '../esports-trader/AGENTS.md'
    # esports-trader
    
    Этот проект содержит полный пайплайн трейдинга на polymarket на рынках dota2 и lol.
    
    Он разделен на разные стадии:
    
    - `src/collect` — Сбор данных для Dota2.
    - `src/prepare_dataset` - Подготовка данных длямодели и backtest Dota2
    - `src/lol` — Сбор данных, Подготовка данных для модели и backtest для Lol
    - `src/backtest` — Бэктест
    - `src/trader`, `config/trading.toml` — Торговля на реальном рынке
    - `src/shared` — paths, parsers, types. Grep here before writing a new reader.
    
    Что за модель:
    predicts the Polymarket midpoint 300 seconds ahead during the first 9 minutes of a match.
    
    Run anything through `uv run python <script>` (or `make run F=<script>`)
    
    Do not run the suite during a live map. `/tmp` is tmpfs; conftest relocates pytest tmp to `/var/tmp/pytest-esports-trader`.
    
    ## Agent rules
    
    - Prefer named intermediate variables over nested one-liners; keep steps readable.
    - Work on `main` by default, unless otherwise specified.
    - Function names should be action verbs.
    - No `dict[str, Any]` for our own data.
    - No anonymous `tuple[A, B]` (or longer) for our own multi-field values: use a frozen dataclass with named fields.
    - No branches for inputs that cannot occur today. Delete the dead one.
    - Make all function arguments required. Of course there are exceptions to this rule (but like 1%).
    - Don't add comments that narrate the code.
    ✓ • 0ms

## Activity

    $ cat '/Users/dimabytes/Desktop/# Codex conversation.txt'
    # Codex conversation
    
    ## User
    
    Users/dimabytes/.cursor/plans/Look-ahead validation filter-d981831f.plan.md Теперь у меня такой план. Мне он сейчас не нравится немного, потому что раньше было просто. У нас было train, validation, в production шло все, что находится в train и в validation. Для early stopping использовался validation, и этот validation использовался в бэктесте.
    
    Все просто. Ну, проверить, что раньше так и было. Этот план предлагает немножечко все изменить, и как бы у нас получится, что у нас в train... Окей, одни карты, там один будет фильтр, хорошо. В validation этот фильтр не применяется, ладно.
    
    Но потом дальше в production должны пойти какие-то, которых не было в validation. Early stopping тоже должны пойти, которых не было в validation. Короче, какая-то хуета, мне не нравится. Какие есть варианты, как там это по-правильному сделать, по-простому? Я не понимаю.
    
    ## Assistant
    
    Прочитаю план и текущую реализацию разбиения train/validation, early stopping и бэктеста. Потом покажу, где план меняет прежнюю схему и какой самый простой вариант сохраняет её смысл.
    
    ## Activity
    
        $ cat /Users/dimabytes/.cursor/plans/Look-ahead\ validation\ filter-d981831f.plan.md
        <!-- d981831f-636d-4bec-bd3c-2323e76d7ae4 -->
        ---
        todos:
          - id: "dota-select"
            content: "Dota: фильтр ленты только на train; validation без него; production_validation для минутных строк"
            status: pending
          - id: "split-flag"
            content: "tape_dense на Dota SplitRow и LoL audit/split; сплит выкинутых LoL-карт по старту события"
            status: pending
          - id: "lol-eligibility"
            content: "LoL: убрать проверку ленты из validation, не класть тонкие карты в production"
            status: pending
          - id: "holdout"
            content: "Early stopping обоих игр только по tape_dense; метрики по полной валидации"
            status: pending
          - id: "tests"
            content: "Тесты сплита, утечки в production и holdout"
            status: pending
          - id: "rebuild"
            content: "Снимок match-id, make market-data / prepare / lol-prepare, сверить train и production; модели не переобучать"
            status: pending
        isProject: false
        ---
        # Look-ahead: валидация без фильтра, production без утечки
    
        Фильтр — `tape_passes_buckets`: в каждом полном 5-минутном окне карты минимум 10 принтов (`MIN_TRADES_PER_BUCKET`). Сейчас он режет каталог до разреза на train/validation. Из-за этого в проверку не попадают 113 карт Dota (из них ~100 уже с market cache) и 389 карт LoL.
    
        Бэктест читает уже опубликованную модель и строки валидации. Переобучение в этот шаг не входит. Паритет лайв/реплей, спред-гейт и тонкий гейт из §4.3–4.5 не делаем.
    
        ## Почему нельзя просто удалить проверку
    
        Оба пайплайна кладут строки периода валидации в production-обучение:
    
        - Dota: [`write_datasets`](../esports-trader/src/prepare_dataset/prepare_dataset.py) делает `production_rows = [*training_rows, *validation_minute_rows]` (строка 280). Минутные строки строятся для каждой карты из `split.validation` (строки 336–343).
        - LoL: [`partition_rows`](../esports-trader/src/lol/05_prepare_dataset.py) кладёт размеченные строки всех принятых карт, включая validation, в `production` (строки 645–663).
    
        Research-модель делает early stopping на всём `validation.parquet` ([`fit_research_members`](../esports-trader/src/shared/utils/gbm.py), строка 317). Число деревьев копируется в production (`boost_rounds` в [`train_model.py`](../esports-trader/src/train_model/train_model.py) и [`06_train_model.py`](../esports-trader/src/lol/06_train_model.py)). Если тонкие карты попадут в этот holdout, состав `production_training.parquet` можно сохранить, а модель всё равно сдвинется.
    
        ```mermaid
        flowchart LR
          catalog[Каталог]
          train[Train: фильтр остаётся]
          valid[Validation и бэктест: фильтр снят]
          prod[production_training: фильтр остаётся]
          holdout[Early stopping: только плотные карты]
          catalog --> train
          catalog --> valid
          train --> prod
          valid --> prod
          train --> holdout
          valid --> holdout
        ```
    
        ## Dota
    
        В [`select_matches`](../esports-trader/src/prepare_dataset/prepare_dataset.py) сначала резать каталог по `VALIDATION_START_TIME`, потом применять `match_passes_tape_buckets` только к `start_time < cutoff`.
    
        Валидация: все карты после cutoff с `playback_available` (как сейчас, `SystemExit` если playback нет) и с market cache (лимит `MAX_VALIDATION_HARD_MISSES = 20`). Фильтр ленты их не выкидывает.
    
        В `MatchIdSplit` добавить `production_validation`: подмножество валидации, которое ленту проходит. В `main` минутные строки для production строить только по нему. `validation_rows`, `game_features` и research split получают тонкие карты.
    
        На [`SplitRow`](../esports-trader/src/shared/types/dataset.py) добавить `tape_dense: bool`. У train и у плотной валидации `True`, у тонкой валидации `False`. Бэктест по-прежнему берёт все строки `split == validation`, кроме `backtest_book_gap_excluded`. Лишняя колонка ему не мешает: [`selection.py`](../esports-trader/src/backtest/selection.py) проверяет только флаг дыры в книге.
    
        ## LoL
    
        В [`eligibility_drop_reason`](../esports-trader/src/lol/05_prepare_dataset.py) оставить `REASON_THIN_TELONEX_TAPE` только в ветке `train`. В ветке validation эту проверку убрать. Остальные причины (`audit["reason"]`) не трогать.
    
        Ленту считать один раз на карту и использовать трижды:
    
        - дроп train;
        - не класть тонкую validation в `production` внутри `partition_rows`;
        - записать `tape_dense` в полный аудит ([`LolPrepareAuditRow`](../esports-trader/src/lol/types.py)) и в [`LolPrepareSplitRow`](../esports-trader/src/lol/types.py).
    
        `audit_split_label` сейчас ставит `none` любой выкинутой карте, даже если старт события известен. Если `start_time` есть, писать `train` или `validation` по старту события. `none` остаётся только без старта. Тогда тонкий train виден как `split=train`, `reason=thin_telonex_tape`, а не как карта без сплита.
    
        `backtest_audit` не расширять: бэктест и так возьмёт новые карты по `eligible`. Поле в кэшируемый `MapBuild` не добавлять.
    
        Первый `make lol-prepare` после правки промахнётся мимо кэша карт. [`cache_version`](../esports-trader/src/lol/map_build_cache.py) хеширует импортированные `src/**/*.py`, так что правка `05_prepare_dataset.py` меняет stamp. Замечание в отчёте, что инвалидации не будет, неверно.
    
        ## Early stopping
    
        Одна функция в `src/shared/utils/`: holdout research — строки валидации, чей `match_id` в split с `tape_dense`. Нет колонки — ошибка, а не тихий обучение на тонких картах.
    
        Её вызывают оба `train_research` до `fit_research_members`. Метрики research считаются по полному `validation.parquet`, включая тонкие карты. В этот шаг `make train` / `make lol-train` не запускать: holdout и строки production остаются прежними, опубликованные модели не публиковать заново.
    
        ## Тесты
    
        - [`tests/test_prepare_dataset.py`](../esports-trader/tests/test_prepare_dataset.py): до cutoff тонкая карта не попадает в train; после cutoff остаётся в validation и не попадает в `production_validation`. Отдельный тест на `main`: тонкая validation есть в `validation_rows` и её нет в `validation_minute_rows`. Стабы загрузчиков, `write_datasets` перехватить, канонические parquet не писать.
        - [`tests/test_lol_prepare_dataset.py`](../esports-trader/tests/test_lol_prepare_dataset.py): `test_thin_telonex_tape_drops_map` (spawn до cutoff) по-прежнему дропает train. Новый кейс со spawn после `LOL_VALIDATION_START_TIME`: карта в `validation.parquet` и в split, `tape_dense` ложь, в `production_training.parquet` её нет.
        - Хелпер `split_row` в [`tests/test_lol_train_model.py`](../esports-trader/tests/test_lol_train_model.py) и сравнения `MatchIdSplit` дописать под новое поле. Плотные фикстуры — `tape_dense=True`.
        - Один тест holdout: две карты, у одной `tape_dense` ложь, в fit-кадр попадает одна.
    
        ## Данные после тестов
    
        Снять множество `match_id` из текущего `training_dataset.parquet` и `production/.../training_dataset.parquet` (и LoL `training.parquet` / `production_training.parquet`).
    
        Потом:
    
        - `make market-data` — до 13 карт Dota без кэша; лимит пропусков 20.
        - `make prepare`
        - `make lol-prepare` — холодный кэш карт.
    
        Проверка: train и production match-id совпали со снимком. Validation выросла примерно на 100 карт Dota с кэшем (до 113, если достроятся 13 кэшей) и до 389 карт LoL. Полный бэктест этой вселенной — следующий запуск, не часть этого диффа.
        ✓ • 0ms
    
    ## Activity
    
        $ cat ../esports-trader/AGENTS.md
        # esports-trader
    
        Этот проект содержит полный пайплайн трейдинга на polymarket на рынках dota2 и lol.
    
        Он разделен на разные стадии:
    
        - `src/collect` — Сбор данных для Dota2.
        - `src/prepare_dataset` - Подготовка данных длямодели и backtest Dota2
        - `src/lol` — Сбор данных, Подготовка данных для модели и backtest для Lol
        - `src/backtest` — Бэктест
        - `src/trader`, `config/trading.toml` — Торговля на реальном рынке
        - `src/shared` — paths, parsers, types. Grep here before writing a new reader.
    
        Что за модель:
        predicts the Polymarket midpoint 300 seconds ahead during the first 9 minutes of a match.
    
        Run anything through `uv run python <script>` (or `make run F=<script>`)
    
        Do not run the suite during a live map. `/tmp` is tmpfs; conftest relocates pytest tmp to `/var/tmp/pytest-esports-trader`.
    
        ## Agent rules
    
        - Prefer named intermediate variables over nested one-liners; keep steps readable.
        - Work on `main` by default, unless otherwise specified.
        - Function names should be action verbs.
        - No `dict[str, Any]` for our own data.
        - No anonymous `tuple[A, B]` (or longer) for our own multi-field values: use a frozen dataclass with named fields.
        - No branches for inputs that cannot occur today. Delete the dead one.
        - Make all function arguments required. Of course there are exceptions to this rule (but like 1%).
        - Don't add comments that narrate the code.
        ✓ • 0ms
    
    ## Activity
    
        $ ls -la ../
        total 24
        drwxr-xr-x  10 dimabytes  staff   320 Sep 24 01:20 .
        drwxr-xr-x  20 dimabytes  staff   640 Aug 27 19:24 ..
        -rw-r--r--@  1 dimabytes  staff  6148 Sep  3 23:22 .DS_Store
        drwxr-xr-x@ 15 dimabytes  staff   480 Sep 21 09:50 betting_workspace
        -rw-r--r--@  1 dimabytes  staff   287 Sep  3 23:26 dota_2_bot.code-workspace
        drwxr-xr-x@ 29 dimabytes  staff   928 Sep 22 14:07 esports-trader
        drwxr-xr-x@ 24 dimabytes  staff   768 Sep 24 01:44 esports-trader-wt
        drwxr-xr-x@ 14 dimabytes  staff   448 Aug 13 13:22 poly-maker
        drwxr-xr-x@ 38 dimabytes  staff  1216 Sep 21 02:24 polymarket-collector
        drwxr-xr-x@ 34 dimabytes  staff  1088 Aug  2 11:22 prediction-market-backtesting
        ✓ • 0ms
    
    ## Activity
    
        $ rg -n 'validation|production|early stopping|tape' README.md docs Makefile | head -120
        rg: README.md: No such file or directory (os error 2)
        Makefile:79:market-data:  ## Build exact-second validation market cache
        Makefile:83:prepare: market-data  ## Prepare research, validation, and production datasets
        Makefile:86:train:  ## Train and publish research/production and the no-XP pair
        Makefile:106:backtest:  ## Dota Follow300: ARGS="--match-id N" | "--validation --name <s> …" | "--since-match N --name <s> […]"
        Makefile:127:lol-train:  ## LoL train research + production models
        Makefile:132:lol-backtest:  ## LoL Follow300 backtest, mandatory 17-league whitelist minus no_live_feed, 3x$100 BUY rungs: ARGS="--validation --name <suffix> [--warm-cache] [--limit 2] [--resume] [--shard i/n] [--merge-shards N]" | "--since-match N --name <s> […]"
        docs/extraction-oracle.md:50:| Dota | 8837869969, 8911784562, 8933879286 | `data/raw/telonex/polymarket` | `data/new_processed/dataset/validation_dataset.parquet` SHA256 `de4ce6ccd1abf32165195901e97af074809d74ab45b6b2cb08be8a823b42ea93` |
        docs/extraction-oracle.md:51:| LoL | 115564793879469302 | `data/lol/raw/telonex/polymarket` | validation `3958eb0a0f38ab5ce20e127f3ae1154be4815dafd3b616958c0333a4cc237ee5`; split `f474e3cdcf945e53f2d0b2387344fd9188c16ffe911b9eecd41b35e128ad3de0`; market_seconds `bd2baaee2fe6708dfd76da8a3cca6c6b208f68d67de5d9666bde4e384277b3eb`; audit `ba9293536733bb2691d38978bb55f29e13c7d2e989aa8e104eea7102777da9c9` |
        docs/extraction-oracle.md:86:Nautilus 1.226.0 zero-qty fill → `PositionOpened` FLAT assert. Bulk validation records LoL `116855104460702379` and `116566854547769589` as `nautilus_zero_fill`. They are not extraction-identity maps. `--match-id` still replays them.
        docs/superpowers/specs/2026-09-06-follow300-shared-core-design.md:137:Live: реальные фиды и production-модель, CLOB HTTP/WS, подписи, rate limits,
        docs/superpowers/specs/2026-09-06-follow300-shared-core-design.md:328:Backtest research и live production могут оставаться разными моделями;
        docs/analysis/2026-09-06-follow300-behavior-changes.md:28:Four seed-0 identity comparisons still passed on that SHA against US-001 goldens (`make test`). Reproduction:
        docs/superpowers/specs/2026-09-21-series-context-features-design.md:70:5. **Сплит.** Карта 2 может попасть в validation, а карта 1 — в train.
        docs/superpowers/specs/2026-09-21-series-context-features-design.md:113:  прокинуть в `build_minute_rows` / `join_validation_rows` /
        docs/analysis/loss-patterns-2026-09-05.md:12:| LoL `tape10` | 1292 карты, сиды 0–2 | research `20260904T193237Z` |
        docs/analysis/loss-patterns-2026-09-05.md:13:| Реальный Dota | 34 матча с исполнениями, 64 BUY, 97 SELL | production `20260825T193532Z` |
        docs/analysis/loss-patterns-2026-09-05.md:14:| Реальный LoL | 73 карты с исполнениями, 153 BUY, 243 SELL | production `20260831T120859Z` |
        docs/analysis/loss-patterns-2026-09-05.md:32:Эта валидация уже использовалась для early stopping и предыдущих экспериментов. Найденные на ней правила требуют нового хронологического периода для проверки переноса результата.
        docs/as-is.md:29:Validation cutoff is `VALIDATION_START_TIME = 1780563592` (~2026-06-04). New maps after that go to validation/production, not the research train set.
        docs/as-is.md:37:Thin Telonex trades excluded 1473 train maps and 101 validation maps. Market-data: selected 3040, built 118 new second caches, excluded_train 157, excluded_validation 8 (under the cap of 20).
        docs/as-is.md:50:`--validation` selection, `--game`, `--name`, `--limit`, `--signal-cadence-seed`,
        docs/as-is.md:66:make lol-backtest ARGS="--validation --name wl17-current"
        docs/as-is.md:67:make lol-backtest ARGS="--validation --warm-cache"
        docs/as-is.md:86:make backtest ARGS="--validation --name my-run"
        docs/as-is.md:108:Live dirs: `data/new_model/research`, `data/new_model/production`. Same 12 features, `source_lag_seconds=10`, horizon 300s.
        docs/as-is.md:118:Research holdout (validation window, 289_349 rows / 285_926 with a 300s future):
        docs/as-is.md:128:Train-set SHA for research is unchanged (`41af3f84…`); the extra data is validation/production only. Production has no holdout metrics.
        docs/as-is.md:130:VPS live production is **not** this pair. Host `sun` still has `data/new_model/production` name `20260825T193532Z` (2510 train matches). Local publish does not deploy the trader.
        docs/next_steps.md:203:Полный разбор с примерами и методикой (.learnings/training-tape-exclusions-audit-20260919.md).
        docs/add_model.txt:60:   сравнить  mae_gain_300 / dir_300  в  validation_scenarios.csv . Стоимость проверки — вечер.
        docs/superpowers/specs/2026-09-20-archive-linking-and-feed-replay-design.md:14:2. Допуск Dota: прежний путь OpenDota + GRID **либо** пригодный live-архив. Проверка действует до research train, validation и production training. Заявки и сделки не обязательны.
        docs/superpowers/specs/2026-09-20-archive-linking-and-feed-replay-design.md:107:       → research train / validation / production training
        docs/superpowers/specs/2026-09-20-archive-linking-and-feed-replay-design.md:128:В рамках этой работы не меняются training lag, способ расчёта цели и разделение research/production. Появление новых матчей меняет состав выборки; фактическая задержка Oddin в бэктесте меняет условия применения модели, но не переписывает обучение автоматически.
        docs/superpowers/specs/2026-09-20-archive-linking-and-feed-replay-design.md:138:Текущий Dota validation parquet уже связывает признаки на `second-10` с рынком на `second`. Нельзя взять строку с номером 180 и без проверки считать её состоянием игры на 3:00. Подготовка должна сохранить однозначный ключ игровой секунды для базовых признаков; нынешние train-выборки и цели продолжают строиться с lag=10. Использовать общие builders признаков, чтобы не получить отдельную реализацию их расчёта для бэктеста.
        docs/superpowers/specs/2026-09-20-archive-linking-and-feed-replay-design.md:195:Использовать общий механизм расписания и выбора режима с профилем GRID LoL. Связывать архив с Lolesports-картой по точному condition ID и проверенным map/token данным. Текущий whitelist продолжает ограничивать бэктест, не train/validation. Экспериментальный Oddin watcher LoL не становится новым поддерживаемым production-источником.
        docs/superpowers/specs/2026-09-20-archive-linking-and-feed-replay-design.md:259:**Дублей нет.** Ни для одного из 11 матчей нет самостоятельного GRID/Oddin-архива (ни по Steam ID, ни по condition ID). При этом все 11 имеют независимые исторические связи через обычный collect-пайплайн: строки в `opendota_links`, `stratz_match_index`, `match_catalog`, `pregame_quotes`, `grid_game_windows`. Каталог и market-data от live-архивов не зависят, поэтому пересборка не потребовалась. В `training_dataset`, `validation_dataset`, production-датасете и во всех `split.parquet` моделей этих матчей нет — из кэша они не используются и как «исторические GRID» не вернутся.
        docs/paper_review.md:137:  validation-рынков без программы. Это измерение, а не бэктест: сколько платит программа
        docs/analysis/2026-09-07-follow300-v4-baseline.md:4:Catalogs: `data/backtests/{dota,lol}_maker/validation_join_delta01_cut540_nw350_p35_follow300-v4`.
        docs/experiments/hybrid-prior-v1-rejected.md:43:**Hybrid hunks reverted in production files (kept market-only):**
        docs/analysis/2026-09-15-live-replay-parity.md:247:  `test_backtest_validation` (2, pyarrow на фикстурах),
        docs/experiments/map-first-entry-and-depth-caps.md:70:`data/backtests/dota_maker/validation_join_delta01_cut540_nw350_p35_{cap-fills-6,cap-notional-300,first-px-045,first-px-050,clip-first-px-050,minpx-050}`
        docs/experiments/map-first-entry-and-depth-caps.md:75:`data/backtests/lol_maker/validation_join_delta01_cut540_nw350_p35_first-px-045`.
        docs/paper_notes/2602.06773.md:10:~0 — validation of theory, not a performance study. Guarantees are TRAINING-set only
        docs/experiments/lol-no-unwind.md:14:`data/backtests/lol_maker/validation_s2_delta01_cut540_nw350_p35_no-unwind`
        docs/experiments/lol-no-unwind.md:38:  (`validation_s2_delta01_cut540_nw350_p35_nw350`)
        docs/paper_notes/2607.06166.md:4:validation. Theory: arbitrary price-impact function rho covering both AMM and CLOB. Empirics:
        docs/experiments/sell-through-ask/README.md:5:`data/backtests/dota_maker/validation_join_delta01_cut540_nw350_p35_throughask03`.
        docs/experiments/sell-through-ask/README.md:22:`validation_join_delta01_cut540_nw350_p35_grid-v1-baseline` seed0/1/2.
        docs/experiments/sell-through-ask/README.md:39:An earlier tape pass used `token_index == 0` as Radiant and submit-time
        docs/experiments/sell-through-ask/README.md:73:`data/backtests/dota_maker/validation_join_delta01_cut540_nw350_p35_throughask03`.
        docs/experiments/sell-through-ask/README.md:81:engine). The raise never fired on the validation tape.
        docs/experiments/lol-minute-train.md:1:# LoL minute-boundary train, 1 Hz validation
        docs/experiments/lol-minute-train.md:14:## Offline CSV (`validation_metrics.csv`, seconds 0–540)
        docs/experiments/lol-minute-train.md:16:Same 757922 validation rows / 1513 maps. Minute train dropped 80 maps with
        docs/experiments/dataset-expansion-shelved.md:66:  holdout" v6 default was never coded into production and is dropped.
        docs/experiments/dataset-expansion-shelved.md:71:`99b29e9`), keeping only the 3-fold split standard. A production re-run
        docs/experiments/sell-live-cadence.md:6:`data/backtests/dota_maker/validation_join_delta01_cut540_nw350_p35_sellcadence2s`
        docs/experiments/sell-live-cadence.md:25:0.35, size $100. BUY still reprices on the 1 Hz timer. 454 validation
        docs/experiments/oddin-no-xp.md:13:- Контроль: `data/backtests/dota_maker/validation_join_delta02_cut480_nw350_p35_pgl25-t10/seed0` — research XP `20260915T105750Z`, train lag 10, exec 25.
        docs/experiments/oddin-no-xp.md:15:- Один и тот же validation parquet и selected-ID hash: `data/experiments/pgl-lag-25/validation_dataset.parquet`, 556 карт.
        docs/paper_notes/2510.15612.md:16:Paradigm's pm-AMM tapers liquidity to expiry but doesn't cover jump resolution — note
        docs/experiments/lol-nwoff.md:14:`validation_join_delta01_cut540_nw350_p35_grid-v1-baseline`).
        docs/experiments/lol-nwoff.md:16:`data/backtests/lol_maker/validation_join_delta01_cut540_nw999999_p35_nwoff`.
        docs/experiments/s2-buy-post.md:27:`data/backtests/dota_maker/validation_s2_delta01_cut540_nw350_p35_20260825T190207Z`
        docs/experiments/s2-buy-post.md:49:- `data/backtests/dota_maker/validation_s2_delta01_cut540_nw350_p35_s2-improve`
        docs/experiments/s2-buy-post.md:50:- `data/backtests/dota_maker/validation_s2_delta01_cut540_nw350_p35_buy-tick`
        docs/experiments/s2-buy-post.md:51:- `data/backtests/dota_maker/validation_s2_delta01_cut540_nw350_p35_buy-plus3`
        docs/experiments/s2-buy-post.md:52:- `data/backtests/dota_maker/validation_s2_delta01_cut540_nw350_p35_buy-fair`
        docs/experiments/sell-through-ask/replay_sell_through_ask.py:3:The 1 Hz mid at fill time still shows us as the ask. One second later the tape
        docs/experiments/sell-through-ask/replay_sell_through_ask.py:362:    """Radiant token index from the collect catalog; winner from the validation dataset."""
        docs/paper_notes/skipped_abstracts.md:41:settlement, no real-data validation in the abstract — and the same class of result as
        docs/experiments/score-gap-proper-betting.md:4:validation splits with the live research ensembles.
        docs/experiments/score-gap-proper-betting.md:13:Setup: research ensembles (honest holdout) on the validation parquets.
        docs/experiments/score-gap-proper-betting.md:75:the production ensemble.
        docs/experiments/lag-grid.md:11:stop on validation), and backtest; raw capture does not change.
        docs/experiments/lag-grid.md:25:`data/backtests/dota_maker/validation_s2_delta01_cut540_nw350_p35_20260824T200814Z`
        docs/experiments/lag-grid.md:44:- `data/backtests/dota_maker/validation_s2_delta01_cut540_nw350_p35_t0b10`
        docs/experiments/lag-grid.md:45:- `data/backtests/dota_maker/validation_s2_delta01_cut540_nw350_p35_t0b60`
        docs/experiments/lag-grid.md:46:- `data/backtests/dota_maker/validation_s2_delta01_cut540_nw350_p35_t2b10`
        docs/experiments/lag-grid.md:47:- `data/backtests/dota_maker/validation_s2_delta01_cut540_nw350_p35_t2b60`
        docs/experiments/lag-grid.md:48:- `data/backtests/dota_maker/validation_s2_delta01_cut540_nw350_p35_t10b60`
        docs/experiments/lag-grid.md:54:outliers. mae_gain_300 drops at backtest lag 60 because validation rows join
        docs/experiments/lag-grid.md:63:`data/new_model/archive/research/<name>/`. Live production still carries
        docs/experiments/draft-features-reverted.md:6:- Вердикт: полностью удалить из production pipeline и оставить модель без
        docs/experiments/draft-features-reverted.md:32:Сравнивались три draft-варианта с production baseline без драфта:
        docs/experiments/fair-ema-min-delta.md:21:`validation_join_delta01_cut540_nw350_p35_grid-v1-baseline`.
        docs/experiments/fair-ema-min-delta.md:23:`validation_join_delta01_cut540_nw350_p35_fairema{05,10,20,40}` with
        docs/experiments/pred-sizing-ramp.md:44:`data/backtests/lol_maker/validation_s2_delta01_cut540_nw350_p35_` plus:
        docs/experiments/pred-sizing-ramp.md:154:(3.92¢ → 2.59¢ per dollar). And validation is also the model-selection set
        docs/experiments/equal-capital-clip.md:24:`data/backtests/dota_maker/validation_s2_delta01_cut540_nw350_p35_no-unwind`
        docs/experiments/equal-capital-clip.md:27:`data/backtests/dota_maker/validation_s2_delta01_cut540_nw350_p35_clips10-cd15`
        docs/experiments/equal-capital-clip.md:30:`data/backtests/dota_maker/validation_s2_delta01_cut540_nw350_p35_size1000`
        docs/experiments/lol-cut900.md:5:`data/backtests/lol_maker/validation_join_delta01_cut540_nw350_p35_cut900`.
        docs/experiments/lol-cut900.md:17:Current research catalogs on the validation parquets. Gated DIR is
        docs/experiments/burst-c20.md:5:`data/backtests/dota_maker/validation_join_delta01_cut540_nw350_p35_burst-c20`.
        docs/experiments/burst-c20.md:16:350, delta 1¢, minpx 0.35, size $100, 3 levels. 454 validation matches,
        docs/experiments/burst-c20.md:19:`data/backtests/dota_maker/validation_join_delta01_cut540_nw350_p35_follow300-v5-budget`.
        docs/experiments/lol-horizon-window-deltas.md:15:Stage 05 writes `data/lol/processed/datasets_roles/` (not production
        docs/experiments/lol-horizon-window-deltas.md:75:research model. Do not rebuild production `datasets/`.
        docs/experiments/pre-draft-prior.md:34:`data/backtests/dota_maker/validation_s2_delta01_cut540_nw350_pre-draft-prior`
        docs/experiments/pre-draft-prior.md:37:`data/backtests/dota_maker/validation_s2_delta01_cut540_nw350_20260822T133407Z`
        docs/experiments/early-stop-gates.md:9:`data/backtests/dota_maker/validation_join_delta02_cut480_nw350_p35_earlystop-cutoff`
        docs/experiments/early-stop-gates.md:11:`data/backtests/dota_maker/validation_join_delta02_cut480_nw350_p35_live-cut480`
        docs/experiments/early-stop-gates.md:48:`validation_join_delta02_cut540_nw350_p35_maxpx-085` — живая модель,
        docs/experiments/no-unwind-nwoff.md:18:`data/backtests/dota_maker/validation_s2_delta01_cut540_nw350_p35_20260825T190207Z`
        docs/experiments/no-unwind-nwoff.md:21:`data/backtests/dota_maker/validation_s2_delta01_cut540_nw350_p35_no-unwind`
        docs/experiments/no-unwind-nwoff.md:24:`data/backtests/dota_maker/validation_s2_delta01_cut540_nw999999_p35_no-unwind`
        docs/experiments/v5-unwind-300.md:5:`data/backtests/dota_maker/validation_join_delta01_cut540_nw350_p35_v5-unwind300`.
        docs/experiments/v5-unwind-300.md:15:350, delta 1¢, minpx 0.35, size $100, 3 levels. 454 validation matches,
        docs/experiments/v5-unwind-300.md:18:`data/backtests/dota_maker/validation_join_delta01_cut540_nw350_p35_follow300-v5-budget`.
        docs/experiments/classif-p-up.diff:19:     select_validation_prediction_frame,
        docs/experiments/classif-p-up.diff:108: def select_validation_prediction_frame(
        docs/experiments/classif-p-up.diff:128:-    """Train LightGBM on 300-second market-price deltas and write validation scenarios."""
        docs/experiments/classif-p-up.diff:129:+    """Train LightGBM on P(300s move >= +1c) and write validation scenarios."""
        docs/experiments/classif-p-up.diff:132:     validation_dataset = load_validation_dataset(VALIDATION_DATASET_PATH)
        docs/experiments/classif-p-up.diff:139:     validation_fit = select_validation_fit_frame(validation_dataset)
        docs/experiments/classif-p-up.diff:140:     validation_features = lagged_source_features(validation_fit)
        docs/experiments/classif-p-up.diff:141:-    validation_target = build_price_delta_labels(validation_fit)
        docs/experiments/classif-p-up.diff:142:+    validation_target = build_up_move_labels(build_price_delta_labels(validation_fit))
        docs/experiments/classif-p-up.diff:145:     validation_data = lgb.Dataset(validation_features, validation_target)
        ✓ • 281ms
    
    ## Activity
    
        $ rg -n 'def (select_matches|write_datasets|main|eligibility_drop_reason|partition_rows|train_research|fit_research_members)|match_passes_tape_buckets|tape_passes_buckets|production_rows|validation_minute_rows|boost_rounds|backtest_book_gap_excluded|VALIDATION_START_TIME|REASON_THIN_TELONEX_TAPE' src/prepare_dataset src/lol src/shared src/backtest src/train_model
        src/prepare_dataset/prepare_dataset.py:26:    VALIDATION_START_TIME,
        src/prepare_dataset/prepare_dataset.py:52:from shared.utils.telonex_tape import match_passes_tape_buckets
        src/prepare_dataset/prepare_dataset.py:76:def select_matches(catalog: MatchCatalog, cutoff: int) -> MatchIdSplit:
        src/prepare_dataset/prepare_dataset.py:80:        if match_passes_tape_buckets(entry, RAW_TELONEX_POLYMARKET_DIR)
        src/prepare_dataset/prepare_dataset.py:254:            backtest_book_gap_excluded=match_id in gap_excluded,
        src/prepare_dataset/prepare_dataset.py:262:def write_datasets(
        src/prepare_dataset/prepare_dataset.py:264:    validation_minute_rows: list[DatasetRow],
        src/prepare_dataset/prepare_dataset.py:280:    production_rows = [*training_rows, *validation_minute_rows]
        src/prepare_dataset/prepare_dataset.py:284:    production_split_rows = split_rows(production_rows, "train", frozenset())
        src/prepare_dataset/prepare_dataset.py:290:    write_rows(production_rows, PRODUCTION_TRAINING_DATASET_PATH)
        src/prepare_dataset/prepare_dataset.py:295:        ("production", production_rows),
        src/prepare_dataset/prepare_dataset.py:321:def main(argv: Sequence[str] | None = None) -> None:
        src/prepare_dataset/prepare_dataset.py:325:    split = select_matches(catalog, VALIDATION_START_TIME)
        src/prepare_dataset/prepare_dataset.py:336:    validation_minute_rows: list[DatasetRow] = []
        src/prepare_dataset/prepare_dataset.py:343:            validation_minute_rows.extend(build_minute_rows(inputs, lag_seconds))
        src/prepare_dataset/prepare_dataset.py:356:        validation_minute_rows,
        src/train_model/train_model.py:176:def train_research(
        src/train_model/train_model.py:276:    boost_rounds: int,
        src/train_model/train_model.py:278:    """Fit K production members at `boost_rounds` trees and publish the catalog."""
        src/train_model/train_model.py:281:    boosters = fit_production_members(train_dataset, boost_rounds, features)
        src/train_model/train_model.py:349:def main(argv: Sequence[str] = ()) -> None:
        src/train_model/train_model.py:366:        boost_rounds = train_research(
        src/train_model/train_model.py:381:            boost_rounds=boost_rounds,
        src/lol/03_link_lolesports.py:858:def main(
        src/shared/utils/gbm.py:123:        """Round of the mean member tree count; production uses this as boost_rounds."""
        src/shared/utils/gbm.py:196:    boost_rounds: int,
        src/shared/utils/gbm.py:198:    """Train one production member with exactly `boost_rounds` trees, one thread, no holdout."""
        src/shared/utils/gbm.py:203:        num_boost_round=boost_rounds,
        src/shared/utils/gbm.py:270:    boost_rounds: int
        src/shared/utils/gbm.py:278:            self.boost_rounds,
        src/shared/utils/gbm.py:317:def fit_research_members(
        src/shared/utils/gbm.py:329:    boost_rounds: int,
        src/shared/utils/gbm.py:333:    return _fit_ensemble_members(train_frame, ProductionMemberFit(boost_rounds, tuple(features)))
        src/lol/02_fetch_telonex_books.py:582:def main(
        src/backtest/run.py:1737:def main() -> None:
        src/backtest/inspect/app.py:287:def main() -> None:
        src/backtest/selection.py:56:    flag_column = "backtest_book_gap_excluded"
        src/shared/utils/telonex_tape.py:45:def tape_passes_buckets(
        src/shared/utils/telonex_tape.py:73:def match_passes_tape_buckets(entry: CatalogEntry, telonex_root: Path) -> bool:
        src/shared/utils/telonex_tape.py:74:    return tape_passes_buckets(
        src/lol/01_build_universe.py:609:def main(
        src/shared/constants/dataset.py:3:VALIDATION_START_TIME = 1_780_563_592
        src/lol/06_train_model.py:153:def train_research(
        src/lol/06_train_model.py:240:    boost_rounds: int,
        src/lol/06_train_model.py:244:    boosters = fit_production_members(train_dataset, boost_rounds, FEATURE_COLUMNS)
        src/lol/06_train_model.py:278:def main() -> None:
        src/lol/06_train_model.py:281:    boost_rounds = train_research(
        src/lol/06_train_model.py:293:        boost_rounds,
        src/lol/constants.py:6:from shared.constants.dataset import VALIDATION_START_TIME
        src/lol/constants.py:142:LOL_VALIDATION_START_TIME = VALIDATION_START_TIME
        src/lol/constants.py:197:REASON_THIN_TELONEX_TAPE = "thin_telonex_trades"
        src/lol/05_prepare_dataset.py:29:    LOL_VALIDATION_START_TIME,
        src/lol/05_prepare_dataset.py:34:    REASON_THIN_TELONEX_TAPE,
        src/lol/05_prepare_dataset.py:82:from shared.utils.telonex_tape import tape_passes_buckets
        src/lol/05_prepare_dataset.py:481:    if event_start < LOL_VALIDATION_START_TIME:
        src/lol/05_prepare_dataset.py:519:    return tape_passes_buckets(
        src/lol/05_prepare_dataset.py:527:def eligibility_drop_reason(
        src/lol/05_prepare_dataset.py:535:            return REASON_THIN_TELONEX_TAPE
        src/lol/05_prepare_dataset.py:542:        return REASON_THIN_TELONEX_TAPE
        src/lol/05_prepare_dataset.py:645:def partition_rows(builds: Sequence[MapBuild], event_starts: Mapping[str, int]) -> PartitionedRows:
        src/lol/05_prepare_dataset.py:887:def main(
        src/lol/04_fetch_lolesports.py:943:def main(
        src/shared/types/dataset.py:96:    backtest_book_gap_excluded: bool
        ✓ • 294ms
    
    ## Activity
    
        $ git status --short --branch
        ## main...origin/main
        ✓ • 327ms
    
    ## Activity
    
        $ sed -n '65,115p;240,365p' src/prepare_dataset/prepare_dataset.py
            train: tuple[int, ...]
            validation: tuple[int, ...]
    
    
        @dataclass(frozen=True)
        class MatchInputs:
            match: UsableStratzMatch
            market_rows: list[MarketSecondRow]
            entry: CatalogEntry
    
    
        def select_matches(catalog: MatchCatalog, cutoff: int) -> MatchIdSplit:
            entries = [
                entry
                for entry in catalog.values()
                if match_passes_tape_buckets(entry, RAW_TELONEX_POLYMARKET_DIR)
            ]
            train_ids = exclude_missing_market_caches(
                tuple(entry.match_id for entry in entries if entry.start_time < cutoff),
                "train",
            )
    
            validation_entries = [entry for entry in entries if entry.start_time >= cutoff]
            missing_playback = [
                entry.match_id for entry in validation_entries if not entry.playback_available
            ]
            if missing_playback:
                ids = ", ".join(str(match_id) for match_id in missing_playback)
                raise SystemExit(f"validation candidates missing playback: {ids}")
            logger.info("validation candidates: %s", len(validation_entries))
            validation_ids = exclude_missing_market_caches(
                tuple(entry.match_id for entry in validation_entries),
                "validation",
            )
            return MatchIdSplit(train=train_ids, validation=validation_ids)
    
    
        def exclude_missing_market_caches(match_ids: Sequence[int], split: CacheSplit) -> tuple[int, ...]:
            missing = [
                match_id for match_id in match_ids if not market_seconds_cache_path(match_id).exists()
            ]
            cap = MAX_TRAIN_HARD_MISSES if split == "train" else MAX_VALIDATION_HARD_MISSES
            report = f"prepare missing {split} market caches ({len(missing)}): {missing}"
            if len(missing) > cap:
                logger.error(report)
                raise SystemExit(report)
            if missing:
                logger.warning(report)
            skipped = set(missing)
            return tuple(match_id for match_id in match_ids if match_id not in skipped)
    
            logger.info("%s rows -> %s", len(rows), path)
    
    
        def split_rows(
            rows: Sequence[DatasetRow] | Sequence[ValidationDatasetRow],
            split: Literal["train", "validation"],
            gap_excluded: frozenset[int],
        ) -> list[SplitRow]:
            match_times = {int(row["match_id"]): int(row["start_time"]) for row in rows}
            result = [
                SplitRow(
                    match_id=match_id,
                    start_time=start_time,
                    split=split,
                    backtest_book_gap_excluded=match_id in gap_excluded,
                )
                for match_id, start_time in match_times.items()
            ]
            result.sort(key=lambda row: (int(row["start_time"]), int(row["match_id"])))
            return result
    
    
        def write_datasets(
            training_rows: list[DatasetRow],
            validation_minute_rows: list[DatasetRow],
            validation_rows: list[ValidationDatasetRow],
            game_feature_rows: list[GameFeatureRow],
            gap_excluded: frozenset[int],
            output_dir: Path | None,
        ) -> None:
            if output_dir is not None:
                output_dir.mkdir(parents=True, exist_ok=True)
                write_rows(training_rows, output_dir / "training_dataset.parquet")
                write_rows(validation_rows, output_dir / "validation_dataset.parquet")
                write_rows(game_feature_rows, output_dir / "game_features.parquet")
                for name, rows in (("train", training_rows), ("validation", validation_rows)):
                    match_count = len({int(row["match_id"]) for row in rows})
                    logger.info("%s: %s matches | %s rows", name, match_count, len(rows))
                return
    
            production_rows = [*training_rows, *validation_minute_rows]
            research_split_rows = split_rows(training_rows, "train", frozenset()) + split_rows(
                validation_rows, "validation", gap_excluded
            )
            production_split_rows = split_rows(production_rows, "train", frozenset())
    
            write_rows(training_rows, TRAINING_DATASET_PATH)
            write_rows(validation_rows, VALIDATION_DATASET_PATH)
            write_rows(game_feature_rows, GAME_FEATURES_DATASET_PATH)
            write_rows(research_split_rows, RESEARCH_MODEL_SPLIT_PATH)
            write_rows(production_rows, PRODUCTION_TRAINING_DATASET_PATH)
            write_rows(production_split_rows, PRODUCTION_DATASET_SPLIT_PATH)
            for name, rows in (
                ("train", training_rows),
                ("validation", validation_rows),
                ("production", production_rows),
            ):
                match_count = len({int(row["match_id"]) for row in rows})
                logger.info("%s: %s matches | %s rows", name, match_count, len(rows))
    
    
        def load_match_inputs(match_id: int, catalog: MatchCatalog) -> MatchInputs:
            return MatchInputs(
                match=get_stratz_match_reach_data(match_id),
                market_rows=load_market_second_rows(market_seconds_cache_path(match_id)),
                entry=catalog[match_id],
            )
    
    
        def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
            parser = argparse.ArgumentParser(description=__doc__)
            parser.add_argument("--lag-seconds", type=int, default=TRAIN_LAG_SECONDS)
            parser.add_argument("--output-dir", type=Path, default=None)
            args = parser.parse_args(argv)
            if args.lag_seconds < 0:
                parser.error("--lag-seconds must be >= 0")
            if args.lag_seconds != TRAIN_LAG_SECONDS and args.output_dir is None:
                parser.error("--lag-seconds other than 10 requires --output-dir")
            return args
    
    
        def main(argv: Sequence[str] | None = None) -> None:
            args = parse_args(argv)
            setup_logging()
            catalog = load_match_catalog(MATCH_CATALOG_PATH)
            split = select_matches(catalog, VALIDATION_START_TIME)
            lag_seconds = args.lag_seconds
            output_dir = args.output_dir
    
            training_rows: list[DatasetRow] = []
            for i, match_id in enumerate(split.train, start=1):
                inputs = load_match_inputs(match_id, catalog)
                training_rows.extend(build_minute_rows(inputs, lag_seconds))
                if i % PROGRESS_EVERY == 0:
                    logger.info("processed train %s / %s matches", i, len(split.train))
    
            validation_minute_rows: list[DatasetRow] = []
            validation_rows: list[ValidationDatasetRow] = []
            game_feature_rows: list[GameFeatureRow] = []
            gap_excluded: set[int] = set()
            for i, match_id in enumerate(split.validation, start=1):
                inputs = load_match_inputs(match_id, catalog)
                if output_dir is None:
                    validation_minute_rows.extend(build_minute_rows(inputs, lag_seconds))
                if find_longest_book_gap(inputs.market_rows) > MAX_BOOK_GAP_SECONDS:
                    gap_excluded.add(match_id)
                states = build_exact_second_states(inputs.match)
                validation_rows.extend(join_validation_rows(states, inputs, lag_seconds))
                game_feature_rows.extend(
                    build_game_feature_rows(match_id, states, inputs.entry.radiant_prior)
                )
                if i % PROGRESS_EVERY == 0:
                    logger.info("processed validation %s / %s matches", i, len(split.validation))
    
            write_datasets(
                training_rows,
                validation_minute_rows,
                validation_rows,
                game_feature_rows,
                frozenset(gap_excluded),
                output_dir,
            )
    
    
        if __name__ == "__main__":
            main(sys.argv[1:])
        ✓ • 295ms
    
    ## Activity
    
        $ sed -n '455,555p;620,690p;865,935p' src/lol/05_prepare_dataset.py
            """Return a cached MapBuild when the input stamp matches, otherwise compute and store one."""
            stamp = build_input_stamp(link, windows_dir, details_dir, telonex_root, item_catalog_dir)
            path = map_cache_path(cache_dir, str(link["esports_game_id"]))
            cached = read_cached_build(path, stamp)
            if cached is not None:
                return MapBuildCacheResult(build=cached, hit=True)
            build = build_one_map(link, windows_dir, details_dir, item_catalog_dir, telonex_root)
            write_cached_build(path, stamp, build)
            return MapBuildCacheResult(build=build, hit=False)
    
    
        def event_start_times(builds: Sequence[MapBuild]) -> dict[str, int]:
            """Min spawn start_time per PM event, including maps later dropped for market."""
            starts: dict[str, int] = {}
            for build in builds:
                if build.start_time is None:
                    continue
                event_id = str(build.link["event_id"])
                current = starts.get(event_id)
                if current is None or build.start_time < current:
                    starts[event_id] = build.start_time
            return starts
    
    
        def split_for_event(event_start: int) -> LolPrepareSplit:
            """Assign the whole PM event to train or validation."""
            if event_start < LOL_VALIDATION_START_TIME:
                return "train"
            return "validation"
    
    
        def ok_quote_fraction(market_rows: Sequence[LolBacktestMarketSecondRow]) -> float:
            """Share of market seconds whose quote status is ok."""
            if not market_rows:
                return 0.0
            ok = 0
            for row in market_rows:
                if row["market_status"] == "ok":
                    ok += 1
            return ok / len(market_rows)
    
    
        def labeled_row_count(build: MapBuild) -> int:
            """Count 0..540 rows that have a 300s midpoint label."""
            count = 0
            for row in build.rows:
                if int(row["second"]) > LOL_GRID_END_SECOND:
                    continue
                if row["signal_market_p_radiant_300s"] is None:
                    continue
                count += 1
            return count
    
    
        def map_passes_tape_buckets(build: MapBuild, telonex_root: Path) -> bool:
            """True when the map's wall span has MIN_TRADES_PER_BUCKET prints in every full 5-minute bucket."""
            audit = build.backtest_audit
            if audit is None or build.start_time is None:
                return False
            ended_ts = audit["game_ended_at_ts"]
            token_0 = str(audit["token_id_0"])
            token_1 = str(audit["token_id_1"])
            if ended_ts is None or not token_0 or not token_1:
                return False
            return tape_passes_buckets(
                spawn_us=build.start_time * US_PER_SECOND,
                ended_us=int(ended_ts) * US_PER_SECOND,
                token_ids=(token_0, token_1),
                telonex_root=telonex_root,
            )
    
    
        def eligibility_drop_reason(
            build: MapBuild, split: LolPrepareSplit, telonex_root: Path
        ) -> str | None:
            """Drop reason after the event split, or None to keep the map."""
            if split == "train":
                if labeled_row_count(build) < 1:
                    return REASON_ZERO_LABELED_ROWS
                if not map_passes_tape_buckets(build, telonex_root):
                    return REASON_THIN_TELONEX_TAPE
                return None
            audit = build.backtest_audit
            assert audit is not None
            if audit["reason"] != REASON_ACCEPTED:
                return audit["reason"]
            if not map_passes_tape_buckets(build, telonex_root):
                return REASON_THIN_TELONEX_TAPE
            return None
    
    
        def drop_build(build: MapBuild, reason: str) -> MapBuild:
            """Excluded map with empty dataset rows; keeps backtest_audit."""
            return replace(build, included=False, reason=reason, rows=(), market_rows=(), feature_rows=())
    
    
        def apply_split_eligibility(
            builds: Sequence[MapBuild], event_starts: Mapping[str, int], telonex_root: Path
        ) -> list[MapBuild]:
            """Keep train maps with a label and validation maps that pass backtest gates."""
            updated: list[MapBuild] = []
                        "reason": build.reason,
                        "invariant_rule": build.invariant_rule,
                        "pause_count": build.pause_count,
                        "pause_seconds": build.pause_seconds,
                        "frame_count": build.frame_count,
                        "grid_seconds": build.grid_seconds,
                        "dataset_rows": len(build.rows),
                        "skipped_age_rows": build.skipped_age_rows,
                        "skipped_market_rows": build.skipped_market_rows,
                        "skipped_invariant_rows": build.skipped_invariant_rows,
                        "skipped_stamp_rows": build.skipped_stamp_rows,
                    }
                )
            return rows
    
    
        @dataclass(frozen=True)
        class PartitionedRows:
            """Accepted dataset rows split into train, validation, and production."""
    
            train: list[LolDatasetRow]
            validation: list[LolDatasetRow]
            production: list[LolDatasetRow]
    
    
        def partition_rows(builds: Sequence[MapBuild], event_starts: Mapping[str, int]) -> PartitionedRows:
            """Split accepted rows into train, validation, and production (all)."""
            train: list[LolDatasetRow] = []
            valid: list[LolDatasetRow] = []
            production: list[LolDatasetRow] = []
            for build in builds:
                if not build.included or build.start_time is None:
                    continue
                split = split_for_event(event_starts[str(build.link["event_id"])])
                labeled = [
                    row
                    for row in build.rows
                    if LOL_GRID_START_SECOND <= int(row["second"]) <= LOL_GRID_END_SECOND
                ]
                production.extend(labeled)
                if split == "train":
                    train.extend(labeled)
                else:
                    valid.extend(build.rows)
            return PartitionedRows(train, valid, production)
    
    
        @dataclass(frozen=True)
        class ValidationReplay:
            """Market seconds and eligibility rows for accepted validation maps."""
    
            market_rows: tuple[LolBacktestMarketSecondRow, ...]
            audit_rows: tuple[LolBacktestAuditRow, ...]
    
    
        def collect_validation_replay(
            builds: Sequence[MapBuild], event_starts: Mapping[str, int]
        ) -> ValidationReplay:
            """Keep replay artifacts only for accepted maps in the validation event split."""
            market_rows: list[LolBacktestMarketSecondRow] = []
            audit_rows: list[LolBacktestAuditRow] = []
            for build in builds:
                if not build.included or build.start_time is None or build.backtest_audit is None:
                    continue
                if split_for_event(event_starts[str(build.link["event_id"])]) != "validation":
                    continue
                market_rows.extend(build.market_rows)
                audit_rows.append(build.backtest_audit)
            return ValidationReplay(tuple(market_rows), tuple(audit_rows))
    
    
            builds = apply_split_eligibility(builds, event_starts, telonex_root)
            partitioned = partition_rows(builds, event_starts)
            split_rows = build_split_rows(builds, event_starts)
            audit_rows = build_audit_rows(builds, event_starts)
            replay = collect_validation_replay(builds, event_starts)
            feature_rows = collect_game_feature_rows(builds, event_starts)
            publish_datasets(
                output_dir,
                partitioned.train,
                partitioned.validation,
                partitioned.production,
                split_rows,
                audit_rows,
                replay,
                feature_rows,
                LOL_DATASET_COLUMNS,
            )
            print_prepare_totals(builds, split_rows)
            print_count("map_build_cache_hit", batch.cache_hits)
            print_count("map_build_cache_miss", len(batch.builds) - batch.cache_hits)
    
    
        def main(
            workers: Annotated[int, typer.Option("--workers")] = LOL_PREPARE_WORKERS,
        ) -> None:
            """Publish canonical LoL datasets."""
            prepare_dataset(
                LOL_LINKS_PATH,
                LOL_WINDOWS_DIR,
                LOL_DETAILS_DIR,
                DEFAULT_ITEM_CATALOG_DIR,
                LOL_RAW_TELONEX_DIR,
                LOL_DATASETS_DIR,
                workers,
                LOL_MAP_BUILD_CACHE_DIR,
            )
    
    
        if __name__ == "__main__":
            typer.run(main)
        ✓ • 304ms
    
    ## Activity
    
        $ sed -n '165,245p;345,395p' src/train_model/train_model.py
                training_path = args.training_dataset.expanduser()
                validation_path = args.validation_dataset.expanduser()
                if not training_path.is_file():
                    parser.error(f"--training-dataset not found: {training_path}")
                if not validation_path.is_file():
                    parser.error(f"--validation-dataset not found: {validation_path}")
                args.training_dataset = training_path.resolve()
                args.validation_dataset = validation_path.resolve()
            return args
    
    
        def train_research(
            training_path: Path,
            validation_path: Path,
            split_path: Path,
            research_dir: Path,
            archive_dir: Path,
            lag_seconds: int,
            features: Sequence[str],
        ) -> int:
            """Fit and publish the research default_sub90 ensemble; return mean member trees."""
            train_dataset_sha256 = sha256_file(training_path)
            validation_dataset_sha256 = sha256_file(validation_path)
            train_dataset = slice_train_rows(load_training_dataset(training_path))
            validation_dataset = load_validation_dataset(validation_path)
            validation_fit = select_validation_fit_frame(validation_dataset)
            validation_features = lagged_source_features(validation_fit, lag_seconds, features)
            validation_target = build_price_delta_labels(validation_fit)
            boosters = fit_research_members(train_dataset, validation_features, validation_target, features)
    
            member_trees = tuple(booster.num_trees() for booster in boosters)
            staging_dir = staging_model_dir(research_dir)
            member_names = write_ensemble_members(staging_dir, boosters)
            predictor = GbmPredictor(
                model_dir=staging_dir,
                feature_names=tuple(features),
                member_names=member_names,
                member_trees=member_trees,
                _boosters=tuple(boosters),
            )
    
            validation_prediction = select_validation_prediction_frame(validation_dataset)
            prediction_features = lagged_source_features(validation_prediction, lag_seconds, features)
            current_prices = validation_prediction["market_p_radiant"].to_numpy(dtype=np.float64)
            future_prices = predict_future_prices(predictor, prediction_features, current_prices)
            validation_rows = build_validation_dataset_rows(validation_prediction)
            metric_rows = evaluate_validation_metrics(validation_rows, future_prices.tolist())
            scenario_stats = build_scenario_stats(metric_rows)
    
            train_matches = train_dataset[["match_id", "start_time"]].drop_duplicates("match_id")
            validation_matches = validation_prediction[["match_id", "start_time"]].drop_duplicates(
                "match_id"
            )
            logger.info(
                "ensemble %s k=%s trees: %s-%s mean=%s (of %s) | train matches: %s | "
                "validation matches: %s | train rows: %s | validation rows: %s",
                ENSEMBLE_ARM,
                ENSEMBLE_K,
                min(member_trees),
                max(member_trees),
                predictor.num_trees(),
                MAX_BOOST_ROUNDS,
                len(train_matches),
                len(validation_matches),
                len(train_dataset),
                len(validation_prediction),
            )
            log_dataset_span("train", train_matches)
            log_dataset_span("validation", validation_matches)
    
            full_window = full_window_scenario_stats(scenario_stats)
            metrics = build_model_metrics(
                predictor,
                HoldoutWindowStats(
                    rows=full_window.rows,
                    future_price_300_count=full_window.future_price_300_count,
                    no_move_mae_300=full_window.no_move_mae_300,
                    model_mae_300=full_window.model_mae_300,
                    mae_gain_300=full_window.mae_gain_300,
                    mae_gain_300_ci=full_window.mae_gain_300_ci,
                    model_bias_300=full_window.model_bias_300,
            ),
        )
    
    
        def main(argv: Sequence[str] = ()) -> None:
            """Publish live research then production for each catalog pair, or one experiment catalog."""
            args = parse_args(argv)
            setup_logging()
            if args.model_dir is not None:
                features = NO_XP_FEATURE_COLUMNS if args.no_xp else FEATURE_COLUMNS
                train_research(
                    args.training_dataset,
                    args.validation_dataset,
                    RESEARCH_MODEL_SPLIT_PATH,
                    args.model_dir,
                    args.model_dir.parent / "archive",
                    args.lag_seconds,
                    features,
                )
                return
            for pair in LIVE_CATALOG_PAIRS:
                boost_rounds = train_research(
                    TRAINING_DATASET_PATH,
                    VALIDATION_DATASET_PATH,
                    RESEARCH_MODEL_SPLIT_PATH,
                    pair.research_dir,
                    pair.research_archive_dir,
                    TRAIN_LAG_SECONDS,
                    pair.features,
                )
                train_production(
                    PRODUCTION_TRAINING_DATASET_PATH,
                    PRODUCTION_DATASET_SPLIT_PATH,
                    pair.production_dir,
                    pair.production_archive_dir,
                    pair.features,
                    boost_rounds=boost_rounds,
                )
    
    
        if __name__ == "__main__":
            main(sys.argv[1:])
        ✓ • 294ms
    
    ## Activity
    
        $ sed -n '140,210p;270,310p' src/lol/06_train_model.py
            for index in range(len(validation)):
                points.append(
                    LolHoldoutPoint(
                        event_id=event_ids[int(match_ids[index])],
                        second=int(seconds[index]),
                        current=float(currents[index]),
                        future=float(futures[index]),
                        model_p=float(model_prices[index]),
                    )
                )
            return points
    
    
        def train_research(
            training_path: Path,
            validation_path: Path,
            split_path: Path,
            research_dir: Path,
            research_archive_dir: Path,
        ) -> int:
            """Fit and publish the research default_sub90 ensemble; return mean member trees."""
            features = list(FEATURE_COLUMNS)
            train_dataset = load_lol_dataset(training_path)
            validation_dataset = load_lol_dataset(validation_path)
            event_ids = load_event_ids(split_path)
            valid_features = validation_dataset[features]
            boosters = fit_research_members(
                train_dataset,
                valid_features,
                build_price_delta_labels(validation_dataset),
                features,
            )
            member_trees = tuple(booster.num_trees() for booster in boosters)
            staging_dir = staging_model_dir(research_dir)
            member_names = write_ensemble_members(staging_dir, boosters)
            predictor = GbmPredictor(
                model_dir=staging_dir,
                feature_names=tuple(features),
                member_names=member_names,
                member_trees=member_trees,
                _boosters=tuple(boosters),
            )
            current_prices = validation_dataset["market_p_radiant"].to_numpy(dtype=np.float64)
            model_prices = predict_future_prices(predictor, valid_features, current_prices)
            points = holdout_points(validation_dataset, model_prices, event_ids)
            aggregates = build_holdout_aggregates(points)
            full_window = aggregates[-1]
            train_matches = unique_match_count(train_dataset)
            validation_matches = unique_match_count(validation_dataset)
            logger.info(
                "ensemble %s k=%s trees: %s-%s mean=%s (of %s) | train matches: %s | "
                "validation matches: %s | train rows: %s | validation rows: %s",
                ENSEMBLE_ARM,
                ENSEMBLE_K,
                min(member_trees),
                max(member_trees),
                predictor.num_trees(),
                MAX_BOOST_ROUNDS,
                train_matches,
                validation_matches,
                len(train_dataset),
                len(validation_dataset),
            )
    
            write_json(
                staging_dir / "model.json",
                build_lol_model_meta(
                    train_matches,
                    sha256_file(training_path),
                    validation_dataset_sha256=sha256_file(validation_path),
                    validation_matches=validation_matches,
                    member_trees=member_trees,
                ),
            )
            copy2(split_path, staging_dir / "split.parquet")
            archived_name = publish_lol_model_dir(staging_dir, production_dir, production_archive_dir)
            logger.info("published: %s | archived previous model: %s", production_dir, archived_name)
    
    
        def main() -> None:
            """Publish canonical LoL research and production models in order."""
            setup_logging()
            boost_rounds = train_research(
                LOL_TRAINING_PATH,
                LOL_VALIDATION_PATH,
                LOL_SPLIT_PATH,
                LOL_RESEARCH_MODEL_DIR,
                LOL_RESEARCH_ARCHIVE_DIR,
            )
            train_production_model(
                LOL_PRODUCTION_TRAINING_PATH,
                LOL_SPLIT_PATH,
                LOL_PRODUCTION_MODEL_DIR,
                LOL_PRODUCTION_ARCHIVE_DIR,
                boost_rounds,
            )
    
    
        if __name__ == "__main__":
            typer.run(main)
        ✓ • 292ms
    
    ## Activity
    
        $ sed -n '45,75p' src/shared/utils/telonex_tape.py; sed -n '45,85p' src/backtest/selection.py
        def tape_passes_buckets(
            *,
            spawn_us: int,
            ended_us: int,
            token_ids: Sequence[str],
            telonex_root: Path,
        ) -> bool:
            buckets = full_trade_bucket_count(spawn_us, ended_us)
            if buckets == 0:
                return False
            bucket_us = TRADE_BUCKET_SECONDS * US_PER_SECOND
            last_end_us = spawn_us + buckets * bucket_us
            timestamps: list[int] = []
            for token_id in token_ids:
                timestamps.extend(
                    load_token_tape_timestamps(
                        token_id=token_id,
                        start_us=spawn_us,
                        end_us=last_end_us,
                        telonex_root=telonex_root,
                    )
                )
            counts = [0] * buckets
            for timestamp_us in timestamps:
                counts[(timestamp_us - spawn_us) // bucket_us] += 1
            return all(count >= MIN_TRADES_PER_BUCKET for count in counts)
    
    
        def match_passes_tape_buckets(entry: CatalogEntry, telonex_root: Path) -> bool:
            return tape_passes_buckets(
                spawn_us=datetime_to_us(entry.anchor_at),
            without_map_market: int
            without_signal_rows: int
            without_local_telonex: int
            book_gap_excluded: int
            archive_excluded: int
            eligible: int
    
    
        def load_validation_split() -> ValidationSplit:
            """Load chronological validation ids and require the backtest gap flag."""
            split = pd.read_parquet(RESEARCH_MODEL_SPLIT_PATH)
            flag_column = "backtest_book_gap_excluded"
            if flag_column not in split.columns:
                raise ValueError(
                    f"research split is missing {flag_column!r}; rerun make prepare and make train"
                )
            validation = split[split["split"] == "validation"].sort_values(
                ["start_time", "match_id"], ignore_index=True
            )
            excluded = validation[validation[flag_column].astype(bool)]
            return ValidationSplit(
                match_ids=tuple(int(match_id) for match_id in validation["match_id"]),
                book_gap_excluded_match_ids=frozenset(int(match_id) for match_id in excluded["match_id"]),
            )
    
    
        def load_validation_match_ids() -> tuple[int, ...]:
            """Load chronological validation match ids from the current research split."""
            return load_validation_split().match_ids
    
    
        def load_market_sources(signal_rows: pd.DataFrame) -> MarketSources:
            """Load the catalog and split the selection and market contexts read."""
            validation_split = load_validation_split()
            usable_signal_match_ids = frozenset(int(match_id) for match_id in signal_rows["match_id"])
            return MarketSources(
                validation_match_ids=validation_split.match_ids,
                catalog=load_match_catalog(MATCH_CATALOG_PATH),
                usable_signal_match_ids=usable_signal_match_ids,
                book_gap_excluded_match_ids=validation_split.book_gap_excluded_match_ids,
            )
        ✓ • 295ms
    
    ## Assistant
    
    По коду исходная схема действительно такая: фильтр ленты применяется до разбиения, research-модель останавливается на всей validation, а production обучается на train плюс минутные строки validation. В плане появляется другая цель — расширить бэктест тонкими картами, но сохранить прежний состав production и прежний holdout для выбора числа деревьев. Именно из-за этого возникают дополнительные подмножества.
    
    ## Activity
    
        $ sed -n '548,625p;690,780p' src/lol/05_prepare_dataset.py; sed -n '25,45p;110,132p' docs/as-is.md
            return replace(build, included=False, reason=reason, rows=(), market_rows=(), feature_rows=())
    
    
        def apply_split_eligibility(
            builds: Sequence[MapBuild], event_starts: Mapping[str, int], telonex_root: Path
        ) -> list[MapBuild]:
            """Keep train maps with a label and validation maps that pass backtest gates."""
            updated: list[MapBuild] = []
            for build in builds:
                if not build.included or build.start_time is None:
                    updated.append(build)
                    continue
                split = split_for_event(event_starts[str(build.link["event_id"])])
                reason = eligibility_drop_reason(build, split, telonex_root)
                if reason is None:
                    updated.append(build)
                    continue
                updated.append(drop_build(build, reason))
            return updated
    
    
        def audit_split_label(build: MapBuild, event_starts: Mapping[str, int]) -> LolPrepareAuditSplit:
            """train/validation for accepted maps; none otherwise."""
            if not build.included or build.start_time is None:
                return "none"
            event_id = str(build.link["event_id"])
            event_start = event_starts.get(event_id)
            if event_start is None:
                return "none"
            return split_for_event(event_start)
    
    
        def build_split_rows(
            builds: Sequence[MapBuild], event_starts: Mapping[str, int]
        ) -> list[LolPrepareSplitRow]:
            """One split row per accepted map."""
            rows: list[LolPrepareSplitRow] = []
            for build in builds:
                if not build.included or build.start_time is None:
                    continue
                event_id = str(build.link["event_id"])
                event_start = event_starts[event_id]
                split = split_for_event(event_start)
                rows.append(
                    {
                        "match_id": build.match_id,
                        "event_id": event_id,
                        "esports_game_id": str(build.link["esports_game_id"]),
                        "game_number": int(build.link["game_number"]),
                        "start_time": build.start_time,
                        "event_start_time": event_start,
                        "split": split,
                    }
                )
            return rows
    
    
        def build_audit_rows(
            builds: Sequence[MapBuild], event_starts: Mapping[str, int]
        ) -> list[LolPrepareAuditRow]:
            """One audit row per linked map."""
            rows: list[LolPrepareAuditRow] = []
            for build in builds:
                rows.append(
                    {
                        "event_id": str(build.link["event_id"]),
                        "esports_game_id": str(build.link["esports_game_id"]),
                        "game_number": int(build.link["game_number"]),
                        "match_id": build.match_id,
                        "start_time": build.start_time,
                        "split": audit_split_label(build, event_starts),
                        "included": build.included,
                        "reason": build.reason,
                        "invariant_rule": build.invariant_rule,
                        "pause_count": build.pause_count,
                        "pause_seconds": build.pause_seconds,
                        "frame_count": build.frame_count,
                        "grid_seconds": build.grid_seconds,
    
        def collect_game_feature_rows(
            builds: Sequence[MapBuild], event_starts: Mapping[str, int]
        ) -> tuple[GameFeatureRow, ...]:
            """Exact-second model inputs for accepted validation maps; no market join needed."""
            rows: list[GameFeatureRow] = []
            for build in builds:
                if not build.included or build.start_time is None:
                    continue
                if split_for_event(event_starts[str(build.link["event_id"])]) != "validation":
                    continue
                rows.extend(build.feature_rows)
            return tuple(rows)
    
    
        def print_prepare_progress(done: int, total: int, finished: Sequence[MapBuild]) -> None:
            """Print running map counts so a long prepare is observable."""
            accepted = sum(1 for build in finished if build.included)
            print(f"prepared: {done}/{total} accepted={accepted}", flush=True)
    
    
        def build_maps_parallel(
            links: Sequence[LolLinkRow],
            windows_dir: Path,
            details_dir: Path,
            item_catalog_dir: Path,
            telonex_root: Path,
            cache_dir: Path,
            workers: int,
        ) -> MapBuildBatch:
            """Build one MapBuild per link on a process pool; preserve input order."""
            slots: list[MapBuild | None] = [None] * len(links)
            total = len(links)
            if total == 0:
                return MapBuildBatch((), 0)
            done = 0
            cache_hits = 0
            workers = min(total, workers)
            # Import by package name so spawn pickle works when this file is __main__.
            worker = import_module("lol.05_prepare_dataset").build_one_map_cached
            pool = ProcessPoolExecutor(max_workers=workers, mp_context=get_context("spawn"))
            try:
                futures = {
                    pool.submit(
                        worker,
                        link,
                        windows_dir,
                        details_dir,
                        item_catalog_dir,
                        telonex_root,
                        cache_dir,
                    ): index
                    for index, link in enumerate(links)
                }
                for future in as_completed(futures):
                    index = futures[future]
                    result = future.result()
                    slots[index] = result.build
                    if result.hit:
                        cache_hits += 1
                    done += 1
                    if done == total or done % PREPARE_PROGRESS_EVERY == 0:
                        finished = [build for build in slots if build is not None]
                        print_prepare_progress(done, total, finished)
            except BaseException:
                pool.shutdown(wait=False, cancel_futures=True)
                raise
            pool.shutdown(wait=True)
            return MapBuildBatch(tuple(cast(list[MapBuild], slots)), cache_hits)
    
    
        def publish_datasets(
            output_dir: Path,
            train: Sequence[LolDatasetRow],
            valid: Sequence[LolDatasetRow],
            production: Sequence[LolDatasetRow],
            split_rows: Sequence[LolPrepareSplitRow],
            audit_rows: Sequence[LolPrepareAuditRow],
            replay: ValidationReplay,
            game_features: Sequence[GameFeatureRow],
            columns: list[str],
        ) -> None:
            """Replace dataset parquets plus validation market seconds and backtest audit."""
            output_dir.mkdir(parents=True, exist_ok=True)
            write_parquet_rows(
                cast(Sequence[Mapping[str, object]], train),
                output_dir / "training.parquet",
                columns,
                DATASET_INTEGER_COLUMNS,
                ["match_id", "second"],
            )
        Catalog keep/drop on publish: kept 3040, dropped 1697 (mostly missing GRID/STRATZ/prior). Gamma fail=1.
    
        ## Datasets
    
        Validation cutoff is `VALIDATION_START_TIME = 1780563592` (~2026-06-04). New maps after that go to validation/production, not the research train set.
    
        | Split | Matches | Rows | Match time span |
        | --- | --- | --- | --- |
        | Research train | **1010** (unchanged) | 9800 | 2025-11-08 .. 2026-05-30 |
        | Validation | **454** (was 367) | 1_182_819 (was 956_388) | 2026-06-04 .. **2026-09-03** |
        | Production train | **1464** (was 1377) | 14_653 (was 13_726) | 2025-11-08 .. **2026-09-03** |
    
        Thin Telonex trades excluded 1473 train maps and 101 validation maps. Market-data: selected 3040, built 118 new second caches, excluded_train 157, excluded_validation 8 (under the cap of 20).
    
        ## Backtest
    
        One policy, no strategy switches: **Follow300** (`buy_ladder_policy=follow300-v1`). At a
        valid model tick the strategy builds up to three BUY rungs on the episode token — best bid,
        one cent lower, two cents lower — each a full $100 notional, alongside at most one SELL.
        Rung prices stay latched until the episode's first BUY execution; after it, eligible
        uncompleted rungs follow the book. A completed rung is not replenished until the episode
        | | Research | Production |
        | --- | --- | --- |
        | name | **`20260904T222046Z`** | **`20260904T222047Z`** |
        | trained_at | 2026-09-04T22:20:46Z | 2026-09-04T22:20:47Z |
        | trees | 41 (was 56) | 41 |
        | train_matches | 1010 | 1464 |
        | predecessor archived | `20260904T174030Z` | `20260904T200130Z` |
    
        Research holdout (validation window, 289_349 rows / 285_926 with a 300s future):
    
        | metric | new | previous (`20260904T174030Z`) |
        | --- | --- | --- |
        | dir_300_cents | 1.260 | 1.158 |
        | mae_gain_300_cents | 0.147 | 0.160 |
        | model_mae_300_cents | 7.899 | 7.927 |
        | no_move_mae_300_cents | 8.046 | 8.087 |
        | model_bias_300_cents | −0.043 | −0.006 |
    
        Train-set SHA for research is unchanged (`41af3f84…`); the extra data is validation/production only. Production has no holdout metrics.
    
        VPS live production is **not** this pair. Host `sun` still has `data/new_model/production` name `20260825T193532Z` (2510 train matches). Local publish does not deploy the trader.
        ✓ • 290ms
    
    ## Activity
    
        $ rg -n 'def fit_research_members|def _fit_ensemble_members|early_stopping|select_validation_fit_frame|build_split_rows|audit_split_label' src/shared/utils/gbm.py src/train_model src/lol/05_prepare_dataset.py
        src/shared/utils/gbm.py:189:        callbacks=[lgb.early_stopping(EARLY_STOP_PATIENCE, verbose=False)],
        src/shared/utils/gbm.py:303:def _fit_ensemble_members(
        src/shared/utils/gbm.py:317:def fit_research_members(
        src/lol/05_prepare_dataset.py:569:def audit_split_label(build: MapBuild, event_starts: Mapping[str, int]) -> LolPrepareAuditSplit:
        src/lol/05_prepare_dataset.py:580:def build_split_rows(
        src/lol/05_prepare_dataset.py:618:                "split": audit_split_label(build, event_starts),
        src/lol/05_prepare_dataset.py:867:    split_rows = build_split_rows(builds, event_starts)
        src/train_model/train_model.py:110:def select_validation_fit_frame(frame: pd.DataFrame) -> pd.DataFrame:
        src/train_model/train_model.py:190:    validation_fit = select_validation_fit_frame(validation_dataset)
        ✓ • 279ms
    
    ## Activity
    
        $ rg -n 'research_model_dir|production_model_dir|RESEARCH_MODEL_DIR|PRODUCTION_MODEL_DIR|model_dir|model_path' src/backtest/run.py src/lol/07_backtest.py src/lol -g '*backtest*' | head -120
        rg: src/lol/07_backtest.py: No such file or directory (os error 2)
        src/backtest/run.py:142:    LOL_RESEARCH_MODEL_DIR,
        src/backtest/run.py:151:    RESEARCH_MODEL_DIR,
        src/backtest/run.py:244:    model_dir: Path
        src/backtest/run.py:697:    model_dir: Path,
        src/backtest/run.py:711:    model_meta = read_model_meta(model_dir / MODEL_META_FILENAME)
        src/backtest/run.py:737:        "model_path": str(model_dir),
        src/backtest/run.py:738:        "model_sha256": model_identity_sha256(model_dir),
        src/backtest/run.py:749:            str(plan_model_dir): model_identity_sha256(plan_model_dir)
        src/backtest/run.py:750:            for plan_model_dir in sorted({plan.model_dir for plan in plans.values()}, key=str)
        src/backtest/run.py:1215:def _pin_cli_model_dir(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
        src/backtest/run.py:1217:    if args.model_dir is None:
        src/backtest/run.py:1219:    pinned = args.model_dir.expanduser()
        src/backtest/run.py:1222:    args.model_dir = pinned.resolve()
        src/backtest/run.py:1368:    _pin_cli_model_dir(parser, args)
        src/backtest/run.py:1396:            probe = load_lol_selection(None, None, LOL_RESEARCH_MODEL_DIR, allowed_event_ids)
        src/backtest/run.py:1402:            lol = load_lol_selection(match_id, limit, LOL_RESEARCH_MODEL_DIR, allowed_event_ids)
        src/backtest/run.py:1417:            model_dir=LOL_RESEARCH_MODEL_DIR,
        src/backtest/run.py:1437:        model_dir=RESEARCH_MODEL_DIR,
        src/backtest/run.py:1672:    model_dir: Path,
        src/backtest/run.py:1697:                model_dir,
        src/backtest/run.py:1772:        selection.archive_join, selection.selected_ids, args.model_dir, args.match_id
        src/backtest/run.py:1788:    if args.model_dir is not None:
        src/backtest/run.py:1789:        selection = replace(selection, model_dir=args.model_dir)
        src/backtest/run.py:1796:    model_dir = selection.model_dir
        src/backtest/run.py:1833:        model_dir=model_dir,
        src/backtest/run.py:1889:        model_dir=model_dir,
        ✓ • 289ms
    
    ## Activity
    
        $ rg -n 'look.ahead|lookahead|thin.*validation|validation.*thin|113 карт|389 карт|tape_dense|production_validation' .learnings docs src tests | head -180
        rg: .learnings: No such file or directory (os error 2)
        tests/test_backtest_signals.py:248:def test_find_signal_asof_is_no_lookahead_and_stale_after_age_budget() -> None:
        tests/test_backtest_signals.py:266:    # Between 2s and 3s: as-of is still the 2s row (no lookahead to 3s).
        docs/experiments/dota-horizon-window.md:7:Hypothesis: a 240s or 420s look-ahead, or a 300s label on every minute
        docs/experiments/delta-sizing/code.patch:650:@@ -245,6 +245,7 @@ def test_find_signal_asof_is_no_lookahead_and_stale_after_age_budget() -> None:
        docs/experiments/pnl-action-v1/engine-gate.diff:677:@@ -237,6 +246,8 @@ def test_find_signal_asof_is_no_lookahead_and_stale_after_age_budget() -> None:
        docs/experiments/lol-horizon-train.md:8:Hypothesis: a GBM trained on a shorter or longer look-ahead, with the maker
        ✓ • 307ms
    
    ## Activity
    
        $ rg -n 'look.ahead|lookahead|thin.*validation|validation.*thin|tape.*filter|фильтр.*ленты|тонк.*валидац' .learnings | head -140
        .learnings/dota-training-filter-and-zero-lag-20260919.md:1:# Dota retraining, tape filter, and train-lag 0: code audit
        .learnings/training-tape-exclusions-audit-20260919.md:1:# Dota and LoL: training examples lost to the whole-map tape filter
        ✓ • 0ms
    
    ## Activity
    
        $ sed -n '175,195p;300,335p' src/shared/utils/gbm.py; sed -n '108,125p' src/train_model/train_model.py; sed -n '1385,1445p' src/backtest/run.py
            train_X: pd.DataFrame,
            train_y: npt.NDArray[np.float64],
            valid_X: pd.DataFrame,
            valid_y: npt.NDArray[np.float64],
        ) -> lgb.Booster:
            """Train one ensemble member with early stopping on the holdout set."""
            train_data = lgb.Dataset(train_X, train_y)
            validation_data = lgb.Dataset(valid_X, valid_y)
            return lgb.train(  # pyright: ignore[reportUnknownMemberType]
                ensemble_member_params(),
                train_data,
                num_boost_round=MAX_BOOST_ROUNDS,
                valid_sets=[validation_data],
                valid_names=["validation"],
                callbacks=[lgb.early_stopping(EARLY_STOP_PATIENCE, verbose=False)],
            )
    
    
        def fit_ensemble_member_fixed_trees(
            train_X: pd.DataFrame,
            train_y: npt.NDArray[np.float64],
            return _worker_fit(member_train_frame(grouped, unique_ids, member_index))
    
    
        def _fit_ensemble_members(
            train_frame: pd.DataFrame,
            fit_member: EnsembleMemberFit,
        ) -> list[lgb.Booster]:
            """Fit K subsampled members, one process each; `map` returns them in member order."""
            with ProcessPoolExecutor(
                max_workers=MEMBER_FIT_WORKERS,
                mp_context=get_context("spawn"),
                initializer=init_member_fit_worker,
                initargs=(train_frame, fit_member),
            ) as pool:
                return list(pool.map(fit_member_job, range(ENSEMBLE_K)))
    
    
        def fit_research_members(
            train_frame: pd.DataFrame,
            valid_X: pd.DataFrame,
            valid_y: npt.NDArray[np.float64],
            features: Sequence[str],
        ) -> list[lgb.Booster]:
            """Fit K default_sub90 members with early stopping on the holdout features."""
            return _fit_ensemble_members(train_frame, ResearchMemberFit(valid_X, valid_y, tuple(features)))
    
    
        def fit_production_members(
            train_frame: pd.DataFrame,
            boost_rounds: int,
            features: Sequence[str],
        ) -> list[lgb.Booster]:
            """Fit K default_sub90 members with a fixed tree count and no holdout."""
            return _fit_ensemble_members(train_frame, ProductionMemberFit(boost_rounds, tuple(features)))
    
    
    
    
        def select_validation_fit_frame(frame: pd.DataFrame) -> pd.DataFrame:
            """Keep fixed-window research rows with a 300-second future midpoint."""
            selected = select_validation_prediction_frame(frame)
            return selected.loc[selected["signal_market_p_radiant_300s"].notna()]
    
    
        def slice_train_rows(frame: pd.DataFrame) -> pd.DataFrame:
            """Keep canonical Dota minute rows with a usable 300-second label."""
            in_window = (frame["second"] >= MODEL_START_SECOND) & (
                frame["second"] < TRAIN_END_SECOND_EXCLUSIVE
            )
            return frame.loc[in_window & frame["signal_market_p_radiant_300s"].notna()]
    
    
        def log_dataset_span(label: str, match_times: pd.DataFrame) -> None:
            """Log how many matches a dataset holds and the first/last match start."""
            match_id: int | None,
            since_match: int | None,
            limit: int | None,
            allowed_event_ids: frozenset[str] | None,
            validation_dataset: Path = VALIDATION_DATASET_PATH,
            lag_seconds: int = BACKTEST_LAG_SECONDS,
        ) -> RunSelection:
            """Load LoL or Dota ids, paths, and the lookup loader for this CLI run."""
            if game == "lol":
                if since_match is not None:
                    # Resolve ids against a full probe load, then pin selection to the cohort.
                    probe = load_lol_selection(None, None, LOL_RESEARCH_MODEL_DIR, allowed_event_ids)
                    since_ids = select_lol_since_match_ids(probe.audit, probe.signal_rows, since_match)
                    if limit is not None:
                        since_ids = since_ids[:limit]
                    lol = replace(probe, selected_ids=since_ids, coverage=None)
                else:
                    lol = load_lol_selection(match_id, limit, LOL_RESEARCH_MODEL_DIR, allowed_event_ids)
                return RunSelection(
                    selected_ids=lol.selected_ids,
                    coverage=lol.coverage,
                    capture_root=lol.capture_root,
                    report_root=lol.report_root,
                    signal_rows=lol.signal_rows,
                    load_lookups=partial(
                        load_lol_replay_lookups,
                        audit=lol.audit,
                        market_seconds=lol.market_seconds,
                        gamma_markets=lol.gamma_markets,
                        buy_cutoff_second=BUY_CUTOFF_SECOND,
                        signal_rows=lol.signal_rows,
                    ),
                    model_dir=LOL_RESEARCH_MODEL_DIR,
                    lag_seconds=0,
                    archive_join=LolArchiveJoin(lol.audit),
                )
            if since_match is not None:
                # Resolve ids against a full probe load, then pin selection to the cohort.
                probe = load_dota_selection(None, None, validation_dataset=validation_dataset)
                since_ids = select_dota_since_match_ids(probe.sources, since_match)
                if limit is not None:
                    since_ids = since_ids[:limit]
                dota = replace(probe, selected_ids=since_ids, coverage=None)
            else:
                dota = load_dota_selection(match_id, limit, validation_dataset=validation_dataset)
            return RunSelection(
                selected_ids=dota.selected_ids,
                coverage=dota.coverage,
                capture_root=dota.capture_root,
                report_root=dota.report_root,
                signal_rows=dota.signal_rows,
                load_lookups=partial(load_replay_lookups, sources=dota.sources),
                model_dir=RESEARCH_MODEL_DIR,
                lag_seconds=lag_seconds,
                archive_join=DotaArchiveJoin(dota.sources.catalog),
            )
    
    
        def merge_shard_run(
            report_dir: Path,
            shard_count: int,
        ✓ • 237ms
    
    ## Activity
    
        $ rg -n 'load_lol_selection|split.*validation|validation.*split' src/backtest/selection.py src/backtest/lol_selection.py src/backtest/run.py | head -100
        rg: src/backtest/lol_selection.py: No such file or directory (os error 2)
        src/backtest/run.py:1:"""Run the maker backtest: one match, the validation split, or a since-match cohort."""
        src/backtest/run.py:65:    load_lol_selection,
        src/backtest/run.py:1396:            probe = load_lol_selection(None, None, LOL_RESEARCH_MODEL_DIR, allowed_event_ids)
        src/backtest/run.py:1402:            lol = load_lol_selection(match_id, limit, LOL_RESEARCH_MODEL_DIR, allowed_event_ids)
        src/backtest/selection.py:53:def load_validation_split() -> ValidationSplit:
        src/backtest/selection.py:61:    validation = split[split["split"] == "validation"].sort_values(
        src/backtest/selection.py:72:    """Load chronological validation match ids from the current research split."""
        src/backtest/selection.py:73:    return load_validation_split().match_ids
        src/backtest/selection.py:78:    validation_split = load_validation_split()
        src/backtest/selection.py:81:        validation_match_ids=validation_split.match_ids,
        src/backtest/selection.py:84:        book_gap_excluded_match_ids=validation_split.book_gap_excluded_match_ids,
        ✓ • 231ms
    
    ## Activity
    
        $ sed -n '1,180p' .learnings/dota-training-filter-and-zero-lag-20260919.md
        # Dota retraining, tape filter, and train-lag 0: code audit
    
        Read-only review on 2026-09-19 after research model `20260919T114801Z`
        and production model `20260919T114805Z` were published in commit `d34e199a`.
        No product edits, training, backtests, or deployments performed by this review.
    
        ## What changed since the September 15 models
    
        Compared commit `a25456e3` with current HEAD, including split parquet loaded
        directly from that old Git commit (the archived split had been overwritten by
        later prepare before archival, so it is unsuitable for reconstructing the old
        training population).
    
        - Research training maps: 1010 -> 1152; **151 added, 9 removed**.
        - Research validation maps: 531 -> 558; **32 added, 5 removed**.
        - Same feature list, horizon 300s, train lag 10s, K=10 sub90 ensemble.
        - No diff in `src/shared/utils/gbm.py`, STRATZ feature construction,
          market cache construction, or quote-quality rules across these commits.
        - Tree counts are refitted: mean 40 -> 44. Research early stopping uses the
          changed validation data, so this is not an isolated addition of train rows.
        - Removed upper `DATASET_END_TIME` filter and added experiment CLI lag flags.
        - Dataset admission now reads onchain fills (`block_timestamp_us`) instead of
          Telonex trades (`timestamp_us`); the 10-record / 300-second threshold stayed.
        - Raw training parquet at the old commit is not tracked; cannot prove all
          numerical rows for shared training IDs remained identical after data refresh.
    
        Thus the fitting recipe stayed the same; changed data composition and refitting
        produced the observed improvement. It does not establish that adding arbitrary
        matches always helps.
    
        ## Tape filter applies to training too
    
        `src/prepare_dataset/prepare_dataset.py:74` filters the entire match catalog with
        `match_passes_tape_buckets` before the chronological train/validation split.
        `src/shared/utils/telonex_tape.py:45` requires at least 10 onchain rows combined
        across the two tokens in every full 300-second bucket from spawn to map end.
        This is an activity/availability heuristic over the whole map, not a direct
        test of corruption of game features or current/future market prices.
    
        The supervised target is future midpoint at +300s minus current midpoint.
        Game features plus correctly timestamped current/future books are sufficient
        for an individual training example. Fill tape is required separately to model
        executions in the queue backtest. Current prepare already skips minute rows
        whose current book is invalid or whose +300s market target is missing.
        Long book gaps >120s have a separate backtest-only exclusion mechanism.
    
        Recommendation: test separating replay-tape eligibility from training-data
        quality. Broaden training only where features, orientation/timing, current price,
        and +300s target are valid. Keep the existing held-out replay panel and training
        recipe fixed for that experiment. Do not silently add held-out maps to research
        training. Low activity can indicate a different liquidity regime, so improvement
        from broader data must be measured rather than assumed.
    
        Local pre-cutoff catalog: 2485 maps, 2324 with a market-cache file, 1152 currently
        in training. The other 1172 cached maps are only candidates; their usable rows,
        STRATZ validity, and exclusion causes were not individually audited.
    
        ## Historical train-lag 0 experiment
    
        `docs/experiments/lag-grid.md`, August 25, 432 common maps, older model/strategy:
    
        - train10 / execute10: $1025.89 before rebate.
        - train0 / execute10: $1087.98; paired gain $62.08, paired t-test p=0.76.
        - train0 / execute60: $229.73.
        - train10 / execute60: $209.57.
    
        Historical verdict was lack of reliable benefit from train0, **not** evidence
        that train0 was consistently worse. No comparable current train0 / execute25
        experiment was identified. That is an optional separate experiment: train0
        and train10 on the same data; evaluate both at execute25 and cadence 1s. Keep
        training-population changes separate so the cause of any improvement is clear.
    
        Execute25 is sufficient for comparing models under the same 25s scenario.
        Execute30 was suggested only as sensitivity to the approximate real PGL delay,
        not as a requirement that invalidates the user's execute25 comparison.
        ✓ • 0ms
    
    ## Activity
    
        $ cat .learnings/training-tape-exclusions-audit-20260919.md
        # Dota and LoL: training examples lost to the whole-map tape filter
    
        Read-only audit on 2026-09-19. No product code, dataset, model, backtest, or live
        service was changed. Analysis used the current local raw data and production
        prepare functions, with the trade-activity admission rule evaluated separately.
        Existing feature/price quality gates were retained.
    
        ## Findings: research training period only
    
        | Metric | Dota | LoL |
        |---|---:|---:|
        | Currently trained maps with labels | 1152 | 2139 |
        | Additional maps with valid labeled examples, failing only tape admission | 903 | 760 |
        | Additional labeled rows | 5757 | 256356 |
        | Possible training maps after row-quality-only admission | 2055 | 2899 |
        | Median usable rows per added map | 7 | 400 |
        | Full configured window present | 1 of 903 (11 minute slots including prehorn) | 77 of 760 (541 second slots) |
        | At least 90% of configured slots present | 211 (>=10 of 11) | 271 (>=487 of 541) |
        | First three 5-minute tape buckets pass; a later bucket fails | 229 | 116 |
    
        These are candidates for a controlled training experiment, not evidence that
        including every candidate improves prediction or live PnL. Some maps have only
        one usable example. Missing rows are not filled or synthesized.
    
        ## Method and exclusions retained
    
        Dota: load the current match catalog, keep only maps before
        `VALIDATION_START_TIME`, and subtract IDs already in the training parquet.
        There were 1333 excluded candidates. Of these, 161 had no market cache, 269
        produced no labeled training rows, and 903 produced usable rows while failing
        the current tape rule. `load_match_inputs` and `build_minute_rows(..., 10)`
        reconstructed the rows using the existing STRATZ validation, net-worth checks,
        current-price checks, and +300s label checks. No exceptions or unexpected
        tape-passing maps remained in this audit.
    
        LoL: current prepare audit has 760 maps in the research-training event period
        with reason `thin_telonex_trades` (the legacy reason name is retained even
        though the reader now uses onchain fills). Typed per-map cached builds retain
        rows before final tape exclusion. All 760 had already passed the source,
        spawn/time, feature, prior, and row-level book gates; each has at least one
        finite labeled row in seconds 0..540. All 760 still fail the current onchain
        tape rule. Audit event starts were reconstructed as the minimum known map
        start per event, preserving the existing series-level chronological split.
    
        LoL training-period exclusions that this proposal does NOT relax:
    
        - Missing prior: 295 maps.
        - Missing books: 42.
        - Zero labeled rows: 39.
        - Livestats invariant violation: 3.
        - Aborted feed, missing spawn, window/details mismatch, zero usable rows:
          one each.
    
        Validation-period maps are not proposed for research training. They remain on
        their original side of the chronological split. Backtest admission remains
        unchanged for the proposed A/B experiment.
    
        Current and future market midpoints use the existing two-sided quote,
        freshness (5s), and pair-consistency gates. The proposed change removes only
        the requirement for >=10 onchain records in EVERY full 300s bucket from spawn
        through map end as a prerequisite for supervised learning.
    
        ## Concrete examples
    
        **Dota, Team Falcons–BetBoom, May 29, 2026**, match `8830011623`,
        slug `dota2-flc-bb4-2026-05-29`:
    
        - All 11 configured minute examples (-60 through 540) have valid features,
          current prices, and +300s targets.
        - Tape counts by 5-minute bucket after spawn:
          `[680, 696, 498, 316, 530, 326, 0, 142]`.
        - The empty 30–35 minute bucket excludes the entire map, including its valid
          early training examples.
    
        **LoL, Team Heretics–Fnatic, February 7, 2026, map 1**, game
        `115548424308414203`:
    
        - All 541 seconds in 0..540 have valid labeled examples.
        - Tape buckets: `[304, 504, 302, 0, 0, 10, 0]`.
        - The first 15 minutes pass the tape threshold, but later buckets exclude the
          entire map. All training labels end by game second 840.
    
        Two further LoL examples with all 541 labeled seconds:
    
        - Verdant–WLGaming, March 9, map 2, `116130138006737519`:
          `[52, 14, 18, 32, 44, 66, 40, 8, 130]`; later bucket has 8 rather than 10.
        - Misa–S2G, February 19, map 2, `115729531998389598`:
          `[18, 16, 44, 38, 8, 28, 76, 88]`.
    
        For all three LoL examples, reran `build_one_map` directly from raw captures and
        books in memory (no cache writes): the full labeled rows exactly matched the
        cached rows; all source/price gates passed and the tape gate alone still failed.
        Other LoL candidates were inspected from the September 19 typed prepare cache,
        with tape independently recalculated; they were not all rebuilt from raw captures.
    
        ## Data-distribution caution
    
        Accepted vs candidate mean absolute +300s price change:
    
        - Dota: 8.49c vs 8.57c; exactly-flat labels 2.21% vs 3.53%.
        - LoL: 9.73c vs 8.37c; exactly-flat labels 2.22% vs 5.28%.
    
        The excluded LoL pool is somewhat quieter on these summaries. The trade filter
        can reflect both capture availability and real activity, so removing it may
        change the market population. These summaries do not establish price quality
        or predictive value beyond the existing prepare checks.
    
        ## Suggested experiment
    
        For training, keep each row only if existing game/time/orientation/prior/current
        mid/+300s-target checks pass; discard a whole map only if no usable training rows
        remain. Remove whole-map tape activity as a training prerequisite. Keep the
        current held-out maps, backtest data requirements, model recipe, and lags fixed.
        Evaluate Dota and LoL independently. Do not change lag and training admission in
        the same comparison.
    
        ## Reproduction
    
        - Script: `/private/tmp/audit_training_tape_exclusions_20260919.py`.
        - Log: `/private/tmp/training-tape-audit-20260919.log`.
        - Per-map audit tables and summaries:
          `/private/tmp/training-tape-audit-20260919/{dota,lol}_excluded.parquet`,
          `{dota,lol}_summary.json`.
        - Fresh LoL checks: `lol_fresh_checks.json` in the same directory.
        - Input hashes recorded in `input_hashes.json`; Dota training parquet, LoL
          training parquet, and LoL prepare audit stayed unchanged throughout the scan.
        ✓ • 0ms
    
    ## Activity
    
        $ rg -n 'def load_lol_selection|split.*validation|eligible' src/backtest/lol* src/backtest/selection.py | head -90
        src/backtest/selection.py:50:    eligible: int
        src/backtest/selection.py:61:    validation = split[split["split"] == "validation"].sort_values(
        src/backtest/selection.py:78:    validation_split = load_validation_split()
        src/backtest/selection.py:113:    eligible: list[int] = []
        src/backtest/selection.py:129:        eligible.append(match_id)
        src/backtest/selection.py:138:        eligible=len(eligible),
        src/backtest/selection.py:140:    return coverage, tuple(eligible)
        src/backtest/lol_inputs.py:85:    ineligible = audit.loc[~audit["eligible"].astype(bool), "reason"]
        src/backtest/lol_inputs.py:86:    without_signal_rows = int((ineligible == REASON_ZERO_SIGNAL_ROWS).sum())
        src/backtest/lol_inputs.py:87:    without_local_telonex = int((ineligible == REASON_MISSING_REQUIRED_CHANNEL).sum())
        src/backtest/lol_inputs.py:95:        eligible=int(audit["eligible"].astype(bool).sum()),
        src/backtest/lol_inputs.py:102:    """Whitelist-filtered eligible maps with horn >= the anchor map's horn.
        src/backtest/lol_inputs.py:112:    eligible = audit[audit["eligible"].astype(bool)]["match_id"]
        src/backtest/lol_inputs.py:115:        for match_id in eligible.tolist()
        src/backtest/lol_inputs.py:119:        raise ValueError(f"no eligible matches at/after --since-match {since_match_id}")
        src/backtest/lol_inputs.py:183:    """Build one MarketContext from an eligible backtest audit row."""
        src/backtest/lol_inputs.py:192:        raise ValueError(f"match {match_id}: eligible audit is missing replay timestamps")
        src/backtest/lol_inputs.py:262:def load_lol_selection(
        src/backtest/lol_inputs.py:268:    """Read LoL prepare artifacts locally and pick eligible maps. No network.
        src/backtest/lol_inputs.py:284:        eligible = (not rows.empty) and bool(rows["eligible"].iloc[0])
        src/backtest/lol_inputs.py:285:        if not eligible or selected_id not in signal_match_ids:
        src/backtest/lol_inputs.py:286:            raise ValueError(f"LoL match {selected_id} is not an eligible backtest card")
        src/backtest/lol_inputs.py:290:        eligible_audit = audit[audit["eligible"].astype(bool)].sort_values(
        src/backtest/lol_inputs.py:293:        ordered = tuple(int(value) for value in eligible_audit["match_id"])
        ✓ • 215ms
    
    ## Assistant
    
    Да, ты правильно помнишь прежнюю схему. Проверил код: фильтр ленты сначала отбирает карты, затем они делятся по времени на train и validation. Early stopping использует всю validation; бэктест запускает research-модель на картах validation; production обучается на картах train и validation. В Dota для production строятся отдельные минутные строки тех же validation-карт. Это видно в [подготовке Dota](/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader/src/prepare_dataset/prepare_dataset.py:76), [обучении](/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader/src/train_model/train_model.py:176) и [разбиении LoL](/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader/src/lol/05_prepare_dataset.py:645).
    
    В [новом плане](/Users/dimabytes/.cursor/plans/Look-ahead%20validation%20filter-d981831f.plan.md) early stopping всё-таки берёт **подмножество validation**, а не какие-то посторонние карты. Но твоё возражение остаётся: слово «validation» начнёт означать разные наборы карт для бэктеста, early stopping и production. Это усложняет схему.
    
    Я бы выбрал один из двух простых вариантов:
    
    1. **Если цель — исправить смещение валидации:** оставить фильтр для train, убрать его для validation и дальше сохранить старое правило: *вся validation идёт и в early stopping, и в production, и в бэктест*. Это моя рекомендация. Придётся принять, что при следующем обучении состав production и выбранное число деревьев могут измениться. Это честное следствие расширения validation; зафиксировать старую модель можно, просто пока не переобучая её.
    
    2. **Если сейчас принципиально нельзя менять состав обучения модели:** оставить текущие train, validation и production как есть, а тонкие карты добавить в отдельный набор **только для диагностического бэктеста** текущей research-модели. Тогда прежнее значение validation сохраняется, а расширенный бэктест явно называется отдельной проверкой.
    
    Комбинацию из плана — расширить validation, но исключить её часть из early stopping и production — я бы не брал без жёсткого требования сохранить прежнюю обучающую выборку *при будущем переобучении*. Именно это требование порождает `production_validation`, `tape_dense` и отдельный holdout.
    
    Ещё важное уточнение: бэктест сейчас использует **research-модель**, а не production-модель, которая обучена на validation. Поэтому само попадание validation-карт в production не делает этот бэктест обучением на тех же картах. Но validation уже влияет на early stopping и многократно используется для выбора стратегии, так что считать результаты бэктеста полностью независимой проверкой тоже нельзя.
    
    ## User
    
    /Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace/.analysis/ladder-thin-2026-09-24/REPORT.md Нет, так здесь же проблема не в этом. Проблема в том, что, в принципе, это правило. То есть я хотел это правило, чтобы оно мне исключало карты, которые вредны, которые токсичны и неправильны для обучения. Потому что они мертвые.
    
    То есть там нет торгов, и там он, допустим, берет на пятой минуте цену, там, не знаю, 50%, а потом смотрит через пять минут, там, не знаю, 100%. Там какая-то херовая карта, сломанная. Я хотел такие карты исключить. И поэтому я хотел их исключить от стрейна.
    
    Просто если сейчас мы оставим их в validation, тогда у нас они, получается, попадут в production. И тогда это тупо будет, что у нас в production все равно эти отравленные карты будут. Идея была в этом. Или ты думаешь, что эти карты, на самом деле, не отравленные.
    
    Или какой-то есть способ. Просто другой вариант это как-то добавить какой-то фильтр, который бы их отовсюду убрал. Просто до этого я убрал их отовсюду, кроме лайва, получается. А в лайве у меня такие карты все еще мы торговали.
    
    И это неправильно. То есть нужно, видимо, какой-то добавить, может, фильтр придумать, который бы их убрал отовсюду. И из бэктеста, и из лайва, и из обучения отовсюду. И он не был бы look ahead. В этом получается смысл.
    
    Какой такой вариант есть? Это было все из этого репорта изначально.
    
    ## Assistant
    
    Понял, я неверно определил цель фильтра. Он нужен, чтобы не допускать подозрительные карты к обучению и торговле, а не только чтобы исправить выборку бэктеста. Прочитаю отчёт и проверю, какие признаки плохой карты доступны в конкретный момент и в replay, и в live. Для выбора правила использую brainstorming skill.
    
    ## Activity
    
        $ cat /Users/dimabytes/.agents/skills/brainstorming/SKILL.md
        ---
        name: brainstorming
        description: "You MUST use this before any creative work - creating features, building components, adding functionality, or modifying behavior. Explores user intent, requirements and design before implementation."
        disable-model-invocation: true
        ---
    
        # Brainstorming Ideas Into Designs
    
        Help turn ideas into fully formed designs and specs through natural collaborative dialogue.
    
        Start by classifying how much process the request needs, then work
        through your path: understand the context, refine the idea, present a
        design, and get your human partner's approval.
    
        <HARD-GATE>
        Do NOT invoke any implementation skill, write any code, scaffold any
        project, or take any implementation action until you have told your
        human partner what you intend and they have approved it. This applies
        to EVERY task on EVERY path below — the ceremony scales with the task;
        the approval gate never does.
        </HARD-GATE>
    
        ## Three Paths
    
        Before your first question, classify the request and say the
        classification out loud — "this looks bounded, so I'll present a short
        design here rather than write a spec" — so your human partner can
        override it:
    
        - **Spike** — a feasibility question ("can we...", "is it possible...",
          "quick and dirty is fine") whose output is an answer, not code you
          keep. Present the question and what you'll try in 2-3 sentences, get
          a nod, then find out as cheaply as correctness allows. No design
          doc, no spec file. Report findings as a recommendation; anything you
          built stays labeled throwaway.
        - **Bounded** — a well-scoped change to code that already exists in
          this repo: a new flag, a small endpoint, a one-file fix.
          Understanding the kind of app is not enough — bounded means the flow
          you are changing is already here to read. If there is no existing
          flow to change, the task is not bounded. Ask the clarifying
          questions that matter, present a short design IN CHAT (a few
          sentences to a few short paragraphs), and STOP. Implementation
          starts only after your human partner says yes to that design — a
          bounded task's approval is as hard a gate as an architectural
          one. No spec file, no implementation plan document.
        - **Architectural** — new projects, new subsystems, changes that
          restructure how components fit together or alter interfaces others
          depend on. Follow the full process: questions, approaches, sectioned
          design, written spec.
    
        When in doubt between two paths, take the heavier one. The ratchet is
        one-way: hidden complexity discovered mid-task upgrades the path —
        stop, say so, and step up. Nothing downgrades mid-task.
    
        ## Anti-Pattern: "Too Simple To Need Approval"
    
        Every path ends with your human partner approving your intent before
        implementation. A todo list, a single-function utility, a config
        change — the design may be two sentences in chat, but you MUST present
        it and get approval. "Simple" tasks are where unexamined assumptions
        cause the most wasted work. What scales with simplicity is the
        artifact, never the approval.
    
        ## Red Flags
    
        | Thought | Reality |
        |---------|---------|
        | "This is too simple to need a design" | Simple means a short design, not no design. Two sentences in chat, then approval. |
        | "I'll call it bounded and skip the spec" | Reaching for a label to skip work IS the doubt — take the heavier path. |
        | "It's bounded and the design is obvious — I'll start while they read it" | The gate is the approval, not the design's length. Present, then stop until you hear yes. |
        | "I understand this kind of app, so it's bounded" | Bounded measures the repo, not your familiarity. A new project has no existing flow — it is architectural. |
        | "The spike works, so I'll keep the code" | A spike's output is an answer. Keeping the code is a new request — classify it. |
        | "It grew, but I'm almost done — no need to re-classify" | Hidden complexity upgrades the path mid-task. Stop and say so. |
        | "They approved the spike, so the follow-up change is approved too" | Each task gets its own classification and its own approval. |
    
        ## Checklist
    
        Classify first, announce the path, then create a task for each item on
        your path and complete them in order.
    
        **Spike:**
        1. **Explore project context** — enough to frame the probe
        2. **Present question + probe plan** — 2-3 sentences
        3. **Get approval** — a nod is enough
        4. **Investigate** — as cheaply as correctness allows
        5. **Report findings** — a recommendation; label anything built as throwaway
    
        **Bounded:**
        1. **Explore project context** — check files, docs, recent commits
        2. **Ask clarifying questions** — one at a time, the ones that matter
        3. **Present short design in chat** — approach, files touched, testing
        4. **Get approval** — STOP and wait for an explicit yes; presenting the design and starting in the same breath is skipping the gate
        5. **Implement** — proceed with the normal development workflow (TDD applies); no plan document
    
        **Architectural:**
        1. **Explore project context** — check files, docs, recent commits
        2. **Offer the visual companion just-in-time** — NOT upfront. The first time a question would genuinely be clearer shown than described, offer it then (its own message); on approval its browser tab opens for you. If no visual question ever arises, never offer it. See the Visual Companion section below.
        3. **Ask clarifying questions** — one at a time, understand purpose/constraints/success criteria
        4. **Propose 2-3 approaches** — with trade-offs and your recommendation
        5. **Present design** — in sections scaled to their complexity, get user approval after each section
        6. **Write design doc** — save to `docs/superpowers/specs/YYYY-MM-DD-<topic>-design.md` and commit
        7. **Spec self-review** — quick inline check for placeholders, contradictions, ambiguity, scope (see below)
        8. **User reviews written spec** — ask user to review the spec file before proceeding
        9. **Transition to implementation** — create implementation plan
    
        ## Process Flow
    
        ```dot
        digraph brainstorming {
            "Classify: spike / bounded / architectural" [shape=diamond];
            "Present question + probe (2-3 sentences)" [shape=box];
            "Ask clarifying questions (bounded)" [shape=box];
            "Present short design in chat" [shape=box];
            "Human approves?" [shape=diamond];
            "Investigate; report recommendation" [shape=doublecircle];
            "Implement via normal workflow (no plan doc)" [shape=doublecircle];
            "Explore project context" [shape=box];
            "Ask clarifying questions" [shape=box];
            "Propose 2-3 approaches" [shape=box];
            "Present design sections" [shape=box];
            "User approves design?" [shape=diamond];
            "Write design doc" [shape=box];
            "Spec self-review\n(fix inline)" [shape=box];
            "User reviews spec?" [shape=diamond];
            "Hidden complexity? Upgrade path" [shape=box];
    
            "Classify: spike / bounded / architectural" -> "Present question + probe (2-3 sentences)" [label="spike"];
            "Classify: spike / bounded / architectural" -> "Ask clarifying questions (bounded)" [label="bounded"];
            "Classify: spike / bounded / architectural" -> "Explore project context" [label="architectural"];
            "Present question + probe (2-3 sentences)" -> "Human approves?";
            "Ask clarifying questions (bounded)" -> "Present short design in chat";
            "Present short design in chat" -> "Human approves?";
            "Human approves?" -> "Investigate; report recommendation" [label="spike: yes"];
            "Human approves?" -> "Implement via normal workflow (no plan doc)" [label="bounded: yes"];
            "Hidden complexity? Upgrade path" -> "Classify: spike / bounded / architectural";
            "Explore project context" -> "Ask clarifying questions";
            "Ask clarifying questions" -> "Propose 2-3 approaches";
            "Propose 2-3 approaches" -> "Present design sections";
            "Present design sections" -> "User approves design?";
            "User approves design?" -> "Present design sections" [label="no, revise"];
            "User approves design?" -> "Write design doc" [label="yes"];
            "Write design doc" -> "Spec self-review\n(fix inline)";
            "Spec self-review\n(fix inline)" -> "User reviews spec?";
            "User reviews spec?" -> "Write design doc" [label="changes requested"];
        }
        ```
    
        ## The Process
    
        The subsections below serve the bounded and architectural paths (a
        spike stops at "present the probe, get a nod"). Sections from
        **Exploring approaches** onward are architectural-path depth — for
        bounded work, context plus a few questions plus a short in-chat design
        is the whole process.
    
        **Understanding the idea:**
    
        - Check out the current project state first (files, docs, recent commits)
        - Before asking detailed questions, assess scope: if the request describes multiple independent subsystems (e.g., "build a platform with chat, file storage, billing, and analytics"), flag this immediately. Don't spend questions refining details of a project that needs to be decomposed first.
        - If the project is too large for a single spec, help the user decompose into sub-projects: what are the independent pieces, how do they relate, what order should they be built? Then brainstorm the first sub-project through the normal design flow. Each sub-project gets its own spec → plan → implementation cycle.
        - For appropriately-scoped projects, ask questions one at a time to refine the idea
        - Prefer multiple choice questions when possible, but open-ended is fine too
        - Only one question per message - if a topic needs more exploration, break it into multiple questions
        - Focus on understanding: purpose, constraints, success criteria
    
        **Exploring approaches:**
    
        - Propose 2-3 different approaches with trade-offs
        - Present options conversationally with your recommendation and reasoning
        - Lead with your recommended option and explain why
        - YAGNI ruthlessly - remove unnecessary features from every approach and design
    
        **Presenting the design:**
    
        - Once you believe you understand what you're building, present the design
        - Scale each section to its complexity: a few sentences if straightforward, up to 200-300 words if nuanced
        - Ask after each section whether it looks right so far
        - Cover: architecture, components, data flow, error handling, testing
        - Be ready to go back and clarify if something doesn't make sense
    
        **Design for isolation and clarity:**
    
        - Break the system into smaller units that each have one clear purpose, communicate through well-defined interfaces, and can be understood and tested independently
        - For each unit, you should be able to answer: what does it do, how do you use it, and what does it depend on?
        - Can someone understand what a unit does without reading its internals? Can you change the internals without breaking consumers? If not, the boundaries need work.
        - Smaller, well-bounded units are also easier for you to work with - you reason better about code you can hold in context at once, and your edits are more reliable when files are focused. When a file grows large, that's often a signal that it's doing too much.
    
        **Working in existing codebases:**
    
        - Explore the current structure before proposing changes. Follow existing patterns.
        - Where existing code has problems that affect the work (e.g., a file that's grown too large, unclear boundaries, tangled responsibilities), include targeted improvements as part of the design - the way a good developer improves code they're working in.
        - Don't propose unrelated refactoring. Stay focused on what serves the current goal.
    
        ## After the Design (architectural path)
    
        **Documentation:**
    
        - Write the validated design (spec) to `docs/superpowers/specs/YYYY-MM-DD-<topic>-design.md`
          - (User preferences for spec location override this default)
        - Use elements-of-style:writing-clearly-and-concisely skill if available
        - Commit the design document to git
    
        **Spec Self-Review:**
        After writing the spec document, look at it with fresh eyes:
    
        1. **Placeholder scan:** Any "TBD", "TODO", incomplete sections, or vague requirements? Fix them.
        2. **Internal consistency:** Do any sections contradict each other? Does the architecture match the feature descriptions?
        3. **Scope check:** Is this focused enough for a single implementation plan, or does it need decomposition?
        4. **Ambiguity check:** Could any requirement be interpreted two different ways? If so, pick one and make it explicit.
    
        Fix any issues inline. No need to re-review — just fix and move on.
    
        **User Review Gate:**
        After the spec review loop passes, ask the user to review the written spec before proceeding:
    
        > "Spec written and committed to `<path>`. Please review it and let me know if you want to make any changes before we start writing out the implementation plan."
    
        Wait for the user's response. If they request changes, make them and re-run the spec review loop. Only proceed once the user approves.
    
        **Implementation:**
    
        - Create a detailed implementation plan
    
        ## Visual Companion
    
        A browser-based companion for showing mockups, diagrams, and visual options during brainstorming. Available as a tool — not a mode. Accepting the companion means it's available for questions that benefit from visual treatment; it does NOT mean every question goes through the browser.
    
        **Offering the companion (just-in-time):** Do NOT offer it upfront. Wait until a question would genuinely be clearer shown than told — a real mockup / layout / diagram question, not merely a UI *topic*. The first time that happens, offer it then, as its own message:
        > "This next part might be easier if I show you — I can put together mockups, diagrams, and comparisons in a browser tab as we go. It's still new and can be token-intensive. Want me to? I'll open it for you."
    
        **This offer MUST be its own message.** Only the offer — no clarifying question, summary, or other content. Wait for the user's response. If they accept, start the server with `--open` so their browser opens to the first screen automatically. If they decline, continue text-only and don't offer again unless they raise it.
    
        **Per-question decision:** Even after the user accepts, decide FOR EACH QUESTION whether to use the browser or the terminal. The test: **would the user understand this better by seeing it than reading it?**
    
        - **Use the browser** for content that IS visual — mockups, wireframes, layout comparisons, architecture diagrams, side-by-side visual designs
        - **Use the terminal** for content that is text — requirements questions, conceptual choices, tradeoff lists, A/B/C/D text options, scope decisions
    
        A question about a UI topic is not automatically a visual question. "What does personality mean in this context?" is a conceptual question — use the terminal. "Which wizard layout works better?" is a visual question — use the browser.
    
        If they agree to the companion, read the detailed guide before proceeding:
        `skills/brainstorming/visual-companion.md`
        ✓ • 0ms
    
    ## Activity
    
        $ cat .analysis/ladder-thin-2026-09-24/REPORT.md
        # Одинокая лесенка и тонкие рынки — итоговый отчёт
    
        Дата: 2026-09-24. 10 агентов через herdr: 6 × devin SWE-2 Max, 2 × codex GPT-6 Luna max,
        2 × cursor Grok 4.7 High. Данные: 612 лайв-архивов (`esports-trader/data/trader`, 2026-08-30 … 09-24),
        полные книги и принты Telonex, бэктест-каталоги. Код не менялся, бэктесты не запускались.
        Ключевые цифры я перепроверил сам: `notes/orchestrator-checks.md`.
    
        ## 1. Главное
    
        1. **Проблема 1 подтверждена, причина найдена в коде.** Лайв берёт best bid из книги вместе с нашими
           заявками (`src/trader/session_core.py:222-255`). Когда все уходят, лучшим бидом становится наша
           заявка. `join_buy_price` возвращает нашу же цену, и лестница стоит. Бэктест видит книгу без нас и
           в той же ситуации переставляется вниз. Бэктест здесь оптимистичнее лайва.
        2. **Это частое состояние.** Наша BUY стоит одна на своём центе 17.9% времени (N=10 329 заявок).
           В Dota 18866 — 31.5%, в LoL — 9.1%. Большинство эпизодов короткие (медиана 0.07 с). Но после ухода
           других 24.4% заявок стоят ещё ≥5 с. Филлы L0 «в одиночестве» в минусе: −$95 на 222 филлах.
        3. **Лучшее правило для проблемы 1:** считать книгу без наших заявок (паритет с бэктестом) и снимать
           L0, если на его цене никого, кроме нас, нет ≥3 с. После снятия ждать следующего сигнала.
           Правило срабатывает на 12.9% ступеней L0. Оно почти не трогает ликвидные рынки. Хвост режет:
           −$124…−$173 убыточных филлов на 14 худших картах. Отдельный фильтр против моргания не нужен:
           66–79% уровней на лучшем биде живут < 1 с, и ни один фильтр не выигрывает во всех лигах.
        4. **Проблема 2 в твоей формулировке не подтвердилась.** Dota 18866 в плюсе: +$188 на 49 картах.
           Самая тонкая лига 18865 тоже в плюсе. «Купили — не продать» тоже не подтвердилось: продажа
           филлится за ~5 с, до сеттлмента доживает 0–2% объёма.
        5. **Настоящая дыра — младшие лиги LoL:** −$407 на 126 картах, 95% CI [−667, −168]. Там дело не в
           ликвидности. В LoL цена падает после нашей покупки (дрейф до +300 с по всему LoL: −$512). Внутри
           младших лиг ни одна метрика объёма или книги не отделяет убыточные карты. Это вопрос модели LoL,
           а не сайзинга.
        6. **Объём на входе плохо ловит плохие карты. Спред на входе ловит лучше.** Спред ≥4 тика на входе:
           −$115 на 80 картах. Спред ≤2: +$564 на 289 картах. Гейт 8 → 6 тиков — дешёвый шаг для проверки.
        7. **Динамический сайзинг не нужен.** Ограничение размера по глубине уровня режет >50% заявок на
           ликвидных рынках. Это та же ошибка, что у K1 в сентябре. Размер «от объёма» упирается в
           `MIN_ORDER_SIZE = 5` и превращается в skip. Если гейт нужен — это skip, а не size-down.
        8. **Look-ahead фильтр можно убрать из бэктеста.** Лайв-карты, которые он режет, не теряли:
           +$41 на 50 торгованных картах; в 18866 23 карты из 80, +$71. В старых бэктестах такие карты тоже
           не хуже остальных. В валидацию добавится 113 карт Dota (из 711) и 389 карт LoL (из 2 075).
           Есть скрытая утечка в production training, её надо закрыть (§4.2).
        9. **Главный риск для всех решений: бэктест сильно оптимистичен на тонких книгах.** Реплеи лайв-карт
           на тех же картах: Dota лайв +$42 против реплея +$299; 18866 +$6 против +$137; LoL −$178 против
           +$465. Пока этот разрыв не закрыт, бэктест неправильно оценит любой гейт для тонких рынков.
           Часть разрыва — проблема 1. Поэтому проблему 1 надо чинить первой.
    
        ## 2. Проблема 1: лесенка остаётся одна
    
        ### 2.1 Причина в коде (проверено)
    
        Источник: `reports/selfjoin-grok.md`, мои проверки.
    
        - Лайв: `books_from_md` копирует `best_bid` / `best_bid_size` из WS-книги. Наши заявки из неё не
          вычитаются нигде (`host_resources.py:90-94` не передаёт ордера).
        - Бэктест: `_snapshot_books` (`src/backtest/strategy.py:1031-1060`) берёт книгу Nautilus, в ней нет
          наших симулированных заявок. Для реплеев лайва `strip_own_book.py` вычитает наши заявки из снапшотов.
        - Цены ступенек пересчитываются только при `follow_book = has_buy_fill or synced.rebuilt`
          (`quoting.py:757`): после филла в эпизоде или на новом сигнале. Между сигналами до первого филла
          ступенька не двигается ни в лайве, ни в бэктесте.
        - На новом сигнале лайв делает `join_buy_price(наш бид)` → та же цена → `keep`. Бэктест делает
          `join_buy_price(бид других)` → лестница ниже → старая заявка отменяется.
        - `wide_spread` и `mid_spike` в лайве тоже видят наш бид. Спред выглядит узким, mid не падает.
          Эти гейты не срабатывают, когда все ушли. В бэктесте срабатывают.
        - `_live_matches` сравнивает BUY только по цене (`quoting.py:512-522`). Уменьшить количество в
          таргете нельзя. Уйти со ступеньки можно только выбросив её таргет.
    
        Пример: `grid-3010621-m1` (league 18866), заявка `c41` по 0.38. 52 обновления книги, 52 `keep`,
        0 отмен за 17.9 с. `bid_size` равен нашему остатку.
    
        **Фикс «против моргания» 3 недели назад.** Ты его не помнил, агент нашёл. Это три изменения:
        `7b0d05d6` (2026-09-02, SELL не отменяется, когда ask моргнул ниже fair), `54cf56cd`
        (2026-09-02, SELL держится 1 с), защёлка цен BUY в Follow300 (2026-09-06). До первого филла лестница
        не бежит за книгой. После первого филла бежит. Если бид моргнул вверх, лестница встаёт на него.
        Моргнувший уходит — и self-join держит нас на этой цене одних.
    
        ### 2.2 Как часто и сколько стоит (данные)
    
        Источник: `reports/lonely-devin.md` (полные книги минус наши заявки), `reports/leave-devin.md`.
    
        | группа | заявок | время одни на своём центе | доля ≥65% уровня |
        |---|---:|---:|---:|
        | все | 10 329 | 17.9% | 27.9% |
        | Dota | 6 195 | 23.9% | 35.6% |
        | Dota 18866 | 1 978 | 31.5% | 45.8% |
        | Dota 18865 | 274 | 36.0% | 58.1% |
        | Dota 19944 | 1 069 | 16.8% | 32.2% |
        | Dota 20279 (Oddin) | 1 908 | 10.6% | 15.1% |
        | LoL | 4 134 | 9.1% | 16.7% |
    
        - Длина эпизода одиночества: медиана 0.07 с, p95 самого длинного эпизода заявки 8.4 с.
          У 8.1% заявок есть эпизод ≥5 с.
        - После ухода других заявка живёт в медиане ещё 0.21 с. Но 24.4% живут ≥5 с, 13.6% ≥10 с.
          70% эпизодов заканчиваются только вместе с заявкой: компания почти не возвращается.
        - Филлы, взятые в одиночестве (за 2 с до филла никого): +$0.45 на $100 против +$1.35 в компании.
          L0 в одиночестве: −$95 на 222 филлах. L0 в компании: +$250 на 649.
        - Уход других — плохой знак в LoL и при свипе. LoL: mid −0.41¢ через 60 с и −1.39¢ через 300 с
          против +1.71¢ в компании. Уход со свипом: −0.37¢ через 60 с, без свипа +0.12¢.
        - В Dota одинокая нижняя ступенька часто безвредна. Книга ушла вверх, ступенька осталась ниже.
          Пример: `grid-3007263-m1`, c19 стояла одна на 0.56 61.7 с при биде 0.57–0.58 (я проверил по книге).
    
        ### 2.3 Моргающие уровни
    
        Источник: `reports/blink-luna.md` (209 карт, 4 842 принятые BUY, 270 187 эпизодов уровней).
    
        | группа | жизнь лучшего бида p50 | < 1 с | наш уровень пропал ≤1 с | после этого мы одни на топе | филл: моргнувший / стабильный уровень |
        |---|---:|---:|---:|---:|---:|
        | Dota 18866 | 0.157 с | 78.7% | 39.4% | 4.1% | 17.2% / 4.6% |
        | Dota 19944 | 0.177 с | 76.1% | 34.1% | 2.3% | 22.4% / 4.9% |
        | Dota 20279 | 0.300 с | 66.4% | 15.5% | 14.0% | 26.8% / 3.9% |
        | LoL | 0.146 с | 77.3% | 11.0% | 19.1% | 29.8% / 4.9% |
    
        - Моргание — норма для этих книг. Внешняя практика объясняет почему: отмены на Polymarket
          бесплатные, reward-боты переставляются к mid на каждое его движение (`reports/web-devin.md`).
        - Моргнувшие уровни меньше стабильных: 40 против 48 шаров в 18866, 40 против 60 в LoL.
        - Встать на моргнувший уровень не так плохо, как кажется. Такие заявки филлятся в 4–6 раз чаще.
          Markout у них смешанный: в одних группах лучше, в других хуже.
        - Ни один фильтр не выигрывает во всех группах. Возраст уровня ≥0.5 с меняет 11.7–33.2% заявок.
          Мин. 5 чужих шаров — 6.7–23.8%. «Стабильный бид» за 2 с — 37–48%.
        - Мы сами переставляемся нечасто: 457 пар «отмена + новая заявка» за окно торговли на 209 картах,
          в основном L2 в 18866 и 19944 (0.15 пары на карту в минуту).
        - В книгах есть дыры: у каждого потока токена есть разрыв >1 с, максимум 72–115 с. Точное время
          жизни уровня эти данные не дают.
    
        **Вывод:** отдельный фильтр против моргания сейчас не нужен. Опасный случай — «встали на моргнувший
        уровень, он ушёл, мы остались одни». Его закрывают фиксы A и B из §2.5. Если фильтр всё же нужен,
        первым в бэктест брать «мин. 5 чужих шаров на цене входа»: он меняет меньше всего заявок.
    
        ### 2.4 Варианты решения
    
        Источник: `reports/leave-devin.md` (контрфактика на 326 лайв-картах), мой разрез по ступенькам.
        «Убрано» — markout +300 с у филлов, которых правило избежало бы. Минус — правило экономит деньги.
    
        | вариант | что делает | убрано (все карты) | убрано на 14 худших картах | срабатывает | цена |
        |---|---|---:|---:|---|---|
        | R1: книга без наших заявок | цена входа, спред и mid_spike считаются по чужой книге | −$15.6 | −$147.5 | 20 филлов | 0 трафика, паритет с бэктестом |
        | R4_3s, все ступеньки | снять ступеньку, если на её цене никого нет ≥3 с | −$19.8 | −$173.3 | 5.5% LoL … 23.6% 18866 | +5.8% трафика |
        | **R4_3s, только L0** | то же, только верхняя ступенька | **−$81.6** | **−$123.6** | 9.1% LoL, 8.3% Oddin, 20% 18866 | меньше, чем у варианта «все» |
        | R4_1s | порог 1 с | −$7.1 | −$174.7 | чаще | +8.6% трафика |
        | R4_10s | порог 10 с | +$10.8 | — | реже | начинает резать хорошие филлы |
        | R2_1.0 | снять сразу, как только одни | −$29.2 | −$221.8 | 35% LoL, 64% 18866 | режет 21% всего объёма |
        | R2_0.65 / 0.8 (твой план) | снять, если мы ≥65–80% уровня | −$149 / −$130 | −$293 (0.8) | вне книги 40–80% времени в тонких лигах, ~40% в LoL | это смена стратегии |
        | R3: мин. компания | ставить, только если других ≥ K × нас | до −$153 | — | 53% объёма при K=2 | доминируется |
    
        При задержке отмены 0.3 с (ближе к реальности, чем 2 с) R4_3s на L0 даёт −$128. Нижние ступеньки
        L1/L2 в одиночестве в сумме давали хорошие филлы: +$62 при задержке 2 с. Поэтому я выделил вариант
        «только L0». Выборка маленькая (57 убранных филлов L0). Выбор между «только L0» и «все ступеньки»
        должен сделать бэктест.
    
        После снятия:
        - Возвращаться на ту же цену плохо: такие повторные филлы дали −$11.2 (15 филлов, 672 шары).
        - Сразу догонять следующий уровень почти ничего не даёт: +$5 при утроенном трафике.
        - Лучше всего ждать следующего сигнала.
    
        ### 2.5 Рекомендация
    
        1. **A. Книга без наших заявок в лайв-адаптере.** В `books_from_md` вычитать остаток наших
           BUY/SELL по центам из `OrderBook` до расчёта `bid`, `ask`, размеров. Передать
           `core.state.orders` из `host_resources.py:90`. Ядро и бэктест не трогать: иначе бэктест вычтет
           второй раз. Кодек менять не надо. Старые трейсы проигрываются как раньше. Вычитать обе стороны:
           Nautilus не держит в книге ни наши BUY, ни наши SELL.
           Не проверено: другие читатели сырой книги в лайве тоже видят наши заявки. Главный из них —
           `read_raw_pair` (`session_quoting.py:48-66`), он даёт mid для сигнала модели. Когда мы одни на
           лучшем биде, этот mid завышен нашей заявкой. Это отдельный вопрос паритета, его стоит проверить.
        2. **B. Снятие одинокой L0.** Если чужой размер на цене L0 равен 0 уже ≥ T секунд (старт T = 3),
           выбросить таргет в `_open_buy_targets` после `_reject_buy` и до блока занятости
           (`quoting.py:305-318`). Проверка работает и при `follow_book = False`, по цене стоящей заявки.
           Нужны: чужая глубина на цене ступеньки в `TokenBook` (оба адаптера), таймер «одни с момента»
           и wake на момент истечения T. Новая причина `lonely`. Рубильник по умолчанию выключен.
        3. **После снятия ждать нового сигнала.** Не ставить ту же цену снова в этом эпизоде.
        4. **Не делать:** снятие при первом нуле (дребезг), share cap 0.5 при подаче (это K1, он проиграл),
           возврат на ту же цену, автодогонялку.
    
        ## 3. Проблема 2: тонкие рынки
    
        ### 3.1 Что показал лайв
    
        Источник: `reports/thin-live-devin.md`, `reports/exit-devin.md`. PnL = realized + unrealized + rebate.
    
        | группа | карт с филлами | PnL | худшая карта |
        |---|---:|---:|---:|
        | вся Dota | 203 | +$1 034.6 | −$94.7 |
        | Dota 18866 (ты считал тонкой) | 49 | +$187.9 | −$94.7 |
        | Dota 18865 (по объёму самая тонкая) | 13 | +$66.7 | −$3.9 |
        | Dota 19944 | 72 | +$369.8 | −$78.9 |
        | Dota 20279 (Oddin) | 45 | +$354.2 | −$21.0 |
        | весь LoL | 210 | −$395.1 | −$65.7 |
        | младшие лиги LoL | 126 | −$406.7 | −$65.7 |
        | LCK + LCS | 47 | +$35.3 | |
    
        - В 18866 проблема в хвосте, а не в среднем. Хвост 18866 — это входы при спреде 6–7 тиков
          (−$101 на 5 картах) и выход с опозданием: ask падает на 3–4 тика после горизонта модели 300 с.
        - В LoL потери идут со стороны входа. Дрейф цены после покупки до +300 с по всему LoL: −$512
          (выход при этом даёт +$206). Внутри младших лиг ни объём, ни спред, ни глубина не отделяют
          убыточные карты (Spearman ρ ≈ 0).
        - После 2026-09-16 вайтлист LoL в лайве работает. Но в нём остались младшие лиги, и они продолжают
          терять с той же скоростью: −$50.8 на 18 картах, −5.7% от объёма покупок (до 09-16 было −5.3%).
    
        ### 3.2 «Купили — и не продать»
    
        Не подтвердилось (`reports/exit-devin.md`):
        - Продажа филлится в медиане за ~5 с на всех группах.
        - До сеттлмента доживает 0–2% купленного объёма.
        - Мы действительно крупные на тонких картах: наша покупка — 2.7% пре-хорн объёма за 30 мин в 18866
          против 0.7% на ликвидной Dota. Но потеря не от того, что «не продать», а от того, что продаём
          после того, как цена уже ушла.
    
        ### 3.3 Что ловит плохие карты на входе
    
        | гейт на входе | карт убрано | их PnL | PnL оставшихся | худшая оставшаяся |
        |---|---:|---:|---:|---:|
        | нет | 0 | 0 | +$639.5 | −$94.7 |
        | спред ≥ 8 (сейчас) | 20 | −$25.4 | +$664.9 | −$94.7 |
        | спред ≥ 6 | 39 | −$106.8 | +$781.4 | −$78.9 |
        | спред ≥ 4 | 80 | −$114.9 | +$789.5 | −$78.9 |
        | пре-хорн объём 30 мин < $1000 | 82 | −$91.6 | +$731.1 | −$94.7 |
        | пре-хорн объём 30 мин < $3000 | 147 | −$130.4 | +$769.9 | −$78.9 |
        | младшие лиги LoL | 126 | −$406.7 | +$1 046.2 | −$94.7 |
        | look-ahead фильтр не прошёл | 50 | **+$40.9** | +$598.6 | −$94.7 |
    
        - Объёмный гейт в основном убирает карты младших лиг LoL. На Dota он выкидывает безубыточные карты
          (в 18866 при пороге $1000: 8 карт, +$9.8).
        - CI у гейтов по спреду и объёму пересекает ноль. Направление видно, N маленький.
        - Гейт по спреду сейчас смотрит на книгу вместе с нашим бидом. После фикса A он станет честнее.
    
        ### 3.4 Skip или size-down
    
        - **Size-down по глубине уровня:** наша заявка больше половины уровня на 57–60% ликвидных заявок.
          Такой кап режет прибыльный объём. Это повтор K1/K2 (`docs/experiments/buy-depth-cap`).
        - **Size-down по объёму ленты:** при объёме ниже ~$2–3k за 30 мин размер уходит ниже
          `MIN_ORDER_SIZE = 5` и превращается в skip. То есть это тот же skip, только сложнее.
        - **Skip на входе** проще и даёт тот же эффект. Внешняя практика говорит то же:
          «ограничивай размер тем, что можешь продать; на тонком рынке — минимальный размер или ничего»
          (`reports/web-devin.md`, poly-maker TIPS.md).
    
        ### 3.5 Рекомендация
    
        1. **Не строить динамический сайзинг.**
        2. **Спред-гейт 8 → 6 тиков** по книге без наших заявок (после фикса A). Проверить в бэктесте
           варианты 8 / 6 / 4.
        3. **Младшие лиги LoL** — самая большая сумма (−$407). Быстрый шаг: убрать убыточные младшие лиги из
           `config/lol_league_whitelist.json`. Отдельно — разбор модели LoL (вход против дрейфа). Это
           отдельная задача, не часть проблем 1–2.
        4. **Объёмный skip-гейт на входе** — только если после шагов 1–3 бэктест всё ещё видит хвост на
           тонких рынках. Ни лайв, ни старые бэктесты не показывают, что тонкие по объёму карты теряют.
           Если его всё же делать, первые кандидаты такие: Dota — объём за 10 мин до входа ≤ $166.66,
           LoL — ≤ 4 уникальных ордера за 60 мин (`reports/lookahead-luna.md`). Пороги выбраны после
           просмотра валидации: их надо проверить на новых картах. Окна пересчитать на момент первого
           `PlaceOrder`, а не на горн. Сначала писать решение гейта в лог в лайве (shadow), без
           блокировки.
    
        ## 4. Бэктест: look-ahead фильтр и паритет
    
        ### 4.1 Что режет фильтр
    
        Источник: `reports/lookahead-luna.md` (8 171 карта, метка совпала с функцией проекта на всех).
    
        | игра | сплит | карт-кандидатов | проходят фильтр | не проходят | доля отсева |
        |---|---|---:|---:|---:|---:|
        | Dota | train | 2 485 | 1 203 | 1 282 | 51.6% |
        | Dota | validation | 711 | 598 | 113 | 15.9% |
        | LoL | train | 2 900 | 2 139 | 761 | 26.2% |
        | LoL | validation | 2 075 | 1 686 | 389 | 18.7% |
    
        - Из 113 отсеянных карт Dota у 13 нет market cache. Значит, в бэктест реально войдёт ~100 карт
          (598 → ~698, `reports/backtest-devin.md`). В LoL войдёт до 389 карт.
        - В 18866 фильтр отсеивает 74% карт train и 40% карт validation.
        - Провалы идут по всей карте, а не только в начале или в конце. У отсеянных карт валидации первая
          5-минутка проваливается в 46% (Dota) и 67% (LoL), последняя — в 60% и 56%.
        - Старые бэктесты с тонкими картами (до 2026-09-04): разница PnL «тонкая минус плотная» на карту
          Dota +$0.17 [−$2.36, +$2.73] (40 против 378 карт), LoL +$0.16 [−$0.72, +$1.11] (256 против 1 254).
          Тонкие карты дают меньше филлов, но не хуже по PnL и markout.
        - Гейты на входе хорошо воспроизводят метку фильтра. Dota: объём за 10 мин до горна ≤ $166.66 —
          ловит 56.6% тонких карт, оставляет 99.3% плотных (AUC 0.92). LoL: ≤ 4 уникальных ордера за
          60 мин — 58.9% и 95.2% (AUC 0.90). Оба порога выбраны после просмотра валидации, поэтому эти
          доли надо проверить на новой отложенной выборке. И главное: сама метка с убытками не связана,
          поэтому такой гейт не обязан улучшить PnL.
        - Лайв-карты, которые фильтр отсёк бы: 126 из 596, торговались 50, их PnL +$40.9. Все крупные
          убыточные карты фильтр проходят.
        - В 18866 фильтр режет 23 из 80 лайв-карт (+$71.2). У 11 из них за всю игру нет ни одного принта.
          Я проверил книгу у трёх: у двух 35–44 тыс. изменений книги (боты переставляют заявки, тейкеров
          нет), у одной книги в данных нет (дыра в сборе). Из остальных 12 восемь проваливают последнюю
          5-минутку, когда исход ясен и торги стихают.
    
        ### 4.2 Как убрать фильтр (план)
    
        Источник: `reports/backtest-devin.md`.
    
        1. Dota, `select_matches` (`src/prepare_dataset/prepare_dataset.py:76-99`): сначала разделить
           каталог по cutoff, фильтр применять только к train.
        2. **Закрыть утечку:** `write_datasets` кладёт `validation_minute_rows` в
           `production_training.parquet` (`prepare_dataset.py:280`). Для этого выхода фильтр оставить.
        3. LoL, `eligibility_drop_reason` (`src/lol/05_prepare_dataset.py:527-543`): удалить проверку в
           ветке validation, в train оставить. Добавить в аудит колонку `tape_dense`. Сейчас отсеянные карты
           видны только в полном аудите со сплитом `none`, сплит надо восстанавливать по старту события.
        4. Пересобрать: `make market-data` (13 карт без кэша), `make prepare`, `make train`, бэктест.
        5. Тесты: `tests/test_prepare_dataset.py:444-453`, `tests/test_lol_prepare_dataset.py:1433`, счётчики
           карт в smoke/validation тестах. Новый тест на утечку в production training.
    
        ### 4.3 Бэктест оптимистичен на тонких книгах
    
        Реплеи лайв-карт по расписанию лайв-сигналов (`reports/backtest-devin.md`, seed 0):
    
        | группа | карт, где торговали обе стороны | лайв PnL | реплей PnL |
        |---|---:|---:|---:|
        | Dota все | 67 | +$42.37 | +$298.60 |
        | Dota 18866 | 12 | +$5.78 | +$137.29 |
        | LoL | 117 | −$177.54 | +$465.30 |
    
        - Клип разный: Dota лайв $60 против $100 в бэктесте, LoL лайв $5–30 против $100. Для LoL
          сравнимы только знаки.
        - В старом бэктесте с тонкими картами (до 2026-09-04) карты с пре-хорн объёмом < $500 дали
          +$136 (90 карт × сид). В лайве та же корзина дала −$48 по `match.json` и −$188 по FIFO-лотам
          (62 карты, `reports/exit-devin.md`).
        - Причины (по убыванию уверенности): модель очереди на тонком уровне даёт филлы, которых в лайве
          нет; self-join есть только в лайве (проблема 1); `strip_own_book` приближённый (центы, время);
          разные клипы.
        - Вывод: **гейт для тонких рынков в бэктесте будет выглядеть хуже, чем в лайве.** Сначала фикс A+B
          в обоих адаптерах. Потом заново сравнить лайв и реплей. Только потом судить гейты.
    
        ### 4.4 Как встраивать гейт (если он понадобится)
    
        - Одна чистая функция решения в `src/strategy/`, два адаптера кормят её одинаковыми данными в один
          момент времени (`reports/backtest-devin.md` §D). Новая причина блокировки — через
          `BlockReason`, кодек подхватит её сам.
        - `schema_version` трейса не поднимать: `replay_core_trace.py:328` сравнивает его строго и отвергнет
          старые архивы. Новые поля декодировать через `setdefault`, как для `mid_spike`
          (`core_trace_codec.py:387-395`). Здесь backtest-devin и selfjoin-grok расходятся; прав второй.
        - Источник данных для пре-хорн гейта в лайве — журнал коллектора (`last_trade_price`, fsync ≤ 1 с,
          смонтирован в трейдер read-only). Сейчас трейдер его не читает. Для LoL это единственный вариант:
          LoL входит через ~12 с после горна, своей истории у трейдера нет.
        - В бэктесте тот же показатель считается из `parquet/trades`. **Локальный канал `trades`
          заканчивается 2026-09-17** (коммит `6ba30b63` перевёл бэктест на `onchain_fills`). Для паритета
          его надо снова синхронизировать. `onchain_fills` в лайве недоступен: коллектор публикует только
          закрытые дни.
        - Gamma `liquidityNum` есть у активных рынков (я проверил), но его значение на момент входа нигде
          не архивируется. Для бэктеста он не годится.
    
        ### 4.5 Протокол оценки
    
        Источник: `reports/backtest-devin.md`, `reports/postmortem-grok.md`.
    
        - Одна замороженная вселенная для всех рук (Dota ~698 карт). Метки «тонкая / ликвидная» фиксировать
          до просмотра результатов.
        - Руки: A1 без фильтра и без гейта; A2 + фикс A+B; A3 + спред-гейт 6; A4 + спред-гейт 4.
        - Сиды 0–2 как скрининг, 12 сидов — только если хвост лучше контроля.
        - Метрики: net, net/депозит, худшая карта, CVaR 5%, число карт < −$50, max drawdown, трафик заявок.
          Отдельно для тонких и ликвидных карт.
        - Правило приёмки (записать до запуска): на ликвидных картах гейт почти не срабатывает
          (≤10% заявок, ≤5% карт); на тонких хвост короче; net падает не больше заранее выбранной цены.
        - Контроль: плоский меньший клип с тем же оборотом. Если он режет хвост так же — гейт не нужен.
    
        ## 5. Почему динамический сайзинг проваливался раньше
    
        Источник: `reports/postmortem-grok.md`.
    
        - Все тесты сайзинга после 2026-09-05 шли на вселенной, уже очищенной look-ahead фильтром. Тонких
          карт там не было. Гейт «только для тонких» на такой выборке проверить нельзя.
        - K1 (кап по глубине, доля 0.5) урезал 48% заявок на 454 ликвидных картах. Net $744 против $1 554.
          Плоский клип F300 дал $1 097 при 41% капитала.
        - Кап работал только при подаче. Правило «все ушли — снимаю» не тестировали ни разу.
        - Delta-sizing, position-scaling, map caps проиграли плоскому клипу при том же депозите.
        - Урок: правило, которое режет размер на ликвидных уровнях, режет ту часть, где заработок.
    
        ## 6. План работ
    
        1. **Фикс A** (книга без наших заявок в `books_from_md`). Маленький дифф, паритет с бэктестом.
           Проверка: юнит-тест, `make parity` на старых архивах, первая лайв-карта в трейсе.
        2. **Фикс B** (снятие одинокой L0 через T с). Бэктест-руки T ∈ {выкл, 1, 3, 5} × {только L0, все}.
        3. **Убрать look-ahead фильтр из validation** и закрыть утечку в production training. В бэктест войдут
           ~100 карт Dota и до 389 карт LoL. Новый baseline.
        4. **Заново сравнить лайв и реплей** на лайв-архивах после A+B. Понять, сколько разрыва осталось.
        5. **Спред-гейт 6 / 4 тика** в бэктесте.
        6. **Младшие лиги LoL:** решение по вайтлисту сейчас, разбор модели — отдельной задачей.
        7. **Объёмный гейт** — только shadow-лог в лайве, если шаги 1–5 не закроют хвост.
    
        ## 7. Что опровергнуто или не подтверждено
    
        - «Дело в объёме, а не в спреде» (H4) — для хвоста не подтвердилось. Худшая карта (−$94.7) имела
          154 принта и $2.9k за 30 мин до входа, но широкий спред.
        - «Купили — и не продать» — не подтвердилось (продажа ~5 с, до сеттлмента 0–2%).
        - «Dota 18866 убыточна, потому что тонкая» — нет, +$188.
        - «Look-ahead фильтр защищал от убытков» — нет: отсечённые им лайв-карты в плюсе.
        - «127 из 127 лайв-карт проходят фильтр» (backtest-devin) — артефакт выборки. Эти карты взяты из
          каталога, где фильтр уже применён. На всех лайв-картах фильтр режет 126 из 596.
        - «246 из 257 карт никогда не закрываются» (thin-live-devin) — артефакт. Остаток считался по `Fill`
          из `core_trace` без удаления дублей. По `session.jsonl` медиана sell/buy = 1.0.
        - «Gamma `liquidityNum` отсутствует» (postmortem-grok) — верно только для закрытых рынков.
        - «Фильтр отсеивает в LoL лишь несколько процентов валидации» (backtest-devin) — неверно: 389 из
          2 075 (18.7%). Агент смотрел аудит, в котором отсеянных карт нет.
        - «Тонкие по фильтру карты теряют в бэктесте» — нет: разница PnL на карту около нуля, CI включает 0.
        - «Уходить при первом нуле» — хуже, чем с задержкой: режет 21% объёма.
        - «Возвращаться на ту же цену, когда компания вернулась» — повторные филлы в минусе (N=15).
    
        Побочные находки:
        - `core_trace` пишет каждый `Fill` дважды (~46% филлов). Ядро дубли отбрасывает (`seen_fill_ids`),
          инвентарь верный. Но `data/order_outcomes/orders.parquet` завышает `n_fills` и `filled_qty`
          примерно в 2 раза.
        - ~4.4% `CancelAck` приходят без записанного в плане запроса отмены.
        - `block_timestamp` в `onchain_fills` отстаёт от времени сделки на ~2.87 с.
    
        ## 8. Вопросы к тебе
    
        1. Фикс B: только L0 (больше в сумме) или все ступеньки (сильнее режет хвост)?
        2. После снятия: ждать нового сигнала (рекомендую) или сразу вставать ниже, к другим?
        3. Младшие лиги LoL: убрать из вайтлиста сейчас? Какие оставить (LCK, LCS, LEC, CBLOL)?
        4. Спред-гейт: 6 или 4 тика?
        5. Production training: оставить фильтр на `validation_minute_rows` (рекомендую)?
        6. Проверять ли отдельно mid для модели в лайве (`read_raw_pair`), который тоже видит наши заявки?
    
        ## 9. Отчёты агентов и проверки
    
        | агент | модель | тема | файл |
        |---|---|---|---|
        | selfjoin-grok | Grok 4.7 High | код self-join, триггеры, паритет, старый фикс | `reports/selfjoin-grok.md` |
        | postmortem-grok | Grok 4.7 High | прошлые эксперименты, план, данные на входе | `reports/postmortem-grok.md` |
        | blink-luna | GPT-6 Luna | моргающие уровни | `reports/blink-luna.md` |
        | lookahead-luna | GPT-6 Luna | look-ahead фильтр на всей вселенной | `reports/lookahead-luna.md` |
        | lonely-devin | SWE-2 Max | частота и цена одиночества | `reports/lonely-devin.md` |
        | leave-devin | SWE-2 Max | правила ухода, пороги, трафик | `reports/leave-devin.md` |
        | thin-live-devin | SWE-2 Max | тонкие рынки в лайве | `reports/thin-live-devin.md` |
        | exit-devin | SWE-2 Max | выход, skip против size-down | `reports/exit-devin.md` |
        | backtest-devin | SWE-2 Max | фильтр в бэктесте, паритет, реализм филлов | `reports/backtest-devin.md` |
        | web-devin | SWE-2 Max | внешняя практика | `reports/web-devin.md` |
    
        Мои проверки и скрипты: `notes/orchestrator-checks.md`, `notes/*.py`.
        ✓ • 0ms
    
    ## Activity
    
        $ rg -n 'tape_passes_buckets|MIN_TRADES_PER_BUCKET|thin_telonex|tape|no.?trade|stale|market_status|quote' ../esports-trader/src/trader ../esports-trader/src/backtest | head -180
        ../esports-trader/src/trader/oddin_client.py:372:    config: WidgetConfig, key: bytes, stale_seconds: float
        ../esports-trader/src/trader/oddin_client.py:377:    frame; TimeoutError when no frame for stale_seconds.
        ../esports-trader/src/trader/oddin_client.py:389:            raw = await asyncio.wait_for(socket.recv(), timeout=stale_seconds)
        ../esports-trader/src/backtest/results.py:29:    """1 Hz no_quote seconds per gate; unknown reasons fold into other."""
        ../esports-trader/src/backtest/results.py:31:    stale_book: int
        ../esports-trader/src/backtest/results.py:33:    stale_signal: int
        ../esports-trader/src/backtest/results.py:159:    """Count 1 Hz no_quote events per gate; unknown reasons fold into other."""
        ../esports-trader/src/backtest/results.py:162:        if event.kind != "no_quote":
        ../esports-trader/src/backtest/results.py:175:    quote_events: Sequence[QuoteEvent],
        ../esports-trader/src/backtest/results.py:184:    match_events = [event for event in quote_events if event.match_id == context.match_id]
        ../esports-trader/src/backtest/results.py:315:    quote_events: Sequence[QuoteEvent],
        ../esports-trader/src/backtest/results.py:338:            quote_events,
        ../esports-trader/src/backtest/results.py:379:def write_quote_events_parquet(*, report_dir: Path, events: Sequence[QuoteEvent]) -> None:
        ../esports-trader/src/backtest/results.py:380:    """Write quote_events.parquet for the run."""
        ../esports-trader/src/backtest/results.py:387:def read_quote_events_checkpoint(report_dir: Path) -> list[QuoteEvent]:
        ../esports-trader/src/backtest/results.py:396:            f"quote_events.parquet is missing columns {missing}; "
        ../esports-trader/src/backtest/report.py:107:    """Calendar length of the fill tape."""
        ../esports-trader/src/backtest/report.py:240:            _format_kv("no-trade", str(arm["no_trades"])),
        ../esports-trader/src/backtest/report.py:241:            _format_kv("traded + no-trade", str(completed)),
        ../esports-trader/src/backtest/maker_orders.py:225:def gate_quote_context(
        ../esports-trader/src/backtest/lol_inputs.py:156:    usable = market_seconds["market_status"].to_numpy() == "ok"
        ../esports-trader/src/backtest/seed0_replay.py:103:        records.quote_events,
        ../esports-trader/src/backtest/seed0_replay.py:118:        quote_events=records.quote_events,
        ../esports-trader/src/backtest/paths.py:13:QUOTE_EVENTS_FILENAME = "quote_events.parquet"
        ../esports-trader/src/trader/grid_live_feed.py:43:    stale_seconds: float = GRID_FEED_STALE_SECONDS
        ../esports-trader/src/trader/archive_types.py:225:    """One placed order line inside a quote record; a quote, not a book mirror."""
        ../esports-trader/src/trader/archive_types.py:241:    kind: Literal["quote"]
        ../esports-trader/src/backtest/report_types.py:100:    no_trades: int
        ../esports-trader/src/backtest/report_types.py:117:    gate_stale_book_seconds: int
        ../esports-trader/src/backtest/report_types.py:119:    gate_stale_signal_seconds: int
        ../esports-trader/src/backtest/report_types.py:132:    quote_events: QuoteEventCounts
        ../esports-trader/src/backtest/quote_store.py:3:A validation run logs millions of quote events. Holding them all in RAM and rewriting
        ../esports-trader/src/backtest/quote_store.py:6:into the compatible `quote_events.parquet` one time, streaming batch by batch.
        ../esports-trader/src/backtest/quote_store.py:28:QUOTE_EVENT_PARTS_DIRNAME = "quote_event_parts"
        ../esports-trader/src/backtest/quote_store.py:47:# build_reserve_events only reads BUY order lifecycle rows out of the tape.
        ../esports-trader/src/backtest/quote_store.py:63:    """What summary.json needs from the tape without materializing every event."""
        ../esports-trader/src/backtest/quote_store.py:69:def empty_quote_telemetry() -> QuoteTelemetry:
        ../esports-trader/src/backtest/quote_store.py:70:    """Telemetry of a run that logged no quote events."""
        ../esports-trader/src/backtest/quote_store.py:75:    """Directory holding the per-match quote-event parts of one run."""
        ../esports-trader/src/backtest/quote_store.py:88:def write_quote_event_parts(
        ../esports-trader/src/backtest/quote_store.py:98:            raise ValueError(f"quote event for match {event.match_id} outside the replayed batch")
        ../esports-trader/src/backtest/quote_store.py:109:def clear_quote_event_parts(report_dir: Path) -> None:
        ../esports-trader/src/backtest/quote_store.py:114:def assert_quote_event_parts_resumable(report_dir: Path) -> None:
        ../esports-trader/src/backtest/quote_store.py:115:    """Refuse a resume whose checkpoint predates per-match parts; its tape is unsplittable."""
        ../esports-trader/src/backtest/quote_store.py:121:            "this checkpoint predates per-match quote-event parts or the run already "
        ../esports-trader/src/backtest/quote_store.py:126:def copy_quote_event_parts(*, source_dir: Path, target_dir: Path) -> None:
        ../esports-trader/src/backtest/quote_store.py:147:def compact_quote_events(*, report_dir: Path, match_ids: Sequence[int]) -> QuoteCompaction:
        ../esports-trader/src/backtest/quote_store.py:148:    """Stream the parts of the given matches into one quote_events.parquet, in replay order."""
        ../esports-trader/src/backtest/quote_store.py:191:def find_quote_events_file_problems(
        ../esports-trader/src/backtest/quote_store.py:194:    """Check quote_events.parquet against what the just-finished compact wrote."""
        ../esports-trader/src/backtest/quote_store.py:229:            raise ValueError("quote-event group-by produced a null key")
        ../esports-trader/src/backtest/quote_store.py:242:def _quote_events_from_table(table: pa.Table) -> tuple[QuoteEvent, ...]:
        ../esports-trader/src/backtest/quote_store.py:248:def read_quote_telemetry(report_dir: Path) -> QuoteTelemetry:
        ../esports-trader/src/backtest/quote_store.py:249:    """Stream quote_events.parquet once for per-match counts and BUY reserve rows."""
        ../esports-trader/src/backtest/quote_store.py:252:        return empty_quote_telemetry()
        ../esports-trader/src/backtest/quote_store.py:266:        reserve_events=_quote_events_from_table(reserve_table),
        ../esports-trader/src/backtest/quote_store.py:270:def quote_telemetry_from_events(events: Sequence[QuoteEvent]) -> QuoteTelemetry:
        ../esports-trader/src/backtest/quote_store.py:271:    """In-memory reference for read_quote_telemetry; small tapes and tests only."""
        ../esports-trader/src/backtest/quote_store.py:289:def sum_quote_event_counts(
        ../esports-trader/src/backtest/report_capital.py:14:from backtest.quote_store import read_quote_telemetry
        ../esports-trader/src/backtest/report_capital.py:57:    quotes: Sequence[QuoteEvent],
        ../esports-trader/src/backtest/report_capital.py:66:    events = build_reserve_events(results, fills, quotes, settlement_at="game_end")
        ../esports-trader/src/backtest/report_capital.py:100:    quote_path = seed_dir / "quote_events.parquet"
        ../esports-trader/src/backtest/report_capital.py:101:    if not quote_path.is_file():
        ../esports-trader/src/backtest/report_capital.py:102:        raise ValueError(f"{quote_path} is required to calculate deposit w/ reserves")
        ../esports-trader/src/backtest/report_capital.py:105:    quotes = [
        ../esports-trader/src/backtest/report_capital.py:107:        for event in read_quote_telemetry(seed_dir).reserve_events
        ../esports-trader/src/backtest/report_capital.py:111:    return calculate_report_capital(results, fills, quotes, winning_tokens)
        ../esports-trader/src/backtest/wallet_path.py:49:    """Open size and last mark for one match on the shared tape."""
        ../esports-trader/src/backtest/wallet_path.py:58:    """Cash and open-lot mark at one tape timestamp."""
        ../esports-trader/src/backtest/wallet_path.py:67:    """Running cash and open inventory as the shared fill/mid/settlement tape replays."""
        ../esports-trader/src/backtest/wallet_path.py:84:        """Fold one sorted tape event into cash and open lots."""
        ../esports-trader/src/backtest/wallet_path.py:149:    calendar windows over the tape span.
        ../esports-trader/src/backtest/wallet_path.py:185:def _build_tape_events(
        ../esports-trader/src/backtest/wallet_path.py:219:    events = _build_tape_events(results, ordered_fills, mids)
        ../esports-trader/src/backtest/wallet_path.py:220:    tape = _Tape(contexts=contexts)
        ../esports-trader/src/backtest/wallet_path.py:230:            tape.apply(events[index])
        ../esports-trader/src/backtest/wallet_path.py:232:        mark = tape.mark
        ../esports-trader/src/backtest/wallet_path.py:233:        if tape.cash == samples[-1].cash and mark == samples[-1].mark:
        ../esports-trader/src/backtest/wallet_path.py:235:        min_cash = min(min_cash, tape.cash)
        ../esports-trader/src/backtest/wallet_path.py:236:        maps_at_once = max(maps_at_once, tape.open_maps)
        ../esports-trader/src/backtest/wallet_path.py:237:        samples.append(_WalletSample(ts_ns, tape.cash, mark))
        ../esports-trader/src/backtest/wallet_path.py:314:    quote_events: Sequence[QuoteEvent],
        ../esports-trader/src/backtest/wallet_path.py:320:    for event in quote_events:
        ../esports-trader/src/backtest/wallet_path.py:374:    quote_events: Sequence[QuoteEvent],
        ../esports-trader/src/backtest/wallet_path.py:380:    events = build_reserve_events(results, fills, quote_events, settlement_at=settlement_at)
        ../esports-trader/src/backtest/postprocess.py:29:from backtest.quote_store import QuoteTelemetry, sum_quote_event_counts
        ../esports-trader/src/backtest/postprocess.py:82:    "wallet: required cash is -min(cash) on the shared fill+settlement tape "
        ../esports-trader/src/backtest/postprocess.py:85:    "(bucket 0 = deposit, 99 equal windows over the fill-tape span), snapshot only "
        ../esports-trader/src/backtest/postprocess.py:117:        frame = pd.read_parquet(path, columns=["state_ts_us", "market_p_radiant", "market_status"])
        ../esports-trader/src/backtest/postprocess.py:118:        ok = frame[frame["market_status"] == "ok"]
        ../esports-trader/src/backtest/postprocess.py:526:    """Per-arm counters, markouts, rebate, quote-event histograms, and stub liquidation."""
        ../esports-trader/src/backtest/postprocess.py:579:    quote_event_counts = sum_quote_event_counts(telemetry.kind_counts, clean_ids)
        ../esports-trader/src/backtest/postprocess.py:597:            "no_trades": completed - len(traded),
        ../esports-trader/src/backtest/postprocess.py:615:            "quote_events": quote_event_counts,
        ../esports-trader/src/trader/engine_seams.py:96:    """Engine journal that keeps our private order/fill tape and drops public books."""
        ../esports-trader/src/trader/engine_seams.py:209:        """Bump the generation so a stale book from before attach cannot count as ready."""
        ../esports-trader/src/trader/engine_seams.py:252:        async def place(quotes: list[Quote], meta: MarketMeta) -> list[OpenOrder]:
        ../esports-trader/src/trader/engine_seams.py:254:                return _refuse_closed_place(quotes)
        ../esports-trader/src/trader/engine_seams.py:258:                    return _refuse_closed_place(quotes)
        ../esports-trader/src/trader/engine_seams.py:259:                return await original_place(quotes, meta)
        ../esports-trader/src/trader/engine_seams.py:324:def _refuse_closed_place(quotes: list[Quote]) -> list[OpenOrder]:
        ../esports-trader/src/trader/engine_seams.py:325:    """Empty quotes stay empty; a non-empty list must not look like a partial batch."""
        ../esports-trader/src/trader/engine_seams.py:326:    if quotes:
        ../esports-trader/src/trader/engine_seams.py:700:    def evaluate(meta: MarketMeta, *, ws_stale: bool, event_group_cost: float) -> RiskDecision:
        ../esports-trader/src/trader/engine_seams.py:701:        decision = original_evaluate(meta, ws_stale=ws_stale, event_group_cost=event_group_cost)
        ../esports-trader/src/trader/engine_seams.py:818:    """Unwrap a CLOB post_orders body to the per-quote item list."""
        ../esports-trader/src/trader/engine_seams.py:844:def sell_is_droppable(store: WalletStateStore, quote: Quote) -> bool:
        ../esports-trader/src/trader/engine_seams.py:846:    if quote.side is not Side.SELL:
        ../esports-trader/src/trader/engine_seams.py:848:    if store.is_sell_frozen(quote.token_id):
        ../esports-trader/src/trader/engine_seams.py:850:    held = store.position(quote.token_id).size
        ../esports-trader/src/trader/engine_seams.py:851:    return quote.size > held + HALF_SHARE_TICK
        ../esports-trader/src/trader/engine_seams.py:885:    store: WalletStateStore, quotes: list[Quote], items: list[object]
        ../esports-trader/src/trader/engine_seams.py:888:    for quote, item in zip(quotes, items, strict=False):
        ../esports-trader/src/trader/engine_seams.py:889:        if quote.side is Side.SELL and _is_balance_reject(item):
        ../esports-trader/src/trader/engine_seams.py:890:            store.freeze_sell(quote.token_id, SELL_REJECT_HOLD_SECONDS)
        ../esports-trader/src/trader/engine_seams.py:894:                quote.token_id[:12],
        ../esports-trader/src/trader/engine_seams.py:911:def _esports_place_quotes(
        ../esports-trader/src/trader/engine_seams.py:912:    quotes: list[Quote],
        ../esports-trader/src/trader/engine_seams.py:918:        return quotes
        ../esports-trader/src/trader/engine_seams.py:919:    return [quote for quote in quotes if not sell_is_droppable(store, quote)]
        ../esports-trader/src/trader/engine_seams.py:933:    def parse_place_response(resp: object, quotes: list[Quote]) -> list[OpenOrder]:
        ../esports-trader/src/trader/engine_seams.py:936:        parsed = original_parse(resp, quotes)
        ../esports-trader/src/trader/engine_seams.py:937:        _freeze_balance_rejects(store, quotes, _clob_place_items(resp))
        ../esports-trader/src/trader/engine_seams.py:940:    async def place(quotes: list[Quote], meta: MarketMeta) -> list[OpenOrder]:
        ../esports-trader/src/trader/engine_seams.py:944:        kept = _esports_place_quotes(quotes, meta, store, cores)
        ../esports-trader/src/trader/engine_seams.py:945:        if len(kept) < len(quotes):
        ../esports-trader/src/trader/engine_seams.py:962:    def evaluate(meta: MarketMeta, *, ws_stale: bool, event_group_cost: float) -> RiskDecision:
        ../esports-trader/src/trader/engine_seams.py:964:        decision = original_evaluate(meta, ws_stale=ws_stale, event_group_cost=event_group_cost)
        ../esports-trader/src/trader/engine_seams.py:996:    def evaluate(meta: MarketMeta, *, ws_stale: bool, event_group_cost: float) -> RiskDecision:
        ../esports-trader/src/trader/engine_seams.py:1002:        return original_evaluate(meta, ws_stale=ws_stale, event_group_cost=event_group_cost)
        ../esports-trader/src/trader/engine_seams.py:1091:    """Copy-assign engine dicts; _token_cid last. Resubscribe market WS. Spawn the quoter.
        ../esports-trader/src/trader/engine_seams.py:1118:    _spawn_quoter(engine, cid)
        ../esports-trader/src/trader/engine_seams.py:1138:    engine._last_quote_fv.pop(cid, None)
        ../esports-trader/src/trader/engine_seams.py:1143:    quote_name = _quote_task_name(cid)
        ../esports-trader/src/trader/engine_seams.py:1144:    engine._task_specs.pop(quote_name, None)
        ../esports-trader/src/trader/engine_seams.py:1145:    engine._tasks.pop(quote_name, None)
        ../esports-trader/src/trader/engine_seams.py:1154:async def stop_quoter(engine: Engine, cid: str) -> None:
        ../esports-trader/src/trader/engine_seams.py:1155:    """Cancel the quote task, keep a no-op _task_specs factory, drop the _tasks entry."""
        ../esports-trader/src/trader/engine_seams.py:1156:    name = _quote_task_name(cid)
        ../esports-trader/src/trader/engine_seams.py:1157:    _install_idle_quoter_spec(engine, cid)
        ../esports-trader/src/trader/engine_seams.py:1196:    """Subscribe extra books without a quoter. Reconnect only when the set changes."""
        ../esports-trader/src/trader/engine_seams.py:1256:def install_core_quoter_wake(engine: Engine, cores: Mapping[str, LiveCore]) -> None:
        ../esports-trader/src/trader/engine_seams.py:1273:def _spawn_quoter(engine: Engine, cid: str) -> None:
        ../esports-trader/src/trader/engine_seams.py:1274:    name = _quote_task_name(cid)
        ../esports-trader/src/trader/engine_seams.py:1277:        return engine._quoter(cid)
        ../esports-trader/src/trader/engine_seams.py:1287:def _install_idle_quoter_spec(engine: Engine, cid: str) -> None:
        ../esports-trader/src/trader/engine_seams.py:1288:    name = _quote_task_name(cid)
        ../esports-trader/src/trader/engine_seams.py:1297:def _quote_task_name(cid: str) -> str:
        ../esports-trader/src/trader/engine_seams.py:1298:    """Engine task name for this market's quoter. Uses the full condition id."""
        ../esports-trader/src/trader/engine_seams.py:1299:    return f"quote:{cid}"
        ../esports-trader/src/trader/session_engine.py:153:        """Fire the entry callback only for the current generation. Does not mark stale."""
        ../esports-trader/src/trader/session_engine.py:159:        """Fire the exit callback only for the current generation; the next consume is stale."""
        ../esports-trader/src/trader/core_state_report.py:5:in neither tape: both hang off the same worker lookup. This replays
        ../esports-trader/src/trader/core_state_report.py:6:`core_trace.jsonl` and prints the state they hide, the plan `requote` would
        ../esports-trader/src/trader/core_state_report.py:20:from strategy.quoting import held_sell, requote
        ../esports-trader/src/trader/core_state_report.py:98:    """The plan `requote` produces on this state, run on a copy the caller discards."""
        ../esports-trader/src/trader/core_state_report.py:99:    _state, plan = requote(state=state, policy=policy, now_ns=now_ns)
        ../esports-trader/src/trader/core_state_report.py:225:        f"requote   block_reason={report.plan.block_reason or '-'} "
        ../esports-trader/src/backtest/telemetry.py:1:"""Batch-scoped maker telemetry: fills and quote decisions harvested after backtest.run().
        ../esports-trader/src/backtest/telemetry.py:48:    """One quote decision or order-lifecycle event for a match.
        ../esports-trader/src/backtest/telemetry.py:51:    cancel_ack (venue confirmed and the reserve is free), rejected, denied, expired, no_quote.
        ../esports-trader/src/backtest/telemetry.py:83:    """Fills, quote events, and uptime harvested from one batch."""
        ../esports-trader/src/backtest/telemetry.py:86:    quote_events: tuple[QuoteEvent, ...]
        ../esports-trader/src/backtest/telemetry.py:96:    """Drop any fills, quote events, and uptimes left from a previous batch or arm."""
        ../esports-trader/src/backtest/telemetry.py:107:def record_quote_event(event: QuoteEvent) -> None:
        ../esports-trader/src/backtest/telemetry.py:108:    """Append one quote/order lifecycle event to the current batch recorder."""
        ../esports-trader/src/backtest/telemetry.py:118:    """Return and clear the current batch's fills, quote events, and uptimes."""
        ../esports-trader/src/backtest/telemetry.py:121:        quote_events=tuple(_QUOTE_EVENTS),
        ../esports-trader/src/backtest/extraction_identity.py:12:    read_quote_events_checkpoint,
        ../esports-trader/src/backtest/extraction_identity.py:90:    """Pinned extraction tape for one seed-0 map or one compact harness scenario."""
        ../esports-trader/src/backtest/extraction_identity.py:125:    """Keep a place/cancel event; drop no_quote and other non-identity kinds."""
        ../esports-trader/src/backtest/extraction_identity.py:153:    quote_events: Sequence[QuoteEvent],
        ../esports-trader/src/backtest/extraction_identity.py:155:    """Project a harvested tape onto the extraction-identity schema."""
        ../esports-trader/src/backtest/extraction_identity.py:158:        event for event in (project_place_cancel(row) for row in quote_events) if event is not None
        ../esports-trader/src/backtest/extraction_identity.py:192:    events = read_quote_events_checkpoint(report_dir)
        ../esports-trader/src/backtest/extraction_identity.py:200:        quote_events=events,
        ../esports-trader/src/backtest/extraction_identity.py:320:    """Empty string when tapes match; otherwise the first differing path."""
        ../esports-trader/src/backtest/extraction_identity.py:337:def first_tape_mismatch(actual: ReplayIdentity, expected: ReplayIdentity) -> str:
        ../esports-trader/src/trader/wallet_host.py:65:    stop_quoter,
        ✓ • 0ms
    
    ## Assistant
    
    Это архитектурный вопрос: одно правило должно одинаково работать при подготовке данных, в replay и в live. Сейчас разбираю отчёт и точки входа, чтобы предложить правило по данным, которые уже были известны к моменту решения, без знания конца карты.
    
    ## Activity
    
        $ sed -n '70,185p' .analysis/ladder-thin-2026-09-24/REPORT.md
        Моргнувший уходит — и self-join держит нас на этой цене одних.
    
        ### 2.2 Как часто и сколько стоит (данные)
    
        Источник: `reports/lonely-devin.md` (полные книги минус наши заявки), `reports/leave-devin.md`.
    
        | группа | заявок | время одни на своём центе | доля ≥65% уровня |
        |---|---:|---:|---:|
        | все | 10 329 | 17.9% | 27.9% |
        | Dota | 6 195 | 23.9% | 35.6% |
        | Dota 18866 | 1 978 | 31.5% | 45.8% |
        | Dota 18865 | 274 | 36.0% | 58.1% |
        | Dota 19944 | 1 069 | 16.8% | 32.2% |
        | Dota 20279 (Oddin) | 1 908 | 10.6% | 15.1% |
        | LoL | 4 134 | 9.1% | 16.7% |
    
        - Длина эпизода одиночества: медиана 0.07 с, p95 самого длинного эпизода заявки 8.4 с.
          У 8.1% заявок есть эпизод ≥5 с.
        - После ухода других заявка живёт в медиане ещё 0.21 с. Но 24.4% живут ≥5 с, 13.6% ≥10 с.
          70% эпизодов заканчиваются только вместе с заявкой: компания почти не возвращается.
        - Филлы, взятые в одиночестве (за 2 с до филла никого): +$0.45 на $100 против +$1.35 в компании.
          L0 в одиночестве: −$95 на 222 филлах. L0 в компании: +$250 на 649.
        - Уход других — плохой знак в LoL и при свипе. LoL: mid −0.41¢ через 60 с и −1.39¢ через 300 с
          против +1.71¢ в компании. Уход со свипом: −0.37¢ через 60 с, без свипа +0.12¢.
        - В Dota одинокая нижняя ступенька часто безвредна. Книга ушла вверх, ступенька осталась ниже.
          Пример: `grid-3007263-m1`, c19 стояла одна на 0.56 61.7 с при биде 0.57–0.58 (я проверил по книге).
    
        ### 2.3 Моргающие уровни
    
        Источник: `reports/blink-luna.md` (209 карт, 4 842 принятые BUY, 270 187 эпизодов уровней).
    
        | группа | жизнь лучшего бида p50 | < 1 с | наш уровень пропал ≤1 с | после этого мы одни на топе | филл: моргнувший / стабильный уровень |
        |---|---:|---:|---:|---:|---:|
        | Dota 18866 | 0.157 с | 78.7% | 39.4% | 4.1% | 17.2% / 4.6% |
        | Dota 19944 | 0.177 с | 76.1% | 34.1% | 2.3% | 22.4% / 4.9% |
        | Dota 20279 | 0.300 с | 66.4% | 15.5% | 14.0% | 26.8% / 3.9% |
        | LoL | 0.146 с | 77.3% | 11.0% | 19.1% | 29.8% / 4.9% |
    
        - Моргание — норма для этих книг. Внешняя практика объясняет почему: отмены на Polymarket
          бесплатные, reward-боты переставляются к mid на каждое его движение (`reports/web-devin.md`).
        - Моргнувшие уровни меньше стабильных: 40 против 48 шаров в 18866, 40 против 60 в LoL.
        - Встать на моргнувший уровень не так плохо, как кажется. Такие заявки филлятся в 4–6 раз чаще.
          Markout у них смешанный: в одних группах лучше, в других хуже.
        - Ни один фильтр не выигрывает во всех группах. Возраст уровня ≥0.5 с меняет 11.7–33.2% заявок.
          Мин. 5 чужих шаров — 6.7–23.8%. «Стабильный бид» за 2 с — 37–48%.
        - Мы сами переставляемся нечасто: 457 пар «отмена + новая заявка» за окно торговли на 209 картах,
          в основном L2 в 18866 и 19944 (0.15 пары на карту в минуту).
        - В книгах есть дыры: у каждого потока токена есть разрыв >1 с, максимум 72–115 с. Точное время
          жизни уровня эти данные не дают.
    
        **Вывод:** отдельный фильтр против моргания сейчас не нужен. Опасный случай — «встали на моргнувший
        уровень, он ушёл, мы остались одни». Его закрывают фиксы A и B из §2.5. Если фильтр всё же нужен,
        первым в бэктест брать «мин. 5 чужих шаров на цене входа»: он меняет меньше всего заявок.
    
        ### 2.4 Варианты решения
    
        Источник: `reports/leave-devin.md` (контрфактика на 326 лайв-картах), мой разрез по ступенькам.
        «Убрано» — markout +300 с у филлов, которых правило избежало бы. Минус — правило экономит деньги.
    
        | вариант | что делает | убрано (все карты) | убрано на 14 худших картах | срабатывает | цена |
        |---|---|---:|---:|---|---|
        | R1: книга без наших заявок | цена входа, спред и mid_spike считаются по чужой книге | −$15.6 | −$147.5 | 20 филлов | 0 трафика, паритет с бэктестом |
        | R4_3s, все ступеньки | снять ступеньку, если на её цене никого нет ≥3 с | −$19.8 | −$173.3 | 5.5% LoL … 23.6% 18866 | +5.8% трафика |
        | **R4_3s, только L0** | то же, только верхняя ступенька | **−$81.6** | **−$123.6** | 9.1% LoL, 8.3% Oddin, 20% 18866 | меньше, чем у варианта «все» |
        | R4_1s | порог 1 с | −$7.1 | −$174.7 | чаще | +8.6% трафика |
        | R4_10s | порог 10 с | +$10.8 | — | реже | начинает резать хорошие филлы |
        | R2_1.0 | снять сразу, как только одни | −$29.2 | −$221.8 | 35% LoL, 64% 18866 | режет 21% всего объёма |
        | R2_0.65 / 0.8 (твой план) | снять, если мы ≥65–80% уровня | −$149 / −$130 | −$293 (0.8) | вне книги 40–80% времени в тонких лигах, ~40% в LoL | это смена стратегии |
        | R3: мин. компания | ставить, только если других ≥ K × нас | до −$153 | — | 53% объёма при K=2 | доминируется |
    
        При задержке отмены 0.3 с (ближе к реальности, чем 2 с) R4_3s на L0 даёт −$128. Нижние ступеньки
        L1/L2 в одиночестве в сумме давали хорошие филлы: +$62 при задержке 2 с. Поэтому я выделил вариант
        «только L0». Выборка маленькая (57 убранных филлов L0). Выбор между «только L0» и «все ступеньки»
        должен сделать бэктест.
    
        После снятия:
        - Возвращаться на ту же цену плохо: такие повторные филлы дали −$11.2 (15 филлов, 672 шары).
        - Сразу догонять следующий уровень почти ничего не даёт: +$5 при утроенном трафике.
        - Лучше всего ждать следующего сигнала.
    
        ### 2.5 Рекомендация
    
        1. **A. Книга без наших заявок в лайв-адаптере.** В `books_from_md` вычитать остаток наших
           BUY/SELL по центам из `OrderBook` до расчёта `bid`, `ask`, размеров. Передать
           `core.state.orders` из `host_resources.py:90`. Ядро и бэктест не трогать: иначе бэктест вычтет
           второй раз. Кодек менять не надо. Старые трейсы проигрываются как раньше. Вычитать обе стороны:
           Nautilus не держит в книге ни наши BUY, ни наши SELL.
           Не проверено: другие читатели сырой книги в лайве тоже видят наши заявки. Главный из них —
           `read_raw_pair` (`session_quoting.py:48-66`), он даёт mid для сигнала модели. Когда мы одни на
           лучшем биде, этот mid завышен нашей заявкой. Это отдельный вопрос паритета, его стоит проверить.
        2. **B. Снятие одинокой L0.** Если чужой размер на цене L0 равен 0 уже ≥ T секунд (старт T = 3),
           выбросить таргет в `_open_buy_targets` после `_reject_buy` и до блока занятости
           (`quoting.py:305-318`). Проверка работает и при `follow_book = False`, по цене стоящей заявки.
           Нужны: чужая глубина на цене ступеньки в `TokenBook` (оба адаптера), таймер «одни с момента»
           и wake на момент истечения T. Новая причина `lonely`. Рубильник по умолчанию выключен.
        3. **После снятия ждать нового сигнала.** Не ставить ту же цену снова в этом эпизоде.
        4. **Не делать:** снятие при первом нуле (дребезг), share cap 0.5 при подаче (это K1, он проиграл),
           возврат на ту же цену, автодогонялку.
    
        ## 3. Проблема 2: тонкие рынки
    
        ### 3.1 Что показал лайв
    
        Источник: `reports/thin-live-devin.md`, `reports/exit-devin.md`. PnL = realized + unrealized + rebate.
    
        | группа | карт с филлами | PnL | худшая карта |
        |---|---:|---:|---:|
        | вся Dota | 203 | +$1 034.6 | −$94.7 |
        | Dota 18866 (ты считал тонкой) | 49 | +$187.9 | −$94.7 |
        | Dota 18865 (по объёму самая тонкая) | 13 | +$66.7 | −$3.9 |
        | Dota 19944 | 72 | +$369.8 | −$78.9 |
        | Dota 20279 (Oddin) | 45 | +$354.2 | −$21.0 |
        | весь LoL | 210 | −$395.1 | −$65.7 |
        | младшие лиги LoL | 126 | −$406.7 | −$65.7 |
        | LCK + LCS | 47 | +$35.3 | |
    
        ✓ • 0ms
    
    ## Activity
    
        $ rg -n '^##|^###|Look-ahead|look-ahead|тонк|фильтр|яд|токс|спред|объ.м|dead|stale' .analysis/ladder-thin-2026-09-24/REPORT.md
        1:# Одинокая лесенка и тонкие рынки — итоговый отчёт
        8:## 1. Главное
        20:   −$124…−$173 убыточных филлов на 14 худших картах. Отдельный фильтр против моргания не нужен:
        21:   66–79% уровней на лучшем биде живут < 1 с, и ни один фильтр не выигрывает во всех лигах.
        23:   Самая тонкая лига 18865 тоже в плюсе. «Купили — не продать» тоже не подтвердилось: продажа
        24:   филлится за ~5 с, до сеттлмента доживает 0–2% объёма.
        27:   младших лиг ни одна метрика объёма или книги не отделяет убыточные карты. Это вопрос модели LoL,
        32:   ликвидных рынках. Это та же ошибка, что у K1 в сентябре. Размер «от объёма» упирается в
        34:8. **Look-ahead фильтр можно убрать из бэктеста.** Лайв-карты, которые он режет, не теряли:
        38:9. **Главный риск для всех решений: бэктест сильно оптимистичен на тонких книгах.** Реплеи лайв-карт
        40:   +$465. Пока этот разрыв не закрыт, бэктест неправильно оценит любой гейт для тонких рынков.
        43:## 2. Проблема 1: лесенка остаётся одна
        45:### 2.1 Причина в коде (проверено)
        58:- `wide_spread` и `mid_spike` в лайве тоже видят наш бид. Спред выглядит узким, mid не падает.
        72:### 2.2 Как часто и сколько стоит (данные)
        97:### 2.3 Моргающие уровни
        113:- Ни один фильтр не выигрывает во всех группах. Возраст уровня ≥0.5 с меняет 11.7–33.2% заявок.
        120:**Вывод:** отдельный фильтр против моргания сейчас не нужен. Опасный случай — «встали на моргнувший
        121:уровень, он ушёл, мы остались одни». Его закрывают фиксы A и B из §2.5. Если фильтр всё же нужен,
        124:### 2.4 Варианты решения
        131:| R1: книга без наших заявок | цена входа, спред и mid_spike считаются по чужой книге | −$15.6 | −$147.5 | 20 филлов | 0 трафика, паритет с бэктестом |
        136:| R2_1.0 | снять сразу, как только одни | −$29.2 | −$221.8 | 35% LoL, 64% 18866 | режет 21% всего объёма |
        137:| R2_0.65 / 0.8 (твой план) | снять, если мы ≥65–80% уровня | −$149 / −$130 | −$293 (0.8) | вне книги 40–80% времени в тонких лигах, ~40% в LoL | это смена стратегии |
        138:| R3: мин. компания | ставить, только если других ≥ K × нас | до −$153 | — | 53% объёма при K=2 | доминируется |
        150:### 2.5 Рекомендация
        169:## 3. Проблема 2: тонкие рынки
        171:### 3.1 Что показал лайв
        178:| Dota 18866 (ты считал тонкой) | 49 | +$187.9 | −$94.7 |
        179:| Dota 18865 (по объёму самая тонкая) | 13 | +$66.7 | −$3.9 |
        186:- В 18866 проблема в хвосте, а не в среднем. Хвост 18866 — это входы при спреде 6–7 тиков
        189:  (выход при этом даёт +$206). Внутри младших лиг ни объём, ни спред, ни глубина не отделяют
        192:  терять с той же скоростью: −$50.8 на 18 картах, −5.7% от объёма покупок (до 09-16 было −5.3%).
        194:### 3.2 «Купили — и не продать»
        198:- До сеттлмента доживает 0–2% купленного объёма.
        199:- Мы действительно крупные на тонких картах: наша покупка — 2.7% пре-хорн объёма за 30 мин в 18866
        203:### 3.3 Что ловит плохие карты на входе
        208:| спред ≥ 8 (сейчас) | 20 | −$25.4 | +$664.9 | −$94.7 |
        209:| спред ≥ 6 | 39 | −$106.8 | +$781.4 | −$78.9 |
        210:| спред ≥ 4 | 80 | −$114.9 | +$789.5 | −$78.9 |
        211:| пре-хорн объём 30 мин < $1000 | 82 | −$91.6 | +$731.1 | −$94.7 |
        212:| пре-хорн объём 30 мин < $3000 | 147 | −$130.4 | +$769.9 | −$78.9 |
        214:| look-ahead фильтр не прошёл | 50 | **+$40.9** | +$598.6 | −$94.7 |
        218:- CI у гейтов по спреду и объёму пересекает ноль. Направление видно, N маленький.
        219:- Гейт по спреду сейчас смотрит на книгу вместе с нашим бидом. После фикса A он станет честнее.
        221:### 3.4 Skip или size-down
        224:  Такой кап режет прибыльный объём. Это повтор K1/K2 (`docs/experiments/buy-depth-cap`).
        225:- **Size-down по объёму ленты:** при объёме ниже ~$2–3k за 30 мин размер уходит ниже
        228:  «ограничивай размер тем, что можешь продать; на тонком рынке — минимальный размер или ничего»
        231:### 3.5 Рекомендация
        240:   тонких рынках. Ни лайв, ни старые бэктесты не показывают, что тонкие по объёму карты теряют.
        241:   Если его всё же делать, первые кандидаты такие: Dota — объём за 10 мин до входа ≤ $166.66,
        247:## 4. Бэктест: look-ahead фильтр и паритет
        249:### 4.1 Что режет фильтр
        253:| игра | сплит | карт-кандидатов | проходят фильтр | не проходят | доля отсева |
        262:- В 18866 фильтр отсеивает 74% карт train и 40% карт validation.
        265:- Старые бэктесты с тонкими картами (до 2026-09-04): разница PnL «тонкая минус плотная» на карту
        268:- Гейты на входе хорошо воспроизводят метку фильтра. Dota: объём за 10 мин до горна ≤ $166.66 —
        269:  ловит 56.6% тонких карт, оставляет 99.3% плотных (AUC 0.92). LoL: ≤ 4 уникальных ордера за
        273:- Лайв-карты, которые фильтр отсёк бы: 126 из 596, торговались 50, их PnL +$40.9. Все крупные
        274:  убыточные карты фильтр проходят.
        275:- В 18866 фильтр режет 23 из 80 лайв-карт (+$71.2). У 11 из них за всю игру нет ни одного принта.
        280:### 4.2 Как убрать фильтр (план)
        285:   каталог по cutoff, фильтр применять только к train.
        287:   `production_training.parquet` (`prepare_dataset.py:280`). Для этого выхода фильтр оставить.
        295:### 4.3 Бэктест оптимистичен на тонких книгах
        307:- В старом бэктесте с тонкими картами (до 2026-09-04) карты с пре-хорн объёмом < $500 дали
        310:- Причины (по убыванию уверенности): модель очереди на тонком уровне даёт филлы, которых в лайве
        313:- Вывод: **гейт для тонких рынков в бэктесте будет выглядеть хуже, чем в лайве.** Сначала фикс A+B
        316:### 4.4 Как встраивать гейт (если он понадобится)
        334:### 4.5 Протокол оценки
        338:- Одна замороженная вселенная для всех рук (Dota ~698 карт). Метки «тонкая / ликвидная» фиксировать
        340:- Руки: A1 без фильтра и без гейта; A2 + фикс A+B; A3 + спред-гейт 6; A4 + спред-гейт 4.
        343:  Отдельно для тонких и ликвидных карт.
        345:  (≤10% заявок, ≤5% карт); на тонких хвост короче; net падает не больше заранее выбранной цены.
        348:## 5. Почему динамический сайзинг проваливался раньше
        352:- Все тесты сайзинга после 2026-09-05 шли на вселенной, уже очищенной look-ahead фильтром. Тонких
        353:  карт там не было. Гейт «только для тонких» на такой выборке проверить нельзя.
        360:## 6. План работ
        365:3. **Убрать look-ahead фильтр из validation** и закрыть утечку в production training. В бэктест войдут
        372:## 7. Что опровергнуто или не подтверждено
        374:- «Дело в объёме, а не в спреде» (H4) — для хвоста не подтвердилось. Худшая карта (−$94.7) имела
        375:  154 принта и $2.9k за 30 мин до входа, но широкий спред.
        377:- «Dota 18866 убыточна, потому что тонкая» — нет, +$188.
        378:- «Look-ahead фильтр защищал от убытков» — нет: отсечённые им лайв-карты в плюсе.
        379:- «127 из 127 лайв-карт проходят фильтр» (backtest-devin) — артефакт выборки. Эти карты взяты из
        380:  каталога, где фильтр уже применён. На всех лайв-картах фильтр режет 126 из 596.
        386:- «Тонкие по фильтру карты теряют в бэктесте» — нет: разница PnL на карту около нуля, CI включает 0.
        387:- «Уходить при первом нуле» — хуже, чем с задержкой: режет 21% объёма.
        397:## 8. Вопросы к тебе
        403:5. Production training: оставить фильтр на `validation_minute_rows` (рекомендую)?
        406:## 9. Отчёты агентов и проверки
        413:| lookahead-luna | GPT-6 Luna | look-ahead фильтр на всей вселенной | `reports/lookahead-luna.md` |
        416:| thin-live-devin | SWE-2 Max | тонкие рынки в лайве | `reports/thin-live-devin.md` |
        418:| backtest-devin | SWE-2 Max | фильтр в бэктесте, паритет, реализм филлов | `reports/backtest-devin.md` |
        ✓ • 0ms
    
    ## Activity
    
        $ rg -n 'spread|book.*age|stale|market.*status|midpoint|bid|ask' ../esports-trader/src/shared/utils ../esports-trader/src/trader/session_core.py ../esports-trader/src/backtest/maker_orders.py | head -150
        ../esports-trader/src/backtest/maker_orders.py:19:from shared.utils.trading import spread_ticks
        ../esports-trader/src/backtest/maker_orders.py:35:    spread: float
        ../esports-trader/src/backtest/maker_orders.py:44:    spread=0.0,
        ../esports-trader/src/backtest/maker_orders.py:196:    levels_fn = getattr(book, "bids" if is_buy else "asks", None)
        ../esports-trader/src/backtest/maker_orders.py:204:    tob_price = book.best_bid_price() if is_buy else book.best_ask_price()
        ../esports-trader/src/backtest/maker_orders.py:205:    tob_size = book.best_bid_size() if is_buy else book.best_ask_size()
        ../esports-trader/src/backtest/maker_orders.py:213:def buy_price_legal(*, price: float, bid: float, ask: float, fair_token: float) -> bool:
        ../esports-trader/src/backtest/maker_orders.py:220:    if spread_ticks(bid, ask) >= MAX_ENTRY_SPREAD_TICKS:
        ../esports-trader/src/backtest/maker_orders.py:237:        spread=0.0,
        ../esports-trader/src/backtest/maker_orders.py:243:    best_bid = book.best_bid_price()
        ../esports-trader/src/backtest/maker_orders.py:244:    best_ask = book.best_ask_price()
        ../esports-trader/src/backtest/maker_orders.py:245:    if best_bid is None or best_ask is None:
        ../esports-trader/src/backtest/maker_orders.py:247:    return (float(best_bid) + float(best_ask)) / 2.0
        ../esports-trader/src/trader/session_core.py:131:    "wide_spread": EntryBlock.WIDE_SPREAD,
        ../esports-trader/src/trader/session_core.py:190:        yes.best_bid is None
        ../esports-trader/src/trader/session_core.py:191:        or yes.best_ask is None
        ../esports-trader/src/trader/session_core.py:192:        or no.best_bid is None
        ../esports-trader/src/trader/session_core.py:193:        or no.best_ask is None
        ../esports-trader/src/trader/session_core.py:194:        or yes.best_bid <= 0
        ../esports-trader/src/trader/session_core.py:195:        or yes.best_ask <= 0
        ../esports-trader/src/trader/session_core.py:196:        or no.best_bid <= 0
        ../esports-trader/src/trader/session_core.py:197:        or no.best_ask <= 0
        ../esports-trader/src/trader/session_core.py:204:                bid=yes.best_bid,
        ../esports-trader/src/trader/session_core.py:205:                ask=yes.best_ask,
        ../esports-trader/src/trader/session_core.py:206:                bid_size=yes.best_bid_size,
        ../esports-trader/src/trader/session_core.py:207:                ask_size=yes.best_ask_size,
        ../esports-trader/src/trader/session_core.py:212:                bid=no.best_bid,
        ../esports-trader/src/trader/session_core.py:213:                ask=no.best_ask,
        ../esports-trader/src/trader/session_core.py:214:                bid_size=no.best_bid_size,
        ../esports-trader/src/trader/session_core.py:215:                ask_size=no.best_ask_size,
        ../esports-trader/src/trader/session_core.py:225:    yes_bid = yes.best_bid()
        ../esports-trader/src/trader/session_core.py:226:    yes_ask = yes.best_ask()
        ../esports-trader/src/trader/session_core.py:227:    no_bid = no.best_bid()
        ../esports-trader/src/trader/session_core.py:228:    no_ask = no.best_ask()
        ../esports-trader/src/trader/session_core.py:229:    if yes_bid is None or yes_ask is None or no_bid is None or no_ask is None:
        ../esports-trader/src/trader/session_core.py:231:    if yes_bid.price <= 0 or yes_ask.price <= 0 or no_bid.price <= 0 or no_ask.price <= 0:
        ../esports-trader/src/trader/session_core.py:240:                bid=yes_bid.price,
        ../esports-trader/src/trader/session_core.py:241:                ask=yes_ask.price,
        ../esports-trader/src/trader/session_core.py:242:                bid_size=yes_bid.size,
        ../esports-trader/src/trader/session_core.py:243:                ask_size=yes_ask.size,
        ../esports-trader/src/trader/session_core.py:248:                bid=no_bid.price,
        ../esports-trader/src/trader/session_core.py:249:                ask=no_ask.price,
        ../esports-trader/src/trader/session_core.py:250:                bid_size=no_bid.size,
        ../esports-trader/src/trader/session_core.py:251:                ask_size=no_ask.size,
        ../esports-trader/src/trader/session_core.py:318:        self._stale_cancels: tuple[str, ...] = ()
        ../esports-trader/src/trader/session_core.py:395:            raise AssertionError(f"stale plan cycle={planned.cycle} current={self._cycle}")
        ../esports-trader/src/trader/session_core.py:637:        self._watch_stale_cancels(sources.now_ns)
        ../esports-trader/src/trader/session_core.py:665:    def _watch_stale_cancels(self, now_ns: int) -> None:
        ../esports-trader/src/trader/session_core.py:674:        stale: list[str] = []
        ../esports-trader/src/trader/session_core.py:684:            stale.append(order.order_id)
        ../esports-trader/src/trader/session_core.py:698:        self._stale_cancels = (*self._stale_cancels, *stale)
        ../esports-trader/src/trader/session_core.py:700:    def take_stale_cancels(self) -> tuple[str, ...]:
        ../esports-trader/src/trader/session_core.py:702:        ids = self._stale_cancels
        ../esports-trader/src/trader/session_core.py:703:        self._stale_cancels = ()
        ../esports-trader/src/shared/utils/model_registry.py:47:    A live_dir without model.json is a stale leftover: it is deleted, not archived.
        ../esports-trader/src/shared/utils/market_scenario_report.py:19:    if row["market_status"] != "ok":
        ../esports-trader/src/shared/utils/telonex_book.py:1:"""Lazy Telonex book reads: as-of quotes and age/pair gates."""
        ../esports-trader/src/shared/utils/telonex_book.py:19:AsOfStatus = Literal["ok", "stale", "missing"]
        ../esports-trader/src/shared/utils/telonex_book.py:24:    """Best bid and ask of one raw snapshot; None on a side with no usable level."""
        ../esports-trader/src/shared/utils/telonex_book.py:26:    bid: float | None
        ../esports-trader/src/shared/utils/telonex_book.py:27:    ask: float | None
        ../esports-trader/src/shared/utils/telonex_book.py:32:    """Sorted best bid/ask per snapshot for one token inside a time window.
        ../esports-trader/src/shared/utils/telonex_book.py:39:    bids: tuple[float | None, ...]
        ../esports-trader/src/shared/utils/telonex_book.py:40:    asks: tuple[float | None, ...]
        ../esports-trader/src/shared/utils/telonex_book.py:45:    """Best bid/ask/mid at one as-of timestamp for one token."""
        ../esports-trader/src/shared/utils/telonex_book.py:48:    bid: float
        ../esports-trader/src/shared/utils/telonex_book.py:49:    ask: float
        ../esports-trader/src/shared/utils/telonex_book.py:58:    `quote` is set exactly when `status` is "ok". `book_ts_us` and `age_seconds`
        ../esports-trader/src/shared/utils/telonex_book.py:59:    always describe the newest snapshot at or before the target, so stale and
        ../esports-trader/src/shared/utils/telonex_book.py:71:    """Age and pair-gated radiant/dire midpoint result."""
        ../esports-trader/src/shared/utils/telonex_book.py:87:def parse_best_bid_ask(raw_bids: TelonexRawLevels, raw_asks: TelonexRawLevels) -> BestBidAsk:
        ../esports-trader/src/shared/utils/telonex_book.py:88:    """Best bid and ask of one raw snapshot; None on a side with no usable level."""
        ../esports-trader/src/shared/utils/telonex_book.py:89:    bid = None
        ../esports-trader/src/shared/utils/telonex_book.py:90:    if raw_bids is not None:
        ../esports-trader/src/shared/utils/telonex_book.py:91:        for item in raw_bids:
        ../esports-trader/src/shared/utils/telonex_book.py:93:            if price is not None and (bid is None or price > bid):
        ../esports-trader/src/shared/utils/telonex_book.py:94:                bid = price
        ../esports-trader/src/shared/utils/telonex_book.py:95:    ask = None
        ../esports-trader/src/shared/utils/telonex_book.py:96:    if raw_asks is not None:
        ../esports-trader/src/shared/utils/telonex_book.py:97:        for item in raw_asks:
        ../esports-trader/src/shared/utils/telonex_book.py:99:            if price is not None and (ask is None or price < ask):
        ../esports-trader/src/shared/utils/telonex_book.py:100:                ask = price
        ../esports-trader/src/shared/utils/telonex_book.py:101:    return BestBidAsk(bid=bid, ask=ask)
        ../esports-trader/src/shared/utils/telonex_book.py:119:        bid = book.bids[index]
        ../esports-trader/src/shared/utils/telonex_book.py:120:        ask = book.asks[index]
        ../esports-trader/src/shared/utils/telonex_book.py:121:        if bid is not None and ask is not None:
        ../esports-trader/src/shared/utils/telonex_book.py:122:            return (bid + ask) / 2.0
        ../esports-trader/src/shared/utils/telonex_book.py:150:    """Load one token's best bid/ask snapshots inside [start_us, end_us], or None when empty."""
        ../esports-trader/src/shared/utils/telonex_book.py:156:    bids: list[float | None] = []
        ../esports-trader/src/shared/utils/telonex_book.py:157:    asks: list[float | None] = []
        ../esports-trader/src/shared/utils/telonex_book.py:166:            columns=["timestamp_us", "bids", "asks"],
        ../esports-trader/src/shared/utils/telonex_book.py:171:        for timestamp_us, cell_bids, cell_asks in zip(
        ../esports-trader/src/shared/utils/telonex_book.py:172:            frame["timestamp_us"], frame["bids"], frame["asks"], strict=True
        ../esports-trader/src/shared/utils/telonex_book.py:174:            best = parse_best_bid_ask(
        ../esports-trader/src/shared/utils/telonex_book.py:175:                cast(TelonexRawLevels, cell_bids), cast(TelonexRawLevels, cell_asks)
        ../esports-trader/src/shared/utils/telonex_book.py:178:            bids.append(best.bid)
        ../esports-trader/src/shared/utils/telonex_book.py:179:            asks.append(best.ask)
        ../esports-trader/src/shared/utils/telonex_book.py:187:        bids=tuple(bids[index] for index in order),
        ../esports-trader/src/shared/utils/telonex_book.py:188:        asks=tuple(asks[index] for index in order),
        ../esports-trader/src/shared/utils/telonex_book.py:194:    bid = book.bids[index]
        ../esports-trader/src/shared/utils/telonex_book.py:195:    ask = book.asks[index]
        ../esports-trader/src/shared/utils/telonex_book.py:196:    if bid is None or ask is None:
        ../esports-trader/src/shared/utils/telonex_book.py:201:        bid=bid,
        ../esports-trader/src/shared/utils/telonex_book.py:202:        ask=ask,
        ../esports-trader/src/shared/utils/telonex_book.py:203:        mid=(bid + ask) / 2.0,
        ../esports-trader/src/shared/utils/telonex_book.py:212:    missing; first candidate too old is stale; only the one-sided walk bails on age.
        ../esports-trader/src/shared/utils/telonex_book.py:213:    A stale result carries no quote, so no caller can read a price past the gate.
        ../esports-trader/src/shared/utils/telonex_book.py:217:        return AsOfResult(status="missing", quote=None, book_ts_us=None, age_seconds=None)
        ../esports-trader/src/shared/utils/telonex_book.py:222:        return AsOfResult(status="stale", quote=None, book_ts_us=first_ts, age_seconds=first_age)
        ../esports-trader/src/shared/utils/telonex_book.py:238:    return AsOfResult(status="missing", quote=None, book_ts_us=first_ts, age_seconds=first_age)
        ../esports-trader/src/shared/utils/telonex_book.py:243:    if radiant.status == "stale" or dire.status == "stale":
        ../esports-trader/src/shared/utils/telonex_book.py:244:        return MarketPairQuote(status="stale_quote", market_p_radiant=None)
        ../esports-trader/src/shared/utils/telonex_book.py:259:    return row["market_status"] == "ok"
        ../esports-trader/src/shared/utils/trading.py:40:def spread_ticks(bid: float, ask: float) -> int:
        ../esports-trader/src/shared/utils/trading.py:41:    """Round ask-bid onto the 0.01 grid so 0.60-0.52 counts as 8, not 7."""
        ../esports-trader/src/shared/utils/trading.py:42:    return round((ask - bid) / QUOTE_GRID)
        ✓ • 0ms
    
    ## Activity
    
        $ sed -n '200,247p;247,345p;360,405p' .analysis/ladder-thin-2026-09-24/REPORT.md
          против 0.7% на ликвидной Dota. Но потеря не от того, что «не продать», а от того, что продаём
          после того, как цена уже ушла.
    
        ### 3.3 Что ловит плохие карты на входе
    
        | гейт на входе | карт убрано | их PnL | PnL оставшихся | худшая оставшаяся |
        |---|---:|---:|---:|---:|
        | нет | 0 | 0 | +$639.5 | −$94.7 |
        | спред ≥ 8 (сейчас) | 20 | −$25.4 | +$664.9 | −$94.7 |
        | спред ≥ 6 | 39 | −$106.8 | +$781.4 | −$78.9 |
        | спред ≥ 4 | 80 | −$114.9 | +$789.5 | −$78.9 |
        | пре-хорн объём 30 мин < $1000 | 82 | −$91.6 | +$731.1 | −$94.7 |
        | пре-хорн объём 30 мин < $3000 | 147 | −$130.4 | +$769.9 | −$78.9 |
        | младшие лиги LoL | 126 | −$406.7 | +$1 046.2 | −$94.7 |
        | look-ahead фильтр не прошёл | 50 | **+$40.9** | +$598.6 | −$94.7 |
    
        - Объёмный гейт в основном убирает карты младших лиг LoL. На Dota он выкидывает безубыточные карты
          (в 18866 при пороге $1000: 8 карт, +$9.8).
        - CI у гейтов по спреду и объёму пересекает ноль. Направление видно, N маленький.
        - Гейт по спреду сейчас смотрит на книгу вместе с нашим бидом. После фикса A он станет честнее.
    
        ### 3.4 Skip или size-down
    
        - **Size-down по глубине уровня:** наша заявка больше половины уровня на 57–60% ликвидных заявок.
          Такой кап режет прибыльный объём. Это повтор K1/K2 (`docs/experiments/buy-depth-cap`).
        - **Size-down по объёму ленты:** при объёме ниже ~$2–3k за 30 мин размер уходит ниже
          `MIN_ORDER_SIZE = 5` и превращается в skip. То есть это тот же skip, только сложнее.
        - **Skip на входе** проще и даёт тот же эффект. Внешняя практика говорит то же:
          «ограничивай размер тем, что можешь продать; на тонком рынке — минимальный размер или ничего»
          (`reports/web-devin.md`, poly-maker TIPS.md).
    
        ### 3.5 Рекомендация
    
        1. **Не строить динамический сайзинг.**
        2. **Спред-гейт 8 → 6 тиков** по книге без наших заявок (после фикса A). Проверить в бэктесте
           варианты 8 / 6 / 4.
        3. **Младшие лиги LoL** — самая большая сумма (−$407). Быстрый шаг: убрать убыточные младшие лиги из
           `config/lol_league_whitelist.json`. Отдельно — разбор модели LoL (вход против дрейфа). Это
           отдельная задача, не часть проблем 1–2.
        4. **Объёмный skip-гейт на входе** — только если после шагов 1–3 бэктест всё ещё видит хвост на
           тонких рынках. Ни лайв, ни старые бэктесты не показывают, что тонкие по объёму карты теряют.
           Если его всё же делать, первые кандидаты такие: Dota — объём за 10 мин до входа ≤ $166.66,
           LoL — ≤ 4 уникальных ордера за 60 мин (`reports/lookahead-luna.md`). Пороги выбраны после
           просмотра валидации: их надо проверить на новых картах. Окна пересчитать на момент первого
           `PlaceOrder`, а не на горн. Сначала писать решение гейта в лог в лайве (shadow), без
           блокировки.
    
        ## 4. Бэктест: look-ahead фильтр и паритет
        ## 4. Бэктест: look-ahead фильтр и паритет
    
        ### 4.1 Что режет фильтр
    
        Источник: `reports/lookahead-luna.md` (8 171 карта, метка совпала с функцией проекта на всех).
    
        | игра | сплит | карт-кандидатов | проходят фильтр | не проходят | доля отсева |
        |---|---|---:|---:|---:|---:|
        | Dota | train | 2 485 | 1 203 | 1 282 | 51.6% |
        | Dota | validation | 711 | 598 | 113 | 15.9% |
        | LoL | train | 2 900 | 2 139 | 761 | 26.2% |
        | LoL | validation | 2 075 | 1 686 | 389 | 18.7% |
    
        - Из 113 отсеянных карт Dota у 13 нет market cache. Значит, в бэктест реально войдёт ~100 карт
          (598 → ~698, `reports/backtest-devin.md`). В LoL войдёт до 389 карт.
        - В 18866 фильтр отсеивает 74% карт train и 40% карт validation.
        - Провалы идут по всей карте, а не только в начале или в конце. У отсеянных карт валидации первая
          5-минутка проваливается в 46% (Dota) и 67% (LoL), последняя — в 60% и 56%.
        - Старые бэктесты с тонкими картами (до 2026-09-04): разница PnL «тонкая минус плотная» на карту
          Dota +$0.17 [−$2.36, +$2.73] (40 против 378 карт), LoL +$0.16 [−$0.72, +$1.11] (256 против 1 254).
          Тонкие карты дают меньше филлов, но не хуже по PnL и markout.
        - Гейты на входе хорошо воспроизводят метку фильтра. Dota: объём за 10 мин до горна ≤ $166.66 —
          ловит 56.6% тонких карт, оставляет 99.3% плотных (AUC 0.92). LoL: ≤ 4 уникальных ордера за
          60 мин — 58.9% и 95.2% (AUC 0.90). Оба порога выбраны после просмотра валидации, поэтому эти
          доли надо проверить на новой отложенной выборке. И главное: сама метка с убытками не связана,
          поэтому такой гейт не обязан улучшить PnL.
        - Лайв-карты, которые фильтр отсёк бы: 126 из 596, торговались 50, их PnL +$40.9. Все крупные
          убыточные карты фильтр проходят.
        - В 18866 фильтр режет 23 из 80 лайв-карт (+$71.2). У 11 из них за всю игру нет ни одного принта.
          Я проверил книгу у трёх: у двух 35–44 тыс. изменений книги (боты переставляют заявки, тейкеров
          нет), у одной книги в данных нет (дыра в сборе). Из остальных 12 восемь проваливают последнюю
          5-минутку, когда исход ясен и торги стихают.
    
        ### 4.2 Как убрать фильтр (план)
    
        Источник: `reports/backtest-devin.md`.
    
        1. Dota, `select_matches` (`src/prepare_dataset/prepare_dataset.py:76-99`): сначала разделить
           каталог по cutoff, фильтр применять только к train.
        2. **Закрыть утечку:** `write_datasets` кладёт `validation_minute_rows` в
           `production_training.parquet` (`prepare_dataset.py:280`). Для этого выхода фильтр оставить.
        3. LoL, `eligibility_drop_reason` (`src/lol/05_prepare_dataset.py:527-543`): удалить проверку в
           ветке validation, в train оставить. Добавить в аудит колонку `tape_dense`. Сейчас отсеянные карты
           видны только в полном аудите со сплитом `none`, сплит надо восстанавливать по старту события.
        4. Пересобрать: `make market-data` (13 карт без кэша), `make prepare`, `make train`, бэктест.
        5. Тесты: `tests/test_prepare_dataset.py:444-453`, `tests/test_lol_prepare_dataset.py:1433`, счётчики
           карт в smoke/validation тестах. Новый тест на утечку в production training.
    
        ### 4.3 Бэктест оптимистичен на тонких книгах
    
        Реплеи лайв-карт по расписанию лайв-сигналов (`reports/backtest-devin.md`, seed 0):
    
        | группа | карт, где торговали обе стороны | лайв PnL | реплей PnL |
        |---|---:|---:|---:|
        | Dota все | 67 | +$42.37 | +$298.60 |
        | Dota 18866 | 12 | +$5.78 | +$137.29 |
        | LoL | 117 | −$177.54 | +$465.30 |
    
        - Клип разный: Dota лайв $60 против $100 в бэктесте, LoL лайв $5–30 против $100. Для LoL
          сравнимы только знаки.
        - В старом бэктесте с тонкими картами (до 2026-09-04) карты с пре-хорн объёмом < $500 дали
          +$136 (90 карт × сид). В лайве та же корзина дала −$48 по `match.json` и −$188 по FIFO-лотам
          (62 карты, `reports/exit-devin.md`).
        - Причины (по убыванию уверенности): модель очереди на тонком уровне даёт филлы, которых в лайве
          нет; self-join есть только в лайве (проблема 1); `strip_own_book` приближённый (центы, время);
          разные клипы.
        - Вывод: **гейт для тонких рынков в бэктесте будет выглядеть хуже, чем в лайве.** Сначала фикс A+B
          в обоих адаптерах. Потом заново сравнить лайв и реплей. Только потом судить гейты.
    
        ### 4.4 Как встраивать гейт (если он понадобится)
    
        - Одна чистая функция решения в `src/strategy/`, два адаптера кормят её одинаковыми данными в один
          момент времени (`reports/backtest-devin.md` §D). Новая причина блокировки — через
          `BlockReason`, кодек подхватит её сам.
        - `schema_version` трейса не поднимать: `replay_core_trace.py:328` сравнивает его строго и отвергнет
          старые архивы. Новые поля декодировать через `setdefault`, как для `mid_spike`
          (`core_trace_codec.py:387-395`). Здесь backtest-devin и selfjoin-grok расходятся; прав второй.
        - Источник данных для пре-хорн гейта в лайве — журнал коллектора (`last_trade_price`, fsync ≤ 1 с,
          смонтирован в трейдер read-only). Сейчас трейдер его не читает. Для LoL это единственный вариант:
          LoL входит через ~12 с после горна, своей истории у трейдера нет.
        - В бэктесте тот же показатель считается из `parquet/trades`. **Локальный канал `trades`
          заканчивается 2026-09-17** (коммит `6ba30b63` перевёл бэктест на `onchain_fills`). Для паритета
          его надо снова синхронизировать. `onchain_fills` в лайве недоступен: коллектор публикует только
          закрытые дни.
        - Gamma `liquidityNum` есть у активных рынков (я проверил), но его значение на момент входа нигде
          не архивируется. Для бэктеста он не годится.
    
        ### 4.5 Протокол оценки
    
        Источник: `reports/backtest-devin.md`, `reports/postmortem-grok.md`.
    
        - Одна замороженная вселенная для всех рук (Dota ~698 карт). Метки «тонкая / ликвидная» фиксировать
          до просмотра результатов.
        - Руки: A1 без фильтра и без гейта; A2 + фикс A+B; A3 + спред-гейт 6; A4 + спред-гейт 4.
        - Сиды 0–2 как скрининг, 12 сидов — только если хвост лучше контроля.
        - Метрики: net, net/депозит, худшая карта, CVaR 5%, число карт < −$50, max drawdown, трафик заявок.
          Отдельно для тонких и ликвидных карт.
        - Правило приёмки (записать до запуска): на ликвидных картах гейт почти не срабатывает
          (≤10% заявок, ≤5% карт); на тонких хвост короче; net падает не больше заранее выбранной цены.
        ## 6. План работ
    
        1. **Фикс A** (книга без наших заявок в `books_from_md`). Маленький дифф, паритет с бэктестом.
           Проверка: юнит-тест, `make parity` на старых архивах, первая лайв-карта в трейсе.
        2. **Фикс B** (снятие одинокой L0 через T с). Бэктест-руки T ∈ {выкл, 1, 3, 5} × {только L0, все}.
        3. **Убрать look-ahead фильтр из validation** и закрыть утечку в production training. В бэктест войдут
           ~100 карт Dota и до 389 карт LoL. Новый baseline.
        4. **Заново сравнить лайв и реплей** на лайв-архивах после A+B. Понять, сколько разрыва осталось.
        5. **Спред-гейт 6 / 4 тика** в бэктесте.
        6. **Младшие лиги LoL:** решение по вайтлисту сейчас, разбор модели — отдельной задачей.
        7. **Объёмный гейт** — только shadow-лог в лайве, если шаги 1–5 не закроют хвост.
    
        ## 7. Что опровергнуто или не подтверждено
    
        - «Дело в объёме, а не в спреде» (H4) — для хвоста не подтвердилось. Худшая карта (−$94.7) имела
          154 принта и $2.9k за 30 мин до входа, но широкий спред.
        - «Купили — и не продать» — не подтвердилось (продажа ~5 с, до сеттлмента 0–2%).
        - «Dota 18866 убыточна, потому что тонкая» — нет, +$188.
        - «Look-ahead фильтр защищал от убытков» — нет: отсечённые им лайв-карты в плюсе.
        - «127 из 127 лайв-карт проходят фильтр» (backtest-devin) — артефакт выборки. Эти карты взяты из
          каталога, где фильтр уже применён. На всех лайв-картах фильтр режет 126 из 596.
        - «246 из 257 карт никогда не закрываются» (thin-live-devin) — артефакт. Остаток считался по `Fill`
          из `core_trace` без удаления дублей. По `session.jsonl` медиана sell/buy = 1.0.
        - «Gamma `liquidityNum` отсутствует» (postmortem-grok) — верно только для закрытых рынков.
        - «Фильтр отсеивает в LoL лишь несколько процентов валидации» (backtest-devin) — неверно: 389 из
          2 075 (18.7%). Агент смотрел аудит, в котором отсеянных карт нет.
        - «Тонкие по фильтру карты теряют в бэктесте» — нет: разница PnL на карту около нуля, CI включает 0.
        - «Уходить при первом нуле» — хуже, чем с задержкой: режет 21% объёма.
        - «Возвращаться на ту же цену, когда компания вернулась» — повторные филлы в минусе (N=15).
    
        Побочные находки:
        - `core_trace` пишет каждый `Fill` дважды (~46% филлов). Ядро дубли отбрасывает (`seen_fill_ids`),
          инвентарь верный. Но `data/order_outcomes/orders.parquet` завышает `n_fills` и `filled_qty`
          примерно в 2 раза.
        - ~4.4% `CancelAck` приходят без записанного в плане запроса отмены.
        - `block_timestamp` в `onchain_fills` отстаёт от времени сделки на ~2.87 с.
    
        ## 8. Вопросы к тебе
    
        1. Фикс B: только L0 (больше в сумме) или все ступеньки (сильнее режет хвост)?
        2. После снятия: ждать нового сигнала (рекомендую) или сразу вставать ниже, к другим?
        3. Младшие лиги LoL: убрать из вайтлиста сейчас? Какие оставить (LCK, LCS, LEC, CBLOL)?
        4. Спред-гейт: 6 или 4 тика?
        5. Production training: оставить фильтр на `validation_minute_rows` (рекомендую)?
        6. Проверять ли отдельно mid для модели в лайве (`read_raw_pair`), который тоже видит наши заявки?
    
        ✓ • 0ms
    
    ## Activity
    
        $ sed -n '1,180p' .analysis/ladder-thin-2026-09-24/notes/orchestrator-checks.md
        # Orchestrator checks (for REPORT.md "refuted/verified" list)
    
        1. Gamma `liquidityNum`/`liquidity` on ACTIVE map markets: present (GET events?tag_id=102366&active=true&closed=false&limit=50,
           2026-09-24: `dota2-tt1-st1-2026-09-24-game2` liquidityNum=1462.1302, rewardsMinSize=50, rewardsMaxSpread=4.5).
           postmortem-grok's "absent" holds only for closed/old payloads. Backtest parity still impossible from local parquet.
        2. H1 top-of-book check (notes/orchestrator-01.md): alone share 7-17% of at-touch time, spells p50 0.1 s / p90 2-4 s.
        3. Join lead (match.json horn - joined): Dota GRID p50 75 s (N=245, 221 before horn), Oddin p50 873 s (N=57),
           LoL p50 -12 s (N=310, 1 before horn). Verified; matches postmortem-grok.
        4. Duplicate Fill events in core_trace: grid-2996035-m1 47 events / 25 unique; grid-3010621-m1 30 / 15.
           Duplicates never change the inventory digest (core dedupes via seen_fill_ids, lifecycle.py:612,664).
           Analysis pitfall only: order_outcomes n_fills/filled_qty ~2x inflated (exit-devin claim, verified on 2 maps).
        5. Look-ahead tape filter applied to ALL live maps (horn <= 2026-09-22, notes/check_tape_live.py):
           Dota 18866 80 maps: 57 pass (+$123.9) / 23 fail (+$71.2); LoL 302: 239 pass (-$405.0) / 63 fail (-$47.6);
           20279 Oddin 42: 28/14 (+$295.9/+$1.3); 19944 90: 81/9; 18865 32: 17/15.
           => backtest-devin's "127/127 live maps pass" is a selection artifact (joined maps come from the filtered catalog).
           Filter-failing live maps did NOT lose money.
        6. Why 18866 maps fail (notes/check_tape_live2.py): 11/23 have zero onchain prints over the whole map; e.g.
           grid-3010154-m1 (35.5k book snapshots, 0 prints), grid-3007236-m1 (44k snapshots, 0 prints) = dead markets
           with re-quoting bots and no takers (our bot: 0 fills). grid-3008839-m1: no book snapshots = data gap.
           Most other failures are the LAST buckets going quiet (outcome decided), e.g. grid-3007266-m3 [..,212,0,0,0].
        7. Local Telonex `trades` channel ends 2026-09-17 (0 files from 09-18; commit 6ba30b63 moved execution to onchain_fills).
           A gate defined on WS last_trade_price needs `parquet/trades` re-synced for backtest parity after 09-17.
        8. LoL whitelist in live: before 2026-09-16 traded non-whitelisted leagues (Liga Portuguesa, NLC, LFL, LJL, Rift Legends,
           Circuito Desafiante, LIT, Hellenic, Prime, TCL, LRS); after 09-16 (ff23c36f) only whitelisted ones
           (EMEA Masters, LEC, LCS, Hitpoint Masters, Road of Legends). thin-live-devin's "whitelist is not a tier filter"
           is true only for the pre-09-16 history; the whitelist still contains losing minor leagues.
        9. Re-aggregated thin-live-devin maps_full.parquet: dota traded 203 maps +$1034.6; lol 210 maps -$395.1 (with rebate).
           Spread at join (bought token) >=4 ticks: 80 maps -$114.9; <=2: 289 maps +$564.4. Minor LoL after 09-16:
           18 maps -$50.8 on $886 buy notional (-5.7%); before 09-16: 108 maps -$355.8 on $6653 (-5.3%).
        10. lonely-devin example 1 re-checked on raw book_snapshot_full (grid-3007263-m1, token 0, 2026-09-18):
            size in [0.56,0.57) 1900.95 -> 107.14 (= c19 qty) at 15:29:37.167; stays 107.14 until 15:30:38.849 (61.7 s);
            best bid 0.57-0.58 above us the whole time => stranded lower rung (benign case). Verified.
        11. leave-devin R4_3s split by placement level (work/leave-devin/fill_eval*.parquet, mode=out, removed pnl = rem_qty*(m300-price)):
            grace 2 s: L0 -81.6, L1 +20.7, L2 +41.1 (total -19.8). grace 0.3 s: L0 -128.0, L1 -16.3, L2 +35.9 (total -108.4).
            L0-only trigger share of L0 rungs: all 12.9%; 18866 20.0%; 18865 23.3%; 19944 14.6%; 20279 8.3%; LoL 9.1%.
            Tail maps (14 maps, map pnl < -$20, sum -$619.1): L0-only removes -$123.6 (g2s) / -$138.5 (g0.3s); all-rung -$173.3 (g2s).
            In-sample, N small (57 L0 fills removed at g2s): rank L0-only vs all-rung needs the backtest.
        12. REFUTED thin-live-devin "246/257 traced maps never flatten via trades" and its inv_mae numbers: its end_qty /
            never_flat come from core_trace Fill events without fill_id dedupe. session.jsonl on the same maps:
            grid-3010621-m1 BUY 1073.96 / SELL 1073.96; grid-3010623-m3 714.22 / 714.22; 9012123019 437.35 / 437.34.
            maps_full end_pos_qty (session-based) median 0.0, sell_qty/buy_qty median 1.0. exit-devin (deduped) holds:
            settled share of bought qty 0-2%. thin-live-devin PnL numbers (match.json + rebate) are not affected.
        ✓ • 0ms
    
    ## Activity
    
        $ rg -n 'mid_spike|wide_spread|MAX_ENTRY_SPREAD|quote_age|stale|price.*move|spread_ticks' ../esports-trader/src/strategy ../esports-trader/src/trader/session_quoting.py ../esports-trader/src/backtest/maker_orders.py | head -160
        ../esports-trader/src/backtest/maker_orders.py:18:from shared.constants.strategy import MAX_ENTRY_PRICE, MAX_ENTRY_SPREAD_TICKS, MIN_ENTRY_PRICE
        ../esports-trader/src/backtest/maker_orders.py:19:from shared.utils.trading import spread_ticks
        ../esports-trader/src/backtest/maker_orders.py:85:    `level_index` is the rung this order holds *now*: a reprice can move a resting
        ../esports-trader/src/backtest/maker_orders.py:220:    if spread_ticks(bid, ask) >= MAX_ENTRY_SPREAD_TICKS:
        ../esports-trader/src/strategy/scheduling.py:6:from strategy.mid_spike import mid_spike_boundary_ns
        ../esports-trader/src/strategy/scheduling.py:74:def entry_stale_boundary_ns(*, state: StrategyState) -> int | None:
        ../esports-trader/src/strategy/scheduling.py:83:    return int(signal.received_ns + round(state.freshness.entry_stale_s * NS_PER_S))
        ../esports-trader/src/strategy/scheduling.py:97:    spike_boundary = mid_spike_boundary_ns(state=state, now_ns=now_ns)
        ../esports-trader/src/strategy/scheduling.py:103:    stale_boundary = entry_stale_boundary_ns(state=state)
        ../esports-trader/src/strategy/scheduling.py:104:    if stale_boundary is not None:
        ../esports-trader/src/strategy/scheduling.py:105:        # Already past entry_stale: wake now so BUYs cancel before the next wipe.
        ../esports-trader/src/strategy/scheduling.py:106:        if stale_boundary <= now_ns:
        ../esports-trader/src/strategy/scheduling.py:108:        if stale_boundary < deadline:
        ../esports-trader/src/strategy/scheduling.py:109:            return stale_boundary
        ../esports-trader/src/strategy/lifecycle.py:7:from strategy.mid_spike import empty_mid_spike
        ../esports-trader/src/strategy/lifecycle.py:70:        mid_spike=empty_mid_spike(),
        ../esports-trader/src/strategy/quoting.py:24:from strategy.mid_spike import mid_spike_active, observe_mid_spike
        ../esports-trader/src/strategy/quoting.py:135:        if not is_fresh(now_ns=now_ns, ts_ns=book.ts_ns, max_age_s=state.freshness.book_stale_s):
        ../esports-trader/src/strategy/quoting.py:170:        max_entry_spread_ticks=policy.max_entry_spread_ticks,
        ../esports-trader/src/strategy/quoting.py:218:        return "stale_signal"
        ../esports-trader/src/strategy/quoting.py:234:        return "stale_book"
        ../esports-trader/src/strategy/quoting.py:246:    for key in ("wide_spread", "min_price", "max_price", "fair"):
        ../esports-trader/src/strategy/quoting.py:289:        return QuoteChoice(state=state, targets=(), no_buy_reason="stale_book")
        ../esports-trader/src/strategy/quoting.py:701:    if signal is None or not _signal_fresh(state, now_ns, state.freshness.exit_stale_s):
        ../esports-trader/src/strategy/quoting.py:719:    entry_fresh = _signal_fresh(state, now_ns, state.freshness.entry_stale_s)
        ../esports-trader/src/strategy/quoting.py:735:    if not _signal_fresh(state, now_ns, state.freshness.exit_stale_s) or latch is None:
        ../esports-trader/src/strategy/quoting.py:736:        return _blocked(state=state, reason="stale_signal")
        ../esports-trader/src/strategy/quoting.py:739:        state = _cancel_orders(state=state, reason="stale_signal", buys_only=True)
        ../esports-trader/src/strategy/quoting.py:741:            return state, empty_plan(reason="stale_signal")
        ../esports-trader/src/strategy/quoting.py:747:        now_ns=now_ns, ts_ns=latch.fair_ts_ns, max_age_s=state.freshness.exit_stale_s
        ../esports-trader/src/strategy/quoting.py:750:        reason = "anchor" if latch.no_buy_reason == "anchor" else "stale_signal"
        ../esports-trader/src/strategy/quoting.py:813:        return _blocked(state=state, reason="stale_book")
        ../esports-trader/src/strategy/quoting.py:828:    if not is_fresh(now_ns=now_ns, ts_ns=latch.fair_ts_ns, max_age_s=state.freshness.exit_stale_s):
        ../esports-trader/src/strategy/quoting.py:839:        return _blocked(state=state, reason="stale_book")
        ../esports-trader/src/strategy/quoting.py:873:    state = observe_mid_spike(state=state, policy=policy, now_ns=now_ns)
        ../esports-trader/src/strategy/quoting.py:877:    if mid_spike_active(state=state, now_ns=now_ns):
        ../esports-trader/src/strategy/quoting.py:879:        return _blocked(state=state, reason="mid_spike", buys_only=True)
        ../esports-trader/src/strategy/types.py:16:    "wide_spread",
        ../esports-trader/src/strategy/types.py:21:    "stale_signal",
        ../esports-trader/src/strategy/types.py:22:    "stale_book",
        ../esports-trader/src/strategy/types.py:32:    "mid_spike",
        ../esports-trader/src/strategy/types.py:102:    book_stale_s: float
        ../esports-trader/src/strategy/types.py:103:    entry_stale_s: float
        ../esports-trader/src/strategy/types.py:104:    exit_stale_s: float
        ../esports-trader/src/strategy/types.py:283:    mid_spike: MidSpikeState
        ../esports-trader/src/strategy/mid_spike.py:11:def empty_mid_spike() -> MidSpikeState:
        ../esports-trader/src/strategy/mid_spike.py:70:def observe_mid_spike(
        ../esports-trader/src/strategy/mid_spike.py:75:    if books is None or policy.mid_spike_lookback_s <= 0.0 or policy.mid_spike_cooloff_s <= 0.0:
        ../esports-trader/src/strategy/mid_spike.py:77:    gate = state.mid_spike
        ../esports-trader/src/strategy/mid_spike.py:78:    lookback_ns = round(policy.mid_spike_lookback_s * NS_PER_S)
        ../esports-trader/src/strategy/mid_spike.py:86:        observed[index].drop + 1e-12 >= policy.mid_spike_threshold
        ../esports-trader/src/strategy/mid_spike.py:91:        cooloff_until = max(cooloff_until, now_ns + round(policy.mid_spike_cooloff_s * NS_PER_S))
        ../esports-trader/src/strategy/mid_spike.py:94:        mid_spike=MidSpikeState(
        ../esports-trader/src/strategy/mid_spike.py:100:def mid_spike_active(*, state: StrategyState, now_ns: int) -> bool:
        ../esports-trader/src/strategy/mid_spike.py:101:    until = state.mid_spike.cooloff_until_ns
        ../esports-trader/src/strategy/mid_spike.py:107:def mid_spike_boundary_ns(*, state: StrategyState, now_ns: int) -> int | None:
        ../esports-trader/src/strategy/mid_spike.py:108:    until = state.mid_spike.cooloff_until_ns
        ../esports-trader/src/strategy/policy.py:13:    MAX_ENTRY_SPREAD_TICKS,
        ../esports-trader/src/strategy/policy.py:35:    max_entry_spread_ticks: int
        ../esports-trader/src/strategy/policy.py:42:    mid_spike_lookback_s: float
        ../esports-trader/src/strategy/policy.py:43:    mid_spike_threshold: float
        ../esports-trader/src/strategy/policy.py:44:    mid_spike_cooloff_s: float
        ../esports-trader/src/strategy/policy.py:61:        max_entry_spread_ticks=MAX_ENTRY_SPREAD_TICKS,
        ../esports-trader/src/strategy/policy.py:68:        mid_spike_lookback_s=MID_SPIKE_LOOKBACK_S,
        ../esports-trader/src/strategy/policy.py:69:        mid_spike_threshold=MID_SPIKE_THRESHOLD,
        ../esports-trader/src/strategy/policy.py:70:        mid_spike_cooloff_s=MID_SPIKE_COOLOFF_S,
        ../esports-trader/src/strategy/policy.py:86:        max_entry_spread_ticks=MAX_ENTRY_SPREAD_TICKS,
        ../esports-trader/src/strategy/policy.py:93:        mid_spike_lookback_s=MID_SPIKE_LOOKBACK_S,
        ../esports-trader/src/strategy/policy.py:94:        mid_spike_threshold=MID_SPIKE_THRESHOLD,
        ../esports-trader/src/strategy/policy.py:95:        mid_spike_cooloff_s=MID_SPIKE_COOLOFF_S,
        ../esports-trader/src/strategy/signals.py:3:from shared.utils.trading import ceil_to_grid, floor_to_grid, spread_ticks
        ../esports-trader/src/strategy/signals.py:72:    max_entry_spread_ticks: int,
        ../esports-trader/src/strategy/signals.py:80:    if spread_ticks(bid, ask) >= max_entry_spread_ticks:
        ../esports-trader/src/strategy/signals.py:81:        return "wide_spread"
        ../esports-trader/src/strategy/signals.py:95:    max_entry_spread_ticks: int,
        ../esports-trader/src/strategy/signals.py:105:            max_entry_spread_ticks=max_entry_spread_ticks,
        ✓ • 0ms
    
    ## Activity
    
        $ sed -n '190,242p' ../esports-trader/src/prepare_dataset/prepare_dataset.py; sed -n '340,420p' ../esports-trader/src/lol/05_prepare_dataset.py
    
    
        def load_market_second_rows(path: Path) -> list[MarketSecondRow]:
            frame = pd.read_parquet(path)
            records = replace_nulls_with_none(frame).to_dict(orient="records")
            return [cast(MarketSecondRow, record) for record in records]
    
    
        def join_validation_rows(
            states: tuple[ExactSecondState, ...],
            inputs: MatchInputs,
            lag_seconds: int,
        ) -> list[ValidationDatasetRow]:
            states_by_second = {state.second: state for state in states}
            rows: list[ValidationDatasetRow] = []
            for market_row in inputs.market_rows:
                market_match_id = int(market_row["match_id"])
                market_second = int(market_row["second"])
                lagged_second = market_second - lag_seconds
                if lagged_second < MODEL_START_SECOND:
                    continue
                state = states_by_second.get(lagged_second)
                if state is None:
                    raise ValueError(
                        "validation join missing lagged state: "
                        f"market=({market_match_id}, {market_second}) "
                        f"lagged_second={lagged_second}"
                    )
                rows.append(
                    ValidationDatasetRow(
                        **market_row,
                        start_time=inputs.entry.start_time,
                        radiant_win=state.radiant_win,
                        **state_features(state),
                        market_radiant_prior=inputs.entry.radiant_prior,
                    )
                )
            return rows
    
    
        def write_rows(
            rows: (
                Sequence[DatasetRow]
                | Sequence[ValidationDatasetRow]
                | Sequence[GameFeatureRow]
                | Sequence[SplitRow]
            ),
            path: Path,
        ) -> None:
            write_parquet(pd.DataFrame(rows), path)
            logger.info("%s rows -> %s", len(rows), path)
    
    
                windows_dir,
                details_dir,
                item_catalog_for_worker(str(item_catalog_dir)),
                LOL_PREPARE_END_SECOND,
            )
            if isinstance(livestats, LivestatsDrop):
                return dropped_map(link, livestats, livestats.reason, livestats.invariant_rule, 0)
            tokens = parse_token_pair(link)
            bounds = replay_bounds(livestats)
            window = book_load_window(livestats, bounds, livestats.grid_rows)
            radiant_book = load_token_book(
                token_id=tokens.radiant,
                start_us=window.start_us,
                end_us=window.end_us,
                telonex_root=telonex_root,
            )
            dire_book = load_token_book(
                token_id=tokens.dire,
                start_us=window.start_us,
                end_us=window.end_us,
                telonex_root=telonex_root,
            )
            if radiant_book is None or dire_book is None:
                return dropped_map(link, livestats, REASON_MISSING_BOOKS, None, 0)
            prior = lookup_strict_prior(
                radiant_book, dire_book, livestats.spawn_us, LOL_PRIOR_WINDOW_SECONDS
            )
            if prior is None:
                return dropped_map(link, livestats, REASON_MISSING_PRIOR, None, 0)
            joined = join_market_rows(livestats, link, prior, radiant_book, dire_book)
            if not joined.rows:
                return dropped_map(
                    link, livestats, REASON_ZERO_USABLE_ROWS, None, joined.skipped_market_rows
                )
            event_id = str(link["event_id"])
            condition_id = str(link["condition_id"])
            clob = parse_clob_tokens(link)
            has_books = False
            has_onchain_fills = False
            if clob is not None:
                channels = has_required_channels(
                    telonex_root, clob, bounds.replay_start_wall, bounds.replay_end_wall
                )
                has_books = channels.has_books
                has_onchain_fills = channels.has_onchain_fills
            market_rows = tuple(
                build_market_seconds(
                    livestats.match_id,
                    event_id,
                    condition_id,
                    livestats.spawn_wall_seconds,
                    livestats.pauses,
                    bounds.last_market_second,
                    radiant_book,
                    dire_book,
                )
            )
            feature_rows = build_game_feature_rows(livestats, prior)
            reason = audit_reason(has_books, has_onchain_fills, len(joined.rows))
            token_id_0 = "" if clob is None else clob.token_id_0
            token_id_1 = "" if clob is None else clob.token_id_1
            radiant_win = int(link["resolved_outcome_index"]) == int(link["radiant_token_index"])
            backtest_audit: LolBacktestAuditRow = {
                "match_id": livestats.match_id,
                "event_id": event_id,
                "condition_id": condition_id,
                "slug": "",
                "token_id_0": token_id_0,
                "token_id_1": token_id_1,
                "radiant_token_index": int(link["radiant_token_index"]),
                "resolved_outcome": str(link["resolved_outcome"]),
                "resolved_outcome_index": int(link["resolved_outcome_index"]),
                "radiant_win": radiant_win,
                "replay_start_ts": unix_seconds(bounds.replay_start_wall),
                "game_ended_at_ts": unix_seconds(bounds.game_ended_at_wall),
                "replay_end_ts": unix_seconds(bounds.replay_end_wall),
                "has_books": has_books,
                "has_onchain_fills": has_onchain_fills,
                "signal_row_count": len(joined.rows),
                "market_second_count": len(market_rows),
                "source_lag_seconds": LOL_SOURCE_LAG_SECONDS,
        ✓ • 0ms
    
    ## Activity
    
        $ sed -n '210,265p' ../esports-trader/src/shared/utils/telonex_book.py; sed -n '15,95p' ../esports-trader/src/shared/utils/telonex_tape.py
    
            First candidate age is checked before any one-sided walk: no candidate is
            missing; first candidate too old is stale; only the one-sided walk bails on age.
            A stale result carries no quote, so no caller can read a price past the gate.
            """
            index = bisect_right(book.timestamps_us, target_us) - 1
            if index < 0:
                return AsOfResult(status="missing", quote=None, book_ts_us=None, age_seconds=None)
    
            first_ts = book.timestamps_us[index]
            first_age = (target_us - first_ts) / US_PER_SECOND
            if first_age > MAX_BOOK_AGE_SECONDS:
                return AsOfResult(status="stale", quote=None, book_ts_us=first_ts, age_seconds=first_age)
    
            while index >= 0:
                age_seconds = (target_us - book.timestamps_us[index]) / US_PER_SECOND
                if age_seconds > MAX_BOOK_AGE_SECONDS:
                    break
                quote = _side_quote_at(book, index, target_us)
                if quote is not None:
                    return AsOfResult(
                        status="ok",
                        quote=quote,
                        book_ts_us=quote.timestamp_us,
                        age_seconds=quote.age_seconds,
                    )
                index -= 1
            # Walk exhausted the usable window without a two-sided book.
            return AsOfResult(status="missing", quote=None, book_ts_us=first_ts, age_seconds=first_age)
    
    
        def resolve_market_pair(radiant: AsOfResult, dire: AsOfResult) -> MarketPairQuote:
            """Apply age and pair gates to a radiant/dire as-of pair."""
            if radiant.status == "stale" or dire.status == "stale":
                return MarketPairQuote(status="stale_quote", market_p_radiant=None)
            if radiant.quote is None or dire.quote is None:
                return MarketPairQuote(status="missing_quote", market_p_radiant=None)
            market_p = normalize_pair_mids(
                radiant_mid=radiant.quote.mid,
                dire_mid=dire.quote.mid,
                tolerance=PAIR_SUM_TOLERANCE,
            )
            if market_p is None:
                return MarketPairQuote(status="inconsistent_pair", market_p_radiant=None)
            return MarketPairQuote(status="ok", market_p_radiant=market_p)
    
    
        def is_ok_market_second(row: MarketSecondRow) -> TypeGuard[OkMarketSecondRow]:
            """True when the second carries a live mid. `resolve_market_pair` sets one only on "ok"."""
            return row["market_status"] == "ok"
    
    
        def lookup_market_p_after(
            radiant_book: TokenBook,
            dire_book: TokenBook,
            anchor_us: int,
        def datetime_to_us(value: datetime) -> int:
            return datetime_to_ns(value) // NS_PER_US
    
    
        def full_trade_bucket_count(spawn_us: int, ended_us: int) -> int:
            if ended_us <= spawn_us:
                return 0
            return (ended_us - spawn_us) // (TRADE_BUCKET_SECONDS * US_PER_SECOND)
    
    
        def load_token_tape_timestamps(
            *, token_id: str, start_us: int, end_us: int, telonex_root: Path
        ) -> list[int]:
            asset_dir = asset_channel_dir(telonex_root, ONCHAIN_CHANNEL, token_id)
            if not asset_dir.is_dir():
                return []
    
            timestamps: list[int] = []
            parquet_filters = [
                ("block_timestamp_us", ">=", start_us),
                ("block_timestamp_us", "<", end_us),
            ]
            for path in sorted(asset_dir.glob("*.parquet")):
                frame = pd.read_parquet(path, columns=["block_timestamp_us"], filters=parquet_filters)
                if frame.empty:
                    continue
                timestamps.extend(int(value) for value in frame["block_timestamp_us"])
            return timestamps
    
    
        def tape_passes_buckets(
            *,
            spawn_us: int,
            ended_us: int,
            token_ids: Sequence[str],
            telonex_root: Path,
        ) -> bool:
            buckets = full_trade_bucket_count(spawn_us, ended_us)
            if buckets == 0:
                return False
            bucket_us = TRADE_BUCKET_SECONDS * US_PER_SECOND
            last_end_us = spawn_us + buckets * bucket_us
            timestamps: list[int] = []
            for token_id in token_ids:
                timestamps.extend(
                    load_token_tape_timestamps(
                        token_id=token_id,
                        start_us=spawn_us,
                        end_us=last_end_us,
                        telonex_root=telonex_root,
                    )
                )
            counts = [0] * buckets
            for timestamp_us in timestamps:
                counts[(timestamp_us - spawn_us) // bucket_us] += 1
            return all(count >= MIN_TRADES_PER_BUCKET for count in counts)
    
    
        def match_passes_tape_buckets(entry: CatalogEntry, telonex_root: Path) -> bool:
            return tape_passes_buckets(
                spawn_us=datetime_to_us(entry.anchor_at),
                ended_us=datetime_to_us(entry.ended_at),
                token_ids=entry.gamma.token_ids,
                telonex_root=telonex_root,
            )
        ✓ • 0ms
    
    ## Activity
    
        $ rg -n 'build_minute_rows|market_p_radiant|market_status|future' ../esports-trader/src/prepare_dataset/prepare_dataset.py | head -100; rg -n 'market_status|signal_market_p_radiant_300s|labeled_row_count' ../esports-trader/src/lol/05_prepare_dataset.py | head -100
        124:        run = run + 1 if row["market_status"] != "ok" else 0
        143:def build_minute_rows(inputs: MatchInputs, lag_seconds: int) -> list[DatasetRow]:
        157:        signal_market_p_radiant_300s = market_row["signal_market_p_radiant_300s"]
        158:        if signal_market_p_radiant_300s is None:
        168:                market_p_radiant=market_row["market_p_radiant"],
        169:                signal_market_p_radiant_300s=signal_market_p_radiant_300s,
        332:        training_rows.extend(build_minute_rows(inputs, lag_seconds))
        343:            validation_minute_rows.extend(build_minute_rows(inputs, lag_seconds))
        262:        "signal_market_p_radiant_300s": label,
        492:        if row["market_status"] == "ok":
        497:def labeled_row_count(build: MapBuild) -> int:
        503:        if row["signal_market_p_radiant_300s"] is None:
        532:        if labeled_row_count(build) < 1:
        ✓ • 0ms
    
    ## Activity
    
        $ sed -n '115,185p' ../esports-trader/src/prepare_dataset/prepare_dataset.py; sed -n '215,275p' ../esports-trader/src/lol/05_prepare_dataset.py
    
    
        def find_longest_book_gap(market_rows: list[MarketSecondRow]) -> int:
            longest = 0
            run = 0
            for row in market_rows:
                second = int(row["second"])
                if second < MODEL_START_SECOND or second >= VALIDATION_SIGNAL_END_SECOND_EXCLUSIVE:
                    continue
                run = run + 1 if row["market_status"] != "ok" else 0
                longest = max(longest, run)
            return longest
    
    
        def state_features(state: ExactSecondState) -> SnapshotFeatures:
            return SnapshotFeatures(
                radiant_nw_adv=state.radiant_nw_adv,
                radiant_nw=state.radiant_nw,
                dire_nw=state.dire_nw,
                radiant_xp_adv=state.radiant_xp_adv,
                deaths_radiant=state.deaths_radiant,
                deaths_dire=state.deaths_dire,
                top1_nw_adv=state.top.top1_nw_adv,
                radiant_top1_nw_ratio=state.top.radiant_top1_nw_ratio,
                dire_top1_nw_ratio=state.top.dire_top1_nw_ratio,
            )
    
    
        def build_minute_rows(inputs: MatchInputs, lag_seconds: int) -> list[DatasetRow]:
            match = inputs.match
            try:
                states = build_minute_states(match, TRAIN_END_SECOND_EXCLUSIVE)
            except MatchDataError as error:
                logger.warning("skip match %s: %s", match["id"], error.reason)
                return []
    
            market_by_second = {int(row["second"]): row for row in inputs.market_rows}
            rows: list[DatasetRow] = []
            for state in states:
                market_row = market_by_second.get(state.second + lag_seconds)
                if market_row is None or not is_ok_market_second(market_row):
                    continue
                signal_market_p_radiant_300s = market_row["signal_market_p_radiant_300s"]
                if signal_market_p_radiant_300s is None:
                    continue
                rows.append(
                    DatasetRow(
                        match_id=state.match_id,
                        start_time=inputs.entry.start_time,
                        second=state.second,
                        radiant_win=state.radiant_win,
                        **state_features(state),
                        market_radiant_prior=inputs.entry.radiant_prior,
                        market_p_radiant=market_row["market_p_radiant"],
                        signal_market_p_radiant_300s=signal_market_p_radiant_300s,
                    )
                )
            return rows
    
    
        def build_game_feature_rows(
            match_id: int,
            states: Sequence[ExactSecondState],
            market_radiant_prior: float,
        ) -> list[GameFeatureRow]:
            """Exact-second model inputs keyed by game second for archive-schedule replay."""
            return [
                GameFeatureRow(
                    match_id=match_id,
                    game_second=state.second,
                    **state_features(state),
            )
    
    
        @dataclass(frozen=True)
        class MarketJoin:
            """Row-level current/target join plus skipped-market count."""
    
            rows: tuple[LolDatasetRow, ...]
            skipped_market_rows: int
    
    
        def finite_mid(value: float | None) -> float | None:
            """Keep a finite midpoint; None otherwise."""
            if value is None or not math.isfinite(value):
                return None
            return value
    
    
        def build_dataset_row(
            livestats: LivestatsOk,
            link: LolLinkRow,
            slot: GridRow,
            radiant_win: bool,
            prior: float,
            current: float,
            label: float | None,
        ) -> LolDatasetRow:
            """Assemble one parquet row from grid features and the optional 300s mid."""
            feat = slot.features
            return {
                "match_id": livestats.match_id,
                "start_time": livestats.start_time,
                "event_id": str(link["event_id"]),
                "second": slot.second,
                "state_ts_us": slot.state_wall_us,
                "radiant_win": radiant_win,
                "radiant_nw_adv": feat.radiant_nw_adv,
                "radiant_nw": feat.radiant_nw,
                "dire_nw": feat.dire_nw,
                "radiant_xp_adv": feat.radiant_xp_adv,
                "deaths_radiant": feat.deaths_radiant,
                "deaths_dire": feat.deaths_dire,
                "top1_nw_adv": feat.top1_nw_adv,
                "radiant_top1_nw_ratio": feat.radiant_top1_nw_ratio,
                "dire_top1_nw_ratio": feat.dire_top1_nw_ratio,
                "market_radiant_prior": prior,
                "market_p_radiant": current,
                "signal_market_p_radiant_300s": label,
            }
    
    
        def join_market_rows(
            livestats: LivestatsOk,
            link: LolLinkRow,
            prior: float,
            radiant_book: TokenBook,
            dire_book: TokenBook,
        ) -> MarketJoin:
            """Attach current mid; 300s labels on seconds 0..540. Drop when current mid fails."""
            radiant_win = int(link["resolved_outcome_index"]) == int(link["radiant_token_index"])
            rows: list[LolDatasetRow] = []
        ✓ • 0ms
    
    ## Activity
    
        $ rg -n 'MAX_BOOK_AGE_SECONDS|PAIR_SUM_TOLERANCE|MIN_TRADES_PER_BUCKET|MAX_ENTRY_SPREAD_TICKS|BOOK_GAP' ../esports-trader/src/shared ../esports-trader/src/lol/constants.py | head -80
        ../esports-trader/src/shared/utils/telonex_tape.py:7:from shared.constants.dataset import MIN_TRADES_PER_BUCKET, TRADE_BUCKET_SECONDS
        ../esports-trader/src/shared/utils/telonex_tape.py:70:    return all(count >= MIN_TRADES_PER_BUCKET for count in counts)
        ../esports-trader/src/shared/utils/telonex_book.py:14:MAX_BOOK_AGE_SECONDS = 5.0
        ../esports-trader/src/shared/utils/telonex_book.py:15:PAIR_SUM_TOLERANCE = 0.05
        ../esports-trader/src/shared/utils/telonex_book.py:143:        tolerance=PAIR_SUM_TOLERANCE,
        ../esports-trader/src/shared/utils/telonex_book.py:221:    if first_age > MAX_BOOK_AGE_SECONDS:
        ../esports-trader/src/shared/utils/telonex_book.py:226:        if age_seconds > MAX_BOOK_AGE_SECONDS:
        ../esports-trader/src/shared/utils/telonex_book.py:250:        tolerance=PAIR_SUM_TOLERANCE,
        ../esports-trader/src/shared/constants/dataset.py:11:MAX_BOOK_GAP_SECONDS = 120
        ../esports-trader/src/shared/constants/dataset.py:25:MIN_TRADES_PER_BUCKET = 10
        ../esports-trader/src/shared/constants/strategy.py:14:MAX_ENTRY_SPREAD_TICKS = 8
        ✓ • 0ms
    
    ## Activity
    
        $ sed -n '270,310p' ../esports-trader/src/lol/05_prepare_dataset.py; sed -n '245,285p' ../esports-trader/src/shared/utils/telonex_book.py
            radiant_book: TokenBook,
            dire_book: TokenBook,
        ) -> MarketJoin:
            """Attach current mid; 300s labels on seconds 0..540. Drop when current mid fails."""
            radiant_win = int(link["resolved_outcome_index"]) == int(link["radiant_token_index"])
            rows: list[LolDatasetRow] = []
            skipped = 0
            for slot in livestats.grid_rows:
                current = finite_mid(
                    lookup_market_p_after(
                        radiant_book, dire_book, slot.state_wall_us, LOL_SOURCE_LAG_SECONDS
                    )
                )
                if current is None:
                    skipped += 1
                    continue
                label: float | None = None
                if LOL_GRID_START_SECOND <= slot.second <= LOL_GRID_END_SECOND:
                    label = finite_mid(
                        lookup_market_p_after(
                            radiant_book,
                            dire_book,
                            slot.state_wall_us,
                            LOL_SOURCE_LAG_SECONDS + LOL_TARGET_HORIZON_SECONDS,
                        )
                    )
                rows.append(build_dataset_row(livestats, link, slot, radiant_win, prior, current, label))
            return MarketJoin(tuple(rows), skipped)
    
    
        def build_game_feature_rows(livestats: LivestatsOk, prior: float) -> tuple[GameFeatureRow, ...]:
            """One exact-second model-input row per age-gated grid slot; no market join."""
            rows: list[GameFeatureRow] = []
            for slot in livestats.grid_rows:
                feat = slot.features
                rows.append(
                    GameFeatureRow(
                        match_id=livestats.match_id,
                        game_second=slot.second,
                        radiant_nw_adv=feat.radiant_nw_adv,
                        radiant_nw=feat.radiant_nw,
            if radiant.quote is None or dire.quote is None:
                return MarketPairQuote(status="missing_quote", market_p_radiant=None)
            market_p = normalize_pair_mids(
                radiant_mid=radiant.quote.mid,
                dire_mid=dire.quote.mid,
                tolerance=PAIR_SUM_TOLERANCE,
            )
            if market_p is None:
                return MarketPairQuote(status="inconsistent_pair", market_p_radiant=None)
            return MarketPairQuote(status="ok", market_p_radiant=market_p)
    
    
        def is_ok_market_second(row: MarketSecondRow) -> TypeGuard[OkMarketSecondRow]:
            """True when the second carries a live mid. `resolve_market_pair` sets one only on "ok"."""
            return row["market_status"] == "ok"
    
    
        def lookup_market_p_after(
            radiant_book: TokenBook,
            dire_book: TokenBook,
            anchor_us: int,
            horizon_seconds: int,
        ) -> float | None:
            """Normalized market_p `horizon_seconds` after an anchor, None when gates fail."""
            target_us = anchor_us + horizon_seconds * US_PER_SECOND
            return resolve_market_pair(
                find_asof_quote(radiant_book, target_us),
                find_asof_quote(dire_book, target_us),
            ).market_p_radiant
        ✓ • 0ms
    
    ## Activity
    
        $ sed -n '1,120p' ../esports-trader/src/shared/types/dataset.py; rg -n 'class MarketSecondRow|class LolBacktestMarketSecondRow|market_status|radiant_bid|radiant_ask|bid|ask' ../esports-trader/src/shared/types ../esports-trader/src/lol/types.py | head -110
        from typing import Literal, TypedDict
    
        HornSource = Literal["grid_derived", "archive"]
        PausesSource = Literal["opendota", "archive"]
        WinnerSource = Literal["stratz"]
    
    
        class MatchCatalogRow(TypedDict):
            """One row of `match_catalog.parquet`; field order is the parquet column order.
    
            The published schema, not the domain object: timestamps stay ISO-8601 text.
            `spawn_at` is GRID `startedAt` (players appear on the map) — null on rows
            whose only timing is an archive horn; horn is never turned into a fake
            spawn. `horn_at` is the confirmed horn instant; `ended_at` is
            `get_state_available_ts(horn=horn_at, second=duration, pauses=pauses)`.
            `pauses_json` null means pauses are unknown — never an empty list.
            `radiant_win` is outcome/settlement data, never a feature.
            `match_catalog.catalog_entry_from_row` parses one row into `CatalogEntry`.
            """
    
            match_id: int
            condition_id: str
            event_id: str
            radiant_token_index: int
            spawn_at: str | None
            ended_at: str
            playback_available: bool
            duration: int
            radiant_prior: float
            seconds_delay: int
            token_id_0: str
            token_id_1: str
            market_slug: str
            market_closed_at: str
            horn_at: str
            horn_source: HornSource
            pauses_json: str | None
            pauses_source: PausesSource | None
            radiant_win: bool
            winner_source: WinnerSource
            archive_id: str | None
            archive_root: str | None
            archive_feed_source: str | None
            schedule_fingerprint: str | None
    
    
        class SnapshotFeatures(TypedDict):
            """STRATZ snapshot fields shared by minute and exact-second dataset rows."""
    
            radiant_nw_adv: int
            radiant_nw: int
            dire_nw: int
            radiant_xp_adv: int
            deaths_radiant: int
            deaths_dire: int
            top1_nw_adv: int
            radiant_top1_nw_ratio: float
            dire_top1_nw_ratio: float
    
    
        class StateFeatures(SnapshotFeatures):
            """A snapshot plus the settled outcome; `radiant_win` never enters feature rows."""
    
            radiant_win: bool
    
    
        class DatasetRow(StateFeatures):
            """Minute-boundary STRATZ state; market mid and 300s future are from second + TRAIN_LAG_SECONDS."""
    
            match_id: int
            start_time: int  # unix seconds; catalog anchor (spawn when known, else horn)
            second: int  # from horn; minute boundary in the model window
            market_radiant_prior: float  # pre-game market P(Radiant); constant per match
            market_p_radiant: float
            signal_market_p_radiant_300s: float
    
    
        class GameFeatureRow(SnapshotFeatures):
            """Exact-second game state keyed by game second for archive-schedule backtests.
    
            Same model-input block as `StateFeatures` minus `radiant_win`: the outcome is
            settlement data and is deliberately not a column of the emitted artifact.
            """
    
            match_id: int
            game_second: int
            market_radiant_prior: float
    
    
        class SplitRow(TypedDict):
            """One match's model split and whether a book gap blocks bulk backtesting."""
    
            match_id: int
            start_time: int
            split: Literal["train", "validation"]
            backtest_book_gap_excluded: bool
    
    
        MarketQuoteStatus = Literal[
            "ok",
            "missing_quote",
            "stale_quote",
            "inconsistent_pair",
        ]
    
        TradeSide = Literal["radiant", "dire"]
    
    
        class MarketSecondRowBase(TypedDict):
            """Every market-second field except the live mid, which the two shapes below narrow."""
    
            match_id: int
            condition_id: str
            event_id: str
            second: int
            state_ts_us: int
    
            market_status: MarketQuoteStatus
    
            signal_market_p_radiant_30s: float | None
        ../esports-trader/src/lol/types.py:218:class LolBacktestMarketSecondRow(TypedDict):
        ../esports-trader/src/lol/types.py:226:    market_status: MarketQuoteStatus
        ../esports-trader/src/shared/types/stratz.py:131:    """Match-level: surviving tower bitmask per side after the death."""
        ../esports-trader/src/shared/types/stratz.py:160:    """v3 match-level towerDeaths — which tower, not a bitmask."""
        ../esports-trader/src/shared/types/dataset.py:109:class MarketSecondRowBase(TypedDict):
        ../esports-trader/src/shared/types/dataset.py:118:    market_status: MarketQuoteStatus
        ../esports-trader/src/shared/types/dataset.py:124:class MarketSecondRow(MarketSecondRowBase):
        ../esports-trader/src/shared/types/telonex.py:8:    """One bid/ask level as stored in a Telonex parquet cell."""
        ✓ • 0ms
    
    ## Assistant
    
    Да, ты прав: я упустил главное. Если карта действительно даёт плохие ценовые метки, пропускать её в production через validation нельзя. Мой предыдущий вариант эту проблему не решал.
    
    Но [отчёт](/Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace/.analysis/ladder-thin-2026-09-24/REPORT.md) не подтверждает, что **нынешнее правило «10 сделок в каждом пятиминутном окне до конца карты»** хорошо распознаёт такие карты. Оно исключило 50 карт, на которых бот реально торговал; суммарно они дали **+$41**. Крупные убыточные карты правило пропустило. Часть карт отсекается из-за тишины *после* того, как исход уже стал ясен. Это не доказывает, что все их метки хорошие; это показывает, что текущий фильтр — плохой заменитель проверки качества цены.
    
    Здесь есть жёсткое ограничение: **нельзя до входа узнать, что через пять минут цена окажется недостоверной**. Поэтому одно правило, которое заранее целиком удаляет карту и при этом не заглядывает вперёд, может опираться только на данные, доступные до входа.
    
    Я бы упростил схему так:
    
    - Ввести **одну проверку качества рынка на момент `t`**: свежая двусторонняя книга, согласованные цены двух токенов, допустимый спред; книгу в live сначала очищать от наших собственных заявок. Существующие проверки свежести и согласованности уже есть, но текущий отбор обучающих строк не проверяет спред ([Dota](/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader/src/prepare_dataset/prepare_dataset.py:143), [проверки книги](/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader/src/shared/utils/telonex_book.py:210)).
    - Если проверка в `t` не проходит, **не брать строку в train и validation и не открывать BUY в live и replay в этот момент**. Production снова просто объединяет прошедшие train и validation. Никаких `production_validation` и отдельного набора для early stopping.
    - Для обучающей метки **отдельно** проверять качество книги в `t+300`. Это разрешено при построении исторической метки, но эту проверку нельзя выдавать за правило, которое live мог применить в `t`. Продажи уже открытой позиции не следует блокировать входным фильтром.
    - В бэктесте сохранять такие карты в отчёте: если правило не допустило ни одного входа, у карты будет ноль сделок. Иначе отбор карт снова скроет эффект самого правила.
    
    Если тебе нужен именно **один допуск на всю карту до её начала**, вариант — объём сделок или качество книги за фиксированное окно *до горна*. Его можно одинаково применять ко всем выборкам, бэктесту и live. Но я бы пока его не выбирал: отчёт не нашёл связи текущей метки «тонкая карта» с убытками, а для точного совпадения live и replay история сделок сейчас доступна не одинаково.
    
    То есть я бы **переписал план вокруг проверки цены и входа в конкретный момент**, а правило по сделкам за всю карту убрал из допуска данных. Порог спреда и дополнительные признаки стоит выбирать после разбора подозрительных примеров вроде твоего `50% → 100%`: была ли там узкая живая книга или midpoint прыгнул из-за пустой и широкой книги. Без этого новый порог будет ещё одной догадкой о «токсичности».
    ✓ • 0ms

## Activity

    $ python - <<'PY'
        from pathlib import Path
        p=Path('/Users/dimabytes/Desktop/# Codex conversation.txt')
        s=p.read_text()
        parts=s.split('\n## ')
        for i,x in enumerate(parts):
         if x.startswith('User') or (x.startswith('Assistant') and any(w in x.lower() for w in ['ансамб','stratz','дополн','старых','учител'])):
          print(f'=== section {i} ===\n{x[:14000]}')
        PY
    zsh:1: command not found: python
    ✗ (127) • 0ms

## Activity

    $ pwd && git status --short && rg --files -g AGENTS.md -g '*train*' -g '*model*' -g '*split*' -g 'pyproject.toml' -g '*path*' -g '*stratz*' -g '*opendota*' -g '*README*' | head -160
    /Users/dimabytes/work/polymarket/dota_2_bot/esports-trader
     M data/new_model/production-noxp/model.json
     M data/new_model/production/model.json
     M data/new_model/research-noxp/model.json
     M data/new_model/research/model.json
    ?? data/backtests/dota_maker/match_8896486914_smoke-grid/
    ?? data/backtests/dota_maker/match_9003685896_smoke-sched/
    ?? data/backtests/dota_maker/validation_join_delta02_x015_cut480_p4_ab-speed/
    ?? data/new_model/archive/production-noxp/20260924T145142Z/
    ?? data/new_model/archive/production/20260924T145121Z/
    ?? data/new_model/archive/research-noxp/20260924T145138Z/
    ?? data/new_model/archive/research/20260924T145117Z/
    ?? docs/paper_notes/Win_Prediction_in_Multiplayer_Esports_Live_Profess.txt
    src/train_model/train_model.py
    src/backtest/paths.py
    src/backtest/wallet_path.py
    src/prepare_dataset/stratz_seconds.py
    src/shared/utils/model_registry.py
    src/shared/utils/stratz.py
    src/shared/utils/opendota.py
    src/shared/constants/paths.py
    src/shared/types/stratz.py
    src/shared/types/model.py
    src/shared/types/opendota.py
    src/collect/s02_link_opendota.py
    src/collect/common/opendota_candidates.py
    src/collect/common/paths.py
    src/collect/common/stratz_client.py
    src/collect/s05b_fetch_stratz_matches.py
    src/collect/s05_fetch_opendota_matches.py
    pyproject.toml
    src/lol/06_train_model.py
    AGENTS.md
    data/new_model/production/split.parquet
    data/new_model/production/model.json
    data/new_model/research-noxp/split.parquet
    data/new_model/research-noxp/model.json
    data/lol/models/production/split.parquet
    data/lol/models/production/model.json
    docs/add_model.txt
    data/new_model/production-noxp/split.parquet
    data/new_model/production-noxp/model.json
    data/new_model/experiments/mono/split.parquet
    data/new_model/experiments/mono/model.txt
    data/new_model/experiments/mono/model.json
    data/new_model/experiments/hubermono/split.parquet
    data/new_model/experiments/hubermono/model.txt
    data/new_model/experiments/hubermono/model.json
    src/trader/archive_paths.py
    src/trader/paths.py
    src/trader/model_server.py
    tests/test_stratz.py
    tests/test_stratz_client.py
    data/lol/models/experiments/h180/split.parquet
    data/lol/models/experiments/h180/model.txt
    data/lol/models/experiments/h180/model.json
    data/new_model/experiments/outcome-p-win/split.parquet
    data/new_model/experiments/outcome-p-win/model.txt
    data/new_model/experiments/outcome-p-win/model.json
    docs/experiments/lol-minute-train.md
    data/new_model/experiments/linear/split.parquet
    data/new_model/experiments/linear/model.txt
    data/new_model/experiments/linear/model.json
    docs/experiments/delta-sizing/README.md
    data/new_model/experiments/huber/split.parquet
    data/new_model/experiments/huber/model.txt
    data/new_model/experiments/huber/model.json
    data/lol/models/experiments/late-phase/p1080/split.parquet
    data/lol/models/experiments/late-phase/p1080/model.txt
    data/lol/models/experiments/late-phase/p1080/model.json
    docs/experiments/lol-horizon-sweep/README.md
    tests/test_fetch_opendota.py
    tests/test_lol_train_model.py
    tests/test_model_server.py
    docs/experiments/pathwise-calibration/README.md
    data/lol/models/experiments/late-phase/all_seconds/split.parquet
    data/lol/models/experiments/late-phase/all_seconds/model.txt
    data/lol/models/experiments/late-phase/all_seconds/model.json
    docs/experiments/rung-economics/README.md
    data/lol/models/experiments/late-phase/late_single/split.parquet
    data/lol/models/experiments/late-phase/late_single/model.txt
    data/lol/models/experiments/late-phase/late_single/model.json
    data/lol/models/experiments/late-phase/bank_late1/split.parquet
    data/lol/models/experiments/late-phase/bank_late1/model.txt
    data/lol/models/experiments/late-phase/bank_late1/model.json
    docs/experiments/sell-through-ask/README.md
    data/new_model/archive/production/20260921T085655Z/split.parquet
    data/new_model/archive/production/20260921T085655Z/model.json
    data/lol/models/experiments/late-phase/p540/split.parquet
    data/lol/models/experiments/late-phase/p540/model.txt
    data/lol/models/experiments/late-phase/p540/model.json
    docs/experiments/dota-minute-specialists/train.py
    docs/experiments/dota-minute-specialists/README.md
    data/lol/models/experiments/late-phase/p1620/split.parquet
    data/lol/models/experiments/late-phase/p1620/model.txt
    data/lol/models/experiments/late-phase/p1620/model.json
    data/new_model/archive/research-noxp/20260921T085713Z/split.parquet
    data/new_model/archive/research-noxp/20260921T085713Z/model.json
    tests/test_link_opendota.py
    tests/test_stratz_seconds.py
    tests/test_fetch_stratz.py
    tests/test_train_model.py
    tests/test_model_registry.py
    docs/experiments/lol-horizon-train.md
    data/lol/models/experiments/late-phase/early/split.parquet
    data/lol/models/experiments/late-phase/early/model.txt
    data/lol/models/experiments/late-phase/early/model.json
    data/new_model/archive/research-noxp/20260919T182833Z/split.parquet
    data/new_model/archive/research-noxp/20260919T182833Z/model.json
    docs/experiments/ladder-budget-v5/README.md
    data/new_model/archive/research-noxp/20260924T145138Z/split.parquet
    data/new_model/archive/research-noxp/20260924T145138Z/model.json
    docs/experiments/README.md
    docs/experiments/pnl-action-v1/fill_model.txt
    data/lol/models/experiments/all-h180-s540/split.parquet
    data/lol/models/experiments/all-h180-s540/model.txt
    data/lol/models/experiments/all-h180-s540/model.json
    docs/experiments/pnl-action-v1/markout_model.txt
    docs/experiments/order-outcomes/README.md
    data/lol/models/experiments/mono/split.parquet
    data/lol/models/experiments/mono/model.txt
    data/lol/models/experiments/mono/model.json
    data/new_model/archive/production-noxp/20260924T145142Z/split.parquet
    data/new_model/archive/production-noxp/20260924T145142Z/model.json
    data/new_model/archive/production/20260904T225911Z/split.parquet
    data/new_model/archive/production/20260904T225911Z/model.txt
    data/new_model/archive/production/20260904T225911Z/model.json
    data/lol/models/experiments/roles/split.parquet
    data/lol/models/experiments/roles/model.txt
    data/lol/models/experiments/roles/model.json
    data/new_model/archive/production-noxp/20260919T182837Z/split.parquet
    data/new_model/archive/production-noxp/20260919T182837Z/model.json
    data/new_model/archive/production/20260919T114805Z/split.parquet
    data/new_model/archive/production/20260919T114805Z/model.json
    data/lol/models/experiments/outcome-p-win/split.parquet
    docs/experiments/lol-sweep-540/README.md
    data/lol/models/experiments/outcome-p-win/model.txt
    data/lol/models/experiments/outcome-p-win/model.json
    data/new_model/archive/production/20260904T200130Z/split.parquet
    data/new_model/archive/production/20260904T200130Z/model.txt
    data/new_model/archive/production/20260904T200130Z/model.json
    data/new_model/archive/production/20260822T145645Z/split.parquet
    data/new_model/archive/production/20260822T145645Z/model.txt
    data/new_model/archive/production/20260822T145645Z/model.json
    data/new_model/archive/production-noxp/20260921T085717Z/split.parquet
    data/new_model/archive/production-noxp/20260921T085717Z/model.json
    data/lol/models/experiments/huber/split.parquet
    data/lol/models/experiments/huber/model.txt
    data/lol/models/experiments/huber/model.json
    data/new_model/archive/production/20260904T222047Z/split.parquet
    data/new_model/archive/production/20260904T222047Z/model.txt
    data/new_model/archive/production/20260904T222047Z/model.json
    data/new_model/archive/production/20260824T232650Z/split.parquet
    data/new_model/archive/production/20260824T232650Z/model.txt
    data/new_model/archive/production/20260824T232650Z/model.json
    data/new_model/archive/production/20260914T182034Z/split.parquet
    data/new_model/archive/production/20260914T182034Z/model.json
    data/lol/models/experiments/h240/split.parquet
    data/lol/models/experiments/h240/model.txt
    data/lol/models/experiments/h240/model.json
    data/new_model/archive/production/20260904T174031Z/split.parquet
    data/new_model/archive/production/20260904T174031Z/model.txt
    data/new_model/archive/production/20260904T174031Z/model.json
    data/new_model/archive/production/20260824T232210Z/split.parquet
    data/new_model/archive/production/20260824T232210Z/model.txt
    data/new_model/archive/production/20260824T232210Z/model.json
    data/new_model/archive/production/20260912T100229Z/split.parquet
    data/new_model/archive/production/20260912T100229Z/model.json
    data/new_model/archive/production/20260924T145121Z/split.parquet
    data/new_model/archive/production/20260924T145121Z/model.json
    data/new_model/archive/production/20260824T152512Z/split.parquet
    data/new_model/archive/production/20260824T152512Z/model.txt
    data/new_model/archive/production/20260824T152512Z/model.json
    ✓ • 1.75s

## Activity

    $ cat src/train_model/train_model.py src/shared/constants/paths.py src/shared/types/model.py
    # pyright: reportMissingTypeStubs=false
    
    import argparse
    import sys
    from collections.abc import Sequence
    from dataclasses import dataclass
    from pathlib import Path
    from shutil import copy2
    from typing import cast
    
    import numpy as np
    import pandas as pd
    
    from shared.constants.dataset import (
        MODEL_START_SECOND,
        TRAIN_END_SECOND_EXCLUSIVE,
        TRAIN_LAG_SECONDS,
    )
    from shared.constants.paths import (
        PRODUCTION_ARCHIVE_DIR,
        PRODUCTION_DATASET_SPLIT_PATH,
        PRODUCTION_MODEL_DIR,
        PRODUCTION_NOXP_ARCHIVE_DIR,
        PRODUCTION_NOXP_MODEL_DIR,
        PRODUCTION_TRAINING_DATASET_PATH,
        RESEARCH_ARCHIVE_DIR,
        RESEARCH_MODEL_DIR,
        RESEARCH_MODEL_SPLIT_PATH,
        RESEARCH_NOXP_ARCHIVE_DIR,
        RESEARCH_NOXP_MODEL_DIR,
        TRAINING_DATASET_PATH,
        VALIDATION_DATASET_PATH,
    )
    from shared.types.dataset import ValidationDatasetRow
    from shared.utils.gbm import (
        ENSEMBLE_ARM,
        ENSEMBLE_K,
        FEATURE_COLUMNS,
        LGB_PARAMS,
        MAX_BOOST_ROUNDS,
        NO_XP_FEATURE_COLUMNS,
        TRAINING_COLUMNS,
        GbmPredictor,
        HoldoutWindowStats,
        build_ensemble_model_meta,
        build_model_metrics,
        build_price_delta_labels,
        fit_production_members,
        fit_research_members,
        load_training_dataset,
        predict_future_prices,
        write_ensemble_members,
    )
    from shared.utils.hashing import sha256_file
    from shared.utils.log import get_logger, setup_logging
    from shared.utils.market_scenario_report import (
        build_scenario_stats,
        full_window_scenario_stats,
        write_scenario_csv,
    )
    from shared.utils.model_registry import (
        publish_model_dir,
        staging_model_dir,
        write_model_meta,
    )
    from shared.utils.parquet_io import replace_nulls_with_none
    from train_model.market_metrics import evaluate_validation_metrics
    
    # LGB_PARAMS and TRAINING_COLUMNS are re-exported for tests / trader.
    __all__ = ["LGB_PARAMS", "TRAINING_COLUMNS"]
    
    logger = get_logger(__name__)
    
    
    def lagged_source_features(
        frame: pd.DataFrame, lag_seconds: int, features: Sequence[str]
    ) -> pd.DataFrame:
        """Copy `features` with second set to the snapshot clock (T - lag)."""
        return frame[list(features)].assign(second=frame["second"] - lag_seconds)
    
    
    # Parquet contracts stay tied to the TypedDicts so new fields cannot drift out of
    # the loaders. LightGBM still receives only FEATURE_COLUMNS below.
    VALIDATION_COLUMNS: list[str] = list(ValidationDatasetRow.__annotations__)
    
    
    def load_validation_dataset(path: Path) -> pd.DataFrame:
        """Read exact-second validation rows for early stopping and metrics."""
        return pd.read_parquet(path, columns=VALIDATION_COLUMNS)
    
    
    def build_validation_dataset_rows(frame: pd.DataFrame) -> list[ValidationDatasetRow]:
        """Convert validation rows to typed records while mapping pandas nulls to None."""
        records = replace_nulls_with_none(frame).to_dict(orient="records")
        return [cast(ValidationDatasetRow, record) for record in records]
    
    
    def select_usable_validation_rows(frame: pd.DataFrame) -> pd.DataFrame:
        """Keep full-map rows that can feed canonical model inference."""
        in_window = frame["second"] >= MODEL_START_SECOND
        return frame.loc[in_window & (frame["market_status"] == "ok")]
    
    
    def select_validation_prediction_frame(frame: pd.DataFrame) -> pd.DataFrame:
        """Keep usable rows inside the fixed research evaluation window."""
        usable = select_usable_validation_rows(frame)
        return usable.loc[usable["second"] < TRAIN_END_SECOND_EXCLUSIVE]
    
    
    def select_validation_fit_frame(frame: pd.DataFrame) -> pd.DataFrame:
        """Keep fixed-window research rows with a 300-second future midpoint."""
        selected = select_validation_prediction_frame(frame)
        return selected.loc[selected["signal_market_p_radiant_300s"].notna()]
    
    
    def slice_train_rows(frame: pd.DataFrame) -> pd.DataFrame:
        """Keep canonical Dota minute rows with a usable 300-second label."""
        in_window = (frame["second"] >= MODEL_START_SECOND) & (
            frame["second"] < TRAIN_END_SECOND_EXCLUSIVE
        )
        return frame.loc[in_window & frame["signal_market_p_radiant_300s"].notna()]
    
    
    def log_dataset_span(label: str, match_times: pd.DataFrame) -> None:
        """Log how many matches a dataset holds and the first/last match start."""
        logger.info(
            "%s: %s matches | %s .. %s",
            label,
            len(match_times),
            pd.to_datetime(float(match_times["start_time"].min()), unit="s", utc=True),
            pd.to_datetime(float(match_times["start_time"].max()), unit="s", utc=True),
        )
    
    
    def parse_args(argv: Sequence[str]) -> argparse.Namespace:
        """Empty argv publishes both live catalogs; the four experiment flags must travel together."""
        parser = argparse.ArgumentParser(description=__doc__)
        parser.add_argument("--lag-seconds", type=int, default=None)
        parser.add_argument("--training-dataset", type=Path, default=None)
        parser.add_argument("--validation-dataset", type=Path, default=None)
        parser.add_argument("--model-dir", type=Path, default=None)
        parser.add_argument("--no-xp", action="store_true")
        args = parser.parse_args(argv)
        flags = (args.lag_seconds, args.training_dataset, args.validation_dataset, args.model_dir)
        if any(value is not None for value in flags) and any(value is None for value in flags):
            parser.error(
                "--lag-seconds, --training-dataset, --validation-dataset, and --model-dir "
                "must be passed together"
            )
        if args.no_xp and args.model_dir is None:
            parser.error("--no-xp requires --model-dir")
        if args.lag_seconds is not None and args.lag_seconds < 0:
            parser.error("--lag-seconds must be >= 0")
        if args.model_dir is not None:
            model_dir = args.model_dir.expanduser().resolve()
            blocked = {
                RESEARCH_MODEL_DIR.resolve(),
                PRODUCTION_MODEL_DIR.resolve(),
                RESEARCH_NOXP_MODEL_DIR.resolve(),
                PRODUCTION_NOXP_MODEL_DIR.resolve(),
            }
            if model_dir in blocked:
                parser.error("--model-dir cannot be a live research or production catalog")
            args.model_dir = model_dir
            training_path = args.training_dataset.expanduser()
            validation_path = args.validation_dataset.expanduser()
            if not training_path.is_file():
                parser.error(f"--training-dataset not found: {training_path}")
            if not validation_path.is_file():
                parser.error(f"--validation-dataset not found: {validation_path}")
            args.training_dataset = training_path.resolve()
            args.validation_dataset = validation_path.resolve()
        return args
    
    
    def train_research(
        training_path: Path,
        validation_path: Path,
        split_path: Path,
        research_dir: Path,
        archive_dir: Path,
        lag_seconds: int,
        features: Sequence[str],
    ) -> int:
        """Fit and publish the research default_sub90 ensemble; return mean member trees."""
        train_dataset_sha256 = sha256_file(training_path)
        validation_dataset_sha256 = sha256_file(validation_path)
        train_dataset = slice_train_rows(load_training_dataset(training_path))
        validation_dataset = load_validation_dataset(validation_path)
        validation_fit = select_validation_fit_frame(validation_dataset)
        validation_features = lagged_source_features(validation_fit, lag_seconds, features)
        validation_target = build_price_delta_labels(validation_fit)
        boosters = fit_research_members(train_dataset, validation_features, validation_target, features)
    
        member_trees = tuple(booster.num_trees() for booster in boosters)
        staging_dir = staging_model_dir(research_dir)
        member_names = write_ensemble_members(staging_dir, boosters)
        predictor = GbmPredictor(
            model_dir=staging_dir,
            feature_names=tuple(features),
            member_names=member_names,
            member_trees=member_trees,
            _boosters=tuple(boosters),
        )
    
        validation_prediction = select_validation_prediction_frame(validation_dataset)
        prediction_features = lagged_source_features(validation_prediction, lag_seconds, features)
        current_prices = validation_prediction["market_p_radiant"].to_numpy(dtype=np.float64)
        future_prices = predict_future_prices(predictor, prediction_features, current_prices)
        validation_rows = build_validation_dataset_rows(validation_prediction)
        metric_rows = evaluate_validation_metrics(validation_rows, future_prices.tolist())
        scenario_stats = build_scenario_stats(metric_rows)
    
        train_matches = train_dataset[["match_id", "start_time"]].drop_duplicates("match_id")
        validation_matches = validation_prediction[["match_id", "start_time"]].drop_duplicates(
            "match_id"
        )
        logger.info(
            "ensemble %s k=%s trees: %s-%s mean=%s (of %s) | train matches: %s | "
            "validation matches: %s | train rows: %s | validation rows: %s",
            ENSEMBLE_ARM,
            ENSEMBLE_K,
            min(member_trees),
            max(member_trees),
            predictor.num_trees(),
            MAX_BOOST_ROUNDS,
            len(train_matches),
            len(validation_matches),
            len(train_dataset),
            len(validation_prediction),
        )
        log_dataset_span("train", train_matches)
        log_dataset_span("validation", validation_matches)
    
        full_window = full_window_scenario_stats(scenario_stats)
        metrics = build_model_metrics(
            predictor,
            HoldoutWindowStats(
                rows=full_window.rows,
                future_price_300_count=full_window.future_price_300_count,
                no_move_mae_300=full_window.no_move_mae_300,
                model_mae_300=full_window.model_mae_300,
                mae_gain_300=full_window.mae_gain_300,
                mae_gain_300_ci=full_window.mae_gain_300_ci,
                model_bias_300=full_window.model_bias_300,
                directional_markout_300s=full_window.directional_markout_300s,
            ),
        )
        write_model_meta(
            build_ensemble_model_meta(
                len(train_matches),
                train_dataset_sha256,
                features=features,
                validation_dataset_sha256=validation_dataset_sha256,
                metrics=metrics,
                members=member_names,
                member_trees=member_trees,
                source_lag_seconds=lag_seconds,
            ),
            staging_dir / "model.json",
        )
        write_scenario_csv(scenario_stats, staging_dir / "validation_scenarios.csv")
        copy2(split_path, staging_dir / "split.parquet")
        archived_name = publish_model_dir(staging_dir, research_dir, archive_dir)
        logger.info("published: %s | archived previous model: %s", research_dir, archived_name)
        return predictor.num_trees()
    
    
    def train_production(
        training_dataset_path: Path,
        split_source_path: Path,
        live_dir: Path,
        archive_dir: Path,
        features: Sequence[str],
        *,
        boost_rounds: int,
    ) -> None:
        """Fit K production members at `boost_rounds` trees and publish the catalog."""
        train_dataset_sha256 = sha256_file(training_dataset_path)
        train_dataset = load_training_dataset(training_dataset_path)
        boosters = fit_production_members(train_dataset, boost_rounds, features)
        member_trees = tuple(booster.num_trees() for booster in boosters)
        train_match_times = train_dataset[["match_id", "start_time"]].drop_duplicates("match_id")
        logger.info(
            "production train: publishing %s | no holdout metrics, use the research model for quality",
            live_dir,
        )
        logger.info(
            "ensemble %s k=%s trees: %s-%s mean=%s | train matches: %s | train rows: %s",
            ENSEMBLE_ARM,
            ENSEMBLE_K,
            min(member_trees),
            max(member_trees),
            round(sum(member_trees) / len(member_trees)),
            len(train_match_times),
            len(train_dataset),
        )
        log_dataset_span("train", train_match_times)
    
        staging_dir = staging_model_dir(live_dir)
        member_names = write_ensemble_members(staging_dir, boosters)
        write_model_meta(
            build_ensemble_model_meta(
                len(train_match_times),
                train_dataset_sha256,
                features=features,
                validation_dataset_sha256=None,
                metrics=None,
                members=member_names,
                member_trees=member_trees,
                source_lag_seconds=TRAIN_LAG_SECONDS,
            ),
            staging_dir / "model.json",
        )
        copy2(split_source_path, staging_dir / "split.parquet")
        archived_name = publish_model_dir(staging_dir, live_dir, archive_dir)
        logger.info("published: %s | archived previous model: %s", live_dir, archived_name)
    
    
    @dataclass(frozen=True)
    class CatalogPair:
        """The research and production catalogs of one feature set."""
    
        features: tuple[str, ...]
        research_dir: Path
        research_archive_dir: Path
        production_dir: Path
        production_archive_dir: Path
    
    
    LIVE_CATALOG_PAIRS = (
        CatalogPair(
            tuple(FEATURE_COLUMNS),
            RESEARCH_MODEL_DIR,
            RESEARCH_ARCHIVE_DIR,
            PRODUCTION_MODEL_DIR,
            PRODUCTION_ARCHIVE_DIR,
        ),
        CatalogPair(
            tuple(NO_XP_FEATURE_COLUMNS),
            RESEARCH_NOXP_MODEL_DIR,
            RESEARCH_NOXP_ARCHIVE_DIR,
            PRODUCTION_NOXP_MODEL_DIR,
            PRODUCTION_NOXP_ARCHIVE_DIR,
        ),
    )
    
    
    def main(argv: Sequence[str] = ()) -> None:
        """Publish live research then production for each catalog pair, or one experiment catalog."""
        args = parse_args(argv)
        setup_logging()
        if args.model_dir is not None:
            features = NO_XP_FEATURE_COLUMNS if args.no_xp else FEATURE_COLUMNS
            train_research(
                args.training_dataset,
                args.validation_dataset,
                RESEARCH_MODEL_SPLIT_PATH,
                args.model_dir,
                args.model_dir.parent / "archive",
                args.lag_seconds,
                features,
            )
            return
        for pair in LIVE_CATALOG_PAIRS:
            boost_rounds = train_research(
                TRAINING_DATASET_PATH,
                VALIDATION_DATASET_PATH,
                RESEARCH_MODEL_SPLIT_PATH,
                pair.research_dir,
                pair.research_archive_dir,
                TRAIN_LAG_SECONDS,
                pair.features,
            )
            train_production(
                PRODUCTION_TRAINING_DATASET_PATH,
                PRODUCTION_DATASET_SPLIT_PATH,
                pair.production_dir,
                pair.production_archive_dir,
                pair.features,
                boost_rounds=boost_rounds,
            )
    
    
    if __name__ == "__main__":
        main(sys.argv[1:])
    """Filesystem roots and data artifacts shared between pipeline stages."""
    
    from pathlib import Path
    
    BASE_DIR = Path(__file__).resolve().parents[3]  # repo root
    DATA_DIR = BASE_DIR / "data"
    RAW_DIR = DATA_DIR / "raw"
    
    NEW_PROCESSED_DIR = DATA_DIR / "new_processed"
    
    RAW_POLYMARKET_DOTA_DIR = RAW_DIR / "polymarket_dota"
    RAW_OPENDOTA_MATCHES_DIR = RAW_DIR / "opendota_matches"
    RAW_STRATZ_MATCHES_DIR = RAW_DIR / "stratz_matches"
    
    # Archived Gamma event pages: the only historical source of closedTime and the
    # sports secondsDelay, both of which the live API rewrites over time.
    POLYMARKET_UNIVERSE_EVENTS_DIR = RAW_POLYMARKET_DOTA_DIR / "universe" / "events"
    
    RAW_TELONEX_POLYMARKET_DIR = RAW_DIR / "telonex" / "polymarket"
    
    DOTA_UNIVERSE_PATH = NEW_PROCESSED_DIR / "universe" / "universe.parquet"
    NEW_DATASET_DIR = NEW_PROCESSED_DIR / "dataset"
    MATCH_CATALOG_DIR = NEW_PROCESSED_DIR / "match_catalog"
    MATCH_CATALOG_PATH = MATCH_CATALOG_DIR / "match_catalog.parquet"
    TRAINING_DATASET_PATH = NEW_DATASET_DIR / "training_dataset.parquet"
    VALIDATION_DATASET_PATH = NEW_DATASET_DIR / "validation_dataset.parquet"
    # Exact game-second feature rows for archive-schedule backtests (Dota).
    GAME_FEATURES_DATASET_PATH = NEW_DATASET_DIR / "game_features.parquet"
    PRODUCTION_DATASET_DIR = NEW_DATASET_DIR / "production"
    PRODUCTION_TRAINING_DATASET_PATH = PRODUCTION_DATASET_DIR / "training_dataset.parquet"
    PRODUCTION_DATASET_SPLIT_PATH = PRODUCTION_DATASET_DIR / "split.parquet"
    
    MARKET_SECONDS_DIR = NEW_PROCESSED_DIR / "market_seconds"
    
    NEW_MODEL_DIR = DATA_DIR / "new_model"
    # Live model dirs: research/ and production/ keep XP; *-noxp drop radiant_xp_adv.
    # A catalog is the directory (model.json plus member_*.txt).
    RESEARCH_MODEL_DIR = NEW_MODEL_DIR / "research"
    RESEARCH_MODEL_SPLIT_PATH = RESEARCH_MODEL_DIR / "split.parquet"  # rows: SplitRow
    PRODUCTION_MODEL_DIR = NEW_MODEL_DIR / "production"
    PRODUCTION_MODEL_META_PATH = PRODUCTION_MODEL_DIR / "model.json"
    RESEARCH_NOXP_MODEL_DIR = NEW_MODEL_DIR / "research-noxp"
    PRODUCTION_NOXP_MODEL_DIR = NEW_MODEL_DIR / "production-noxp"
    # Each publish moves the replaced live dir here, under its own branch.
    MODEL_ARCHIVE_DIR = NEW_MODEL_DIR / "archive"
    RESEARCH_ARCHIVE_DIR = MODEL_ARCHIVE_DIR / "research"
    PRODUCTION_ARCHIVE_DIR = MODEL_ARCHIVE_DIR / "production"
    RESEARCH_NOXP_ARCHIVE_DIR = MODEL_ARCHIVE_DIR / "research-noxp"
    PRODUCTION_NOXP_ARCHIVE_DIR = MODEL_ARCHIVE_DIR / "production-noxp"
    
    # Trader match archive: one directory per tracked match.
    TRADER_DIR = DATA_DIR / "trader"
    STATE_ARCHIVE_FILENAME = "state.jsonl"
    
    # Archive index + extracted feed schedules (archive_index package output).
    ARCHIVE_INDEX_DIR = DATA_DIR / "archive_index"
    from typing import TypedDict
    
    
    class ModelMetrics(TypedDict):
        """Validation metrics stored with one trained model."""
    
        trees: int
        rows: int
        future_300_n: int
        no_move_mae_300_cents: float
        model_mae_300_cents: float
        mae_gain_300_cents: float
        mae_gain_300_ci_low_cents: float
        mae_gain_300_ci_high_cents: float
        model_bias_300_cents: float
        dir_300_cents: float
    
    
    class ModelMeta(TypedDict):
        """JSON metadata stored beside one trained ensemble catalog."""
    
        name: str
        trained_at: str
        train_dataset_sha256: str
        validation_dataset_sha256: str | None
        features: list[str]
        source_lag_seconds: int
        train_matches: int
        # None for a production fit: it trains on every match, so there is no holdout.
        metrics: ModelMetrics | None
        members: list[str]
        member_trees: list[int]
        ensemble_arm: str
        ensemble_k: int
        ensemble_sampling: str
    ✓ • 1.15s

## Activity

    $ uv run python - <<'PY'
        from pathlib import Path
        s=Path('/Users/dimabytes/Desktop/# Codex conversation.txt').read_text()
        for i,x in enumerate(s.split('\n## ')):
         if x.startswith('User'):
          print(f'=== section {i} ===\n{x}')
        PY
    error: failed to open file `/Users/dimabytes/.cache/uv/sdists-v9/.git`: Operation not permitted (os error 1)
    ✗ (2) • 1.21s

## Activity

    $ cat src/shared/utils/gbm.py src/prepare_dataset/stratz_seconds.py src/shared/constants/dataset.py
    # pyright: reportMissingTypeStubs=false
    
    """Shared LightGBM fit helpers used by Dota and LoL trainers."""
    
    import hashlib
    import os
    from collections.abc import Sequence
    from concurrent.futures import ProcessPoolExecutor
    from dataclasses import dataclass
    from datetime import UTC, datetime
    from multiprocessing import get_context
    from pathlib import Path
    from typing import Any, Protocol, cast
    
    import lightgbm as lgb
    import numpy as np
    import numpy.typing as npt
    import pandas as pd
    
    from shared.types.dataset import DatasetRow
    from shared.types.model import ModelMeta, ModelMetrics
    from shared.utils.hashing import sha256_file
    from shared.utils.market_scenario_report import MetricConfidenceInterval
    from shared.utils.model_registry import MODEL_META_FILENAME, read_model_meta
    
    MAX_BOOST_ROUNDS = 3000
    LGB_PARAMS = {
        "objective": "regression_l1",
        "metric": "l1",
        "learning_rate": 0.05,
        "num_leaves": 63,
        "min_data_in_leaf": 200,
        "verbosity": -1,
    }
    
    # Pregame map-load prior is a live feature for this experiment; mid stays last.
    FEATURE_COLUMNS = [
        "second",
        "radiant_nw_adv",
        "radiant_nw",
        "dire_nw",
        "radiant_xp_adv",
        "deaths_radiant",
        "deaths_dire",
        "top1_nw_adv",
        "radiant_top1_nw_ratio",
        "dire_top1_nw_ratio",
        "market_radiant_prior",
        "market_p_radiant",
    ]
    NO_XP_FEATURE_COLUMNS = [column for column in FEATURE_COLUMNS if column != "radiant_xp_adv"]
    
    TRAINING_COLUMNS: list[str] = list(DatasetRow.__annotations__)
    
    ENSEMBLE_IDENTITY_VERSION = b"ensemble-identity-v1"
    EARLY_STOP_PATIENCE = 100
    MEMBER_THREADS = 1
    ENSEMBLE_K = 10
    # One process per member, MEMBER_THREADS inside it: the reduction order stays fixed,
    # so a refit reproduces the same bytes, and the K fits still use every core.
    MEMBER_FIT_WORKERS = min(ENSEMBLE_K, os.process_cpu_count() or 1)
    # Quality-fit RNG from docs/experiments/ensemble: QUALITY_SEED=1, default_sub90
    # is ARMS[3], outer_index=0. Keep this tuple so retraining reproduces the
    # sub90-20260912 catalogs member for member.
    ENSEMBLE_SEED = 1
    ENSEMBLE_ARM_INDEX = 3
    ENSEMBLE_OUTER_INDEX = 0
    ENSEMBLE_ARM = "default_sub90"
    ENSEMBLE_SAMPLING = "sub90"
    ENSEMBLE_SUBSAMPLE_FRACTION = 0.90
    
    
    class ModelCatalogError(ValueError):
        """A model directory cannot be loaded as an ensemble catalog."""
    
        def __init__(self, reason: str) -> None:
            super().__init__(reason)
            self.reason = reason
    
    
    class DeltaModel(Protocol):
        """Anything that turns feature rows into 300-second price-delta predictions."""
    
        def predict(self, data: Any) -> Any: ...
    
        def num_trees(self) -> int: ...
    
    
    @dataclass(frozen=True)
    class GbmPredictor:
        """Loaded catalog: mean of the listed LightGBM members."""
    
        model_dir: Path
        feature_names: tuple[str, ...]
        member_names: tuple[str, ...]
        member_trees: tuple[int, ...]
        _boosters: tuple[lgb.Booster, ...]
    
        def predict(self, data: Any) -> npt.NDArray[np.float64]:
            """Return the mean member delta for every row."""
            stacked = np.stack(
                [
                    np.asarray(booster.predict(data), dtype=np.float64)  # pyright: ignore[reportUnknownMemberType]
                    for booster in self._boosters
                ]
            )
            return stacked.mean(axis=0)
    
        def predict_one_thread(self, data: Any) -> npt.NDArray[np.float64]:
            """Return the mean member delta for every row on one LightGBM thread."""
            stacked = np.stack(
                [
                    np.asarray(
                        booster.predict(data, num_threads=1),  # pyright: ignore[reportUnknownMemberType]
                        dtype=np.float64,
                    )
                    for booster in self._boosters
                ]
            )
            return stacked.mean(axis=0)
    
        def num_trees(self) -> int:
            """Round of the mean member tree count; production uses this as boost_rounds."""
            return round(sum(self.member_trees) / len(self.member_trees))
    
    
    @dataclass(frozen=True)
    class HoldoutWindowStats:
        """300s holdout fields used to populate ModelMetrics cents."""
    
        rows: int
        future_price_300_count: int
        no_move_mae_300: float | None
        model_mae_300: float | None
        mae_gain_300: float | None
        mae_gain_300_ci: MetricConfidenceInterval | None
        model_bias_300: float | None
        directional_markout_300s: float | None
    
    
    def load_training_dataset(path: Path) -> pd.DataFrame:
        """Read DatasetRow columns from a Dota training parquet."""
        frame = pd.read_parquet(path)
        missing = [column for column in TRAINING_COLUMNS if column not in frame.columns]
        if missing:
            raise ValueError(f"training dataset missing columns: {', '.join(missing)}")
        return frame[TRAINING_COLUMNS]
    
    
    def build_price_delta_labels(frame: pd.DataFrame) -> npt.NDArray[np.float64]:
        """Return the canonical 300-second future-price delta target."""
        future = frame["signal_market_p_radiant_300s"].to_numpy(dtype=np.float64)
        current = frame["market_p_radiant"].to_numpy(dtype=np.float64)
        return future - current
    
    
    def predict_future_prices(
        model: DeltaModel,
        features: pd.DataFrame,
        current_market_prices: npt.NDArray[np.float64],
    ) -> npt.NDArray[np.float64]:
        """Predict price deltas and restore clipped future market prices."""
        deltas = np.asarray(model.predict(features), dtype=np.float64)
        return np.clip(current_market_prices + deltas, 0.0, 1.0)
    
    
    def ensemble_member_params() -> dict[str, object]:
        """Live LightGBM knobs with one thread so member fits stay reproducible."""
        params: dict[str, object] = dict(LGB_PARAMS)
        params["num_threads"] = MEMBER_THREADS
        return params
    
    
    def fit_ensemble_member_booster(
        train_X: pd.DataFrame,
        train_y: npt.NDArray[np.float64],
        valid_X: pd.DataFrame,
        valid_y: npt.NDArray[np.float64],
    ) -> lgb.Booster:
        """Train one ensemble member with early stopping on the holdout set."""
        train_data = lgb.Dataset(train_X, train_y)
        validation_data = lgb.Dataset(valid_X, valid_y)
        return lgb.train(  # pyright: ignore[reportUnknownMemberType]
            ensemble_member_params(),
            train_data,
            num_boost_round=MAX_BOOST_ROUNDS,
            valid_sets=[validation_data],
            valid_names=["validation"],
            callbacks=[lgb.early_stopping(EARLY_STOP_PATIENCE, verbose=False)],
        )
    
    
    def fit_ensemble_member_fixed_trees(
        train_X: pd.DataFrame,
        train_y: npt.NDArray[np.float64],
        boost_rounds: int,
    ) -> lgb.Booster:
        """Train one production member with exactly `boost_rounds` trees, one thread, no holdout."""
        train_data = lgb.Dataset(train_X, train_y)
        return lgb.train(  # pyright: ignore[reportUnknownMemberType]
            ensemble_member_params(),
            train_data,
            num_boost_round=boost_rounds,
        )
    
    
    def _ensemble_member_rng(member_index: int) -> np.random.Generator:
        """Independent Generator for one default_sub90 quality member."""
        return np.random.default_rng(
            np.random.SeedSequence(
                (ENSEMBLE_SEED, ENSEMBLE_ARM_INDEX, ENSEMBLE_OUTER_INDEX, member_index)
            )
        )
    
    
    def _member_match_ids(
        unique_ids: npt.NDArray[np.int64], member_index: int
    ) -> npt.NDArray[np.int64]:
        """Draw this member's 90% of the matches, without replacement."""
        rng = _ensemble_member_rng(member_index)
        n_keep = round(len(unique_ids) * ENSEMBLE_SUBSAMPLE_FRACTION)
        indices = rng.choice(len(unique_ids), size=n_keep, replace=False)
        return unique_ids[indices]
    
    
    def group_rows_by_match_id(
        frame: pd.DataFrame,
    ) -> tuple[dict[int, pd.DataFrame], npt.NDArray[np.int64]]:
        """Split a training frame by match_id, preserving first-seen match order."""
        grouped = {
            int(cast(int, match_id)): group for match_id, group in frame.groupby("match_id", sort=False)
        }
        unique_ids = frame["match_id"].drop_duplicates().to_numpy(dtype=np.int64)
        return grouped, unique_ids
    
    
    def member_train_frame(
        grouped: dict[int, pd.DataFrame],
        unique_ids: npt.NDArray[np.int64],
        member_index: int,
    ) -> pd.DataFrame:
        """Rows for one default_sub90 member: a random 90% of the matches."""
        match_ids = _member_match_ids(unique_ids, member_index)
        pieces = [grouped[int(match_id)] for match_id in match_ids]
        return pd.concat(pieces, ignore_index=True)
    
    
    @dataclass(frozen=True, eq=False)
    class ResearchMemberFit:
        """Research member fit: early stopping on the holdout picks each member's tree count."""
    
        valid_X: pd.DataFrame
        valid_y: npt.NDArray[np.float64]
        features: tuple[str, ...]
    
        def __call__(self, train: pd.DataFrame) -> lgb.Booster:
            """Fit one research member on its subsampled rows."""
            return fit_ensemble_member_booster(
                train[list(self.features)],
                build_price_delta_labels(train),
                self.valid_X,
                self.valid_y,
            )
    
    
    @dataclass(frozen=True, eq=False)
    class ProductionMemberFit:
        """Production member fit: every member gets the same fixed tree count and no holdout."""
    
        boost_rounds: int
        features: tuple[str, ...]
    
        def __call__(self, train: pd.DataFrame) -> lgb.Booster:
            """Fit one production member on its subsampled rows."""
            return fit_ensemble_member_fixed_trees(
                train[list(self.features)],
                build_price_delta_labels(train),
                self.boost_rounds,
            )
    
    
    EnsembleMemberFit = ResearchMemberFit | ProductionMemberFit
    
    _worker_rows: tuple[dict[int, pd.DataFrame], npt.NDArray[np.int64]] | None = None
    _worker_fit: EnsembleMemberFit | None = None
    
    
    def init_member_fit_worker(train_frame: pd.DataFrame, fit_member: EnsembleMemberFit) -> None:
        """Group the training rows once per worker so a member job only carries its index."""
        global _worker_rows, _worker_fit
        _worker_rows = group_rows_by_match_id(train_frame)
        _worker_fit = fit_member
    
    
    def fit_member_job(member_index: int) -> lgb.Booster:
        """Draw this member's rows inside the worker and fit it there."""
        if _worker_rows is None or _worker_fit is None:
            raise RuntimeError("member fit worker was not initialised")
        grouped, unique_ids = _worker_rows
        return _worker_fit(member_train_frame(grouped, unique_ids, member_index))
    
    
    def _fit_ensemble_members(
        train_frame: pd.DataFrame,
        fit_member: EnsembleMemberFit,
    ) -> list[lgb.Booster]:
        """Fit K subsampled members, one process each; `map` returns them in member order."""
        with ProcessPoolExecutor(
            max_workers=MEMBER_FIT_WORKERS,
            mp_context=get_context("spawn"),
            initializer=init_member_fit_worker,
            initargs=(train_frame, fit_member),
        ) as pool:
            return list(pool.map(fit_member_job, range(ENSEMBLE_K)))
    
    
    def fit_research_members(
        train_frame: pd.DataFrame,
        valid_X: pd.DataFrame,
        valid_y: npt.NDArray[np.float64],
        features: Sequence[str],
    ) -> list[lgb.Booster]:
        """Fit K default_sub90 members with early stopping on the holdout features."""
        return _fit_ensemble_members(train_frame, ResearchMemberFit(valid_X, valid_y, tuple(features)))
    
    
    def fit_production_members(
        train_frame: pd.DataFrame,
        boost_rounds: int,
        features: Sequence[str],
    ) -> list[lgb.Booster]:
        """Fit K default_sub90 members with a fixed tree count and no holdout."""
        return _fit_ensemble_members(train_frame, ProductionMemberFit(boost_rounds, tuple(features)))
    
    
    def build_ensemble_model_meta(
        train_matches: int,
        train_dataset_sha256: str,
        *,
        features: Sequence[str],
        validation_dataset_sha256: str | None,
        metrics: ModelMetrics | None,
        members: Sequence[str],
        member_trees: Sequence[int],
        source_lag_seconds: int,
    ) -> ModelMeta:
        """Build catalog metadata: members, trees, and the subsampling training rule."""
        now = datetime.now(UTC)
        return ModelMeta(
            name=now.strftime("%Y%m%dT%H%M%SZ"),
            trained_at=now.isoformat(timespec="seconds").replace("+00:00", "Z"),
            train_dataset_sha256=train_dataset_sha256,
            validation_dataset_sha256=validation_dataset_sha256,
            features=list(features),
            source_lag_seconds=source_lag_seconds,
            train_matches=train_matches,
            metrics=metrics,
            members=list(members),
            member_trees=list(member_trees),
            ensemble_arm=ENSEMBLE_ARM,
            ensemble_k=len(members),
            ensemble_sampling=ENSEMBLE_SAMPLING,
        )
    
    
    def require_metric(value: float | None, field_name: str) -> float:
        """Require a populated validation metric for model metadata."""
        if value is None:
            raise ValueError(f"missing validation metric: {field_name}")
        return value
    
    
    def build_model_metrics(
        model: DeltaModel,
        full_window_stats: HoldoutWindowStats,
    ) -> ModelMetrics:
        """Build holdout metrics from the full-window 300s validation stats."""
        mae_ci = full_window_stats.mae_gain_300_ci
        if mae_ci is None:
            raise ValueError("missing validation confidence interval: mae_gain_300")
        return ModelMetrics(
            trees=model.num_trees(),
            rows=full_window_stats.rows,
            future_300_n=full_window_stats.future_price_300_count,
            no_move_mae_300_cents=require_metric(full_window_stats.no_move_mae_300, "no_move_mae_300")
            * 100,
            model_mae_300_cents=require_metric(full_window_stats.model_mae_300, "model_mae_300") * 100,
            mae_gain_300_cents=require_metric(full_window_stats.mae_gain_300, "mae_gain_300") * 100,
            mae_gain_300_ci_low_cents=mae_ci.low * 100,
            mae_gain_300_ci_high_cents=mae_ci.high * 100,
            model_bias_300_cents=require_metric(full_window_stats.model_bias_300, "model_bias_300")
            * 100,
            dir_300_cents=require_metric(
                full_window_stats.directional_markout_300s,
                "directional_markout_300s",
            )
            * 100,
        )
    
    
    def member_filename(index: int) -> str:
        """Return the catalog filename for ensemble member `index`."""
        return f"member_{index:02d}.txt"
    
    
    def catalog_member_names(meta: ModelMeta) -> tuple[str, ...]:
        """Resolve member filenames; an empty, missing, or invalid list is a load error."""
        raw = meta.get("members")
        if type(raw) is not list or not raw:
            raise ModelCatalogError("members list is empty")
        if any(type(item) is not str for item in raw):
            raise ModelCatalogError("members list is empty")
        names = tuple(raw)
        if any(not _is_plain_filename(name) for name in names):
            raise ModelCatalogError("member name is not a plain filename")
        if len(set(names)) != len(names):
            raise ModelCatalogError("members list repeats a file")
        return names
    
    
    def catalog_member_trees(meta: ModelMeta) -> tuple[int, ...]:
        """Resolve recorded tree counts; missing, null, or a non-int list is a load error."""
        raw = meta.get("member_trees")
        if type(raw) is not list or not raw:
            raise ModelCatalogError("member_trees list is empty")
        if any(type(item) is not int for item in raw):
            raise ModelCatalogError("member_trees list is empty")
        return tuple(raw)
    
    
    def _is_plain_filename(name: str) -> bool:
        """True when `name` is a single path component, not empty, `.`, or `..`."""
        return name != "" and Path(name).name == name and name not in {".", ".."}
    
    
    def require_regular_file(path: Path, reason: str) -> None:
        """Require `path` to be a regular file, or raise a catalog error."""
        try:
            is_file = path.is_file()
        except OSError as exc:
            raise ModelCatalogError("model artifacts are unreadable") from exc
        if not is_file:
            raise ModelCatalogError(reason)
    
    
    def load_booster(path: Path) -> lgb.Booster:
        """Construct one LightGBM booster from a member file."""
        try:
            return lgb.Booster(model_file=str(path))
        except (lgb.basic.LightGBMError, ValueError, RuntimeError) as exc:
            raise ModelCatalogError("ensemble member cannot be parsed by LightGBM") from exc
        except OSError as exc:
            raise ModelCatalogError("ensemble member cannot be read") from exc
    
    
    def load_predictor_from_meta(model_dir: Path, meta: ModelMeta) -> GbmPredictor:
        """Load every listed member and refuse a partial catalog."""
        features = list(meta["features"])
        if not features:
            raise ModelCatalogError("model.json features empty")
        names = catalog_member_names(meta)
        recorded_trees = catalog_member_trees(meta)
        if len(recorded_trees) != len(names):
            raise ModelCatalogError("member_trees do not match members")
        boosters: list[lgb.Booster] = []
        trees: list[int] = []
        for name in names:
            path = model_dir / name
            require_regular_file(path, "ensemble member is missing or not a regular file")
            booster = load_booster(path)
            try:
                booster_features = list(booster.feature_name())
            except (lgb.basic.LightGBMError, OSError, ValueError, RuntimeError) as exc:
                raise ModelCatalogError("ensemble member features cannot be read") from exc
            if booster_features != features:
                raise ModelCatalogError("ensemble member features do not match model.json")
            boosters.append(booster)
            trees.append(booster.num_trees())
        if recorded_trees != tuple(trees):
            raise ModelCatalogError("member_trees do not match the loaded boosters")
        return GbmPredictor(
            model_dir=model_dir,
            feature_names=tuple(features),
            member_names=names,
            member_trees=tuple(trees),
            _boosters=tuple(boosters),
        )
    
    
    def load_predictor(model_dir: Path) -> GbmPredictor:
        """Load an ensemble catalog from `model_dir` / model.json."""
        meta_path = model_dir / MODEL_META_FILENAME
        require_regular_file(meta_path, "model.json is missing or not a regular file")
        try:
            meta = read_model_meta(meta_path)
        except (OSError, ValueError) as exc:
            raise ModelCatalogError("model.json cannot be read or parsed") from exc
        return load_predictor_from_meta(model_dir, meta)
    
    
    def model_identity_sha256(model_dir: Path) -> str:
        """Identity of a catalog: ordered members, their SHAs, and predict metadata."""
        meta = read_model_meta(model_dir / MODEL_META_FILENAME)
        names = catalog_member_names(meta)
        hasher = hashlib.sha256()
        hasher.update(ENSEMBLE_IDENTITY_VERSION)
        hasher.update(b"\n")
        for name in names:
            hasher.update(name.encode("utf-8"))
            hasher.update(b"\n")
            hasher.update(sha256_file(model_dir / name).encode("ascii"))
            hasher.update(b"\n")
        hasher.update(b"features\n")
        for feature in meta["features"]:
            hasher.update(feature.encode("utf-8"))
            hasher.update(b"\n")
        hasher.update(b"source_lag_seconds\n")
        hasher.update(str(int(meta["source_lag_seconds"])).encode("ascii"))
        return hasher.hexdigest()
    
    
    def write_ensemble_members(staging_dir: Path, boosters: Sequence[lgb.Booster]) -> tuple[str, ...]:
        """Write `member_00.txt` … into staging and return those filenames."""
        names = tuple(member_filename(index) for index in range(len(boosters)))
        for name, booster in zip(names, boosters, strict=True):
            booster.save_model(staging_dir / name)
        return names
    from bisect import bisect_right
    from dataclasses import dataclass
    from typing import cast
    
    from shared.constants.dataset import MODEL_START_SECOND
    from shared.types.stratz import (
        StratzMatchBase,
        StratzPlayerPlayback,
        StratzPlayerUpdateGoldEvent,
        UsableStratzMatch,
    )
    from shared.utils.dota_levels import (
        level_at_second,
        radiant_xp_advantage,
        radiant_xp_advantage_at_second,
    )
    from shared.utils.stratz import death_times
    from shared.utils.top_players import TopPlayerFeatures, build_top_player_features
    
    
    @dataclass(frozen=True)
    class ExactSecondState:
        match_id: int
        second: int
        radiant_win: bool
        radiant_nw_adv: int
        radiant_nw: int
        dire_nw: int
        radiant_xp_adv: int
        deaths_radiant: int
        deaths_dire: int
        top: TopPlayerFeatures
    
    
    @dataclass(frozen=True)
    class MinuteLeadMismatch:
        match_id: int
        second: int
        expected: int
        actual: int
    
    
    class MinuteLeadMismatchError(ValueError):
        def __init__(self, mismatch: MinuteLeadMismatch) -> None:
            self.mismatch = mismatch
            super().__init__(
                f"match_id={mismatch.match_id} second={mismatch.second} "
                f"expected={mismatch.expected} actual={mismatch.actual}"
            )
    
    
    @dataclass(frozen=True)
    class _PlayerPlayback:
        radiant: bool
        gold_events: tuple[StratzPlayerUpdateGoldEvent, ...]
        level_seconds: tuple[int, ...]
        player_index: int
    
    
    @dataclass
    class _PlayerCursor:
        gold_index: int = 0
        networth: int = 0
    
    
    def lead_array_index(second: int) -> int:
        """Map minute-boundary second to lead index: 0→1, 60→2 (index 0 is pre-horn -60)."""
        return second // 60 + 1
    
    
    def validate_minute_leads(
        match_id: int,
        second: int,
        radiant_nw_adv: int,
        nw_leads: list[int],
    ) -> None:
        index = lead_array_index(second)
        if index >= len(nw_leads):
            raise ValueError(
                f"match_id={match_id} second={second} index={index} "
                f"radiantNetworthLeads length={len(nw_leads)}"
            )
        expected_nw = nw_leads[index]
        if radiant_nw_adv != expected_nw:
            raise MinuteLeadMismatchError(
                MinuteLeadMismatch(
                    match_id=match_id,
                    second=second,
                    expected=expected_nw,
                    actual=radiant_nw_adv,
                )
            )
    
    
    def assert_minute_consistency(
        *,
        match: StratzMatchBase,
        second: int,
        player_playbacks: list[_PlayerPlayback],
        cursors: list[_PlayerCursor],
        radiant_nw_adv: int,
        nw_leads: list[int],
    ) -> None:
        validate_minute_leads(
            match_id=match["id"],
            second=second,
            radiant_nw_adv=radiant_nw_adv,
            nw_leads=nw_leads,
        )
        if second < 0:
            return
        npm_index = second // 60
        for playback, cursor in zip(player_playbacks, cursors, strict=True):
            player = match["players"][playback.player_index]
            expected_nw = player["stats"]["networthPerMinute"][npm_index]
            if cursor.networth != expected_nw:
                raise ValueError(
                    f"match_id={match['id']} player_index={playback.player_index} "
                    f"second={second} expected={expected_nw} actual={cursor.networth}"
                )
    
    
    class MatchDataError(ValueError):
        def __init__(self, reason: str) -> None:
            self.reason = reason
            super().__init__(reason)
    
    
    @dataclass(frozen=True)
    class SideNetworths:
        radiant: tuple[int, ...]
        dire: tuple[int, ...]
    
    
    def _split_by_side(match: StratzMatchBase, networths: list[int]) -> SideNetworths:
        radiant: list[int] = []
        dire: list[int] = []
        for player, networth in zip(match["players"], networths, strict=True):
            if player["isRadiant"]:
                radiant.append(networth)
            else:
                dire.append(networth)
        return SideNetworths(radiant=tuple(radiant), dire=tuple(dire))
    
    
    def _playback_networths(match: StratzMatchBase, second: int) -> SideNetworths | None:
        networths: list[int] = []
        for player in match["players"]:
            playback_data = player.get("playbackData")
            if playback_data is None:
                return None
            playback = cast(StratzPlayerPlayback, playback_data)
            networth = 0
            for event in sorted(playback["playerUpdateGoldEvents"], key=lambda item: item["time"]):
                if event["time"] > second:
                    break
                networth = event["networth"]
            networths.append(networth)
        return _split_by_side(match, networths)
    
    
    def _minute_networths(match: StratzMatchBase, second: int) -> SideNetworths | None:
        if second < 0:
            return _playback_networths(match, second)
        npm_index = second // 60
        networths: list[int] = []
        for player in match["players"]:
            per_minute = player["stats"]["networthPerMinute"]
            if npm_index >= len(per_minute):
                raise MatchDataError("short networthPerMinute")
            networths.append(per_minute[npm_index])
        return _split_by_side(match, networths)
    
    
    def build_second_state(
        match: StratzMatchBase,
        second: int,
        *,
        networths: SideNetworths,
        radiant_xp_adv: int,
        radiant_deaths: list[int],
        dire_deaths: list[int],
    ) -> ExactSecondState:
        radiant_nw = sum(networths.radiant)
        dire_nw = sum(networths.dire)
        return ExactSecondState(
            match_id=match["id"],
            second=second,
            radiant_win=match["didRadiantWin"],
            radiant_nw_adv=radiant_nw - dire_nw,
            radiant_nw=radiant_nw,
            dire_nw=dire_nw,
            radiant_xp_adv=radiant_xp_adv,
            deaths_radiant=bisect_right(radiant_deaths, second),
            deaths_dire=bisect_right(dire_deaths, second),
            top=build_top_player_features(networths.radiant, networths.dire),
        )
    
    
    def build_minute_states(
        match: UsableStratzMatch, end_second_exclusive: int
    ) -> tuple[ExactSecondState, ...]:
        nw_leads = match["radiantNetworthLeads"]
    
        radiant_deaths = death_times(match, radiant=True)
        dire_deaths = death_times(match, radiant=False)
        states: list[ExactSecondState] = []
        for second in range(MODEL_START_SECOND, end_second_exclusive, 60):
            if lead_array_index(second) >= len(nw_leads):
                break
            networths = _minute_networths(match, second)
            if networths is None:
                continue
            state = build_second_state(
                match,
                second,
                networths=networths,
                radiant_xp_adv=radiant_xp_advantage_at_second(match["players"], second),
                radiant_deaths=radiant_deaths,
                dire_deaths=dire_deaths,
            )
            validate_minute_leads(match["id"], second, state.radiant_nw_adv, nw_leads)
            states.append(state)
        return tuple(states)
    
    
    def collect_player_playbacks(match: StratzMatchBase) -> list[_PlayerPlayback]:
        player_playbacks: list[_PlayerPlayback] = []
        for player_index, player in enumerate(match["players"]):
            playback_data = player["playbackData"]
            if playback_data is None:
                raise ValueError(
                    f"match_id={match['id']} player_index={player_index} player playbackData is missing"
                )
            playback = cast(StratzPlayerPlayback, playback_data)
            gold_events = tuple(
                sorted(playback["playerUpdateGoldEvents"], key=lambda event: event["time"])
            )
            player_playbacks.append(
                _PlayerPlayback(
                    radiant=player["isRadiant"],
                    gold_events=gold_events,
                    level_seconds=tuple(player["stats"]["level"]),
                    player_index=player_index,
                )
            )
        return player_playbacks
    
    
    def build_exact_second_states(match: UsableStratzMatch) -> tuple[ExactSecondState, ...]:
        nw_leads = match["radiantNetworthLeads"]
        player_playbacks = collect_player_playbacks(match)
    
        radiant_deaths = death_times(match, True)
        dire_deaths = death_times(match, False)
        cursors = [_PlayerCursor() for _ in player_playbacks]
        rows: list[ExactSecondState] = []
    
        for second in range(MODEL_START_SECOND, match["durationSeconds"] + 1):
            radiant_levels: list[int] = []
            dire_levels: list[int] = []
            radiant_networths: list[int] = []
            dire_networths: list[int] = []
            for playback, cursor in zip(player_playbacks, cursors, strict=True):
                gold_events = playback.gold_events
                while (
                    cursor.gold_index < len(gold_events)
                    and gold_events[cursor.gold_index]["time"] <= second
                ):
                    cursor.networth = gold_events[cursor.gold_index]["networth"]
                    cursor.gold_index += 1
    
                if playback.radiant:
                    radiant_levels.append(level_at_second(playback.level_seconds, second))
                    radiant_networths.append(cursor.networth)
                else:
                    dire_levels.append(level_at_second(playback.level_seconds, second))
                    dire_networths.append(cursor.networth)
            state = build_second_state(
                match,
                second,
                networths=SideNetworths(
                    radiant=tuple(radiant_networths),
                    dire=tuple(dire_networths),
                ),
                radiant_xp_adv=radiant_xp_advantage(radiant_levels, dire_levels),
                radiant_deaths=radiant_deaths,
                dire_deaths=dire_deaths,
            )
            if second % 60 == 0:
                assert_minute_consistency(
                    match=match,
                    second=second,
                    player_playbacks=player_playbacks,
                    cursors=cursors,
                    radiant_nw_adv=state.radiant_nw_adv,
                    nw_leads=nw_leads,
                )
            rows.append(state)
    
        return tuple(rows)
    """Shared dataset chronology constants."""
    
    VALIDATION_START_TIME = 1_780_563_592
    # Collector/Telonex day holes (e.g. 2026-08-08) skip this many empty-book
    # misses; more than this still aborts so a broken capture cannot silently
    # empty the split. Train is larger, so its cap sits on the known hole
    # (161 maps with no in-window book as of the spread-6 rebuild).
    MAX_VALIDATION_HARD_MISSES = 20
    MAX_TRAIN_HARD_MISSES = 161
    # A longer run of signal-window seconds without a fresh two-sided book marks a
    # validation match as ineligible for bulk backtesting. ML validation keeps it.
    MAX_BOOK_GAP_SECONDS = 120
    MODEL_START_SECOND = -60
    # Train rows and train_model eval stay in this window; prepare writes validation further.
    TRAIN_END_SECOND_EXCLUSIVE = 600
    MODEL_TARGET_HORIZON_SECONDS = 300
    # Book-gap eligibility still ends here. Prepare writes validation rows through
    # map duration so backtest FV can roll until game end.
    VALIDATION_SIGNAL_END_SECOND_EXCLUSIVE = TRAIN_END_SECOND_EXCLUSIVE + MODEL_TARGET_HORIZON_SECONDS
    # STRATZ snapshot age baked into training rows: state at S, market at S+lag.
    TRAIN_LAG_SECONDS = 10
    # STRATZ snapshot age at execution: validation rows and backtest predict.
    BACKTEST_LAG_SECONDS = 10
    PREHORN_LEAD_SECONDS = -(MODEL_START_SECOND + BACKTEST_LAG_SECONDS)
    ✓ • 1.37s

## Activity

    $ cat src/collect/s05b_fetch_stratz_matches.py src/collect/s05_fetch_opendota_matches.py src/collect/common/paths.py && rg -n 'GbmPredictor|load_predictor|predict_future_prices|FEATURE_COLUMNS|predict_delta' src/backtest src/shared/utils/gbm* && ls data/raw && ls docs/experiments
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
    import gzip
    import json
    import sys
    import time
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from datetime import UTC, datetime
    from pathlib import Path
    from typing import Annotated, Any
    
    import httpx
    import typer
    
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    
    from collect.common.opendota_candidates import opendota_params
    from collect.common.window_ids import load_admitted_match_ids
    from shared.constants import api
    from shared.types.opendota import OpenDotaMatch, OpenDotaMatchCache
    from shared.utils.http import get_json, http_client
    from shared.utils.log import print_count
    from shared.utils.opendota import opendota_match_cache_path
    
    
    def write_raw_payload(match_id: int, match: OpenDotaMatch) -> None:
        payload: OpenDotaMatchCache = {
            "match_id": int(match_id),
            "fetched_at": datetime.now(tz=UTC).isoformat(),
            "data": match,
        }
        out = opendota_match_cache_path(match_id)
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp = out.with_suffix(out.suffix + ".tmp")
        with gzip.open(tmp, "wt", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        tmp.replace(out)
    
    
    def fetch_one(client: httpx.Client, params: dict[str, Any], match_id: int) -> None:
        match: OpenDotaMatch = get_json(client, f"{api.OPENDOTA_API}/matches/{match_id}", params)
        write_raw_payload(match_id, match)
    
    
    def main(
        budget: Annotated[
            int, typer.Option(help="Max API requests this run (free tier: 2,000/day).")
        ] = 1900,
        sleep: Annotated[
            float,
            typer.Option(
                help="Seconds after each request in sequential mode; ignored with --workers>1."
            ),
        ] = 1.1,
        workers: Annotated[
            int,
            typer.Option(help="Parallel HTTP workers. Paid key (~3000/min): try 40-60 with --sleep 0."),
        ] = 1,
    ) -> None:
        params = opendota_params()
        match_ids = load_admitted_match_ids()
        to_fetch = [mid for mid in match_ids if not opendota_match_cache_path(mid).exists()][
            : max(0, budget)
        ]
        print(
            f"to_fetch={len(to_fetch)} workers={workers} budget={budget} "
            f"api_key={'yes' if params.get('api_key') else 'no'}",
            flush=True,
        )
    
        failed = 0
        with http_client() as client:
            if workers <= 1:
                for match_id in to_fetch:
                    try:
                        fetch_one(client, params, match_id)
                    except Exception as exc:
                        failed += 1
                        print(f"failed OpenDota match {match_id}: {exc}", flush=True)
                    time.sleep(sleep)
            else:
                with ThreadPoolExecutor(max_workers=workers) as pool:
                    match_id_by_future = {
                        pool.submit(fetch_one, client, params, mid): mid for mid in to_fetch
                    }
                    for fut in as_completed(match_id_by_future):
                        match_id = match_id_by_future[fut]
                        try:
                            fut.result()
                        except Exception as exc:
                            failed += 1
                            print(f"failed OpenDota match {match_id}: {exc}", flush=True)
    
        print_count("matches_in_corpus", len(match_ids))
        print_count("saved_this_run", len(to_fetch) - failed)
        print_count("failed_this_run", failed)
    
    
    if __name__ == "__main__":
        app = typer.Typer()
        app.command(help="Fetch the OpenDota parsed match cache for linked dataset matches.")(main)
        app()
    from shared.constants.paths import (
        DOTA_UNIVERSE_PATH,
        NEW_PROCESSED_DIR,
        RAW_DIR,
        RAW_POLYMARKET_DOTA_DIR,
    )
    
    RAW_POLYMARKET_DOTA_PRICES_HISTORY_DIR = RAW_POLYMARKET_DOTA_DIR / "prices_history"
    RAW_UNIVERSE_DIR = RAW_POLYMARKET_DOTA_DIR / "universe"
    RAW_OPENDOTA_CANDIDATE_PAGES_DIR = RAW_DIR / "opendota_candidate_index" / "pages"
    
    UNIVERSE_DIR = NEW_PROCESSED_DIR / "universe"
    UNIVERSE_PATH = DOTA_UNIVERSE_PATH
    UNIVERSE_MANIFEST_PATH = UNIVERSE_DIR / "universe_manifest.json"
    OPENDOTA_LINKS_DIR = NEW_PROCESSED_DIR / "opendota_links"
    OPENDOTA_LINKS_PATH = OPENDOTA_LINKS_DIR / "opendota_links.parquet"
    OPENDOTA_LINK_AUDIT_PATH = OPENDOTA_LINKS_DIR / "opendota_link_audit.parquet"
    MATCH_LINKS_DIR = NEW_PROCESSED_DIR / "match_links"
    MATCH_LINKS_PATH = MATCH_LINKS_DIR / "match_links.parquet"
    MATCH_LINK_AUDIT_PATH = MATCH_LINKS_DIR / "match_link_audit.parquet"
    NEW_STRATZ_MATCH_INDEX_DIR = NEW_PROCESSED_DIR / "stratz_match_index"
    STRATZ_MATCH_INDEX_PATH = NEW_STRATZ_MATCH_INDEX_DIR / "stratz_match_index.parquet"
    GRID_GAME_WINDOWS_DIR = NEW_PROCESSED_DIR / "grid_game_starts"
    GRID_GAME_WINDOWS_PATH = GRID_GAME_WINDOWS_DIR / "grid_game_windows.parquet"
    PREGAME_QUOTES_DIR = NEW_PROCESSED_DIR / "pregame_quotes"
    PREGAME_QUOTES_PATH = PREGAME_QUOTES_DIR / "pregame_quotes.parquet"
    src/shared/utils/gbm.py:37:FEATURE_COLUMNS = [
    src/shared/utils/gbm.py:51:NO_XP_FEATURE_COLUMNS = [column for column in FEATURE_COLUMNS if column != "radiant_xp_adv"]
    src/shared/utils/gbm.py:90:class GbmPredictor:
    src/shared/utils/gbm.py:157:def predict_future_prices(
    src/shared/utils/gbm.py:456:def load_predictor_from_meta(model_dir: Path, meta: ModelMeta) -> GbmPredictor:
    src/shared/utils/gbm.py:481:    return GbmPredictor(
    src/shared/utils/gbm.py:490:def load_predictor(model_dir: Path) -> GbmPredictor:
    src/shared/utils/gbm.py:498:    return load_predictor_from_meta(model_dir, meta)
    src/backtest/signals.py:21:from archive_index.schedule import MODEL_WINDOWS, SNAPSHOT_FEATURE_COLUMNS, ScheduleTick
    src/backtest/signals.py:37:from shared.utils.gbm import load_predictor
    src/backtest/signals.py:213:    predictor = load_predictor(model_dir)
    src/backtest/signals.py:642:    for column in SNAPSHOT_FEATURE_COLUMNS:
    opendota_candidate_index
    opendota_constants
    opendota_matches
    polymarket_dota
    pro_matches
    stratz_matches
    telonex
    README.md
    abs-label-weight.md
    buildings-adv-features.md
    burst-c20.md
    buy-depth-cap
    buy-ladder-v2
    classif-p-up.diff
    classif-p-up.md
    dataset-expansion-shelved.md
    delta-sizing
    dota-horizon-window.md
    dota-minute-specialists
    draft-features-reverted.md
    early-stop-gates
    early-stop-gates.md
    ensemble
    entry-fair-floor.md
    entry-fair-sell-gate.md
    entry-k-ceiling.md
    equal-capital-clip.md
    fair-ema-min-delta.md
    gated-early-stop.md
    ghost-fill-audit
    grid-v1-cadence.md
    hold-gate
    hold-gate.md
    huber-monotone.md
    hybrid-prior-v1-rejected.md
    kill-gate.md
    label-horizon-120.md
    ladder-budget-v5
    lag-grid.md
    leaf-size
    leaf-size.md
    lol-cut900.md
    lol-grid-widget.md
    lol-horizon-scaled-delta.md
    lol-horizon-sweep
    lol-horizon-train.md
    lol-horizon-window-deltas.md
    lol-late-phase
    lol-league-whitelist.md
    lol-minute-train.md
    lol-no-unwind.md
    lol-nwoff.md
    lol-overnight-maker-sweep.md
    lol-sweep-540
    map-first-entry-and-depth-caps.md
    match-balanced-fixed-minute.md
    min-abs-delta-015.md
    no-unwind-nwoff.md
    no-unwind.md
    no-xp
    oddin-no-xp
    oddin-no-xp.md
    order-outcomes
    outcome-p-win.md
    patch-aware-weighting.md
    pathwise-calibration
    pinnacle-p.md
    pnl-action-v1
    position-scaling.md
    pre-draft-prior.md
    pred-sizing-ramp.md
    prior-delta60-features.md
    rung-economics
    s2-buy-post.md
    score-gap-proper-betting.md
    sell-drop-block
    sell-gate-held-delta.diff
    sell-gate-held-delta.md
    sell-live-cadence.md
    sell-through-ask
    top1-player-features.md
    train-1800
    tree-complexity-stability
    v5-delta-02.md
    v5-unwind-300.md
    ✓ • 1.47s

## Activity

    $ UV_CACHE_DIR=/tmp/codex-uv-cache uv run --no-sync python - <<'PY'
        from pathlib import Path
        s=Path('/Users/dimabytes/Desktop/# Codex conversation.txt').read_text()
        for i,x in enumerate(s.split('\n## ')):
         if x.startswith('User'):
          print(f'=== section {i} ===\n{x}')
        PY
    === section 1 ===
    User
    
    Users/dimabytes/.cursor/plans/Look-ahead validation filter-d981831f.plan.md Теперь у меня такой план. Мне он сейчас не нравится немного, потому что раньше было просто. У нас было train, validation, в production шло все, что находится в train и в validation. Для early stopping использовался validation, и этот validation использовался в бэктесте.
    
    Все просто. Ну, проверить, что раньше так и было. Этот план предлагает немножечко все изменить, и как бы у нас получится, что у нас в train... Окей, одни карты, там один будет фильтр, хорошо. В validation этот фильтр не применяется, ладно.
    
    Но потом дальше в production должны пойти какие-то, которых не было в validation. Early stopping тоже должны пойти, которых не было в validation. Короче, какая-то хуета, мне не нравится. Какие есть варианты, как там это по-правильному сделать, по-простому? Я не понимаю.
    
    === section 26 ===
    User
    
    /Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace/.analysis/ladder-thin-2026-09-24/REPORT.md Нет, так здесь же проблема не в этом. Проблема в том, что, в принципе, это правило. То есть я хотел это правило, чтобы оно мне исключало карты, которые вредны, которые токсичны и неправильны для обучения. Потому что они мертвые.
    
    То есть там нет торгов, и там он, допустим, берет на пятой минуте цену, там, не знаю, 50%, а потом смотрит через пять минут, там, не знаю, 100%. Там какая-то херовая карта, сломанная. Я хотел такие карты исключить. И поэтому я хотел их исключить от стрейна.
    
    Просто если сейчас мы оставим их в validation, тогда у нас они, получается, попадут в production. И тогда это тупо будет, что у нас в production все равно эти отравленные карты будут. Идея была в этом. Или ты думаешь, что эти карты, на самом деле, не отравленные.
    
    Или какой-то есть способ. Просто другой вариант это как-то добавить какой-то фильтр, который бы их отовсюду убрал. Просто до этого я убрал их отовсюду, кроме лайва, получается. А в лайве у меня такие карты все еще мы торговали.
    
    И это неправильно. То есть нужно, видимо, какой-то добавить, может, фильтр придумать, который бы их убрал отовсюду. И из бэктеста, и из лайва, и из обучения отовсюду. И он не был бы look ahead. В этом получается смысл.
    
    Какой такой вариант есть? Это было все из этого репорта изначально.
    
    ✓ • 1.49s

## Assistant

Это изменение затрагивает и обучение, и вычисление признаков в бэктесте, поэтому прорабатываю его как отдельный эксперимент.

Уже вижу важное различие: основная модель предсказывает изменение рыночной цены через 300 секунд. Старые матчи без Polymarket не дают такую метку, но могут обучить модель вероятности победы, прогноз которой станет дополнительным признаком основной модели. Проверяю, достаточно ли для этого сохранённых данных STRATZ.

## Activity

    $ cat src/prepare_dataset/stratz_seconds.py src/shared/utils/stratz.py src/shared/constants/dataset.py docs/experiments/hybrid-prior-v1-rejected.md docs/experiments/dataset-expansion-shelved.md docs/experiments/outcome-p-win.md
    from bisect import bisect_right
    from dataclasses import dataclass
    from typing import cast
    
    from shared.constants.dataset import MODEL_START_SECOND
    from shared.types.stratz import (
        StratzMatchBase,
        StratzPlayerPlayback,
        StratzPlayerUpdateGoldEvent,
        UsableStratzMatch,
    )
    from shared.utils.dota_levels import (
        level_at_second,
        radiant_xp_advantage,
        radiant_xp_advantage_at_second,
    )
    from shared.utils.stratz import death_times
    from shared.utils.top_players import TopPlayerFeatures, build_top_player_features
    
    
    @dataclass(frozen=True)
    class ExactSecondState:
        match_id: int
        second: int
        radiant_win: bool
        radiant_nw_adv: int
        radiant_nw: int
        dire_nw: int
        radiant_xp_adv: int
        deaths_radiant: int
        deaths_dire: int
        top: TopPlayerFeatures
    
    
    @dataclass(frozen=True)
    class MinuteLeadMismatch:
        match_id: int
        second: int
        expected: int
        actual: int
    
    
    class MinuteLeadMismatchError(ValueError):
        def __init__(self, mismatch: MinuteLeadMismatch) -> None:
            self.mismatch = mismatch
            super().__init__(
                f"match_id={mismatch.match_id} second={mismatch.second} "
                f"expected={mismatch.expected} actual={mismatch.actual}"
            )
    
    
    @dataclass(frozen=True)
    class _PlayerPlayback:
        radiant: bool
        gold_events: tuple[StratzPlayerUpdateGoldEvent, ...]
        level_seconds: tuple[int, ...]
        player_index: int
    
    
    @dataclass
    class _PlayerCursor:
        gold_index: int = 0
        networth: int = 0
    
    
    def lead_array_index(second: int) -> int:
        """Map minute-boundary second to lead index: 0→1, 60→2 (index 0 is pre-horn -60)."""
        return second // 60 + 1
    
    
    def validate_minute_leads(
        match_id: int,
        second: int,
        radiant_nw_adv: int,
        nw_leads: list[int],
    ) -> None:
        index = lead_array_index(second)
        if index >= len(nw_leads):
            raise ValueError(
                f"match_id={match_id} second={second} index={index} "
                f"radiantNetworthLeads length={len(nw_leads)}"
            )
        expected_nw = nw_leads[index]
        if radiant_nw_adv != expected_nw:
            raise MinuteLeadMismatchError(
                MinuteLeadMismatch(
                    match_id=match_id,
                    second=second,
                    expected=expected_nw,
                    actual=radiant_nw_adv,
                )
            )
    
    
    def assert_minute_consistency(
        *,
        match: StratzMatchBase,
        second: int,
        player_playbacks: list[_PlayerPlayback],
        cursors: list[_PlayerCursor],
        radiant_nw_adv: int,
        nw_leads: list[int],
    ) -> None:
        validate_minute_leads(
            match_id=match["id"],
            second=second,
            radiant_nw_adv=radiant_nw_adv,
            nw_leads=nw_leads,
        )
        if second < 0:
            return
        npm_index = second // 60
        for playback, cursor in zip(player_playbacks, cursors, strict=True):
            player = match["players"][playback.player_index]
            expected_nw = player["stats"]["networthPerMinute"][npm_index]
            if cursor.networth != expected_nw:
                raise ValueError(
                    f"match_id={match['id']} player_index={playback.player_index} "
                    f"second={second} expected={expected_nw} actual={cursor.networth}"
                )
    
    
    class MatchDataError(ValueError):
        def __init__(self, reason: str) -> None:
            self.reason = reason
            super().__init__(reason)
    
    
    @dataclass(frozen=True)
    class SideNetworths:
        radiant: tuple[int, ...]
        dire: tuple[int, ...]
    
    
    def _split_by_side(match: StratzMatchBase, networths: list[int]) -> SideNetworths:
        radiant: list[int] = []
        dire: list[int] = []
        for player, networth in zip(match["players"], networths, strict=True):
            if player["isRadiant"]:
                radiant.append(networth)
            else:
                dire.append(networth)
        return SideNetworths(radiant=tuple(radiant), dire=tuple(dire))
    
    
    def _playback_networths(match: StratzMatchBase, second: int) -> SideNetworths | None:
        networths: list[int] = []
        for player in match["players"]:
            playback_data = player.get("playbackData")
            if playback_data is None:
                return None
            playback = cast(StratzPlayerPlayback, playback_data)
            networth = 0
            for event in sorted(playback["playerUpdateGoldEvents"], key=lambda item: item["time"]):
                if event["time"] > second:
                    break
                networth = event["networth"]
            networths.append(networth)
        return _split_by_side(match, networths)
    
    
    def _minute_networths(match: StratzMatchBase, second: int) -> SideNetworths | None:
        if second < 0:
            return _playback_networths(match, second)
        npm_index = second // 60
        networths: list[int] = []
        for player in match["players"]:
            per_minute = player["stats"]["networthPerMinute"]
            if npm_index >= len(per_minute):
                raise MatchDataError("short networthPerMinute")
            networths.append(per_minute[npm_index])
        return _split_by_side(match, networths)
    
    
    def build_second_state(
        match: StratzMatchBase,
        second: int,
        *,
        networths: SideNetworths,
        radiant_xp_adv: int,
        radiant_deaths: list[int],
        dire_deaths: list[int],
    ) -> ExactSecondState:
        radiant_nw = sum(networths.radiant)
        dire_nw = sum(networths.dire)
        return ExactSecondState(
            match_id=match["id"],
            second=second,
            radiant_win=match["didRadiantWin"],
            radiant_nw_adv=radiant_nw - dire_nw,
            radiant_nw=radiant_nw,
            dire_nw=dire_nw,
            radiant_xp_adv=radiant_xp_adv,
            deaths_radiant=bisect_right(radiant_deaths, second),
            deaths_dire=bisect_right(dire_deaths, second),
            top=build_top_player_features(networths.radiant, networths.dire),
        )
    
    
    def build_minute_states(
        match: UsableStratzMatch, end_second_exclusive: int
    ) -> tuple[ExactSecondState, ...]:
        nw_leads = match["radiantNetworthLeads"]
    
        radiant_deaths = death_times(match, radiant=True)
        dire_deaths = death_times(match, radiant=False)
        states: list[ExactSecondState] = []
        for second in range(MODEL_START_SECOND, end_second_exclusive, 60):
            if lead_array_index(second) >= len(nw_leads):
                break
            networths = _minute_networths(match, second)
            if networths is None:
                continue
            state = build_second_state(
                match,
                second,
                networths=networths,
                radiant_xp_adv=radiant_xp_advantage_at_second(match["players"], second),
                radiant_deaths=radiant_deaths,
                dire_deaths=dire_deaths,
            )
            validate_minute_leads(match["id"], second, state.radiant_nw_adv, nw_leads)
            states.append(state)
        return tuple(states)
    
    
    def collect_player_playbacks(match: StratzMatchBase) -> list[_PlayerPlayback]:
        player_playbacks: list[_PlayerPlayback] = []
        for player_index, player in enumerate(match["players"]):
            playback_data = player["playbackData"]
            if playback_data is None:
                raise ValueError(
                    f"match_id={match['id']} player_index={player_index} player playbackData is missing"
                )
            playback = cast(StratzPlayerPlayback, playback_data)
            gold_events = tuple(
                sorted(playback["playerUpdateGoldEvents"], key=lambda event: event["time"])
            )
            player_playbacks.append(
                _PlayerPlayback(
                    radiant=player["isRadiant"],
                    gold_events=gold_events,
                    level_seconds=tuple(player["stats"]["level"]),
                    player_index=player_index,
                )
            )
        return player_playbacks
    
    
    def build_exact_second_states(match: UsableStratzMatch) -> tuple[ExactSecondState, ...]:
        nw_leads = match["radiantNetworthLeads"]
        player_playbacks = collect_player_playbacks(match)
    
        radiant_deaths = death_times(match, True)
        dire_deaths = death_times(match, False)
        cursors = [_PlayerCursor() for _ in player_playbacks]
        rows: list[ExactSecondState] = []
    
        for second in range(MODEL_START_SECOND, match["durationSeconds"] + 1):
            radiant_levels: list[int] = []
            dire_levels: list[int] = []
            radiant_networths: list[int] = []
            dire_networths: list[int] = []
            for playback, cursor in zip(player_playbacks, cursors, strict=True):
                gold_events = playback.gold_events
                while (
                    cursor.gold_index < len(gold_events)
                    and gold_events[cursor.gold_index]["time"] <= second
                ):
                    cursor.networth = gold_events[cursor.gold_index]["networth"]
                    cursor.gold_index += 1
    
                if playback.radiant:
                    radiant_levels.append(level_at_second(playback.level_seconds, second))
                    radiant_networths.append(cursor.networth)
                else:
                    dire_levels.append(level_at_second(playback.level_seconds, second))
                    dire_networths.append(cursor.networth)
            state = build_second_state(
                match,
                second,
                networths=SideNetworths(
                    radiant=tuple(radiant_networths),
                    dire=tuple(dire_networths),
                ),
                radiant_xp_adv=radiant_xp_advantage(radiant_levels, dire_levels),
                radiant_deaths=radiant_deaths,
                dire_deaths=dire_deaths,
            )
            if second % 60 == 0:
                assert_minute_consistency(
                    match=match,
                    second=second,
                    player_playbacks=player_playbacks,
                    cursors=cursors,
                    radiant_nw_adv=state.radiant_nw_adv,
                    nw_leads=nw_leads,
                )
            rows.append(state)
    
        return tuple(rows)
    from pathlib import Path
    from typing import Literal, TypeGuard, cast
    
    from shared.constants.paths import RAW_STRATZ_MATCHES_DIR
    from shared.types.stratz import (
        StratzMatch,
        StratzMatchBase,
        StratzMatchCache,
        StratzPlayer,
        UsableStratzMatch,
    )
    from shared.utils.dota_levels import is_level_timeline_monotonic
    from shared.utils.json_io import read_gzip_json
    
    # Longest constant prefix a real match shows is 3; broken parses start at 60.
    FLAT_PREFIX_LIMIT = 5
    RESET_SAMPLE_LIMIT = 2000
    
    UnusableReason = Literal["missing_leads", "flat_edge", "reset_tail", "level_timeline"]
    
    
    def player_death_times(player: StratzPlayer) -> list[int]:
        return sorted(event["time"] for event in player["stats"]["deathEvents"])
    
    
    def death_times(match: StratzMatchBase, radiant: bool) -> list[int]:
        times: list[int] = []
        for player in match["players"]:
            if player["isRadiant"] != radiant:
                continue
            times.extend(player_death_times(player))
        return sorted(times)
    
    
    def stratz_match_cache_path(match_id: int) -> Path:
        return RAW_STRATZ_MATCHES_DIR / f"match_{int(match_id)}.json.gz"
    
    
    def try_load_stratz_winners(match_ids: tuple[int, ...]) -> dict[int, bool]:
        """`didRadiantWin` per cached match; ids without a cache file are absent."""
        winners: dict[int, bool] = {}
        for match_id in match_ids:
            try:
                payload = cast(StratzMatchCache, read_gzip_json(stratz_match_cache_path(match_id)))
            except FileNotFoundError:
                continue
            match = cast("StratzMatch | None", payload["data"].get("match"))
            if match is None:
                continue
            winners[match_id] = bool(match["didRadiantWin"])
        return winners
    
    
    def get_stratz_match_reach_data(match_id: int) -> UsableStratzMatch:
        """Read one cached match. Callers pass catalog ids, so the collect gate already ran."""
        payload = cast(StratzMatchCache, read_gzip_json(stratz_match_cache_path(match_id)))
        match = payload["data"]["match"]
        assert is_usable_stratz_match(match)
        return match
    
    
    def stratz_networth_xp_leads(match: StratzMatch) -> tuple[list[float], list[float]]:
        raw_nw = match.get("radiantNetworthLeads")
        raw_xp = match.get("radiantExperienceLeads")
        nw = [float(value) for value in raw_nw] if raw_nw else []
        xp = [float(value) for value in raw_xp] if raw_xp else []
        return nw, xp
    
    
    def constant_prefix_length(values: list[float]) -> int:
        length = 0
        while length < len(values) and values[length] == values[0]:
            length += 1
        return length
    
    
    def has_reset_tail(nw: list[float], xp: list[float]) -> bool:
        end = len(xp)
        if end >= 2 and xp[end - 1] == 0:
            networth_is_live = abs(nw[end - 1]) > RESET_SAMPLE_LIMIT
            experience_was_live = abs(xp[end - 2]) > RESET_SAMPLE_LIMIT
            return networth_is_live or experience_was_live
        return False
    
    
    def stratz_match_unusable_reason(match: StratzMatch | None) -> UnusableReason | None:
        if match is None:
            return "missing_leads"
        nw, xp = stratz_networth_xp_leads(match)
        if len(nw) < 2 or len(xp) < 2:
            return "missing_leads"
        edges = [nw, nw[::-1], xp, xp[::-1]]
        longest_flat_edge = max(constant_prefix_length(values) for values in edges)
        if longest_flat_edge >= FLAT_PREFIX_LIMIT:
            return "flat_edge"
        if has_reset_tail(nw, xp):
            return "reset_tail"
        if not all(is_level_timeline_monotonic(player) for player in match.get("players") or []):
            return "level_timeline"
        return None
    
    
    def is_usable_stratz_match(match: StratzMatch) -> TypeGuard[UsableStratzMatch]:
        return stratz_match_unusable_reason(match) is None
    """Shared dataset chronology constants."""
    
    VALIDATION_START_TIME = 1_780_563_592
    # Collector/Telonex day holes (e.g. 2026-08-08) skip this many empty-book
    # misses; more than this still aborts so a broken capture cannot silently
    # empty the split. Train is larger, so its cap sits on the known hole
    # (161 maps with no in-window book as of the spread-6 rebuild).
    MAX_VALIDATION_HARD_MISSES = 20
    MAX_TRAIN_HARD_MISSES = 161
    # A longer run of signal-window seconds without a fresh two-sided book marks a
    # validation match as ineligible for bulk backtesting. ML validation keeps it.
    MAX_BOOK_GAP_SECONDS = 120
    MODEL_START_SECOND = -60
    # Train rows and train_model eval stay in this window; prepare writes validation further.
    TRAIN_END_SECOND_EXCLUSIVE = 600
    MODEL_TARGET_HORIZON_SECONDS = 300
    # Book-gap eligibility still ends here. Prepare writes validation rows through
    # map duration so backtest FV can roll until game end.
    VALIDATION_SIGNAL_END_SECOND_EXCLUSIVE = TRAIN_END_SECOND_EXCLUSIVE + MODEL_TARGET_HORIZON_SECONDS
    # STRATZ snapshot age baked into training rows: state at S, market at S+lag.
    TRAIN_LAG_SECONDS = 10
    # STRATZ snapshot age at execution: validation rows and backtest predict.
    BACKTEST_LAG_SECONDS = 10
    PREHORN_LEAD_SECONDS = -(MODEL_START_SECOND + BACKTEST_LAG_SECONDS)
    # Hybrid prior experiment (hybrid_prior_v1) — rejected on effect holdout
    
    Date: 2026-07-12
    
    ## Decision
    
    **reject** — do not promote. Production `current.json` unchanged
    (`prod_20260712T114927Z_7348bfd1`).
    
    Effect holdout (969 PM matches from 2026-05-01): classic Elo × weight 1.0
    candidate vs internal STRATZ PM-only baseline:
    
    - paired actionable ΔLL (candidate − baseline) = **+0.00185**
    - bootstrap CI 1000 = **[−0.00179, +0.00551]**
    - gate `delta_ll_le_zero` failed (non-inferiority not met)
    
    Fold A/B had selected `classic_w1.0` (mean fold LL 0.5273 vs 0.5310), but
    the sealed effect holdout did not confirm the gain.
    
    ## Artifacts (not in git)
    
    `data/processed/experiments/hybrid_prior_v1/`
    
    ## Code removed (2026-07-12, commit `99b29e9`)
    
    Only the 3-fold split standard was kept (moved inline into `split_manifest.py`).
    Everything else the experiment added was removed from the pipeline.
    
    **Files deleted entirely:**
    
    - `scripts/model_pipeline/hybrid_common.py`
    - `scripts/model_pipeline/exp_hybrid_build_dataset.py`
    - `scripts/model_pipeline/exp_hybrid_build_elo.py`
    - `scripts/model_pipeline/exp_hybrid_build_splits.py`
    - `scripts/model_pipeline/exp_hybrid_effect_holdout.py`
    - `scripts/model_pipeline/exp_hybrid_input_manifest.py`
    - `scripts/model_pipeline/exp_hybrid_league_tiers.py`
    - `scripts/model_pipeline/exp_hybrid_run_ab.py`
    - `tests/test_hybrid_contracts.py`
    - `tests/test_hybrid_foundations.py`
    - `config/league_tiers_v1.csv`
    
    **Hybrid hunks reverted in production files (kept market-only):**
    
    - `scripts/model_pipeline/trainer.py` — removed `attach_hybrid_prior`;
      `train_core` meta back to `artifact_format=MODEL_ARTIFACT_FORMAT`,
      `prior="market_only"`.
    - `scripts/model_pipeline/shared.py` — removed `HYBRID_MODEL_ARTIFACT_FORMAT`,
      `HYBRID_EXPERIMENT_DIR`, `HOLDOUT_ACCESS_REGISTRY_PATH`, the `prior_is_market`
      comment; `load_model_bundle` back to the market-only gate/messages.
    - `polymarket/live_winprob_dashboard.py` — `add_prior_features` back to
      market-only (no `prior_is_market`).
    - `scripts/model_pipeline/split_manifest.py` — `RESEARCH_FOLDS` (3 monthly)
      defined inline; removed the `hybrid_common` import, `--fold-spec`/v5 legacy,
      and the `fold_specs` plumbing.
    - `tests/test_holdout_v2.py`, `tests/test_model_contract.py` — dropped hybrid
      assertions.
    
    **Where the implementation still lives:** the full hybrid runtime is in commit
    `2b50401` ("Implement hybrid non-market research pipeline"). Its
    input/data artifacts remain (not in git) under
    `data/processed/experiments/hybrid_prior_v1/`.
    Inspect a removed file without changing the tree with
    `git show 2b50401:scripts/model_pipeline/hybrid_common.py`. The separate corpus
    fetcher is in commit `8c4447e`; see
    [dataset-expansion-shelved.md](dataset-expansion-shelved.md).
    # Non-market dataset expansion (corpus 13.9k → 44k) — shelved
    
    Date: 2026-07-12
    
    ## Decision
    
    **shelve** — deprioritize non-market dataset expansion; focus on feature
    improvements first. The 13k hybrid corpus already failed to beat the market on
    the sealed effect holdout (see [hybrid-prior-v1-rejected.md](hybrid-prior-v1-rejected.md)),
    so scaling the corpus further was not worth pursuing before the features improve.
    Production stays PM-only (~3,900 matches, `current.json` unchanged).
    
    ## Hypothesis
    
    Does expanding the OpenDota non-market pro corpus beyond ~13.9k (toward ~26k+)
    improve the hybrid model enough to beat the market?
    
    ## What was done
    
    - Restored the retired corpus fetcher `00b_fetch_pro_matches.py` with a new
      **`match_id`-cursor pagination**. OpenDota Explorer now returns HTTP 400
      `{"err":"Query read timeout"}` on any `start_time`-filtered scan ordered by
      `match_id` — confirmed at both the old 2025-12-24 default and the 2025-01-01
      target, and NOT caused by patch/JOIN (bare-column start_time scans time out
      too). The rewrite binary-searches the start-ts→match_id boundary and pages by
      `match_id > cursor` (rides the PK index, <1s/page). Patch is not fetched from
      OpenDota (to be derived downstream from dates/STRATZ).
    - Extended the corpus back to 2025-01-01: `pro_matches.parquet`
      **13,947 → 44,325** matches (+30,378), 227 leagues, window
      2025-01-01 → 2026-07-12. All prior match_ids retained (superset).
    - Started the STRATZ pro-corpus NW/XP fill
      (`05b --universe pro-corpus`).
    
    ## Stopping point
    
    - STRATZ fetch stopped at **3,330 / 29,078 (~11.4%)**. Old matches fetched
      usable per-minute NW/XP leads; per-second `playbackData` absent for
      >~50-day-old matches, as expected.
    - ~3,331 new STRATZ cache files remain in `data/raw/stratz_matches/`. Harmless:
      the pro index and pro-corpus universe are back at 13,947, so these orphan
      caches are ignored; they are reusable if expansion resumes.
    - `stratz_pro_match_index.parquet` untouched (13,947 = 13,160 usable / 787
      unusable) — the index only rebuilds on fetch completion, and the run was
      stopped early.
    - STRATZ has a ~15k/day cap on top of the hourly limit, so the full ~29k fill
      spans >1 day on one token.
    
    ## Final state
    
    - `00b_fetch_pro_matches.py` removed from the pipeline; implementation is in
      commit `8c4447e` and can be inspected with
      `git show 8c4447e:scripts/model_pipeline/00b_fetch_pro_matches.py`.
    - Live `pro_matches.parquet` restored to 13,947 from backup.
    - Expanded 44,325 corpus + 222 raw cursor pages archived (not in git) at
      `data/archive/pro_matches_expansion_20260712/`.
    
    ## Splits standard going forward
    
    - **Keep** the 3 equal monthly walk-forward folds, now defined inline in
      `split_manifest.py` as `RESEARCH_FOLDS` (test windows Feb/Mar/Apr 2026). Three
      equal folds replaced the older four uneven ones — retained as a good idea.
      `08_run_research.py` derives fold ids from `RESEARCH_FOLDS`, so the count is no
      longer hard-coded to four. (The `--fold-spec v5` legacy switch was removed.)
    - **Holdout stays v4 (~252)**: rolling ~21–35 day window, min 150 PM matches
      (`07_build_holdout.py`, unchanged). The hybrid plan's aspirational "~1000
      holdout" v6 default was never coded into production and is dropped.
    
    ## Experiment code removed
    
    All hybrid experiment runtime code was removed from the pipeline (commit
    `99b29e9`), keeping only the 3-fold split standard. A production re-run
    (06→08→09) now trains strictly on PM-linked matches (~3,900) with the market
    prior — no Elo, no non-market rows. The full deleted-file inventory is in
    [hybrid-prior-v1-rejected.md](hybrid-prior-v1-rejected.md)
    ("Code removed"). The corpus fetcher `00b` implementation is in commit
    `8c4447e`.
    
    ## Artifacts (not in git)
    
    - `data/archive/pro_matches_expansion_20260712/` — 44,325 corpus + cursor pages.
    - `data/processed/experiments/hybrid_prior_v1/` — prior hybrid experiment.
    # P(win) instead of 300s midpoint delta
    
    Date: 2026-09-02. Status: **lost, code dropped**. `--target radiant-win`
    never landed on main. Boosters sit in
    `data/new_model/experiments/outcome-p-win` and
    `data/lol/models/experiments/outcome-p-win`. Default train is 300s
    `regression_l1`.
    
    Hypothesis: the same maker overlay (join-bid BUY until 540, SELL on rolling
    fair, unwind off) would make more money if the booster predicted P(Radiant
    wins the map) instead of the 300s mid move. Fair = clip(P). The 1¢ gate and
    size ramp still see `fair − market`.
    
    The experiment fit LightGBM `binary` / `binary_logloss` on `radiant_win` for
    seconds ≤ 540 (Dota still includes −60). Same 12 features. `model.json`
    recorded `"target": "radiant_win"`. Inference mapped P → edge so strategy
    code stayed unchanged.
    
    300s MAE in these `model.json` files is the wrong object (P(win) vs a 5-minute
    mid). The maker is the decision.
    
    ## Dota maker (s2-join, `|edge| ≥ 1¢`)
    
    Model `20260902T205854Z` (37 trees, 2055 train matches).
    `data/new_model/experiments/outcome-p-win`
    
    Three-seed catalog:
    `data/backtests/dota_maker/validation_join_delta01_cut540_nw350_p35_outcome-p-win`
    
    LIVE 300s baseline:
    `data/backtests/dota_maker/LIVE` → `…_grid-v1-baseline`
    
    Same knobs: cut 540, nw 350, delta 1¢, minpx 0.35, unwind off, size $100,
    4 shards. 447 shared matches.
    
    Engine PnL before rebate vs LIVE:
    
    | | LIVE | outcome P(win) | paired delta |
    |---|---|---|---|
    | seed0 | +$172.01 | −$159.85 | −$331.85 |
    | seed1 | +$372.96 | −$442.07 | −$815.03 |
    | seed2 | +$399.70 | +$78.72 | −$320.98 |
    | pooled t / wilcoxon | | | t=−1.37 p=0.172; W p=0.264 |
    
    All three seeds lose. Mean seed-total effect −$489.
    
    Buy 300s on the outcome run is ~0.06¢ mean across seeds (CI crosses 0). LIVE
    300s models sit near 2¢. Hold p50 138s / p90 641s on seed2; leftover losers
    still print (worst −$103).
    
    ## LoL maker (s2-join, `|edge| ≥ 1¢`)
    
    Model `20260902T205848Z` (38 trees, 2895 train matches).
    `data/lol/models/experiments/outcome-p-win`
    
    Three-seed catalog:
    `data/backtests/lol_maker/validation_join_delta01_cut540_nw350_p35_outcome-p-win`
    
    LIVE 300s baseline:
    `data/backtests/lol_maker/LIVE` → `…_grid-v1-baseline`
    
    1510 shared maps. 4 shards.
    
    Engine PnL before rebate vs LIVE:
    
    | | LIVE | outcome P(win) | paired delta |
    |---|---|---|---|
    | seed0 | +$1,152.54 | −$2,637.89 | −$3,790.44 |
    | seed1 | +$609.98 | −$1,811.48 | −$2,421.46 |
    | seed2 | +$985.07 | −$1,905.77 | −$2,890.83 |
    | pooled t / wilcoxon | | | t=−4.81 p=0.000; W p=0.000 |
    
    All three seeds lose, and the loss is large. All three chronological thirds
    lose. Mean net_pnl with rebate −$1,333. Buy 300s ~0.4¢ and sell 300s −1.3¢:
    the resolution-priced fair does not match a 300s exit overlay.
    
    ## Decision
    
    Drop. Do not re-add `--target radiant-win` or map P(win) into the
    `predicted_delta` slot. Keep quoting and training on the 300s midpoint.
    P(win) as fair with the current SELL-on-rolling-FV overlay is a different
    product that lost on both games.
    ✓ • 1.10s

## Activity

    $ sed -n '175,270p' src/backtest/signals.py && sed -n '1,210p' src/prepare_dataset/prepare_dataset.py && rg -n 'split|VALIDATION|cutoff' src/prepare_dataset && rg --files data/raw/stratz_matches | head -8 && cat docs/experiments/dota-minute-specialists/README.md
    
    
    @dataclass(frozen=True)
    class SignalSample:
        """One as-of signal row the strategy may use at the current clock."""
    
        predicted_delta: float
        dataset_market_p: float
    
    
    def load_usable_signal_rows(path: Path | None = None) -> pd.DataFrame:
        """Read validation rows that can feed model prediction through map duration."""
        frame = pd.read_parquet(VALIDATION_DATASET_PATH if path is None else path)
        return select_usable_validation_rows(frame)
    
    
    def load_model_feature_columns(model_dir: Path) -> list[str]:
        """Read the training feature list from the catalog's model.json."""
        meta_path = model_dir / "model.json"
        features = read_model_meta(meta_path)["features"]
        if not features:
            raise ValueError(f"model.json features empty: {meta_path}")
        return list(features)
    
    
    def assert_expected_feature_names(feature_names: list[str], expected: Sequence[str]) -> None:
        """Reject a model whose feature names do not match training order."""
        expected_list = list(expected)
        if feature_names != expected_list:
            raise ValueError(f"model feature names {feature_names!r} != expected {expected_list!r}")
    
    
    def predict_model_deltas(
        model_dir: Path,
        feature_columns: Sequence[str],
        features: pd.DataFrame,
    ) -> list[float]:
        """Predict every row with the catalog predictor (mean of members)."""
        predictor = load_predictor(model_dir)
        assert_expected_feature_names(list(predictor.feature_names), feature_columns)
        predicted = predictor.predict(features)
        return [float(value) for value in predicted]
    
    
    def mean_interval_seconds(feature_second: int, bands: tuple[CadenceBand, ...]) -> int:
        """Mean GRID interval for a feature second from a rising band ladder."""
        chosen = bands[0]
        for band in bands:
            if feature_second < band.start_second:
                break
            chosen = band
        return chosen.mean_interval_seconds
    
    
    def grid_v1_unit_interval(*, seed: int, game: BacktestGame, match_id: int, second: int) -> float:
        """Stable u in [0, 1) for one grid-v1 (seed, game, match, second)."""
        payload = f"{GRID_V1_PROFILE}:{seed}:{game}:{match_id}:{second}".encode()
        digest = hashlib.blake2b(payload, digest_size=8).digest()
        return int.from_bytes(digest, "big") / 2**64
    
    
    def grid_v1_scoreboard_delay_seconds(
        *, seed: int, game: BacktestGame, match_id: int, second: int
    ) -> float:
        """Draw one scoreboard lag for a kill from the per-game quantile table.
    
        ponytail: delays draw independently per kill; real GRID board stalls run in
        series inside a map, so consecutive-kill stalls are underrepresented.
        """
        payload = f"{GRID_V1_PROFILE}:{seed}:{game}:{match_id}:{second}:scoreboard".encode()
        digest = hashlib.blake2b(payload, digest_size=8).digest()
        unit = int.from_bytes(digest, "big") / 2**64
        quantiles, values = SCOREBOARD_LAG_QUANTILES[game]
        return float(np.interp(unit, quantiles, values))
    
    
    def cadence_bands_for_game(game: BacktestGame) -> tuple[CadenceBand, ...]:
        """Versioned grid-v1 bands for Dota or LoL."""
        if game == "dota":
            return DOTA_GRID_V1_BANDS
        return LOL_GRID_V1_BANDS
    
    
    def select_cadence_rows(
        rows: pd.DataFrame,
        *,
        game: BacktestGame,
        timing: SignalTiming,
        lag_seconds: int,
    ) -> pd.DataFrame:
        """Sample independent grid-v1 ticks per second, or every second when interval is 1.
    
        A row where either side's deaths grew versus the previous map row is always
        kept: live GRID pushes the kill-bearing table update on the change, so
        cadence sampling must not hide it behind the mean interval.
        """
    import argparse
    import sys
    from collections.abc import Sequence
    from dataclasses import dataclass
    from pathlib import Path
    from typing import Literal, cast
    
    import pandas as pd
    
    from market_data.build_market_data import market_seconds_cache_path
    from prepare_dataset.stratz_seconds import (
        ExactSecondState,
        MatchDataError,
        build_exact_second_states,
        build_minute_states,
    )
    from shared.constants.dataset import (
        MAX_BOOK_GAP_SECONDS,
        MAX_TRAIN_HARD_MISSES,
        MAX_VALIDATION_HARD_MISSES,
        MODEL_START_SECOND,
        MODEL_TARGET_HORIZON_SECONDS,
        TRAIN_END_SECOND_EXCLUSIVE,
        TRAIN_LAG_SECONDS,
        VALIDATION_SIGNAL_END_SECOND_EXCLUSIVE,
        VALIDATION_START_TIME,
    )
    from shared.constants.paths import (
        GAME_FEATURES_DATASET_PATH,
        MATCH_CATALOG_PATH,
        PRODUCTION_DATASET_SPLIT_PATH,
        PRODUCTION_TRAINING_DATASET_PATH,
        RESEARCH_MODEL_SPLIT_PATH,
        TRAINING_DATASET_PATH,
        VALIDATION_DATASET_PATH,
    )
    from shared.types.dataset import (
        DatasetRow,
        GameFeatureRow,
        MarketSecondRow,
        SnapshotFeatures,
        SplitRow,
        ValidationDatasetRow,
    )
    from shared.types.stratz import UsableStratzMatch
    from shared.utils.log import get_logger, setup_logging
    from shared.utils.match_catalog import CatalogEntry, MatchCatalog, load_match_catalog
    from shared.utils.parquet_io import replace_nulls_with_none, write_parquet
    from shared.utils.stratz import get_stratz_match_reach_data
    from shared.utils.telonex_book import is_ok_market_second
    
    PROGRESS_EVERY = 500
    
    assert MODEL_TARGET_HORIZON_SECONDS == 300
    
    logger = get_logger(__name__)
    
    CacheSplit = Literal["train", "validation"]
    
    
    @dataclass(frozen=True)
    class MatchIdSplit:
        train: tuple[int, ...]
        validation: tuple[int, ...]
    
    
    @dataclass(frozen=True)
    class MatchInputs:
        match: UsableStratzMatch
        market_rows: list[MarketSecondRow]
        entry: CatalogEntry
    
    
    def select_matches(catalog: MatchCatalog, cutoff: int) -> MatchIdSplit:
        entries = list(catalog.values())
        train_ids = exclude_missing_market_caches(
            tuple(entry.match_id for entry in entries if entry.start_time < cutoff),
            "train",
        )
    
        validation_entries = [entry for entry in entries if entry.start_time >= cutoff]
        missing_playback = [
            entry.match_id for entry in validation_entries if not entry.playback_available
        ]
        if missing_playback:
            ids = ", ".join(str(match_id) for match_id in missing_playback)
            raise SystemExit(f"validation candidates missing playback: {ids}")
        logger.info("validation candidates: %s", len(validation_entries))
        validation_ids = exclude_missing_market_caches(
            tuple(entry.match_id for entry in validation_entries),
            "validation",
        )
        return MatchIdSplit(train=train_ids, validation=validation_ids)
    
    
    def exclude_missing_market_caches(match_ids: Sequence[int], split: CacheSplit) -> tuple[int, ...]:
        missing = [
            match_id for match_id in match_ids if not market_seconds_cache_path(match_id).exists()
        ]
        cap = MAX_TRAIN_HARD_MISSES if split == "train" else MAX_VALIDATION_HARD_MISSES
        report = f"prepare missing {split} market caches ({len(missing)}): {missing}"
        if len(missing) > cap:
            logger.error(report)
            raise SystemExit(report)
        if missing:
            logger.warning(report)
        skipped = set(missing)
        return tuple(match_id for match_id in match_ids if match_id not in skipped)
    
    
    def find_longest_book_gap(market_rows: list[MarketSecondRow]) -> int:
        longest = 0
        run = 0
        for row in market_rows:
            second = int(row["second"])
            if second < MODEL_START_SECOND or second >= VALIDATION_SIGNAL_END_SECOND_EXCLUSIVE:
                continue
            run = run + 1 if row["market_status"] != "ok" else 0
            longest = max(longest, run)
        return longest
    
    
    def state_features(state: ExactSecondState) -> SnapshotFeatures:
        return SnapshotFeatures(
            radiant_nw_adv=state.radiant_nw_adv,
            radiant_nw=state.radiant_nw,
            dire_nw=state.dire_nw,
            radiant_xp_adv=state.radiant_xp_adv,
            deaths_radiant=state.deaths_radiant,
            deaths_dire=state.deaths_dire,
            top1_nw_adv=state.top.top1_nw_adv,
            radiant_top1_nw_ratio=state.top.radiant_top1_nw_ratio,
            dire_top1_nw_ratio=state.top.dire_top1_nw_ratio,
        )
    
    
    def build_minute_rows(inputs: MatchInputs, lag_seconds: int) -> list[DatasetRow]:
        match = inputs.match
        try:
            states = build_minute_states(match, TRAIN_END_SECOND_EXCLUSIVE)
        except MatchDataError as error:
            logger.warning("skip match %s: %s", match["id"], error.reason)
            return []
    
        market_by_second = {int(row["second"]): row for row in inputs.market_rows}
        rows: list[DatasetRow] = []
        for state in states:
            market_row = market_by_second.get(state.second + lag_seconds)
            if market_row is None or not is_ok_market_second(market_row):
                continue
            signal_market_p_radiant_300s = market_row["signal_market_p_radiant_300s"]
            if signal_market_p_radiant_300s is None:
                continue
            rows.append(
                DatasetRow(
                    match_id=state.match_id,
                    start_time=inputs.entry.start_time,
                    second=state.second,
                    radiant_win=state.radiant_win,
                    **state_features(state),
                    market_radiant_prior=inputs.entry.radiant_prior,
                    market_p_radiant=market_row["market_p_radiant"],
                    signal_market_p_radiant_300s=signal_market_p_radiant_300s,
                )
            )
        return rows
    
    
    def build_game_feature_rows(
        match_id: int,
        states: Sequence[ExactSecondState],
        market_radiant_prior: float,
    ) -> list[GameFeatureRow]:
        """Exact-second model inputs keyed by game second for archive-schedule replay."""
        return [
            GameFeatureRow(
                match_id=match_id,
                game_second=state.second,
                **state_features(state),
                market_radiant_prior=market_radiant_prior,
            )
            for state in states
        ]
    
    
    def load_market_second_rows(path: Path) -> list[MarketSecondRow]:
        frame = pd.read_parquet(path)
        records = replace_nulls_with_none(frame).to_dict(orient="records")
        return [cast(MarketSecondRow, record) for record in records]
    
    
    def join_validation_rows(
        states: tuple[ExactSecondState, ...],
        inputs: MatchInputs,
        lag_seconds: int,
    ) -> list[ValidationDatasetRow]:
        states_by_second = {state.second: state for state in states}
        rows: list[ValidationDatasetRow] = []
        for market_row in inputs.market_rows:
            market_match_id = int(market_row["match_id"])
            market_second = int(market_row["second"])
            lagged_second = market_second - lag_seconds
            if lagged_second < MODEL_START_SECOND:
                continue
            state = states_by_second.get(lagged_second)
            if state is None:
                raise ValueError(
                    "validation join missing lagged state: "
                    f"market=({market_match_id}, {market_second}) "
                    f"lagged_second={lagged_second}"
    src/prepare_dataset/prepare_dataset.py:20:    MAX_VALIDATION_HARD_MISSES,
    src/prepare_dataset/prepare_dataset.py:25:    VALIDATION_SIGNAL_END_SECOND_EXCLUSIVE,
    src/prepare_dataset/prepare_dataset.py:26:    VALIDATION_START_TIME,
    src/prepare_dataset/prepare_dataset.py:35:    VALIDATION_DATASET_PATH,
    src/prepare_dataset/prepare_dataset.py:74:def select_matches(catalog: MatchCatalog, cutoff: int) -> MatchIdSplit:
    src/prepare_dataset/prepare_dataset.py:77:        tuple(entry.match_id for entry in entries if entry.start_time < cutoff),
    src/prepare_dataset/prepare_dataset.py:81:    validation_entries = [entry for entry in entries if entry.start_time >= cutoff]
    src/prepare_dataset/prepare_dataset.py:96:def exclude_missing_market_caches(match_ids: Sequence[int], split: CacheSplit) -> tuple[int, ...]:
    src/prepare_dataset/prepare_dataset.py:100:    cap = MAX_TRAIN_HARD_MISSES if split == "train" else MAX_VALIDATION_HARD_MISSES
    src/prepare_dataset/prepare_dataset.py:101:    report = f"prepare missing {split} market caches ({len(missing)}): {missing}"
    src/prepare_dataset/prepare_dataset.py:116:        if second < MODEL_START_SECOND or second >= VALIDATION_SIGNAL_END_SECOND_EXCLUSIVE:
    src/prepare_dataset/prepare_dataset.py:237:def split_rows(
    src/prepare_dataset/prepare_dataset.py:239:    split: Literal["train", "validation"],
    src/prepare_dataset/prepare_dataset.py:247:            split=split,
    src/prepare_dataset/prepare_dataset.py:275:    research_split_rows = split_rows(training_rows, "train", frozenset()) + split_rows(
    src/prepare_dataset/prepare_dataset.py:278:    production_split_rows = split_rows(production_rows, "train", frozenset())
    src/prepare_dataset/prepare_dataset.py:281:    write_rows(validation_rows, VALIDATION_DATASET_PATH)
    src/prepare_dataset/prepare_dataset.py:283:    write_rows(research_split_rows, RESEARCH_MODEL_SPLIT_PATH)
    src/prepare_dataset/prepare_dataset.py:285:    write_rows(production_split_rows, PRODUCTION_DATASET_SPLIT_PATH)
    src/prepare_dataset/prepare_dataset.py:319:    split = select_matches(catalog, VALIDATION_START_TIME)
    src/prepare_dataset/prepare_dataset.py:324:    for i, match_id in enumerate(split.train, start=1):
    src/prepare_dataset/prepare_dataset.py:328:            logger.info("processed train %s / %s matches", i, len(split.train))
    src/prepare_dataset/prepare_dataset.py:334:    for i, match_id in enumerate(split.validation, start=1):
    src/prepare_dataset/prepare_dataset.py:346:            logger.info("processed validation %s / %s matches", i, len(split.validation))
    src/prepare_dataset/stratz_seconds.py:135:def _split_by_side(match: StratzMatchBase, networths: list[int]) -> SideNetworths:
    src/prepare_dataset/stratz_seconds.py:159:    return _split_by_side(match, networths)
    src/prepare_dataset/stratz_seconds.py:172:    return _split_by_side(match, networths)
    data/raw/stratz_matches/match_8691927746.json.gz
    data/raw/stratz_matches/match_8684648694.json.gz
    data/raw/stratz_matches/match_8782534661.json.gz
    data/raw/stratz_matches/match_8742039319.json.gz
    data/raw/stratz_matches/match_8131299566.json.gz
    data/raw/stratz_matches/match_8887432754.json.gz
    data/raw/stratz_matches/match_8805661699.json.gz
    data/raw/stratz_matches/match_8771176095.json.gz
    # Dota minute specialists
    
    Date: 2026-09-01. Status: **lost. Do not backtest or promote.** The live
    `research/` model stayed `20260825T190207Z`.
    
    ## Hypothesis
    
    One booster per minute can learn clock-specific interactions that the general
    booster misses. A bank routes the lagged source clock to the nearest minute
    booster before second 540 and uses the general booster afterward.
    
    ## Initial fit
    
    The train data is the canonical minute dataset. Each specialist trains on one
    exact minute. Validation uses the specialist's assigned 1 Hz interval. The
    general booster has 48 trees.
    
    | source second | trees | train rows | DIR 300 | MAE gain |
    |---:|---:|---:|---:|---:|
    | -60 | 1 | 46 | 0.079¢ | -0.008¢ |
    | 0 | 42 | 1600 | 0.566¢ | 0.053¢ |
    | 60 | 148 | 1636 | 0.906¢ | 0.090¢ |
    | 120 | 90 | 1663 | 1.980¢ | 0.309¢ |
    | 180 | 74 | 1650 | 1.919¢ | 0.320¢ |
    | 240 | 26 | 1682 | 1.464¢ | 0.202¢ |
    | 300 | 47 | 1694 | 1.599¢ | 0.232¢ |
    | 360 | 56 | 1706 | 1.342¢ | 0.243¢ |
    | 420 | 37 | 1743 | 1.797¢ | 0.224¢ |
    | 480 | 41 | 1684 | 1.235¢ | 0.176¢ |
    | 540 | 31 | 1714 | 0.884¢ | 0.193¢ |
    
    The stitched bank lost to the general booster on the full model window.
    
    | model | DIR 300 | MAE gain | Δ DIR | Δ MAE gain |
    |---|---:|---:|---:|---:|
    | general | 1.501¢ | 0.226¢ | - | - |
    | nearest bank | 1.282¢ | 0.194¢ | -0.219¢ | -0.033¢ |
    | floor bank | 1.140¢ | 0.171¢ | -0.361¢ | -0.055¢ |
    
    The `m-60` stump is a data failure. Its 46 rows cannot split with
    `min_data_in_leaf=200`. The early-stop count at `m60` is also unstable: the
    specialist reached 148 trees on a highly correlated 1 Hz interval.
    
    ## Parameter sweep
    
    The second pass split 200 validation event series chronologically. The older
    100 series selected parameters from 129,524 rows. The newer 100 series measured
    the result on 148,902 rows.
    
    The sweep tested these values:
    
    - `min_data_in_leaf`: 25, 50, 100, 200, and 400.
    - tree count: 24, 48, 96, and 144.
    - fixed `num_leaves=63`.
    - individual minutes, `[-60, 0, 60]`, and groups of three minute centers.
    
    The table reports the newer holdout half. Deltas compare each bank with the
    general booster on the same rows.
    
    | variant | DIR 300 | MAE gain | Δ DIR | Δ MAE gain |
    |---|---:|---:|---:|---:|
    | original early-stop bank | 1.455¢ | 0.204¢ | -0.144¢ | -0.018¢ |
    | 48 trees per minute | 1.466¢ | 0.199¢ | -0.134¢ | -0.023¢ |
    | 48 trees, pooled early model | 1.486¢ | 0.202¢ | -0.113¢ | -0.019¢ |
    | 48 trees, three-minute groups | 1.512¢ | 0.200¢ | -0.087¢ | -0.021¢ |
    | tuned selective minutes | 1.602¢ | 0.210¢ | +0.002¢ | -0.012¢ |
    | tuned selective groups | 1.582¢ | 0.208¢ | -0.017¢ | -0.014¢ |
    
    Only `m420` improved both metrics on both chronological halves. Its selected
    configuration used `min_data_in_leaf=400` and 96 trees. On its decision window,
    seconds 400 through 459, it gained 0.110¢ DIR and 0.014¢ MAE on the full
    validation set. The gain shrank to 0.010¢ DIR and 0.001¢ MAE after weighting the
    whole model window.
    
    The `m420` paired 95% intervals on the newer holdout were -0.083¢ to 0.106¢ for
    DIR and -0.017¢ to 0.021¢ for MAE gain. Its four chronological quarters had
    signs `-`, `+`, `+`, `-`. It changed the one-cent action on 30.6% of its local
    rows, but the result is too small and unstable to justify a backtest.
    
    ## Decision
    
    Drop the minute bank. Fixed tree counts do not repair it. Pooling the early
    minutes or every three minute centers also loses. Failed experiment remains
    self-contained under `data/experiments/dota-minute-specialists/` for local
    audit; mainline never loads `bank.json`.
    
    The next experiment keeps the early general model and trains separate models
    only for late-game ranges. That experiment is separate from this result.
    
    ## Artifacts and reproduction
    
    Tracked files in this directory:
    
    - `train.py`: original per-minute trainer and nearest or floor stitch.
    - `sweep.py`: chronological parameter sweep and stitched variants.
    - `check_m420.py`: focused stability and signal-change check.
    - `sweep.csv`: every parameter combination.
    - `selected_units.csv`: tune-selected configuration per minute or group.
    - `variants.csv`: stitched-bank totals and paired holdout intervals.
    
    Run the scripts from the repository root:
    
    ```bash
    uv run python docs/experiments/dota-minute-specialists/train.py
    uv run python docs/experiments/dota-minute-specialists/sweep.py
    uv run python docs/experiments/dota-minute-specialists/check_m420.py
    ```
    ✓ • 1.24s

## Activity

    $ UV_CACHE_DIR=/tmp/codex-uv-cache uv run --no-sync python - <<'PY'
        from pathlib import Path
        import pandas as pd
        for name in ['stratz_matches','opendota_matches','pro_matches']:
         p=Path('data/raw')/name
         fs=list(p.rglob('*.json*'))
         print(name,len(fs),[str(x) for x in fs[:2]])
        for f in ['data/new_model/research/split.parquet','data/new_processed/stratz_match_index/stratz_match_index.parquet','data/new_processed/dataset/training_dataset.parquet']:
         d=pd.read_parquet(f)
         print(f,len(d),list(d.columns))
         if 'split' in d:
          print(d.groupby('split').start_time.agg(['count','min','max']).to_string())
          print(d.groupby('split').start_time.agg(['min','max']).apply(lambda c:pd.to_datetime(c,unit='s',utc=True)).to_string())
        PY
    stratz_matches 19372 ['data/raw/stratz_matches/match_8656269477.json.gz', 'data/raw/stratz_matches/match_8782534661.json.gz']
    opendota_matches 5745 ['data/raw/opendota_matches/match_8742039319.json.gz', 'data/raw/opendota_matches/match_8691927746.json.gz']
    pro_matches 111 ['data/raw/pro_matches/page_1772928000_8789343260.json', 'data/raw/pro_matches/page_1772928000_8749463873.json']
    data/new_model/research/split.parquet 2280 ['match_id', 'start_time', 'split', 'backtest_book_gap_excluded']
                count         min         max
    split
    train        1563  1760534009  1780162466
    validation    717  1780564481  1789937428
                                     min                       max
    split
    train      2025-10-15 13:13:29+00:00 2026-05-30 17:34:26+00:00
    validation 2026-06-04 09:14:41+00:00 2026-09-20 20:50:28+00:00
    data/new_processed/stratz_match_index/stratz_match_index.parquet 3300 ['match_id', 'status', 'reason', 'playback_available', 'start_time', 'duration']
    data/new_processed/dataset/training_dataset.parquet 11070 ['match_id', 'start_time', 'second', 'radiant_win', 'radiant_nw_adv', 'radiant_nw', 'dire_nw', 'radiant_xp_adv', 'deaths_radiant', 'deaths_dire', 'top1_nw_adv', 'radiant_top1_nw_ratio', 'dire_top1_nw_ratio', 'market_radiant_prior', 'market_p_radiant', 'signal_market_p_radiant_300s']
    ✓ • 1.94s

## Activity

    $ cat docs/experiments/dataset-expansion-shelved.md src/shared/utils/dota_levels.py && sed -n '400,565p' src/shared/utils/gbm.py && rg -n 'features\[|feature_columns|predict_model_deltas|fingerprint' src/backtest/signals.py && cat src/shared/utils/model_registry.py
    # Non-market dataset expansion (corpus 13.9k → 44k) — shelved
    
    Date: 2026-07-12
    
    ## Decision
    
    **shelve** — deprioritize non-market dataset expansion; focus on feature
    improvements first. The 13k hybrid corpus already failed to beat the market on
    the sealed effect holdout (see [hybrid-prior-v1-rejected.md](hybrid-prior-v1-rejected.md)),
    so scaling the corpus further was not worth pursuing before the features improve.
    Production stays PM-only (~3,900 matches, `current.json` unchanged).
    
    ## Hypothesis
    
    Does expanding the OpenDota non-market pro corpus beyond ~13.9k (toward ~26k+)
    improve the hybrid model enough to beat the market?
    
    ## What was done
    
    - Restored the retired corpus fetcher `00b_fetch_pro_matches.py` with a new
      **`match_id`-cursor pagination**. OpenDota Explorer now returns HTTP 400
      `{"err":"Query read timeout"}` on any `start_time`-filtered scan ordered by
      `match_id` — confirmed at both the old 2025-12-24 default and the 2025-01-01
      target, and NOT caused by patch/JOIN (bare-column start_time scans time out
      too). The rewrite binary-searches the start-ts→match_id boundary and pages by
      `match_id > cursor` (rides the PK index, <1s/page). Patch is not fetched from
      OpenDota (to be derived downstream from dates/STRATZ).
    - Extended the corpus back to 2025-01-01: `pro_matches.parquet`
      **13,947 → 44,325** matches (+30,378), 227 leagues, window
      2025-01-01 → 2026-07-12. All prior match_ids retained (superset).
    - Started the STRATZ pro-corpus NW/XP fill
      (`05b --universe pro-corpus`).
    
    ## Stopping point
    
    - STRATZ fetch stopped at **3,330 / 29,078 (~11.4%)**. Old matches fetched
      usable per-minute NW/XP leads; per-second `playbackData` absent for
      >~50-day-old matches, as expected.
    - ~3,331 new STRATZ cache files remain in `data/raw/stratz_matches/`. Harmless:
      the pro index and pro-corpus universe are back at 13,947, so these orphan
      caches are ignored; they are reusable if expansion resumes.
    - `stratz_pro_match_index.parquet` untouched (13,947 = 13,160 usable / 787
      unusable) — the index only rebuilds on fetch completion, and the run was
      stopped early.
    - STRATZ has a ~15k/day cap on top of the hourly limit, so the full ~29k fill
      spans >1 day on one token.
    
    ## Final state
    
    - `00b_fetch_pro_matches.py` removed from the pipeline; implementation is in
      commit `8c4447e` and can be inspected with
      `git show 8c4447e:scripts/model_pipeline/00b_fetch_pro_matches.py`.
    - Live `pro_matches.parquet` restored to 13,947 from backup.
    - Expanded 44,325 corpus + 222 raw cursor pages archived (not in git) at
      `data/archive/pro_matches_expansion_20260712/`.
    
    ## Splits standard going forward
    
    - **Keep** the 3 equal monthly walk-forward folds, now defined inline in
      `split_manifest.py` as `RESEARCH_FOLDS` (test windows Feb/Mar/Apr 2026). Three
      equal folds replaced the older four uneven ones — retained as a good idea.
      `08_run_research.py` derives fold ids from `RESEARCH_FOLDS`, so the count is no
      longer hard-coded to four. (The `--fold-spec v5` legacy switch was removed.)
    - **Holdout stays v4 (~252)**: rolling ~21–35 day window, min 150 PM matches
      (`07_build_holdout.py`, unchanged). The hybrid plan's aspirational "~1000
      holdout" v6 default was never coded into production and is dropped.
    
    ## Experiment code removed
    
    All hybrid experiment runtime code was removed from the pipeline (commit
    `99b29e9`), keeping only the 3-fold split standard. A production re-run
    (06→08→09) now trains strictly on PM-linked matches (~3,900) with the market
    prior — no Elo, no non-market rows. The full deleted-file inventory is in
    [hybrid-prior-v1-rejected.md](hybrid-prior-v1-rejected.md)
    ("Code removed"). The corpus fetcher `00b` implementation is in commit
    `8c4447e`.
    
    ## Artifacts (not in git)
    
    - `data/archive/pro_matches_expansion_20260712/` — 44,325 corpus + cursor pages.
    - `data/processed/experiments/hybrid_prior_v1/` — prior hybrid experiment.
    """Convert Dota 2 level-up timelines to cumulative XP advantages."""
    
    from bisect import bisect_right
    from itertools import pairwise
    
    from shared.constants.dota import LEVEL_XP
    from shared.types.stratz import StratzPlayer
    from shared.utils.level_xp import cumulative_xp, xp_advantage
    
    
    def experience_at_level(level: int) -> int:
        """Return cumulative XP at a Dota 2 level. Steam draft players are level 0 (0 XP)."""
        return cumulative_xp(LEVEL_XP, level)
    
    
    def level_at_second(level_seconds: list[int] | tuple[int, ...], second: int) -> int:
        """Return the level reached by a player at one game second."""
        return bisect_right(level_seconds, second)
    
    
    def radiant_xp_advantage(radiant_levels: list[int], dire_levels: list[int]) -> int:
        """Return cumulative Radiant XP minus cumulative Dire XP."""
        return xp_advantage(LEVEL_XP, radiant_levels, dire_levels)
    
    
    def radiant_xp_advantage_at_second(players: list[StratzPlayer], second: int) -> int:
        """Return the level-based Radiant XP advantage at one game second."""
        radiant_levels: list[int] = []
        dire_levels: list[int] = []
        for player in players:
            level = level_at_second(player["stats"]["level"], second)
            if player["isRadiant"]:
                radiant_levels.append(level)
            else:
                dire_levels.append(level)
        return radiant_xp_advantage(radiant_levels, dire_levels)
    
    
    def is_level_timeline_monotonic(player: StratzPlayer) -> bool:
        """Report whether a player's level-up seconds never go backwards."""
        level_seconds = player["stats"]["level"]
        return all(left <= right for left, right in pairwise(level_seconds))
    
    def member_filename(index: int) -> str:
        """Return the catalog filename for ensemble member `index`."""
        return f"member_{index:02d}.txt"
    
    
    def catalog_member_names(meta: ModelMeta) -> tuple[str, ...]:
        """Resolve member filenames; an empty, missing, or invalid list is a load error."""
        raw = meta.get("members")
        if type(raw) is not list or not raw:
            raise ModelCatalogError("members list is empty")
        if any(type(item) is not str for item in raw):
            raise ModelCatalogError("members list is empty")
        names = tuple(raw)
        if any(not _is_plain_filename(name) for name in names):
            raise ModelCatalogError("member name is not a plain filename")
        if len(set(names)) != len(names):
            raise ModelCatalogError("members list repeats a file")
        return names
    
    
    def catalog_member_trees(meta: ModelMeta) -> tuple[int, ...]:
        """Resolve recorded tree counts; missing, null, or a non-int list is a load error."""
        raw = meta.get("member_trees")
        if type(raw) is not list or not raw:
            raise ModelCatalogError("member_trees list is empty")
        if any(type(item) is not int for item in raw):
            raise ModelCatalogError("member_trees list is empty")
        return tuple(raw)
    
    
    def _is_plain_filename(name: str) -> bool:
        """True when `name` is a single path component, not empty, `.`, or `..`."""
        return name != "" and Path(name).name == name and name not in {".", ".."}
    
    
    def require_regular_file(path: Path, reason: str) -> None:
        """Require `path` to be a regular file, or raise a catalog error."""
        try:
            is_file = path.is_file()
        except OSError as exc:
            raise ModelCatalogError("model artifacts are unreadable") from exc
        if not is_file:
            raise ModelCatalogError(reason)
    
    
    def load_booster(path: Path) -> lgb.Booster:
        """Construct one LightGBM booster from a member file."""
        try:
            return lgb.Booster(model_file=str(path))
        except (lgb.basic.LightGBMError, ValueError, RuntimeError) as exc:
            raise ModelCatalogError("ensemble member cannot be parsed by LightGBM") from exc
        except OSError as exc:
            raise ModelCatalogError("ensemble member cannot be read") from exc
    
    
    def load_predictor_from_meta(model_dir: Path, meta: ModelMeta) -> GbmPredictor:
        """Load every listed member and refuse a partial catalog."""
        features = list(meta["features"])
        if not features:
            raise ModelCatalogError("model.json features empty")
        names = catalog_member_names(meta)
        recorded_trees = catalog_member_trees(meta)
        if len(recorded_trees) != len(names):
            raise ModelCatalogError("member_trees do not match members")
        boosters: list[lgb.Booster] = []
        trees: list[int] = []
        for name in names:
            path = model_dir / name
            require_regular_file(path, "ensemble member is missing or not a regular file")
            booster = load_booster(path)
            try:
                booster_features = list(booster.feature_name())
            except (lgb.basic.LightGBMError, OSError, ValueError, RuntimeError) as exc:
                raise ModelCatalogError("ensemble member features cannot be read") from exc
            if booster_features != features:
                raise ModelCatalogError("ensemble member features do not match model.json")
            boosters.append(booster)
            trees.append(booster.num_trees())
        if recorded_trees != tuple(trees):
            raise ModelCatalogError("member_trees do not match the loaded boosters")
        return GbmPredictor(
            model_dir=model_dir,
            feature_names=tuple(features),
            member_names=names,
            member_trees=tuple(trees),
            _boosters=tuple(boosters),
        )
    
    
    def load_predictor(model_dir: Path) -> GbmPredictor:
        """Load an ensemble catalog from `model_dir` / model.json."""
        meta_path = model_dir / MODEL_META_FILENAME
        require_regular_file(meta_path, "model.json is missing or not a regular file")
        try:
            meta = read_model_meta(meta_path)
        except (OSError, ValueError) as exc:
            raise ModelCatalogError("model.json cannot be read or parsed") from exc
        return load_predictor_from_meta(model_dir, meta)
    
    
    def model_identity_sha256(model_dir: Path) -> str:
        """Identity of a catalog: ordered members, their SHAs, and predict metadata."""
        meta = read_model_meta(model_dir / MODEL_META_FILENAME)
        names = catalog_member_names(meta)
        hasher = hashlib.sha256()
        hasher.update(ENSEMBLE_IDENTITY_VERSION)
        hasher.update(b"\n")
        for name in names:
            hasher.update(name.encode("utf-8"))
            hasher.update(b"\n")
            hasher.update(sha256_file(model_dir / name).encode("ascii"))
            hasher.update(b"\n")
        hasher.update(b"features\n")
        for feature in meta["features"]:
            hasher.update(feature.encode("utf-8"))
            hasher.update(b"\n")
        hasher.update(b"source_lag_seconds\n")
        hasher.update(str(int(meta["source_lag_seconds"])).encode("ascii"))
        return hasher.hexdigest()
    
    
    def write_ensemble_members(staging_dir: Path, boosters: Sequence[lgb.Booster]) -> tuple[str, ...]:
        """Write `member_00.txt` … into staging and return those filenames."""
        names = tuple(member_filename(index) for index in range(len(boosters)))
        for name, booster in zip(names, boosters, strict=True):
            booster.save_model(staging_dir / name)
        return names
    191:def load_model_feature_columns(model_dir: Path) -> list[str]:
    207:def predict_model_deltas(
    209:    feature_columns: Sequence[str],
    214:    assert_expected_feature_names(list(predictor.feature_names), feature_columns)
    483:        feature_columns = load_model_feature_columns(model_dir)
    484:        features = model_rows[feature_columns].assign(second=model_rows["second"] - lag_seconds)
    485:        deltas = predict_model_deltas(model_dir, feature_columns, features)
    648:        feature_columns = load_model_feature_columns(model_dir)
    649:        predicted = predict_model_deltas(
    650:            model_dir, feature_columns, decision_rows.iloc[members][feature_columns]
    """Read, write, and publish named model directories."""
    
    import shutil
    from pathlib import Path
    from typing import cast
    
    from shared.types.model import ModelMeta
    from shared.utils.json_io import read_json, write_json
    
    MODEL_META_FILENAME = "model.json"
    
    
    def read_model_meta(path: Path) -> ModelMeta:
        """Read model metadata from JSON."""
        return cast(ModelMeta, read_json(path))
    
    
    def write_model_meta(meta: ModelMeta, path: Path) -> None:
        """Write model metadata as JSON."""
        write_json(path, meta)
    
    
    def _archive_target(archive_dir: Path, name: str) -> Path:
        """First free archive directory: <name>, then <name>_2, <name>_3, ..."""
        candidate = archive_dir / name
        suffix = 2
        while candidate.exists():
            candidate = archive_dir / f"{name}_{suffix}"
            suffix += 1
        return candidate
    
    
    def staging_model_dir(live_dir: Path) -> Path:
        """Create an empty staging sibling of one live model dir, dropping a leftover one."""
        staging_dir = live_dir.with_name(f".next-{live_dir.name}")
        if staging_dir.exists():
            shutil.rmtree(staging_dir)
        staging_dir.mkdir(parents=True)
        return staging_dir
    
    
    def publish_model_dir(staging_dir: Path, live_dir: Path, archive_dir: Path) -> str | None:
        """Archive live_dir under archive_dir, then move a fully written staging_dir in its place.
    
        Both moves are renames on one filesystem, so a reader of live_dir sees either the
        whole old catalog or the whole new one, never a mixed members / model.json pair.
        A live_dir without model.json is a stale leftover: it is deleted, not archived.
        Returns the archived directory name, or None on a first publish.
        """
        if not (live_dir / MODEL_META_FILENAME).is_file():
            if live_dir.exists():
                shutil.rmtree(live_dir)
            live_dir.parent.mkdir(parents=True, exist_ok=True)
            staging_dir.rename(live_dir)
            return None
        archived_name = read_model_meta(live_dir / MODEL_META_FILENAME)["name"]
        archive_dir.mkdir(parents=True, exist_ok=True)
        archived_dir = _archive_target(archive_dir, archived_name)
        live_dir.rename(archived_dir)
        staging_dir.rename(live_dir)
        return archived_dir.name
    ✓ • 950ms

## Activity

    $ UV_CACHE_DIR=/tmp/codex-uv-cache uv run --no-sync python - <<'PY'
        from pathlib import Path
        import gzip,json
        import pandas as pd
        fs=sorted(Path('data/raw/stratz_matches').glob('*.gz'))
        print('compressed_total_gb',sum(f.stat().st_size for f in fs)/1e9)
        for f in fs[::max(1,len(fs)//10)]:
         x=json.loads(gzip.decompress(f.read_bytes())); m=(x.get('data') or {}).get('match')
         if not m:print(f.name,'empty');continue
         p=(m.get('players') or [{}])[0]
         print(f.name,'profile',x.get('cache_profile'),'start',m.get('startDateTime'),'end',m.get('endDateTime'),'duration',m.get('durationSeconds'),'match_keys',list(m),'player_stats',list((p.get('stats') or {})),'playback',bool(p.get('playbackData')))
        PY
    compressed_total_gb 1.500548796
    match_8107483721.json.gz profile stratz_rich_v2 start 1735690145 end 1735692204 duration 2059 match_keys ['barracksStatusDire', 'barracksStatusRadiant', 'didRadiantWin', 'direTeam', 'direTeamId', 'durationSeconds', 'endDateTime', 'gameMode', 'id', 'leagueId', 'lobbyType', 'playbackData', 'players', 'radiantExperienceLeads', 'radiantNetworthLeads', 'radiantTeam', 'radiantTeamId', 'seriesId', 'startDateTime', 'towerStatusDire', 'towerStatusRadiant'] player_stats [] playback False
    match_8135806452.json.gz profile stratz_rich_v2 start 1737269983 end 1737271569 duration 1586 match_keys ['barracksStatusDire', 'barracksStatusRadiant', 'didRadiantWin', 'direTeam', 'direTeamId', 'durationSeconds', 'endDateTime', 'gameMode', 'id', 'leagueId', 'lobbyType', 'playbackData', 'players', 'radiantExperienceLeads', 'radiantNetworthLeads', 'radiantTeam', 'radiantTeamId', 'seriesId', 'startDateTime', 'towerStatusDire', 'towerStatusRadiant'] player_stats [] playback False
    match_8528227419.json.gz profile stratz_rich_v3 start 1761419096 end 1761421544 duration 2448 match_keys ['barracksStatusDire', 'barracksStatusRadiant', 'didRadiantWin', 'direTeam', 'direTeamId', 'durationSeconds', 'endDateTime', 'firstBloodTime', 'gameMode', 'id', 'leagueId', 'lobbyType', 'playbackData', 'players', 'radiantExperienceLeads', 'radiantNetworthLeads', 'radiantTeam', 'radiantTeamId', 'seriesId', 'startDateTime', 'towerDeaths', 'towerStatusDire', 'towerStatusRadiant'] player_stats ['deathEvents', 'itemPurchases', 'level', 'networthPerMinute'] playback False
    match_8641622702.json.gz profile stratz_rich_v2 start 1767948460 end 1767949951 duration 1491 match_keys ['barracksStatusDire', 'barracksStatusRadiant', 'didRadiantWin', 'direTeam', 'direTeamId', 'durationSeconds', 'endDateTime', 'gameMode', 'id', 'leagueId', 'lobbyType', 'playbackData', 'players', 'radiantExperienceLeads', 'radiantNetworthLeads', 'radiantTeam', 'radiantTeamId', 'seriesId', 'startDateTime', 'towerStatusDire', 'towerStatusRadiant'] player_stats [] playback False
    match_8671484775.json.gz profile stratz_rich_v2 start 1769851128 end 1769852750 duration 1622 match_keys ['barracksStatusDire', 'barracksStatusRadiant', 'didRadiantWin', 'direTeam', 'direTeamId', 'durationSeconds', 'endDateTime', 'gameMode', 'id', 'leagueId', 'lobbyType', 'playbackData', 'players', 'radiantExperienceLeads', 'radiantNetworthLeads', 'radiantTeam', 'radiantTeamId', 'seriesId', 'startDateTime', 'towerStatusDire', 'towerStatusRadiant'] player_stats [] playback False
    match_8704584247.json.gz profile stratz_rich_v3 start 1771939498 end 1771940941 duration 1443 match_keys ['barracksStatusDire', 'barracksStatusRadiant', 'didRadiantWin', 'direTeam', 'direTeamId', 'durationSeconds', 'endDateTime', 'firstBloodTime', 'gameMode', 'id', 'leagueId', 'lobbyType', 'playbackData', 'players', 'radiantExperienceLeads', 'radiantNetworthLeads', 'radiantTeam', 'radiantTeamId', 'seriesId', 'startDateTime', 'towerDeaths', 'towerStatusDire', 'towerStatusRadiant'] player_stats ['deathEvents', 'itemPurchases', 'level', 'networthPerMinute'] playback False
    match_8739348815.json.gz profile stratz_rich_v2 start 1774181636 end 1774183096 duration 1460 match_keys ['barracksStatusDire', 'barracksStatusRadiant', 'didRadiantWin', 'direTeam', 'direTeamId', 'durationSeconds', 'endDateTime', 'gameMode', 'id', 'leagueId', 'lobbyType', 'playbackData', 'players', 'radiantExperienceLeads', 'radiantNetworthLeads', 'radiantTeam', 'radiantTeamId', 'seriesId', 'startDateTime', 'towerStatusDire', 'towerStatusRadiant'] player_stats [] playback False
    match_8770941904.json.gz profile stratz_rich_v2 start 1776147996 end 1776150056 duration 2060 match_keys ['barracksStatusDire', 'barracksStatusRadiant', 'didRadiantWin', 'direTeam', 'direTeamId', 'durationSeconds', 'endDateTime', 'gameMode', 'id', 'leagueId', 'lobbyType', 'playbackData', 'players', 'radiantExperienceLeads', 'radiantNetworthLeads', 'radiantTeam', 'radiantTeamId', 'seriesId', 'startDateTime', 'towerStatusDire', 'towerStatusRadiant'] player_stats [] playback False
    match_8805272676.json.gz profile stratz_rich_v2 start 1778407434 end 1778408895 duration 1461 match_keys ['barracksStatusDire', 'barracksStatusRadiant', 'didRadiantWin', 'direTeam', 'direTeamId', 'durationSeconds', 'endDateTime', 'gameMode', 'id', 'leagueId', 'lobbyType', 'playbackData', 'players', 'radiantExperienceLeads', 'radiantNetworthLeads', 'radiantTeam', 'radiantTeamId', 'seriesId', 'startDateTime', 'towerStatusDire', 'towerStatusRadiant'] player_stats [] playback False
    match_8835155619.json.gz profile stratz_rich_v2 start 1780367557 end 1780369365 duration 1808 match_keys ['barracksStatusDire', 'barracksStatusRadiant', 'didRadiantWin', 'direTeam', 'direTeamId', 'durationSeconds', 'endDateTime', 'gameMode', 'id', 'leagueId', 'lobbyType', 'playbackData', 'players', 'radiantExperienceLeads', 'radiantNetworthLeads', 'radiantTeam', 'radiantTeamId', 'seriesId', 'startDateTime', 'towerStatusDire', 'towerStatusRadiant'] player_stats [] playback True
    match_9008779767.json.gz profile stratz_rich_v3 start 1789932674 end 1789934826 duration 2152 match_keys ['barracksStatusDire', 'barracksStatusRadiant', 'didRadiantWin', 'direTeam', 'direTeamId', 'durationSeconds', 'endDateTime', 'firstBloodTime', 'gameMode', 'id', 'leagueId', 'lobbyType', 'playbackData', 'players', 'radiantExperienceLeads', 'radiantNetworthLeads', 'radiantTeam', 'radiantTeamId', 'seriesId', 'startDateTime', 'towerDeaths', 'towerStatusDire', 'towerStatusRadiant'] player_stats ['deathEvents', 'itemPurchases', 'level', 'networthPerMinute'] playback True
    ✓ • 2.21s

## Activity

    $ sed -n '450,505p' src/backtest/signals.py && sed -n '580,675p' src/backtest/signals.py && rg -n 'model_identity_sha256|signals.*cache|SIGNAL.*VERSION' src/backtest && rg -n 'noxp|no-xp|model_dir' config/trading.toml src/backtest/run_backtest.py src/backtest/cli* pyproject.toml && cat docs/experiments/no-xp/README.md
        timing: SignalTiming,
        mids: Mapping[int, MidSeries],
    ) -> dict[int, MatchSignals]:
        """Predict on fresh model ticks; v5 drops recovery-stale GRID ticks from decisions.
    
        LoL grid-v1 acts LOL_SOURCE_LAG_SECONDS after the frame and prices the
        book then. `second` stays the frame's game second. A tick with no mid at
        the delayed clock stays on the feed and is not a decision.
        """
        requested = set(match_ids)
        selected = signal_rows[signal_rows["match_id"].isin(list(requested))]
        rows = selected.sort_values(["match_id", "second"], ignore_index=True)
        if bool(rows.duplicated(["match_id", "second"]).any()):
            raise ValueError("validation rows must have unique seconds per match")
    
        present: set[int] = (
            {int(match_id) for match_id in rows["match_id"].to_list()} if not rows.empty else set()
        )
        missing = sorted(requested - present)
        if missing:
            raise ValueError(f"validation dataset has no usable signal rows for matches {missing}")
    
        feed_rows = select_cadence_rows(rows, game=game, timing=timing, lag_seconds=lag_seconds)
        decision_lag_ns = LOL_SOURCE_LAG_SECONDS * NS_PER_SECOND if game == "lol" else 0
    
        model_parts, feed_ts_by_match = _shifted_feed(
            feed_rows, game, mids, decision_lag_ns, timing.max_age_seconds
        )
    
        model_rows = pd.concat(model_parts, ignore_index=True) if model_parts else feed_rows.iloc[0:0]
        if model_rows.empty:
            deltas: list[float] = []
        else:
            feature_columns = load_model_feature_columns(model_dir)
            features = model_rows[feature_columns].assign(second=model_rows["second"] - lag_seconds)
            deltas = predict_model_deltas(model_dir, feature_columns, features)
    
        timestamps_by_match: dict[int, list[int]] = {}
        deltas_by_match: dict[int, list[float]] = {}
        market_ps_by_match: dict[int, list[float]] = {}
        deaths_radiant_by_match: dict[int, list[int]] = {}
        deaths_dire_by_match: dict[int, list[int]] = {}
        for raw_match_id, raw_state_ts_us, delta, market_p, deaths_r, deaths_d in zip(
            model_rows["match_id"],
            model_rows["state_ts_us"],
            deltas,
            model_rows["market_p_radiant"],
            model_rows["deaths_radiant"],
            model_rows["deaths_dire"],
            strict=True,
        ):
            match_id = int(raw_match_id)
            timestamps_by_match.setdefault(match_id, []).append(int(raw_state_ts_us) * NS_PER_US)
            deltas_by_match.setdefault(match_id, []).append(delta)
            market_ps_by_match.setdefault(match_id, []).append(float(market_p))
            deaths_radiant_by_match.setdefault(match_id, []).append(int(deaths_r))
        feature_rows: pd.DataFrame,
        mids: Mapping[int, MidSeries],
    ) -> dict[int, MatchSignals]:
        """Recompute model decisions on archive feed ticks keyed by game second.
    
        Game-state columns come from the tick snapshot the live reducer stored.
        The dataset row still supplies the pregame prior and the second key.
        Order follows each schedule's ticks; equal received_ns keep seq order (one
        feed per match, so no cross-feed interleave exists). A tick with no ok
        market row at/before its arrival stays feed-only, like a market gap under
        grid-v1; a missing feature row is a dataset readiness failure, not a gap.
        """
        groups = feature_rows.groupby("match_id", sort=False).indices
        signals: dict[int, MatchSignals] = {}
        decisions: list[ScheduleDecision] = []
        for match_id, plan in plans.items():
            binding = plan.binding
            schedule = binding.schedule
            feed_ts = [tick.received_ns for tick in schedule.ticks]
            if any(later < earlier for earlier, later in itertools.pairwise(feed_ts)):
                raise ValueError(f"match {match_id}: schedule received_ns is not non-decreasing")
            stale = recovery_stale_mask(feed_ts, grid_exit_age_seconds(entry_stale_seconds(binding)))
            positions = groups.get(match_id)
            frame_slice = (
                feature_rows.take(list(positions)) if positions is not None else feature_rows.iloc[0:0]
            )
            by_second = _feature_positions_by_second(
                match_id,
                [] if positions is None else [int(position) for position in positions],
                frame_slice["game_second"].astype(np.int64).tolist(),
            )
            decisions.extend(
                _match_schedule_decisions(match_id, plan, by_second, mids[match_id], stale)
            )
            signals[match_id] = MatchSignals(
                feed_timestamps_ns=tuple(feed_ts),
                timestamps_ns=(),
                predicted_deltas=(),
                dataset_market_ps=(),
                deaths_radiant=(),
                deaths_dire=(),
                kill_gates=tuple(
                    KillGateUpdate(
                        now_ns=gate.received_ns,
                        gate=KillGate(
                            radiant=KillWait(
                                awaited_deaths=gate.radiant.awaited_deaths,
                                until_ns=gate.radiant.until_ns,
                            ),
                            dire=KillWait(
                                awaited_deaths=gate.dire.awaited_deaths,
                                until_ns=gate.dire.until_ns,
                            ),
                        ),
                    )
                    for gate in schedule.kill_gates
                ),
            )
    
        decision_rows = feature_rows.take([d.row_position for d in decisions]).copy()
        decision_rows["second"] = decision_rows["game_second"]
        decision_rows["market_p_radiant"] = [d.anchor_p for d in decisions]
        for column in SNAPSHOT_FEATURE_COLUMNS:
            decision_rows[column] = [getattr(decision.tick, column) for decision in decisions]
        deltas: list[float] = [0.0] * len(decisions)
        model_dirs = [decision.model_dir for decision in decisions]
        for model_dir in sorted(set(model_dirs), key=str):
            members = [i for i, dir_ in enumerate(model_dirs) if dir_ == model_dir]
            feature_columns = load_model_feature_columns(model_dir)
            predicted = predict_model_deltas(
                model_dir, feature_columns, decision_rows.iloc[members][feature_columns]
            )
            for index, value in zip(members, predicted, strict=True):
                deltas[index] = value
    
        timestamps_by_match: dict[int, list[int]] = {}
        deltas_by_match: dict[int, list[float]] = {}
        anchors_by_match: dict[int, list[float]] = {}
        deaths_radiant_by_match: dict[int, list[int]] = {}
        deaths_dire_by_match: dict[int, list[int]] = {}
        for decision, delta in zip(decisions, deltas, strict=True):
            timestamps_by_match.setdefault(decision.match_id, []).append(decision.received_ns)
            deltas_by_match.setdefault(decision.match_id, []).append(delta)
            anchors_by_match.setdefault(decision.match_id, []).append(decision.anchor_p)
            deaths_radiant_by_match.setdefault(decision.match_id, []).append(
                decision.tick.deaths_radiant
            )
            deaths_dire_by_match.setdefault(decision.match_id, []).append(decision.tick.deaths_dire)
    
        return {
            match_id: replace(
                signals[match_id],
                timestamps_ns=tuple(timestamps_by_match.get(match_id, ())),
                predicted_deltas=tuple(deltas_by_match.get(match_id, ())),
                dataset_market_ps=tuple(anchors_by_match.get(match_id, ())),
                deaths_radiant=tuple(deaths_radiant_by_match.get(match_id, ())),
    src/backtest/replay_inputs.py:23:from shared.utils.gbm import model_identity_sha256
    src/backtest/replay_inputs.py:186:    loaded = ModelInputs(name=meta["name"], sha256=model_identity_sha256(model_dir))
    src/backtest/run.py:166:from shared.utils.gbm import MODEL_META_FILENAME, model_identity_sha256
    src/backtest/run.py:738:        "model_sha256": model_identity_sha256(model_dir),
    src/backtest/run.py:749:            str(plan_model_dir): model_identity_sha256(plan_model_dir)
    zsh:1: no matches found: src/backtest/cli*
    ✗ (1) • 1.26s

## Assistant

В кэше нашлось 19 372 файла STRATZ, хотя текущий индекс collect содержит только 3 300 матчей. Значит, для эксперимента действительно нужно обходить весь кэш.

Есть существенный нюанс: старые файлы часто содержат только поминутные преимущества по золоту и опыту, без статистики отдельных игроков. Поэтому взять полный набор признаков основной модели для всех старых матчей не получится. Сейчас считаю, сколько данных останется при разных вариантах.

## Activity

    $ rg -n 'noxp|no-xp|model_dir' config/trading.toml src/backtest/run.py && rg --files docs/experiments/no-xp && cat docs/experiments/outcome-p-win.md && sed -n '1,100p' src/shared/types/stratz.py
    src/backtest/run.py:244:    model_dir: Path
    src/backtest/run.py:697:    model_dir: Path,
    src/backtest/run.py:711:    model_meta = read_model_meta(model_dir / MODEL_META_FILENAME)
    src/backtest/run.py:737:        "model_path": str(model_dir),
    src/backtest/run.py:738:        "model_sha256": model_identity_sha256(model_dir),
    src/backtest/run.py:749:            str(plan_model_dir): model_identity_sha256(plan_model_dir)
    src/backtest/run.py:750:            for plan_model_dir in sorted({plan.model_dir for plan in plans.values()}, key=str)
    src/backtest/run.py:1215:def _pin_cli_model_dir(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    src/backtest/run.py:1217:    if args.model_dir is None:
    src/backtest/run.py:1219:    pinned = args.model_dir.expanduser()
    src/backtest/run.py:1222:    args.model_dir = pinned.resolve()
    src/backtest/run.py:1368:    _pin_cli_model_dir(parser, args)
    src/backtest/run.py:1417:            model_dir=LOL_RESEARCH_MODEL_DIR,
    src/backtest/run.py:1437:        model_dir=RESEARCH_MODEL_DIR,
    src/backtest/run.py:1672:    model_dir: Path,
    src/backtest/run.py:1697:                model_dir,
    src/backtest/run.py:1772:        selection.archive_join, selection.selected_ids, args.model_dir, args.match_id
    src/backtest/run.py:1788:    if args.model_dir is not None:
    src/backtest/run.py:1789:        selection = replace(selection, model_dir=args.model_dir)
    src/backtest/run.py:1796:    model_dir = selection.model_dir
    src/backtest/run.py:1833:        model_dir=model_dir,
    src/backtest/run.py:1889:        model_dir=model_dir,
    docs/experiments/no-xp/run.py
    # P(win) instead of 300s midpoint delta
    
    Date: 2026-09-02. Status: **lost, code dropped**. `--target radiant-win`
    never landed on main. Boosters sit in
    `data/new_model/experiments/outcome-p-win` and
    `data/lol/models/experiments/outcome-p-win`. Default train is 300s
    `regression_l1`.
    
    Hypothesis: the same maker overlay (join-bid BUY until 540, SELL on rolling
    fair, unwind off) would make more money if the booster predicted P(Radiant
    wins the map) instead of the 300s mid move. Fair = clip(P). The 1¢ gate and
    size ramp still see `fair − market`.
    
    The experiment fit LightGBM `binary` / `binary_logloss` on `radiant_win` for
    seconds ≤ 540 (Dota still includes −60). Same 12 features. `model.json`
    recorded `"target": "radiant_win"`. Inference mapped P → edge so strategy
    code stayed unchanged.
    
    300s MAE in these `model.json` files is the wrong object (P(win) vs a 5-minute
    mid). The maker is the decision.
    
    ## Dota maker (s2-join, `|edge| ≥ 1¢`)
    
    Model `20260902T205854Z` (37 trees, 2055 train matches).
    `data/new_model/experiments/outcome-p-win`
    
    Three-seed catalog:
    `data/backtests/dota_maker/validation_join_delta01_cut540_nw350_p35_outcome-p-win`
    
    LIVE 300s baseline:
    `data/backtests/dota_maker/LIVE` → `…_grid-v1-baseline`
    
    Same knobs: cut 540, nw 350, delta 1¢, minpx 0.35, unwind off, size $100,
    4 shards. 447 shared matches.
    
    Engine PnL before rebate vs LIVE:
    
    | | LIVE | outcome P(win) | paired delta |
    |---|---|---|---|
    | seed0 | +$172.01 | −$159.85 | −$331.85 |
    | seed1 | +$372.96 | −$442.07 | −$815.03 |
    | seed2 | +$399.70 | +$78.72 | −$320.98 |
    | pooled t / wilcoxon | | | t=−1.37 p=0.172; W p=0.264 |
    
    All three seeds lose. Mean seed-total effect −$489.
    
    Buy 300s on the outcome run is ~0.06¢ mean across seeds (CI crosses 0). LIVE
    300s models sit near 2¢. Hold p50 138s / p90 641s on seed2; leftover losers
    still print (worst −$103).
    
    ## LoL maker (s2-join, `|edge| ≥ 1¢`)
    
    Model `20260902T205848Z` (38 trees, 2895 train matches).
    `data/lol/models/experiments/outcome-p-win`
    
    Three-seed catalog:
    `data/backtests/lol_maker/validation_join_delta01_cut540_nw350_p35_outcome-p-win`
    
    LIVE 300s baseline:
    `data/backtests/lol_maker/LIVE` → `…_grid-v1-baseline`
    
    1510 shared maps. 4 shards.
    
    Engine PnL before rebate vs LIVE:
    
    | | LIVE | outcome P(win) | paired delta |
    |---|---|---|---|
    | seed0 | +$1,152.54 | −$2,637.89 | −$3,790.44 |
    | seed1 | +$609.98 | −$1,811.48 | −$2,421.46 |
    | seed2 | +$985.07 | −$1,905.77 | −$2,890.83 |
    | pooled t / wilcoxon | | | t=−4.81 p=0.000; W p=0.000 |
    
    All three seeds lose, and the loss is large. All three chronological thirds
    lose. Mean net_pnl with rebate −$1,333. Buy 300s ~0.4¢ and sell 300s −1.3¢:
    the resolution-priced fair does not match a 300s exit overlay.
    
    ## Decision
    
    Drop. Do not re-add `--target radiant-win` or map P(win) into the
    `predicted_delta` slot. Keep quoting and training on the 300s midpoint.
    P(win) as fair with the current SELL-on-rolling-FV overlay is a different
    product that lost on both games.
    """Shape of the gzipped Stratz match cache written by s05b_fetch_stratz_matches.
    
    Derived from a 1200-file sample of data/raw/stratz_matches/. Two cache profiles
    exist: `stratz_rich_v2` and `stratz_rich_v3`; v3 adds `firstBloodTime` and
    `towerDeaths` on the match and `stats` on each player. Those three are
    NotRequired — everything else is present in both.
    
    These are static types only: `cast()` at the read boundary, no runtime
    validation. Run `python src/shared/types/stratz.py` to check the cache still
    matches (see check_cache below).
    """
    
    from typing import Any, Literal, NotRequired, TypedDict
    
    Lane = Literal["SAFE_LANE", "OFF_LANE", "MID_LANE", "ROAMING", "JUNGLE"]
    Position = Literal["POSITION_1", "POSITION_2", "POSITION_3", "POSITION_4", "POSITION_5"]
    Role = Literal["CORE", "LIGHT_SUPPORT", "HARD_SUPPORT"]
    Award = Literal["NONE", "MVP", "TOP_CORE", "TOP_SUPPORT"]
    LeaverStatus = Literal["NONE", "DISCONNECTED"]
    
    
    class StratzTeam(TypedDict):
        id: int
        name: str
    
    
    class StratzPlayerStats(TypedDict):
        """v3 only. Level-up seconds, per-minute series, and sparse event lists."""
    
        level: list[int]
        networthPerMinute: list[int]
        itemPurchases: list["StratzItemPurchase"]
        deathEvents: list["StratzPlayerDeathEvent"]
    
    
    class StratzItemPurchase(TypedDict):
        itemId: int
        time: int
    
    
    class StratzPlayerDeathEvent(TypedDict):
        time: int
        timeDead: int
    
    
    class StratzPlayerUpdateGoldEvent(TypedDict):
        """One absolute networth update from player playback."""
    
        time: int
        gold: int
        networth: int
        networthDifference: int
        unreliableGold: int
    
    
    class StratzExperienceEvent(TypedDict):
        """One experience gain event from player playback."""
    
        time: int
        amount: int
        positionX: int
        positionY: int
    
    
    class StratzPlayerPlayback(TypedDict):
        """Per-player playback fields used for exact-second validation features."""
    
        playerUpdateGoldEvents: list[StratzPlayerUpdateGoldEvent]
        experienceEvents: list[StratzExperienceEvent]
    
    
    class StratzPlayer(TypedDict):
        matchId: int
        steamAccountId: int
        heroId: int
        playerSlot: int
        isRadiant: bool
        isVictory: bool
        level: int
        kills: int
        deaths: int
        assists: int
        numLastHits: int
        numDenies: int
        gold: int
        goldSpent: int
        goldPerMinute: int
        experiencePerMinute: int
        networth: int
        heroDamage: int
        heroHealing: int
        towerDamage: int
        leaverStatus: LeaverStatus
        roleBasic: Role
        # null in ~4% of players (abandons, unparsed drafts)
        lane: Lane | None
        position: Position | None
        role: Role | None
        award: Award | None
        imp: int | None
    ✓ • 1.33s

## Activity

    $ UV_CACHE_DIR=/tmp/codex-uv-cache uv run --no-sync python - <<'PY'
        from pathlib import Path
        from collections import Counter,defaultdict
        import gzip,json
        import pandas as pd
        cutoff=1780563592
        s=pd.read_parquet('data/new_model/research/split.parquet'); excluded=set(s.match_id)
        counts=Counter(); spans=defaultdict(list)
        files=sorted(Path('data/raw/stratz_matches').glob('*.gz'))
        for f in files:
         mid=int(f.name.split('_')[1].split('.')[0])
         if mid in excluded:counts['main_split']+=1;continue
         x=json.loads(gzip.decompress(f.read_bytes()));m=(x.get('data') or {}).get('match')
         if not m:counts['empty']+=1;continue
         end=m.get('endDateTime')
         if not isinstance(end,(int,float)):counts['missing_end']+=1;continue
         if end>=cutoff:counts['after_cutoff']+=1;continue
         profile=x.get('cache_profile','unknown');counts['before_'+profile]+=1
         spans[profile].append(m['startDateTime'])
         ps=m.get('players') or []
         rich=len(ps)==10 and all(p.get('stats') and all(p['stats'].get(k) is not None for k in ['networthPerMinute','deathEvents','level']) for p in ps)
         counts['before_rich' if rich else 'before_not_rich']+=1
         nw=m.get('radiantNetworthLeads') or [];xp=m.get('radiantExperienceLeads') or []
         if len(nw)>=11 and len(xp)>=11:counts['before_11_leads']+=1
        print(dict(counts))
        for k,v in spans.items():print(k,len(v),pd.to_datetime(min(v),unit='s',utc=True),pd.to_datetime(max(v),unit='s',utc=True))
        for f in files[::max(1,len(files)//4)]:
         x=json.loads(gzip.decompress(f.read_bytes()));m=(x.get('data') or {}).get('match') or {};p=(m.get('players') or [{}])[0]
         print(f.name,'player_keys',list(p),'nw',m.get('radiantNetworthLeads',[])[:4])
        PY
    {'before_stratz_rich_v2': 13121, 'before_not_rich': 13149, 'before_11_leads': 15286, 'before_stratz_rich_v3': 2960, 'before_rich': 2932, 'main_split': 2280, 'after_cutoff': 1008, 'empty': 3}
    stratz_rich_v2 13121 2025-01-01 00:09:05+00:00 2026-06-04 08:35:15+00:00
    stratz_rich_v3 2960 2025-10-01 11:02:49+00:00 2026-06-04 04:59:09+00:00
    match_8107483721.json.gz player_keys ['assists', 'award', 'backpack0Id', 'backpack1Id', 'backpack2Id', 'deaths', 'experiencePerMinute', 'gold', 'goldPerMinute', 'goldSpent', 'heroDamage', 'heroHealing', 'heroId', 'imp', 'isRadiant', 'isVictory', 'item0Id', 'item1Id', 'item2Id', 'item3Id', 'item4Id', 'item5Id', 'kills', 'lane', 'leaverStatus', 'level', 'matchId', 'networth', 'neutral0Id', 'numDenies', 'numLastHits', 'playbackData', 'playerSlot', 'position', 'role', 'roleBasic', 'steamAccountId', 'towerDamage'] nw [0, -50, 2, -882]
    match_8624346048.json.gz player_keys ['assists', 'award', 'backpack0Id', 'backpack1Id', 'backpack2Id', 'deaths', 'experiencePerMinute', 'gold', 'goldPerMinute', 'goldSpent', 'heroDamage', 'heroHealing', 'heroId', 'imp', 'isRadiant', 'isVictory', 'item0Id', 'item1Id', 'item2Id', 'item3Id', 'item4Id', 'item5Id', 'kills', 'lane', 'leaverStatus', 'level', 'matchId', 'networth', 'neutral0Id', 'numDenies', 'numLastHits', 'playbackData', 'playerSlot', 'position', 'role', 'roleBasic', 'steamAccountId', 'towerDamage'] nw [50, 50, 513, 583]
    match_8704626671.json.gz player_keys ['assists', 'award', 'backpack0Id', 'backpack1Id', 'backpack2Id', 'deaths', 'experiencePerMinute', 'gold', 'goldPerMinute', 'goldSpent', 'heroDamage', 'heroHealing', 'heroId', 'imp', 'isRadiant', 'isVictory', 'item0Id', 'item1Id', 'item2Id', 'item3Id', 'item4Id', 'item5Id', 'kills', 'lane', 'leaverStatus', 'level', 'matchId', 'networth', 'neutral0Id', 'numDenies', 'numLastHits', 'playbackData', 'playerSlot', 'position', 'role', 'roleBasic', 'steamAccountId', 'towerDamage'] nw [0, 0, 54, 80]
    Traceback (most recent call last):
      File "<stdin>", line 28, in <module>
    TypeError: 'NoneType' object is not subscriptable
    ✗ (1) • 2m 02s

## User

Посмотри, насколько там есть большая разница. И чего именно нету? Нету отдельных игроков? Насколько сильно это обрежет нам, если у нас не будет отдельных игроков? И какие будут фичи тогда в простой модели и в сложной?

Посмотри. И учитывая, что мы еще именно... посчитай мне, короче, цифры, дай мне. Учитывая, что мы еще исключаем эти матчи, на которых мы учим нашу основную модель. У нас есть еще отсечка по валидейшену.

Сколько у нас, короче, будет в трейне, в валидейшене для этих обоих сценариев? Для простой модели и для более сложной? Какие там будут фичи.

## Assistant

Посчитаю оба варианта по всему кэшу: после исключения матчей основной модели, временной отсечки и проверки нужных признаков. Покажу, чего именно не хватает старым файлам, какие признаки доступны и сколько матчей получится в train/validation.

## Activity

    $ cat src/shared/utils/stratz.py src/shared/utils/dota_levels.py src/shared/constants/dataset.py && sed -n '100,260p' src/shared/types/stratz.py && sed -n '1,100p' pyproject.toml
    from pathlib import Path
    from typing import Literal, TypeGuard, cast
    
    from shared.constants.paths import RAW_STRATZ_MATCHES_DIR
    from shared.types.stratz import (
        StratzMatch,
        StratzMatchBase,
        StratzMatchCache,
        StratzPlayer,
        UsableStratzMatch,
    )
    from shared.utils.dota_levels import is_level_timeline_monotonic
    from shared.utils.json_io import read_gzip_json
    
    # Longest constant prefix a real match shows is 3; broken parses start at 60.
    FLAT_PREFIX_LIMIT = 5
    RESET_SAMPLE_LIMIT = 2000
    
    UnusableReason = Literal["missing_leads", "flat_edge", "reset_tail", "level_timeline"]
    
    
    def player_death_times(player: StratzPlayer) -> list[int]:
        return sorted(event["time"] for event in player["stats"]["deathEvents"])
    
    
    def death_times(match: StratzMatchBase, radiant: bool) -> list[int]:
        times: list[int] = []
        for player in match["players"]:
            if player["isRadiant"] != radiant:
                continue
            times.extend(player_death_times(player))
        return sorted(times)
    
    
    def stratz_match_cache_path(match_id: int) -> Path:
        return RAW_STRATZ_MATCHES_DIR / f"match_{int(match_id)}.json.gz"
    
    
    def try_load_stratz_winners(match_ids: tuple[int, ...]) -> dict[int, bool]:
        """`didRadiantWin` per cached match; ids without a cache file are absent."""
        winners: dict[int, bool] = {}
        for match_id in match_ids:
            try:
                payload = cast(StratzMatchCache, read_gzip_json(stratz_match_cache_path(match_id)))
            except FileNotFoundError:
                continue
            match = cast("StratzMatch | None", payload["data"].get("match"))
            if match is None:
                continue
            winners[match_id] = bool(match["didRadiantWin"])
        return winners
    
    
    def get_stratz_match_reach_data(match_id: int) -> UsableStratzMatch:
        """Read one cached match. Callers pass catalog ids, so the collect gate already ran."""
        payload = cast(StratzMatchCache, read_gzip_json(stratz_match_cache_path(match_id)))
        match = payload["data"]["match"]
        assert is_usable_stratz_match(match)
        return match
    
    
    def stratz_networth_xp_leads(match: StratzMatch) -> tuple[list[float], list[float]]:
        raw_nw = match.get("radiantNetworthLeads")
        raw_xp = match.get("radiantExperienceLeads")
        nw = [float(value) for value in raw_nw] if raw_nw else []
        xp = [float(value) for value in raw_xp] if raw_xp else []
        return nw, xp
    
    
    def constant_prefix_length(values: list[float]) -> int:
        length = 0
        while length < len(values) and values[length] == values[0]:
            length += 1
        return length
    
    
    def has_reset_tail(nw: list[float], xp: list[float]) -> bool:
        end = len(xp)
        if end >= 2 and xp[end - 1] == 0:
            networth_is_live = abs(nw[end - 1]) > RESET_SAMPLE_LIMIT
            experience_was_live = abs(xp[end - 2]) > RESET_SAMPLE_LIMIT
            return networth_is_live or experience_was_live
        return False
    
    
    def stratz_match_unusable_reason(match: StratzMatch | None) -> UnusableReason | None:
        if match is None:
            return "missing_leads"
        nw, xp = stratz_networth_xp_leads(match)
        if len(nw) < 2 or len(xp) < 2:
            return "missing_leads"
        edges = [nw, nw[::-1], xp, xp[::-1]]
        longest_flat_edge = max(constant_prefix_length(values) for values in edges)
        if longest_flat_edge >= FLAT_PREFIX_LIMIT:
            return "flat_edge"
        if has_reset_tail(nw, xp):
            return "reset_tail"
        if not all(is_level_timeline_monotonic(player) for player in match.get("players") or []):
            return "level_timeline"
        return None
    
    
    def is_usable_stratz_match(match: StratzMatch) -> TypeGuard[UsableStratzMatch]:
        return stratz_match_unusable_reason(match) is None
    """Convert Dota 2 level-up timelines to cumulative XP advantages."""
    
    from bisect import bisect_right
    from itertools import pairwise
    
    from shared.constants.dota import LEVEL_XP
    from shared.types.stratz import StratzPlayer
    from shared.utils.level_xp import cumulative_xp, xp_advantage
    
    
    def experience_at_level(level: int) -> int:
        """Return cumulative XP at a Dota 2 level. Steam draft players are level 0 (0 XP)."""
        return cumulative_xp(LEVEL_XP, level)
    
    
    def level_at_second(level_seconds: list[int] | tuple[int, ...], second: int) -> int:
        """Return the level reached by a player at one game second."""
        return bisect_right(level_seconds, second)
    
    
    def radiant_xp_advantage(radiant_levels: list[int], dire_levels: list[int]) -> int:
        """Return cumulative Radiant XP minus cumulative Dire XP."""
        return xp_advantage(LEVEL_XP, radiant_levels, dire_levels)
    
    
    def radiant_xp_advantage_at_second(players: list[StratzPlayer], second: int) -> int:
        """Return the level-based Radiant XP advantage at one game second."""
        radiant_levels: list[int] = []
        dire_levels: list[int] = []
        for player in players:
            level = level_at_second(player["stats"]["level"], second)
            if player["isRadiant"]:
                radiant_levels.append(level)
            else:
                dire_levels.append(level)
        return radiant_xp_advantage(radiant_levels, dire_levels)
    
    
    def is_level_timeline_monotonic(player: StratzPlayer) -> bool:
        """Report whether a player's level-up seconds never go backwards."""
        level_seconds = player["stats"]["level"]
        return all(left <= right for left, right in pairwise(level_seconds))
    """Shared dataset chronology constants."""
    
    VALIDATION_START_TIME = 1_780_563_592
    # Collector/Telonex day holes (e.g. 2026-08-08) skip this many empty-book
    # misses; more than this still aborts so a broken capture cannot silently
    # empty the split. Train is larger, so its cap sits on the known hole
    # (161 maps with no in-window book as of the spread-6 rebuild).
    MAX_VALIDATION_HARD_MISSES = 20
    MAX_TRAIN_HARD_MISSES = 161
    # A longer run of signal-window seconds without a fresh two-sided book marks a
    # validation match as ineligible for bulk backtesting. ML validation keeps it.
    MAX_BOOK_GAP_SECONDS = 120
    MODEL_START_SECOND = -60
    # Train rows and train_model eval stay in this window; prepare writes validation further.
    TRAIN_END_SECOND_EXCLUSIVE = 600
    MODEL_TARGET_HORIZON_SECONDS = 300
    # Book-gap eligibility still ends here. Prepare writes validation rows through
    # map duration so backtest FV can roll until game end.
    VALIDATION_SIGNAL_END_SECOND_EXCLUSIVE = TRAIN_END_SECOND_EXCLUSIVE + MODEL_TARGET_HORIZON_SECONDS
    # STRATZ snapshot age baked into training rows: state at S, market at S+lag.
    TRAIN_LAG_SECONDS = 10
    # STRATZ snapshot age at execution: validation rows and backtest predict.
    BACKTEST_LAG_SECONDS = 10
    PREHORN_LEAD_SECONDS = -(MODEL_START_SECOND + BACKTEST_LAG_SECONDS)
        imp: int | None
        item0Id: int | None
        item1Id: int | None
        item2Id: int | None
        item3Id: int | None
        item4Id: int | None
        item5Id: int | None
        backpack0Id: int | None
        backpack1Id: int | None
        backpack2Id: int | None
        neutral0Id: int | None
        # ponytail: ~23 replay event lists, non-null in ~15% of matches. Left as Any
        # until something actually reads it — see PLAYER_PLAYBACK_KEYS below.
        playbackData: dict[str, Any] | None
        stats: StratzPlayerStats
    
    
    class StratzRuneEvent(TypedDict):
        """Match-level rune event. Note `action`/`rune` are ints here but strings in
        the per-player playbackData."""
    
        action: int
        rune: int
        location: int
        indexId: int
        positionX: int
        positionY: int
        time: int
    
    
    class StratzTowerDeathEvent(TypedDict):
        """Match-level: surviving tower bitmask per side after the death."""
    
        radiant: int
        dire: int
        time: int
    
    
    class StratzWardEvent(TypedDict):
        action: str
        wardType: str
        fromPlayer: int
        playerDestroyed: int | None
        indexId: int
        positionX: int
        positionY: int
        time: int
    
    
    class StratzMatchPlayback(TypedDict):
        runeEvents: list[StratzRuneEvent]
        towerDeathEvents: list[StratzTowerDeathEvent]
        wardEvents: list[StratzWardEvent]
        # always [] across the whole sample — shape unknown, type when one shows up
        courierEvents: list[Any]
        roshanEvents: list[Any]
        buildingEvents: list[Any]
    
    
    class StratzTowerDeath(TypedDict):
        """v3 match-level towerDeaths — which tower, not a bitmask."""
    
        npcId: int
        isRadiant: bool
        time: int
    
    
    class StratzMatchBase(TypedDict):
        """Every match field except the lead arrays, which the two shapes below narrow."""
    
        id: int
        leagueId: int
        seriesId: int
        didRadiantWin: bool
        durationSeconds: int
        startDateTime: int
        endDateTime: int
        gameMode: str  # CAPTAINS_MODE | ALL_PICK | ... — open set, not a Literal
        lobbyType: str  # PRACTICE for everything we collect
        radiantTeamId: int
        direTeamId: int
        radiantTeam: StratzTeam
        direTeam: StratzTeam
        towerStatusRadiant: int
        towerStatusDire: int
        barracksStatusRadiant: int
        barracksStatusDire: int
        playbackData: StratzMatchPlayback | None
        players: list[StratzPlayer]  # always exactly 10
        firstBloodTime: NotRequired[int]
        towerDeaths: NotRequired[list[StratzTowerDeath]]
    
    
    class StratzMatch(StratzMatchBase):
        """The raw cache shape. STRATZ leaves both lead arrays null in ~2% of matches."""
    
        radiantNetworthLeads: list[int] | None
        radiantExperienceLeads: list[int] | None
    
    
    class UsableStratzMatch(StratzMatchBase):
        """A match that passed the collect gate in stratz_match_unusable_reason."""
    
        radiantNetworthLeads: list[int]
        radiantExperienceLeads: list[int]
    
    
    class StratzMatchData(TypedDict):
        match: StratzMatch
    
    
    class StratzMatchCache(TypedDict):
        """Top level of match_<id>.json.gz."""
    
        match_id: int
        cache_profile: Literal["stratz_rich_v2", "stratz_rich_v3"]
        source: str
        fetched_at: str
        response_bytes: int
        graphql_errors: list[Any]
        data: StratzMatchData
    
    
    PLAYER_PLAYBACK_KEYS = (
        "abilityActiveLists",
        "abilityLearnEvents",
        "abilityUsedEvents",
        "assistEvents",
        "buyBackEvents",
        "csEvents",
        "deathEvents",
        "experienceEvents",
        "goldEvents",
        "healEvents",
        "heroDamageEvents",
        "inventoryEvents",
        "itemUsedEvents",
        "killEvents",
        "playerUpdateAttributeEvents",
        "playerUpdateBattleEvents",
        "playerUpdateGoldEvents",
        "playerUpdateHealthEvents",
        "playerUpdateLevelEvents",
        "playerUpdatePositionEvents",
        "purchaseEvents",
        "runeEvents",
        "towerDamageEvents",
    )
    
    
    def check_cache(payload: dict[str, Any]) -> None:
        """Raise if a cached payload has drifted from the types above."""
    
        def check(name: str, td: Any, obj: dict[str, Any]) -> None:
            missing = set(td.__required_keys__) - obj.keys()
            extra = obj.keys() - td.__required_keys__ - set(td.__optional_keys__)
            if missing or extra:
                raise AssertionError(f"{name}: missing={sorted(missing)} extra={sorted(extra)}")
    
        check("StratzMatchCache", StratzMatchCache, payload)
        match = payload["data"]["match"]
    [project]
    name = "esports-trader"
    version = "0.1.0"
    requires-python = ">=3.13"
    dependencies = [
        "httpx>=0.27.0",
        "lightgbm>=4.3.0",
        "msgspec>=0.19.0",
        "pandas>=2.3.3,<3.0.0",
        "polymaker",
        "polymarket-client>=0.3.0",
        "pyarrow>=16.0.0",
        "pytest>=8.3.0",
        "scikit-learn>=1.5.0",
        "tenacity>=8.3.0",
        "typer>=0.27.1",
        "watchdog>=6.0.0",
        "websockets>=13.0",
    ]
    
    [tool.uv]
    package = false
    
    [tool.uv.sources]
    polymaker = { path = "../poly-maker", editable = true }
    
    [dependency-groups]
    dev = [
        "basedpyright>=1.39.9",
        "matplotlib>=3.9",
        "pandas-stubs>=2.3.3.260113,<3.0",
        "pre-commit>=4.0",
        "pyarrow-stubs>=20.0.0.20260819",
        "ruff>=0.14",
    ]
    backtest = [
        "aiohttp>=3.10.0",
        "bokeh>=3.0.0",
        "duckdb>=1.0.0",
        "maturin>=1.12,<2",
        "nautilus_trader[polymarket,visualization]==1.226.0",
        "optuna>=4.0.0",
        "plotly>=5.0.0",
        "py-clob-client>=0.34.0",
        "python-dotenv>=1.0.0",
        "streamlit>=1.30.0",
    ]
    
    [tool.pytest.ini_options]
    testpaths = ["tests"]
    # Don't keep the last 3 sessions: a killed run left 3GiB under /tmp (tmpfs).
    tmp_path_retention_policy = "none"
    
    [tool.ruff]
    line-length = 100
    
    [tool.ruff.lint]
    # E/F pyflakes+pycodestyle, I import sort, UP modernize, B bugbear, SIM simplify,
    # RUF ruff-specific, PD pandas, TID251 no __future__.annotations, PLC0415 no nested imports.
    # C901 McCabe. Typing is basedpyright's job, so no ANN here.
    select = ["E", "F", "I", "UP", "B", "SIM", "RUF", "PD", "TID251", "PLC0415", "C901"]
    ignore = [
        "E501",    # formatter owns line width; only comments/URLs it can't split trip this
        "RUF001",  # en-dashes in regexes/strings are intentional
        "SIM108",  # wants `x = a if c else b`; we keep a flat if/else assignment
    ]
    
    [tool.ruff.lint.flake8-tidy-imports.banned-api]
    "__future__.annotations".msg = "Python 3.13+; don't import annotations from __future__"
    ✓ • 1.30s

## Assistant

Предварительно после исключения 2 280 матчей основной модели и отсечки по началу её validation остаётся 16 081 матч. Из них только у 2 932 есть все три нужные истории по игрокам: золото по минутам, смерти и получение уровней. Ещё проверю качество этих данных — это пока не окончательные размеры выборок.

Для расчёта возьму собственной validation последние 30 дней перед основной отсечкой, остальное — train. Это явно заданный вариант разбиения для сравнения, поскольку её длительность мы ещё не выбирали.

## Activity

    $ UV_CACHE_DIR=/tmp/codex-uv-cache uv run --no-sync python - <<'PY'
        import gzip,json
        from pathlib import Path
        f=Path('data/raw/stratz_matches/match_8835155619.json.gz')
        x=json.loads(gzip.decompress(f.read_bytes()));m=x['data']['match'];p=m['players'][0]
        print('profile',x['cache_profile'],'start',m['startDateTime'])
        for k in ['stats','playbackData']:
         d=p.get(k)
         print(k,'keys',list(d or {}))
         if k=='playbackData' and d:
          for name in ['playerUpdateGoldEvents','playerUpdateLevelEvents','deathEvents','experienceEvents']:
           e=d.get(name)
           print(name,'len',len(e) if e is not None else None,'sample',e[:2] if e else e)
        PY
    profile stratz_rich_v2 start 1780367557
    stats keys []
    playbackData keys ['abilityActiveLists', 'abilityLearnEvents', 'abilityUsedEvents', 'assistEvents', 'buyBackEvents', 'csEvents', 'deathEvents', 'experienceEvents', 'goldEvents', 'healEvents', 'heroDamageEvents', 'inventoryEvents', 'itemUsedEvents', 'killEvents', 'playerUpdateAttributeEvents', 'playerUpdateBattleEvents', 'playerUpdateGoldEvents', 'playerUpdateHealthEvents', 'playerUpdateLevelEvents', 'playerUpdatePositionEvents', 'purchaseEvents', 'runeEvents', 'towerDamageEvents']
    playerUpdateGoldEvents len 1811 sample [{'gold': 0, 'networth': 600, 'networthDifference': 600, 'time': -89, 'unreliableGold': 10}, {'gold': 0, 'networth': 600, 'networthDifference': 0, 'time': -88, 'unreliableGold': 10}]
    playerUpdateLevelEvents len 21 sample [{'level': 1, 'time': -89}, {'level': 2, 'time': 101}]
    deathEvents len 5 sample [{'assist': [105], 'attacker': 81, 'goldFed': 399, 'goldLost': 102, 'positionX': 170, 'positionY': 86, 'reliableGold': 342, 'target': 35, 'time': 552, 'timeDead': 26, 'unreliableGold': 347, 'xpFed': 474}, {'assist': [81, 19], 'attacker': 49, 'goldFed': 835, 'goldLost': 256, 'positionX': 108, 'positionY': 116, 'reliableGold': 80, 'target': 35, 'time': 1179, 'timeDead': 50, 'unreliableGold': 256, 'xpFed': 1725}]
    experienceEvents len 375 sample [{'amount': 28, 'positionX': 174, 'positionY': 83, 'time': 41}, {'amount': 34, 'positionX': 175, 'positionY': 87, 'time': 43}]
    ✓ • 1.12s

## Activity

    $ UV_CACHE_DIR=/tmp/codex-uv-cache uv run --no-sync python - <<'PY'
        import gzip,json
        from pathlib import Path
        f=Path('data/raw/stratz_matches/match_9008779767.json.gz');m=json.loads(gzip.decompress(f.read_bytes()))['data']['match']
        for i,p in enumerate(m['players'][:2]):
         print('player',i,'stats_level',p['stats']['level'][:12])
         print('playback_level',p['playbackData']['playerUpdateLevelEvents'][:12])
         print('stats_death',p['stats']['deathEvents'][:3])
         print('playback_death',[e['time'] for e in p['playbackData']['deathEvents'][:3]])
        PY
    player 0 stats_level [-89, 109, 182, 289, 392, 444, 517, 644, 722, 782, 856, 925]
    playback_level [{'level': 1, 'time': -89}, {'level': 2, 'time': 109}, {'level': 3, 'time': 182}, {'level': 4, 'time': 289}, {'level': 5, 'time': 392}, {'level': 6, 'time': 444}, {'level': 7, 'time': 517}, {'level': 8, 'time': 644}, {'level': 9, 'time': 722}, {'level': 10, 'time': 782}, {'level': 11, 'time': 856}, {'level': 12, 'time': 925}]
    stats_death []
    playback_death []
    player 1 stats_level [-89, 66, 118, 207, 304, 431, 540, 565, 622, 740, 800, 868]
    playback_level [{'level': 1, 'time': -89}, {'level': 2, 'time': 66}, {'level': 3, 'time': 118}, {'level': 4, 'time': 207}, {'level': 5, 'time': 304}, {'level': 6, 'time': 431}, {'level': 7, 'time': 540}, {'level': 8, 'time': 565}, {'level': 9, 'time': 622}, {'level': 10, 'time': 740}, {'level': 11, 'time': 800}, {'level': 12, 'time': 868}]
    stats_death [{'time': 1365, 'timeDead': 65}]
    playback_death [1365]
    ✓ • 1.24s

## Assistant

Уточнение: сами игроки в старых файлах есть. Обычно отсутствует именно их история по ходу карты; итоговые золото, смерти и уровень сохранены, но использовать их для прогноза на ранней минуте нельзя — это утечка будущего.

Кроме того, у части старых файлов есть подробный replay (`playbackData`). Из него потенциально можно восстановить недостающие признаки, поэтому проверю и этот запас, чтобы не занизить размер сложного варианта.

## Activity

    $ ps -Ao pid,etime,pcpu,rss,command | rg 'python -u|uv run --no-sync'
    zsh:1: operation not permitted: ps
    ✗ (1) • 754ms

## Activity

    $ UV_CACHE_DIR=/tmp/codex-uv-cache uv run --no-sync python -u - <<'PY'
        import sys
        sys.path.insert(0,'src')
        from pathlib import Path
        from collections import Counter
        from concurrent.futures import ThreadPoolExecutor
        import gzip,json,math
        from datetime import datetime,timezone
        import pandas as pd
        from shared.constants.dataset import VALIDATION_START_TIME
        from shared.utils.stratz import constant_prefix_length,has_reset_tail,stratz_match_unusable_reason
        from prepare_dataset.stratz_seconds import build_minute_states
        cut=VALIDATION_START_TIME; vstart=cut-30*86400
        split=pd.read_parquet('data/new_model/research/split.parquet'); excluded=set(split.match_id)
        actual_train=set(pd.read_parquet('data/new_processed/dataset/training_dataset.parquet',columns=['match_id']).match_id)
        actual_val=set(pd.read_parquet('data/new_processed/dataset/validation_dataset.parquet',columns=['match_id']).match_id)
        print('MAIN_SPLIT',len(excluded),'train',len(actual_train),'validation',len(actual_val),'union_equals',excluded==actual_train|actual_val,flush=True)
        print('BOUNDARIES',datetime.fromtimestamp(vstart,timezone.utc).isoformat(),datetime.fromtimestamp(cut,timezone.utc).isoformat(),flush=True)
        files=sorted(Path('data/raw/stratz_matches').glob('*.gz'))
        def scan(f):
         mid=int(f.name.split('_')[1].split('.')[0]);r={'id':mid}
         if mid in excluded:r['drop']='main';return r
         try:x=json.loads(gzip.decompress(f.read_bytes()))
         except Exception as e:r['drop']='read_'+type(e).__name__;return r
         m=(x.get('data') or {}).get('match')
         if not m:r['drop']='empty';return r
         end=m.get('endDateTime');start=m.get('startDateTime');r.update(start=start,end=end,profile=x.get('cache_profile'))
         if not isinstance(end,(int,float)) or not isinstance(start,(int,float)):r['drop']='time';return r
         if end>=cut:r['drop']='after_cutoff';return r
         r['split']='validation' if start>=vstart else 'train'
         if start<vstart<=end:r['drop']='cross_inner_cutoff';return r
         ps=m.get('players') or []
         r['players10']=len(ps)==10
         r['stats_nw']=len(ps)==10 and all(isinstance((p.get('stats') or {}).get('networthPerMinute'),list) for p in ps)
         r['stats_deaths']=len(ps)==10 and all(isinstance((p.get('stats') or {}).get('deathEvents'),list) for p in ps)
         r['stats_level']=len(ps)==10 and all(isinstance((p.get('stats') or {}).get('level'),list) for p in ps)
         r['playback10']=len(ps)==10 and all(bool(p.get('playbackData')) for p in ps)
         r['opendota']=Path(f'data/raw/opendota_matches/match_{mid}.json.gz').exists()
         nw=m.get('radiantNetworthLeads') or [];xp=m.get('radiantExperienceLeads') or []
         reason=None
         if type(m.get('didRadiantWin')) is not bool:reason='outcome'
         elif len(nw)<2 or len(xp)<2:reason='missing_leads'
         elif max(constant_prefix_length(v) for v in [nw,nw[::-1],xp,xp[::-1]])>=5:reason='flat_edge'
         elif len(xp)>len(nw):reason='lead_length_mismatch'
         elif has_reset_tail(nw,xp):reason='reset_tail'
         elif m.get('durationSeconds',0)<=540:reason='short_match'
         elif len(nw)<11:reason='short_nw'
         elif not all(isinstance(v,(int,float)) and math.isfinite(v) for v in nw[1:11]):reason='invalid_nw'
         r['simple_reason']=reason
         r['simple']=reason is None
         r['complex']=False
         if not all(r[k] for k in ['stats_nw','stats_deaths','stats_level']):r['complex_reason']='missing_player_histories'
         elif reason:r['complex_reason']=reason
         else:
          try:
           reason2=stratz_match_unusable_reason(m)
           if reason2:r['complex_reason']=reason2
           else:
            states=build_minute_states(m,600);positive=[s for s in states if s.second>=0]
            if len(positive)!=10:r['complex_reason']='incomplete_minutes'
            else:r['complex']=True;r['complex_reason']=None;r['prehorn']=any(s.second==-60 for s in states)
          except Exception as e:r['complex_reason']=type(e).__name__+': '+str(e)[:120]
         return r
        rows=[]
        with ThreadPoolExecutor(max_workers=8) as pool:
         for i,r in enumerate(pool.map(scan,files),1):
          rows.append(r)
          if i%3000==0:print('SCANNED',i,flush=True)
        Path('/tmp/dota-teacher-cache-audit.json').write_text(json.dumps(rows))
        print('DROPS',dict(Counter(r.get('drop','candidate') for r in rows)))
        candidates=[r for r in rows if 'drop' not in r]
        for key in ['players10','stats_nw','stats_deaths','stats_level','playback10','opendota','simple','complex']:
         print(key,dict(Counter(r['split'] for r in candidates if r.get(key))))
        print('SIMPLE_FAILURES',dict(Counter(r['simple_reason'] for r in candidates if not r['simple'])))
        print('COMPLEX_FAILURES',dict(Counter(r['complex_reason'] for r in candidates if not r['complex'])))
        for key in ['simple','complex']:
         for part in ['train','validation']:
          selected=[r for r in candidates if r[key] and r['split']==part]
          print('RESULT',key,part,'matches',len(selected),'rows',len(selected)*10,'span',*[datetime.fromtimestamp(v,timezone.utc).isoformat() for v in [min(r['start'] for r in selected),max(r['start'] for r in selected)]],'opendota',sum(r['opendota'] for r in selected),'prehorn',sum(r.get('prehorn',False) for r in selected))
        print('OUTPUT /tmp/dota-teacher-cache-audit.json')
        PY
    MAIN_SPLIT 2280 train 1563 validation 717 union_equals True
    BOUNDARIES 2026-05-05T08:59:52+00:00 2026-06-04T08:59:52+00:00
    SCANNED 3000
    SCANNED 6000
    SCANNED 9000
    SCANNED 12000
    SCANNED 15000
    SCANNED 18000
    DROPS {'candidate': 16079, 'main': 2280, 'cross_inner_cutoff': 2, 'after_cutoff': 1008, 'empty': 3}
    players10 {'train': 13891, 'validation': 2188}
    stats_nw {'train': 2663, 'validation': 269}
    stats_deaths {'train': 2663, 'validation': 269}
    stats_level {'train': 2663, 'validation': 269}
    playback10 {'train': 12, 'validation': 748}
    opendota {'train': 2723, 'validation': 285}
    simple {'train': 13281, 'validation': 1965}
    complex {'train': 2651, 'validation': 264}
    SIMPLE_FAILURES {'flat_edge': 17, 'missing_leads': 778, 'reset_tail': 21, 'short_match': 17}
    COMPLEX_FAILURES {'missing_player_histories': 13147, 'reset_tail': 8, 'flat_edge': 9}
    RESULT simple train matches 13281 rows 132810 span 2025-01-01T00:09:05+00:00 2026-05-05T07:05:28+00:00 opendota 2682 prehorn 0
    RESULT simple validation matches 1965 rows 19650 span 2026-05-05T09:03:18+00:00 2026-06-04T08:35:15+00:00 opendota 280 prehorn 112
    RESULT complex train matches 2651 rows 26510 span 2025-10-01T11:02:49+00:00 2026-05-04T19:45:29+00:00 opendota 2651 prehorn 0
    RESULT complex validation matches 264 rows 2640 span 2026-05-05T09:03:18+00:00 2026-06-04T04:59:09+00:00 opendota 264 prehorn 112
    OUTPUT /tmp/dota-teacher-cache-audit.json
    ✓ • 1m 37s

## Assistant

После проверки данных простой вариант даёт **13 281 матч в train и 1 965 в validation**. Полный набор признаков через существующий код — **2 651 и 264** соответственно.

Нашёл ещё 642 подходящих матча с replay, из которого можно попробовать восстановить истории игроков. Почти все они попадают в validation: сейчас проверяю восстановление и совпадение золота с поминутными данными STRATZ.

## Activity

    $ UV_CACHE_DIR=/tmp/codex-uv-cache uv run --no-sync python -u - <<'PY'
        import sys
        sys.path.insert(0,'src')
        import json,gzip
        from pathlib import Path
        from collections import Counter
        from concurrent.futures import ThreadPoolExecutor
        from bisect import bisect_right
        from prepare_dataset.stratz_seconds import build_minute_states
        from shared.utils.stratz import stratz_match_unusable_reason
        rows=json.loads(Path('/tmp/dota-teacher-cache-audit.json').read_text())
        candidates=[r for r in rows if r.get('simple') and r.get('playback10') and not r.get('complex')]
        print('REPLAY_CANDIDATES',len(candidates),dict(Counter(r['split'] for r in candidates)),flush=True)
        def check(r):
         m=json.loads(gzip.decompress(Path(f"data/raw/stratz_matches/match_{r['id']}.json.gz").read_bytes()))['data']['match']
         try:
          for p in m['players']:
           pb=p['playbackData'];gold=sorted(pb['playerUpdateGoldEvents'],key=lambda x:x['time']);times=[e['time'] for e in gold]
           levels=pb['playerUpdateLevelEvents']
           if not levels or [e['level'] for e in levels]!=list(range(1,len(levels)+1)):
            return {**r,'recovered':False,'recovery_reason':'invalid_level_sequence'}
           npm=[]
           for second in range(0,600,60):
            pos=bisect_right(times,second)-1
            if pos<0:return {**r,'recovered':False,'recovery_reason':'missing_gold_at_minute'}
            npm.append(gold[pos]['networth'])
           p['stats']={'networthPerMinute':npm,'level':[e['time'] for e in levels],'deathEvents':[{'time':e['time'],'timeDead':e['timeDead']} for e in pb['deathEvents']]}
          reason=stratz_match_unusable_reason(m)
          if reason:return {**r,'recovered':False,'recovery_reason':reason}
          states=build_minute_states(m,600)
          if len([s for s in states if s.second>=0])!=10:return {**r,'recovered':False,'recovery_reason':'incomplete_minutes'}
          return {**r,'recovered':True,'recovery_reason':None}
         except Exception as e:return {**r,'recovered':False,'recovery_reason':type(e).__name__+': '+str(e)[:90]}
        results=[]
        with ThreadPoolExecutor(max_workers=4) as pool:
         for i,r in enumerate(pool.map(check,candidates),1):
          results.append(r)
          if i%100==0:print('CHECKED',i,flush=True)
        Path('/tmp/dota-teacher-replay-audit.json').write_text(json.dumps(results))
        print('RECOVERED',dict(Counter(r['split'] for r in results if r['recovered'])))
        print('FAILURES',dict(Counter(r['recovery_reason'] for r in results if not r['recovered'])))
        print('RECOVERED_OPENDOTA',dict(Counter(r['split'] for r in results if r['recovered'] and r['opendota'])))
        print('TOTAL_COMPLEX',dict(Counter([r['split'] for r in rows if r.get('complex')]+[r['split'] for r in results if r['recovered']])))
        PY
    REPLAY_CANDIDATES 642 {'train': 8, 'validation': 634}
    CHECKED 100
    CHECKED 200
    CHECKED 300
    CHECKED 400
    CHECKED 500
    CHECKED 600
    RECOVERED {'train': 2, 'validation': 298}
    FAILURES {'invalid_level_sequence': 341, 'missing_gold_at_minute': 1}
    RECOVERED_OPENDOTA {'validation': 10}
    TOTAL_COMPLEX {'train': 2653, 'validation': 562}
    ✓ • 25.07s

## Activity

    $ UV_CACHE_DIR=/tmp/codex-uv-cache uv run --no-sync python - <<'PY'
        import json
        from pathlib import Path
        from collections import Counter
        rows=json.loads(Path('/tmp/dota-teacher-cache-audit.json').read_text());rs=[r for r in rows if r.get('simple')]
        print('simple_profiles',dict(Counter(r['profile'] for r in rs)))
        print('missing_each among_simple', {k:sum(not r[k] for r in rs) for k in ['stats_nw','stats_deaths','stats_level']})
        print('detailed retained',2915/15246,'train retained',2651/13281,'validation retained',264/1965)
        for days in [14,30,60]:
         cutoff=1780563592-days*86400
         print('window_days',days)
         for key in ['simple','complex']:
          selected=[r for r in rows if r.get(key)]
          print(key,'train',sum(r['end']<cutoff for r in selected),'validation',sum(r['start']>=cutoff for r in selected),'cross',sum(r['start']<cutoff<=r['end'] for r in selected))
        PY
    simple_profiles {'stratz_rich_v2': 12331, 'stratz_rich_v3': 2915}
    missing_each among_simple {'stats_nw': 12331, 'stats_deaths': 12331, 'stats_level': 12331}
    detailed retained 0.1911976911976912 train retained 0.19960846321813117 validation retained 0.13435114503816795
    window_days 14
    simple train 14110 validation 1134 cross 2
    complex train 2780 validation 135 cross 0
    window_days 30
    simple train 13281 validation 1965 cross 0
    complex train 2651 validation 264 cross 0
    window_days 60
    simple train 11313 validation 3933 cross 0
    complex train 2277 validation 638 cross 0
    ✓ • 722ms

## Activity

    $ UV_CACHE_DIR=/tmp/codex-uv-cache uv run --no-sync python - <<'PY'
        import json,gzip
        from pathlib import Path
        r=json.loads(Path('/tmp/dota-teacher-replay-audit.json').read_text())
        for x in [r for r in r if r['recovery_reason']=='invalid_level_sequence'][:4]:
         m=json.loads(gzip.decompress(Path(f"data/raw/stratz_matches/match_{x['id']}.json.gz").read_bytes()))['data']['match']
         print('MATCH',x['id'])
         for i,p in enumerate(m['players']):
          es=p['playbackData']['playerUpdateLevelEvents'];seq=[e['level'] for e in es]
          if seq!=list(range(1,len(seq)+1)):print(i,es[:16])
        PY
    MATCH 8644980442
    8 [{'level': 1, 'time': -89}, {'level': 2, 'time': 58}, {'level': 3, 'time': 131}, {'level': 4, 'time': 185}, {'level': 5, 'time': 272}, {'level': 6, 'time': 386}, {'level': 7, 'time': 448}, {'level': 8, 'time': 551}, {'level': 9, 'time': 686}, {'level': 10, 'time': 787}, {'level': 11, 'time': 873}, {'level': 12, 'time': 991}, {'level': 13, 'time': 1131}, {'level': 14, 'time': 1198}, {'level': 15, 'time': 1291}, {'level': 16, 'time': 1391}]
    MATCH 8702016205
    3 [{'level': 1, 'time': -89}, {'level': 2, 'time': 98}, {'level': 3, 'time': 219}, {'level': 4, 'time': 379}, {'level': 5, 'time': 432}, {'level': 6, 'time': 573}, {'level': 7, 'time': 708}, {'level': 8, 'time': 827}, {'level': 9, 'time': 957}, {'level': 10, 'time': 1072}, {'level': 11, 'time': 1192}, {'level': 12, 'time': 1327}, {'level': 13, 'time': 1400}, {'level': 14, 'time': 1641}, {'level': 15, 'time': 1734}, {'level': 16, 'time': 1923}]
    MATCH 8738500753
    1 [{'level': 1, 'time': -89}, {'level': 2, 'time': 83}, {'level': 3, 'time': 183}, {'level': 4, 'time': 372}, {'level': 5, 'time': 442}, {'level': 6, 'time': 505}, {'level': 7, 'time': 571}, {'level': 9, 'time': 682}, {'level': 10, 'time': 789}, {'level': 11, 'time': 854}, {'level': 12, 'time': 955}, {'level': 13, 'time': 1037}, {'level': 14, 'time': 1141}, {'level': 15, 'time': 1170}, {'level': 16, 'time': 1225}, {'level': 17, 'time': 1271}]
    6 [{'level': 1, 'time': -89}, {'level': 2, 'time': 49}, {'level': 3, 'time': 137}, {'level': 4, 'time': 199}, {'level': 5, 'time': 302}, {'level': 6, 'time': 486}, {'level': 7, 'time': 622}, {'level': 8, 'time': 665}, {'level': 9, 'time': 728}, {'level': 10, 'time': 858}, {'level': 11, 'time': 925}, {'level': 12, 'time': 1030}, {'level': 13, 'time': 1150}, {'level': 14, 'time': 1324}, {'level': 15, 'time': 1481}, {'level': 16, 'time': 1691}]
    9 [{'level': 1, 'time': -89}, {'level': 2, 'time': 78}, {'level': 3, 'time': 133}, {'level': 4, 'time': 244}, {'level': 5, 'time': 379}, {'level': 6, 'time': 417}, {'level': 7, 'time': 505}, {'level': 8, 'time': 624}, {'level': 9, 'time': 801}, {'level': 10, 'time': 1022}, {'level': 11, 'time': 1106}, {'level': 13, 'time': 1282}, {'level': 14, 'time': 1371}, {'level': 15, 'time': 1502}, {'level': 16, 'time': 1636}, {'level': 17, 'time': 1728}]
    MATCH 8776961561
    8 [{'level': 1, 'time': -89}, {'level': 2, 'time': 153}, {'level': 3, 'time': 338}, {'level': 4, 'time': 424}, {'level': 5, 'time': 487}, {'level': 6, 'time': 707}, {'level': 8, 'time': 736}, {'level': 9, 'time': 815}, {'level': 10, 'time': 871}, {'level': 11, 'time': 1049}, {'level': 12, 'time': 1416}, {'level': 13, 'time': 1502}, {'level': 14, 'time': 1622}, {'level': 15, 'time': 1669}, {'level': 16, 'time': 1818}, {'level': 17, 'time': 1981}]
    9 [{'level': 1, 'time': -89}, {'level': 2, 'time': 109}, {'level': 3, 'time': 232}, {'level': 4, 'time': 366}, {'level': 5, 'time': 472}, {'level': 6, 'time': 670}, {'level': 7, 'time': 788}, {'level': 8, 'time': 907}, {'level': 9, 'time': 1017}, {'level': 10, 'time': 1070}, {'level': 11, 'time': 1152}, {'level': 12, 'time': 1264}, {'level': 13, 'time': 1443}, {'level': 15, 'time': 1581}, {'level': 16, 'time': 1769}, {'level': 17, 'time': 1928}]
    ✓ • 1.07s

## Activity

    $ UV_CACHE_DIR=/tmp/codex-uv-cache uv run --no-sync python -u - <<'PY'
        import sys
        sys.path.insert(0,'src')
        import json,gzip
        from pathlib import Path
        from collections import Counter
        from concurrent.futures import ThreadPoolExecutor
        from bisect import bisect_right
        from prepare_dataset.stratz_seconds import validate_minute_leads
        rows=json.loads(Path('/tmp/dota-teacher-cache-audit.json').read_text())
        candidates=[r for r in rows if r.get('simple') and r.get('playback10') and not r.get('complex')]
        def check(r):
         m=json.loads(gzip.decompress(Path(f"data/raw/stratz_matches/match_{r['id']}.json.gz").read_bytes()))['data']['match']
         out={'id':r['id'],'split':r['split'],'noxp':False,'xp':False}
         try:
          sides={True:[],False:[]};xp_ok=True
          for p in m['players']:
           pb=p['playbackData'];gold=sorted(pb['playerUpdateGoldEvents'],key=lambda x:x['time']);times=[e['time'] for e in gold];npm=[]
           assert isinstance(pb.get('deathEvents'),list)
           for second in range(0,600,60):
            pos=bisect_right(times,second)-1
            if pos<0:out['reason']='missing_gold';return out
            npm.append(gold[pos]['networth'])
           sides[p['isRadiant']].append(npm)
           levels=[e for e in pb['playerUpdateLevelEvents'] if e['time']<=540]
           xp_ok &= bool(levels) and [e['level'] for e in levels]==list(range(1,len(levels)+1)) and all(a['time']<=b['time'] for a,b in zip(levels,levels[1:]))
          assert len(sides[True])==len(sides[False])==5
          for i,second in enumerate(range(0,600,60)):
           adv=sum(p[i] for p in sides[True])-sum(p[i] for p in sides[False])
           validate_minute_leads(m['id'],second,adv,m['radiantNetworthLeads'])
          out.update(noxp=True,xp=xp_ok,reason=None if xp_ok else 'level_gap_in_0_540');return out
         except Exception as e:out['reason']=type(e).__name__+': '+str(e)[:80];return out
        results=[]
        with ThreadPoolExecutor(max_workers=4) as pool:
         for i,r in enumerate(pool.map(check,candidates),1):
          results.append(r)
          if i%200==0:print('CHECKED',i,flush=True)
        Path('/tmp/dota-teacher-replay-window-audit.json').write_text(json.dumps(results))
        for k in ['xp','noxp']:print('RECOVERY',k,dict(Counter(r['split'] for r in results if r[k])))
        print('FAILURES',dict(Counter(r['reason'] for r in results if r['reason'])))
        PY
    CHECKED 200
    CHECKED 400
    CHECKED 600
    RECOVERY xp {'train': 8, 'validation': 629}
    RECOVERY noxp {'train': 8, 'validation': 633}
    FAILURES {'level_gap_in_0_540': 4, 'missing_gold': 1}
    ✓ • 24.38s

## Assistant

Посчитал весь кэш. **Простая модель получает примерно в пять раз больше обучающих матчей.** Полный набор признаков тоже возможен, но требует существенно меньшей выборки.

Для сравнения зафиксировал одинаковые правила:

- Исключил **2 280 матчей основной модели**: 1 563 train + 717 validation.
- Оставил только матчи, закончившиеся до основной отсечки: **4 июня 2026, 08:59:52 UTC**.
- Собственную validation взял за **последние 30 дней**: с 5 мая до этой отсечки. Длительность пока предлагается для расчёта.
- Проверял снимки на минутах **0–9**, по 10 строк на матч; исключил повреждённые данные, короткие карты и карты, пересекающие внутреннюю границу train/validation.

**Получились такие размеры:**

| Вариант дополнительной модели | Train, матчей | Validation, матчей | Всего |
|---|---:|---:|---:|
| Простая: время + преимущество по золоту | **13 281** | **1 965** | **15 246** |
| Полная: готовые истории игроков, существующий способ расчёта | **2 651** | **264** | **2 915** |
| Полная + восстановление признаков из сохранённого replay | **2 659** | **893** | **3 552** |

В последнем варианте это **26 590 обучающих строк и 8 930 валидационных**. У простой — **132 810 и 19 650** соответственно.

Восстановление из replay я проверил в памяти, без скачивания: оно добавляет **637 матчей**, но почти все — в validation. Поэтому проблему маленького train оно практически не решает.

**Чего именно не хватает**

Игроки в старых файлах **есть**: их герои, стороны, итоговые показатели. У **12 331 из 15 246** пригодных для простой модели матчей отсутствуют три истории игроков:

- золото каждого игрока по минутам — `networthPerMinute`;
- времена смертей — `deathEvents`;
- времена получения уровней — `level`.

Итоговое золото, итоговые смерти и итоговый уровень использовать нельзя: на пятой минуте они ещё неизвестны.

При этом командные массивы преимущества по золоту и опыту в старом STRATZ сохранены. Для простой модели **OpenDota не нужен**. Для полной он тоже не обязателен, если есть нужные истории STRATZ или пригодный replay.

**Какие будут признаки**

| Признак | Простая | Полная |
|---|:---:|:---:|
| Время от горна — `second` | ✓ | ✓ |
| Преимущество Radiant по золоту — `radiant_nw_adv` | ✓ | ✓ |
| Общее золото Radiant и Dire — 2 признака | — | ✓ |
| Преимущество по опыту, рассчитанному из уровней | — | ✓ |
| Смерти Radiant и Dire к этому моменту — 2 признака | — | ✓ |
| Разница золота самых богатых игроков двух сторон | — | ✓ |
| Доля золота самого богатого игрока в каждой команде — 2 признака | — | ✓ |
| **Всего** | **2** | **10** |

Обе модели предсказывают победу Radiant. Рыночных признаков у них нет.

Старый командный XP технически позволяет сделать простую модель с **тремя** признаками. Но это другой показатель, чем нынешний XP из уровней: напрямую подставлять один вместо другого в бэктесте нельзя. Поэтому в сопоставимый простой вариант я его не включал.

Полная версия **без XP** имеет 9 признаков; с восстановлением replay получится **2 659 train / 897 validation** — всего на четыре карты больше.

**Что это означает для эксперимента**

Полный вариант теряет около **80% обучающих матчей**. Взамен он видит распределение золота, смерти и уровни. Насколько это улучшит прогноз, по количеству данных определить нельзя — нужно сравнить обучение.

При этом **основная модель сохранит все свои признаки**: ограничения относятся только к дополнительной модели. Поэтому простой вариант не лишает всю систему информации об игроках.

Я бы сравнил два дополнительных ансамбля: **простой на 13 281 матчах** и **полный на 2 659**, затем отдельно добавил прогноз каждого в основную модель и сравнил их на одной и той же основной validation. Это напрямую проверит, что полезнее: больший старый корпус или более подробное игровое состояние.

## User

Давай, добавим два варианта: простую и полную с восстановлением. У нас будет 2 таких варианта. Мы обучим обе модели.

И на самом деле можем потом прогнать бэктест на них, на обоих. Понял идею? Мы обучим 2 такие модели. Окей, посмотрим на их цифры, как бы, но... Если даже видно, что одна лучше другой, мы все равно...

Ну, или посмотрим, короче. Тут еще нужно подумать, хотели ли мы колебать эти модели. То есть тоже вопрос интересный. Может быть, или как-то нужно добавить какой-то шаг, который будет проверять, хорошо ли они уже откалиброваны или нет, или нужно их откалибровать тоже. На калибровку нужно какой-то еще отложить процент датасета. Нужно, да, наверное, добавим, что мы обучим обе, посмотрим на их цифры. Если цифры будут видно, что, одна я бы лучше другой, то, ну, окей.

Как бы, будем по ней дальше. Если будут они примерно будут равны, то можем их оставить обе и подпродать бэктест на обеих потом. И все.

## Assistant

Да, фиксирую два варианта: простую модель и полную с восстановлением из replay. Обе обучаем и сравниваем до подключения к основной модели. Добавлю проверку калибровки; отдельно определим, нужен ли этап её исправления и как выделить для него данные, не затрагивая основную validation.

## Activity

    $ rg -n 'calibrat|brier|log_loss|binary_logloss|bootstrap' docs/experiments/ensemble docs/experiments/pathwise-calibration src/shared/utils tests/test_train_model.py && cat docs/experiments/ensemble/README.md && sed -n '1,180p' docs/experiments/no-xp/run.py
    tests/test_train_model.py:503:    assert "model_log_loss" not in scenarios.columns
    docs/experiments/pathwise-calibration/analyze.py:1:"""Pathwise calibration of Polymarket mid paths (paper_review.md item C, 2601.18774).
    docs/experiments/pathwise-calibration/analyze.py:3:Under a calibrated bounded martingale ending at 0/1, the losing side's peak
    docs/experiments/pathwise-calibration/analyze.py:9:    make run F=docs/experiments/pathwise-calibration/analyze.py
    docs/experiments/pathwise-calibration/analyze.py:175:def bootstrap_excess(
    docs/experiments/pathwise-calibration/analyze.py:257:    _section(f"{game}: excess tail mass (cluster bootstrap over event_id)")
    docs/experiments/pathwise-calibration/analyze.py:260:        obs, lo, hi, p_excess = bootstrap_excess(stats, x, "loser_peak", seed)
    docs/experiments/pathwise-calibration/analyze.py:261:        obs_ff, lo_ff, hi_ff, p_ff = bootstrap_excess(stats, x, "loser_peak_ff", seed + 1)
    docs/experiments/pathwise-calibration/analyze.py:274:    _section(f"{game}: first-crossing calibration (loss rate vs 1 - x)")
    docs/experiments/pathwise-calibration/README.md:14:    make run F=docs/experiments/pathwise-calibration/analyze.py
    docs/experiments/pathwise-calibration/README.md:28:- Значимость: кластерный bootstrap по event_id, B=5000, CI95 для
    src/shared/utils/market_scenario_report.py:157:def bootstrap_series_cluster_ci(
    src/shared/utils/market_scenario_report.py:227:    mae_gain_300_ci = bootstrap_series_cluster_ci(
    src/shared/utils/market_scenario_report.py:236:    directional_markout_300_ci = bootstrap_series_cluster_ci(
    docs/experiments/ensemble/sub90-20260912/offline_quality_lol.csv:3:bootstrap K=10 20260911T224351Z,0.117310,1.344558,1.081654,42.9840,"13,19,18,27,17,15,19,19,13,16"
    docs/experiments/ensemble/sub90-20260912/seed_metrics_lol.csv:5:bootstrap K=10,0,943,682,2644,1743.65978513,2500.896443710549,757.2366585805493,137136.68336218,232319.446613,0.7505440506814763,1.0764903585004517,688.41172425,-49.44486299104167,-139.27113563999998,2.616459219176644,0.01
    docs/experiments/ensemble/sub90-20260912/seed_metrics_lol.csv:6:bootstrap K=10,1,943,681,2709,1575.6196326900013,2323.3658196044644,747.7461869144632,136669.5758332,230067.58258000002,0.6848507795061134,1.0098623167810157,576.7714141500003,-45.07329671541666,-155.22124255999998,2.5464923742562933,0.01
    docs/experiments/ensemble/sub90-20260912/seed_metrics_lol.csv:7:bootstrap K=10,2,943,692,2699,2247.9224368600007,2996.100594770117,748.1781579101159,137867.83713098,230800.42983100002,0.9739680461193276,1.2981347551925986,654.43665185,-40.737963699999995,-154.78047143999996,2.9818342548771675,0.01
    docs/experiments/ensemble/run.py:1:"""Compare K-member ensembles under bootstrap and 90% subsample.
    docs/experiments/ensemble/run.py:170:    """1.3: nested-bootstrap SD of the ensemble prediction."""
    docs/experiments/ensemble/run.py:392:    """Draw match copies from `population` by slot, so outer-bootstrap dups stay distinct."""
    docs/experiments/ensemble/run.py:498:    """20 match-bootstrap draws of the 1010 train ids, shared across arms."""
    docs/experiments/ensemble/run.py:847:    """1.3 for one arm: 20 outer bootstraps, each a full K-member ensemble."""
    docs/experiments/ensemble/run.py:1022:    """Print the 1.3 nested-bootstrap table."""
    docs/experiments/ensemble/run.py:1491:    """Fit missing ensemble arms through nested bootstrap and print the 1.4 table."""
    docs/experiments/ensemble/run.py:1550:    print("1.3 nested bootstrap")
    docs/experiments/ensemble/sub90-20260912/README.md:5:old single model on maker PnL over seeds 0-2. Against the current bootstrap K=10
    docs/experiments/ensemble/sub90-20260912/README.md:16:the ones the existing research training uses. The bootstrap path in
    docs/experiments/ensemble/sub90-20260912/README.md:60:control. The bootstrap arm is `lol-ens-val` on `20260911T224351Z`. Against both
    docs/experiments/ensemble/sub90-20260912/README.md:68:catalog `20260904T222046Z` replayed on the current inputs; the bootstrap arm is
    docs/experiments/ensemble/sub90-20260912/README.md:96:| sub90 − bootstrap | −83.33 | −121.19 | +298.09 | **+31.19** |
    docs/experiments/ensemble/sub90-20260912/README.md:97:| bootstrap − single | +688.35 | +302.06 | +96.31 | **+362.24** |
    docs/experiments/ensemble/sub90-20260912/README.md:104:| sub90 − bootstrap | −129.12 / +169.76 / −128.10 | +27.47 / −152.32 / +585.14 | +18.32 / −138.62 / −158.95 |
    docs/experiments/ensemble/sub90-20260912/README.md:106:The tool flags a recent conflict on sub90 − bootstrap: the late third has the
    docs/experiments/ensemble/sub90-20260912/README.md:108:one seed on the middle third. Read the Dota sub90-versus-bootstrap result as
    docs/experiments/ensemble/sub90-20260912/README.md:132:| sub90 − bootstrap | +873.59 | +1,301.13 | +715.07 | **+963.26** |
    docs/experiments/ensemble/sub90-20260912/README.md:133:| bootstrap − single | −918.03 | −1,192.63 | −164.20 | **−758.29** |
    docs/experiments/ensemble/sub90-20260912/README.md:142:| sub90 − bootstrap | +43.53 / +304.90 / +211.50 | +478.40 / +578.78 / +323.38 | +351.66 / +417.45 / +180.18 |
    docs/experiments/ensemble/sub90-20260912/README.md:144:sub90 beats the bootstrap ensemble on every seed and in every third. Pooled
    docs/experiments/ensemble/sub90-20260912/README.md:149:and 27% above the bootstrap. Profit per 100 bought shares is below the single
    docs/experiments/ensemble/sub90-20260912/README.md:174:the gate share near the single model. For LoL the bootstrap scheme loses offline
    docs/experiments/ensemble/sub90-20260912/README.md:195:- `dota_sub90_vs_single.txt`, `dota_sub90_vs_bootstrap.txt`, `dota_bootstrap_vs_single.txt`.
    docs/experiments/ensemble/sub90-20260912/README.md:196:- `lol_sub90_vs_single.txt`, `lol_sub90_vs_bootstrap.txt`, `lol_bootstrap_vs_single.txt`.
    docs/experiments/ensemble/sub90-20260912/README.md:197:  The two `bootstrap_vs_single` files cover all 12 seeds those runs hold; the
    docs/experiments/ensemble/sub90-20260912/collect_seed_metrics.py:26:        "bootstrap K=10",
    docs/experiments/ensemble/sub90-20260912/collect_seed_metrics.py:40:        "bootstrap K=10 ens-val454",
    docs/experiments/ensemble/sub90-20260912/offline_quality_dota.csv:3:bootstrap K=10 20260911T162611Z,0.155152,1.310750,1.322578,55.5246,"35,21,28,25,23,41,29,24,25,23"
    docs/experiments/ensemble/sub90-20260912/seed_metrics_dota.csv:5:bootstrap K=10 ens-val454,0,454,364,1316,1191.35300603,1615.4794510861393,424.12644505613923,72693.46400219,126874.421591,0.9390017239806752,1.2732901012103888,768.7580903699999,-45.8386867873913,-95.23390063999997,0.9461606586935204,0.01
    docs/experiments/ensemble/sub90-20260912/seed_metrics_dota.csv:6:bootstrap K=10 ens-val454,1,454,367,1481,1155.87036856,1605.709040676838,449.838672116838,79445.22804199,135958.175411,0.8501661375388551,1.1810316193364598,706.3268490500001,-59.04349511391304,-152.6606,0.9780238498562678,0.01
    docs/experiments/ensemble/sub90-20260912/seed_metrics_dota.csv:7:bootstrap K=10 ens-val454,2,454,365,1338,1124.7697947700008,1560.766303520239,435.99650875023826,75362.42844630001,130749.219544,0.8602497198015708,1.1937098431360094,741.0761906299999,-56.48660589304348,-182.13163839999999,1.053917295411676,0.01
    docs/experiments/ensemble/plan.md:18:Match-bootstrap (`tree-complexity-stability/bootstrap.csv`, 20 перевыборок
    docs/experiments/ensemble/plan.md:84:(63% без возврата — столько же уникальных, сколько у bootstrap, но без
    docs/experiments/ensemble/plan.md:98:### 1.3. Дисперсия: вложенный bootstrap
    docs/experiments/ensemble/plan.md:106:- `row_mean_sd`, `row_p90_sd`, `row_sd_gt_1c` — как в `bootstrap.csv`.
    docs/experiments/ensemble/plan.md:178:Для выбранной руки взять одни и те же 10 внешних bootstrap-перевыборок
    docs/experiments/ensemble/plan.md:185:внешнего bootstrap, для качества каждого K. Использовать те же вложенные
    docs/experiments/ensemble/plan.md:314:bootstrap SD из этапа 1 не задаёт ширину гистерезиса или время ожидания.
    docs/experiments/ensemble/plan.md:323:| E2 | гистерезис | бит «buy открыт» в `StrategyState`; `entry_abs_delta` / `exit_abs_delta` в `Follow300Policy`; `entry_delta_blocked` смотрит порог по состоянию | кандидаты из E1 с проверкой исполнения и издержек; ширина полосы проверяется по временным пересечениям в E0, без условия «полоса ≥ bootstrap SD» |
    docs/experiments/ensemble/plan.md:381:  production-датасете, тот же bootstrap с возвратом по матчам, что у
    docs/experiments/ensemble/plan.md:499:1.3, вложенный bootstrap:
    docs/experiments/ensemble/plan.md:542:`default_boot`, K_max=40, 10 внешних bootstrap (первые 10 из матрицы 1.3).
    docs/experiments/ensemble/plan.md:569:сжатие `|pred|` (1.67¢ → 1.32¢) уже на K=5–10; дальше плато. Один bootstrap-
    docs/experiments/ensemble/plan.md:658:гистерезиса bootstrap SD из этапа 1 не задаёт; кандидаты порога — из E1.
    docs/experiments/ensemble/plan.md:816:Кривая растёт и к K=10 не догоняет одиночную модель. Один bootstrap-член
    docs/experiments/ensemble/plan.md:826:0–540 у 1933 карт, поэтому bootstrap по картам оставляет члену ~63% уникальных
    docs/experiments/ensemble/plan.md:926:и одиночную модель, и bootstrap на всех трёх сидах; на Dota sub90 не отличим
    docs/experiments/ensemble/plan.md:927:от bootstrap и лучше одиночной на всех трёх сидах.
    docs/experiments/ensemble/plan.md:932:`_bootstrap_match_ids` → `_member_match_ids`: `rng.choice` без возврата на
    docs/experiments/ensemble/plan.md:933:`round(n * 0.90)` матчей. `bootstrap_member_frame` → `member_train_frame`.
    docs/experiments/ensemble/plan.md:983:Против bootstrap-ансамбля `ens-val454`: +$227 до rebate, девять сидов из
    docs/experiments/ensemble/plan.md:985:−$18.8. То есть sub90 обыгрывает обе базы по деньгам, но у bootstrap хвост
    docs/experiments/ensemble/plan.md:1004:Это снимает провал bootstrap-ансамбля на LoL: там было −$367 и одиннадцать
    docs/experiments/ensemble/sub90-20260912/score_offline.py:36:    ("bootstrap K=10 20260911T224351Z", "data/lol/models/research"),
    docs/experiments/ensemble/sub90-20260912/score_offline.py:41:    ("bootstrap K=10 20260911T162611Z", "docs/experiments/ensemble/catalog"),
    docs/experiments/ensemble/lol-diagnosis-20260912/diagnose.py:72:bootstrap_ids = rng.integers(0, len(event_names), size=(5000, len(event_names)))
    docs/experiments/ensemble/lol-diagnosis-20260912/diagnose.py:77:    estimates = sums[bootstrap_ids].sum(axis=1) / event_counts[bootstrap_ids].sum(axis=1)
    docs/experiments/ensemble/lol-diagnosis-20260912/README.md:39:Each bootstrap member draws 1,933 maps with replacement but sees only 1,199–1,238 unique maps. Members stop at 13–27 trees (mean 17.6), compared with 26 for the single full-data model. All ten individual members have lower validation MAE gain than the full-data model: 0.053–0.107¢ versus 0.135¢. The average member gain is about 0.087¢; averaging raises it to 0.117¢, still below the full-data fit.
    docs/experiments/ensemble/lol-diagnosis-20260912/README.md:49:Step 3.3 legitimately shows reduced retraining sensitivity: row SD 0.989→0.664¢, map-mean SD 0.590→0.437¢, threshold disagreement 28.6→24.7%. Some of this reduction comes from smaller predictions. Multiplying the single model's outer-bootstrap predictions by the observed amplitude ratio (~0.731) would already reduce row SD to ~0.723¢ and map-mean SD to ~0.431¢. Raw SD alone cannot demonstrate superior information or trading behavior.
    docs/experiments/ensemble/lol-diagnosis-20260912/README.md:51:I also scored the saved 20 outer-bootstrap predictions. The nested ensemble beats the corresponding single bootstrap fit in all 20 cases, with mean MAE gain 0.1067 versus 0.0942¢. Bagging does improve these perturbed fits. However, they are resamples of the same training population, evaluated on the same validation, not new unseen histories; they do not prove the original full-data singleton was lying.
    docs/experiments/ensemble/lol-diagnosis-20260912/README.md:55:The observed offline gain delta (ensemble minus single) is −0.01747¢. A paired event bootstrap gives [−0.03770, +0.00189]¢; the evidence is not a precise estimate of the future difference. Gain is improvement over the 9.874¢ no-move MAE, not profit: a 13% loss of MAE *gain* changes total model MAE by only about 0.18%.
    docs/experiments/ensemble/lol-diagnosis-20260912/README.md:66:| 50/50 single + bootstrap | 0.13190¢ | 1.35955¢ | 1.26163¢ | 48.76% |
    docs/experiments/ensemble/lol-diagnosis-20260912/README.md:69:The sub90 members use 20–37 trees, mean 26.3. This result is consistent with the bootstrap scheme removing too much useful map coverage for these LoL models. It does not separately identify the contribution of reduced unique coverage, duplicated weights, and earlier stopping.
    docs/experiments/ensemble/lol-diagnosis-20260912/README.md:71:Sub90's gain advantage over the singleton is only +0.00547¢, with paired event-bootstrap interval [−0.00272, +0.01348]¢. Treat it as preservation of offline quality, not an established win. Its stability under repeated training samples and maker PnL have not been measured.
    docs/experiments/ensemble/lol-diagnosis-20260912/README.md:73:Increasing the bootstrap amplitude helps only partially; ×1.37 remains below the old model, and ×2 drops MAE gain to 0.09311¢. Lowering an entry gate would not restore the fair values used for exits. These are diagnostic multipliers examined on the same history, not chosen production settings.
    cat: docs/experiments/ensemble/README.md: No such file or directory
    ✗ (1) • 267ms

## Activity

    $ UV_CACHE_DIR=/tmp/codex-uv-cache uv run --no-sync python - <<'PY'
        import json
        from pathlib import Path
        from datetime import datetime,UTC
        from collections import Counter
        rows=json.loads(Path('/tmp/dota-teacher-cache-audit.json').read_text())
        recovered=json.loads(Path('/tmp/dota-teacher-replay-window-audit.json').read_text())
        full_ids={r['id'] for r in rows if r.get('complex')}|{r['id'] for r in recovered if r.get('xp')}
        end=1780563592
        for days in [30,20,15,10]:
         boundary=end-days*86400
         print('BOUNDARY',days,datetime.fromtimestamp(boundary,UTC).isoformat())
         for label,ids in [('simple',{r['id'] for r in rows if r.get('simple')}),('full',full_ids)]:
          selected=[r for r in rows if r['id'] in ids and r.get('split')=='validation']
          early=sum(r['end']<boundary for r in selected);late=sum(r['start']>=boundary for r in selected);cross=len(selected)-early-late
          print(label,'earlier',early,'later',late,'cross',cross)
        PY
    BOUNDARY 30 2026-05-05T08:59:52+00:00
    simple earlier 0 later 1965 cross 0
    full earlier 0 later 893 cross 0
    BOUNDARY 20 2026-05-15T08:59:52+00:00
    simple earlier 509 later 1455 cross 1
    full earlier 115 later 778 cross 0
    BOUNDARY 15 2026-05-20T08:59:52+00:00
    simple earlier 771 later 1193 cross 1
    full earlier 151 later 742 cross 0
    BOUNDARY 10 2026-05-25T08:59:52+00:00
    simple earlier 1121 later 842 cross 2
    full earlier 334 later 558 cross 1
    ✓ • 318ms

## Assistant

Проверку калибровки добавим обязательно: например, среди прогнозов «60%» Radiant должен выигрывать примерно в 60% случаев.

Я бы сначала проверял её без автоматического исправления: прогноз будет признаком другой модели, а лучшая точность вероятности победы ещё не гарантирует лучший результат торговли. Для честного сравнения предлагаю разделить собственную validation: первые 15 дней — остановка деревьев, последние 15 — оценка качества и калибровки. В последней части есть 742 матча, доступных обеим моделям; сравнивать их будем на этих одинаковых матчах.

## Assistant

Зафиксировал: калибровку только проверяем. Явно худший вариант отсекаем, а при неубедительной разнице оставляем оба.

Критерий отсева задам заранее: сравниваем ошибки вероятностей на одинаковых отложенных матчах, учитывая, что строки одной карты зависимы. Одной небольшой разницы в средней метрике для отсева будет недостаточно. Проверяю последние детали подключения, чтобы план сохранял текущую логику основного обучения и бэктеста.

## Activity

    $ rg -n 'noxp|NOXP|model_dir|xp' src/backtest/replay_inputs.py src/backtest/archive_join.py src/archive_index/schedule.py src/backtest/run.py | head -110 && sed -n '1,180p' src/backtest/replay_inputs.py && sed -n '1130,1230p' src/backtest/run.py && sed -n '1,110p' docs/experiments/no-xp/run.py && sed -n '150,195p' src/shared/utils/market_scenario_report.py
    rg: src/backtest/archive_join.py: No such file or directory (os error 2)
    src/backtest/replay_inputs.py:54:# zero fills is expected until a second Oddin map is captured — see
    src/backtest/replay_inputs.py:115:        raise StrictJsonError(f"{label}: unexpected {', '.join(extra)}")
    src/backtest/replay_inputs.py:181:def _model_inputs(model_dir: Path, cache: dict[Path, ModelInputs]) -> ModelInputs:
    src/backtest/replay_inputs.py:182:    cached = cache.get(model_dir)
    src/backtest/replay_inputs.py:185:    meta = read_model_meta(model_dir / MODEL_META_FILENAME)
    src/backtest/replay_inputs.py:186:    loaded = ModelInputs(name=meta["name"], sha256=model_identity_sha256(model_dir))
    src/backtest/replay_inputs.py:187:    cache[model_dir] = loaded
    src/backtest/replay_inputs.py:206:    model = _model_inputs(plan.model_dir, models)
    src/archive_index/schedule.py:61:    "radiant_xp_adv",
    src/archive_index/schedule.py:107:    radiant_xp_adv: int
    src/archive_index/schedule.py:228:        radiant_xp_adv=snapshot.radiant_xp_adv,
    src/backtest/run.py:34:from prediction_market_extensions.backtesting._experiments import (
    src/backtest/run.py:36:    build_backtest_for_experiment,
    src/backtest/run.py:37:    build_replay_experiment,
    src/backtest/run.py:244:    model_dir: Path
    src/backtest/run.py:290:    values["expiration_ns"] = datetime_to_ns(closed_at)
    src/backtest/run.py:317:    """Expose the wrapped event's instrument so Nautilus can route custom data."""
    src/backtest/run.py:343:    """Give every loaded leg its real expiration, zero engine fee, and one clock boundary."""
    src/backtest/run.py:556:    experiment = build_replay_experiment(
    src/backtest/run.py:583:    backtest = build_backtest_for_experiment(experiment)
    src/backtest/run.py:592:    results = cast(list[ReplayInstrumentResult], _finalize_replay_results(experiment, raw_results))
    src/backtest/run.py:618:            experiment = build_replay_experiment(
    src/backtest/run.py:635:            backtest = build_backtest_for_experiment(experiment)
    src/backtest/run.py:697:    model_dir: Path,
    src/backtest/run.py:711:    model_meta = read_model_meta(model_dir / MODEL_META_FILENAME)
    src/backtest/run.py:714:        "experiment": EXPERIMENT_NAME,
    src/backtest/run.py:737:        "model_path": str(model_dir),
    src/backtest/run.py:738:        "model_sha256": model_identity_sha256(model_dir),
    src/backtest/run.py:749:            str(plan_model_dir): model_identity_sha256(plan_model_dir)
    src/backtest/run.py:750:            for plan_model_dir in sorted({plan.model_dir for plan in plans.values()}, key=str)
    src/backtest/run.py:810:        raise ValueError(f"run-dir frac token expects (0, 1), got {value}")
    src/backtest/run.py:1080:    expected_names = {
    src/backtest/run.py:1088:        f"{shard_dir.name}: unexpected entry {entry.name}"
    src/backtest/run.py:1090:        if entry.name not in expected_names
    src/backtest/run.py:1215:def _pin_cli_model_dir(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    src/backtest/run.py:1217:    if args.model_dir is None:
    src/backtest/run.py:1219:    pinned = args.model_dir.expanduser()
    src/backtest/run.py:1222:    args.model_dir = pinned.resolve()
    src/backtest/run.py:1230:def _pin_dota_experiment_flags(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    src/backtest/run.py:1231:    """Reject LoL use of Dota experiment flags and resolve --validation-dataset."""
    src/backtest/run.py:1245:        validation_dataset = args.validation_dataset.expanduser()
    src/backtest/run.py:1367:    _pin_dota_experiment_flags(parser, args)
    src/backtest/run.py:1368:    _pin_cli_model_dir(parser, args)
    src/backtest/run.py:1417:            model_dir=LOL_RESEARCH_MODEL_DIR,
    src/backtest/run.py:1437:        model_dir=RESEARCH_MODEL_DIR,
    src/backtest/run.py:1672:    model_dir: Path,
    src/backtest/run.py:1697:                model_dir,
    src/backtest/run.py:1772:        selection.archive_join, selection.selected_ids, args.model_dir, args.match_id
    src/backtest/run.py:1788:    if args.model_dir is not None:
    src/backtest/run.py:1789:        selection = replace(selection, model_dir=args.model_dir)
    src/backtest/run.py:1796:    model_dir = selection.model_dir
    src/backtest/run.py:1833:        model_dir=model_dir,
    src/backtest/run.py:1889:        model_dir=model_dir,
    """Fingerprints of what feeds a smoke golden, taken from the resolved feed plan.
    
    The goldens pin a result. The plan already names the model directory, and for an
    archive match the schedule file. `resolve_plan_dataset_path` names the parquet
    `resolve_run_signals` reads. Hashing those is the fingerprint: a new map does
    not add a field. Book captures under `data/raw/telonex` stay unhashed.
    """
    
    from collections.abc import Mapping
    from dataclasses import dataclass
    from pathlib import Path
    from typing import Literal
    
    from backtest.feed_schedules import (
        MatchFeedPlan,
        SchedulePlan,
        assert_archive_binding,
        resolve_feed_plans,
    )
    from backtest.run import load_run_selection
    from backtest.signals import BacktestGame, resolve_plan_dataset_path
    from shared.constants import strategy as strategy_constants
    from shared.utils.gbm import model_identity_sha256
    from shared.utils.hashing import sha256_file
    from shared.utils.json_io import read_json
    from shared.utils.model_registry import MODEL_META_FILENAME, read_model_meta
    from trader.strict_json import (
        StrictJsonError,
        require_int,
        require_list,
        require_object,
        require_str,
    )
    
    SMOKE_INPUTS_FILENAME = "inputs.json"
    _SMOKE_KEYS = frozenset({"feeds", "strategy_constants_sha256"})
    _MODEL_KEYS = frozenset({"name", "sha256"})
    _GRID_FEED_KEYS = frozenset({"game", "kind", "match_id", "model", "signals_sha256"})
    _SCHEDULE_FEED_KEYS = frozenset(
        {"features_sha256", "game", "kind", "match_id", "model", "schedule_sha256"}
    )
    
    
    @dataclass(frozen=True)
    class SmokeMap:
        """One golden map and the archive its feed plan must bind. Empty means grid-v1."""
    
        game: BacktestGame
        match_id: int
        archive_id: str
    
    
    # Three Dota grid-v1 maps, one Dota Oddin archive (schedule ticks, no-XP model;
    # zero fills is expected until a second Oddin map is captured — see
    # EMPTY_FILL_SMOKE_MATCHES in tests/test_follow300_replay.py), two LoL grid-v1
    # maps; the second LoL map settles a leftover position.
    SMOKE_MAPS = (
        SmokeMap(game="dota", match_id=8837869969, archive_id=""),
        SmokeMap(game="dota", match_id=8911784562, archive_id=""),
        SmokeMap(game="dota", match_id=8933879286, archive_id=""),
        SmokeMap(game="dota", match_id=9007208887, archive_id="9007208887"),
        SmokeMap(game="lol", match_id=115564793879469302, archive_id=""),
        SmokeMap(game="lol", match_id=116634566264113530, archive_id=""),
    )
    
    
    @dataclass(frozen=True)
    class ModelInputs:
        """One model catalog a replay predicts with."""
    
        name: str
        sha256: str
    
    
    @dataclass(frozen=True)
    class GridFeedInputs:
        """Fingerprints a grid-v1 plan: its model and the validation parquet."""
    
        kind: Literal["grid_v1"]
        match_id: int
        game: BacktestGame
        model: ModelInputs
        signals_sha256: str
    
    
    @dataclass(frozen=True)
    class ScheduleFeedInputs:
        """Fingerprints a schedule plan: its model, feature parquet, and schedule file."""
    
        kind: Literal["schedule"]
        match_id: int
        game: BacktestGame
        model: ModelInputs
        features_sha256: str
        schedule_sha256: str
    
    
    FeedInputs = GridFeedInputs | ScheduleFeedInputs
    
    
    @dataclass(frozen=True)
    class SmokeInputs:
        """Strategy knobs plus one fingerprint per smoke map, in `SMOKE_MAPS` order."""
    
        strategy_constants_sha256: str
        feeds: tuple[FeedInputs, ...]
    
    
    def _require_key_set(fields: Mapping[str, object], keys: frozenset[str], label: str) -> None:
        missing = sorted(keys.difference(fields))
        extra = sorted(set(fields).difference(keys))
        if missing:
            raise StrictJsonError(f"{label}: missing {', '.join(missing)}")
        if extra:
            raise StrictJsonError(f"{label}: unexpected {', '.join(extra)}")
    
    
    def _decode_game(fields: Mapping[str, object], label: str) -> BacktestGame:
        game = require_str(fields, "game", label)
        if game == "dota":
            return "dota"
        if game == "lol":
            return "lol"
        raise StrictJsonError(f"{label} 'game' must be dota or lol")
    
    
    def _decode_model(value: object, label: str) -> ModelInputs:
        fields = require_object(value, label)
        _require_key_set(fields, _MODEL_KEYS, label)
        return ModelInputs(
            name=require_str(fields, "name", label),
            sha256=require_str(fields, "sha256", label),
        )
    
    
    def _decode_grid(fields: Mapping[str, object], label: str) -> GridFeedInputs:
        return GridFeedInputs(
            kind="grid_v1",
            match_id=require_int(fields, "match_id", label),
            game=_decode_game(fields, label),
            model=_decode_model(fields["model"], f"{label}.model"),
            signals_sha256=require_str(fields, "signals_sha256", label),
        )
    
    
    def _decode_schedule(fields: Mapping[str, object], label: str) -> ScheduleFeedInputs:
        return ScheduleFeedInputs(
            kind="schedule",
            match_id=require_int(fields, "match_id", label),
            game=_decode_game(fields, label),
            model=_decode_model(fields["model"], f"{label}.model"),
            features_sha256=require_str(fields, "features_sha256", label),
            schedule_sha256=require_str(fields, "schedule_sha256", label),
        )
    
    
    def _decode_feed(value: object, index: int) -> FeedInputs:
        label = f"feeds[{index}]"
        fields = require_object(value, label)
        kind = require_str(fields, "kind", label)
        if kind == "grid_v1":
            _require_key_set(fields, _GRID_FEED_KEYS, label)
            return _decode_grid(fields, label)
        if kind == "schedule":
            _require_key_set(fields, _SCHEDULE_FEED_KEYS, label)
            return _decode_schedule(fields, label)
        raise StrictJsonError(f"{label} 'kind' must be grid_v1 or schedule")
    
    
    def load_smoke_inputs(path: Path) -> SmokeInputs:
        """Read a captured smoke fingerprint. A missing or mistyped field names itself."""
        payload = require_object(read_json(path), str(path))
        _require_key_set(payload, _SMOKE_KEYS, "smoke inputs")
        feeds = require_list(payload, "feeds", "smoke inputs")
        return SmokeInputs(
            strategy_constants_sha256=require_str(payload, "strategy_constants_sha256", "smoke inputs"),
            feeds=tuple(_decode_feed(item, index) for index, item in enumerate(feeds)),
        )
    
    
        return MergedCheckpoints(
            results=tuple(merged_results),
            fills=tuple(fills),
            shard_dirs=tuple(shard_dirs),
        )
    
    
    def load_replay_lookups(match_ids: Sequence[int], sources: MarketSources) -> ReplayLookups:
        """Load pauses, market contexts, and mid series for the given match ids."""
        return ReplayLookups(
            pauses_by_match={match_id: sources.catalog[match_id].pauses for match_id in match_ids},
            context_by_match={
                match_id: build_market_context(sources, match_id) for match_id in match_ids
            },
            mids=load_match_mid_series(tuple(match_ids)),
        )
    
    
    def load_dota_selection(
        match_id: int | None,
        limit: int | None,
        *,
        match_ids: tuple[int, ...] | None = None,
        validation_dataset: Path = VALIDATION_DATASET_PATH,
    ) -> DotaSelection:
        """Load Dota validation rows and pick replayable matches."""
        signal_rows = load_usable_signal_rows(validation_dataset)
        sources = load_market_sources(signal_rows)
        capture_root = RAW_TELONEX_POLYMARKET_DIR
        report_root = BACKTESTS_DIR / EXPERIMENT_NAME
        if match_ids is not None:
            return DotaSelection(
                selected_ids=tuple(int(mid) for mid in match_ids),
                coverage=None,
                capture_root=capture_root,
                report_root=report_root,
                signal_rows=signal_rows,
                sources=sources,
            )
        if match_id is not None:
            return DotaSelection(
                selected_ids=(int(match_id),),
                coverage=None,
                capture_root=capture_root,
                report_root=report_root,
                signal_rows=signal_rows,
                sources=sources,
            )
        coverage, eligible = select_validation_matches(sources)
        selected_ids = eligible if limit is None else eligible[: int(limit)]
        return DotaSelection(
            selected_ids=selected_ids,
            coverage=coverage,
            capture_root=capture_root,
            report_root=report_root,
            signal_rows=signal_rows,
            sources=sources,
        )
    
    
    def finalize_from_checkpoint(
        *,
        results: Sequence[MakerMatchResult],
        fills: Sequence[EnrichedFill],
        lookups: ReplayLookups,
        coverage: ValidationCoverage | None,
        selected: int,
        manifest: Mapping[str, Any],
        report_dir: Path,
        wall_seconds: float,
    ) -> tuple[str, ...]:
        """Write summary.json from checkpoint rows without replaying."""
        return write_finished_summary(
            results=results,
            fills=fills,
            context_by_match=lookups.context_by_match,
            mids=lookups.mids,
            coverage=coverage,
            selected=selected,
            wall_seconds=wall_seconds,
            manifest=manifest,
            report_dir=report_dir,
        )
    
    
    def _pin_cli_model_dir(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
        """Resolve --model-dir and reject a catalog without model.json."""
        if args.model_dir is None:
            return
        pinned = args.model_dir.expanduser()
        if not (pinned / MODEL_META_FILENAME).is_file():
            parser.error(f"--model-dir has no {MODEL_META_FILENAME}: {pinned}")
        args.model_dir = pinned.resolve()
    
    
    def _reject_negative(parser: argparse.ArgumentParser, value: float | None, flag: str) -> None:
        if value is not None and value < 0:
            parser.error(f"{flag} must be >= 0")
    
    
    def _pin_dota_experiment_flags(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    """Fit the live Dota ensemble without radiant_xp_adv. Does not publish.
    
    PYTHONPATH=src uv run python docs/experiments/no-xp/run.py
    """
    
    import shutil
    from dataclasses import dataclass
    
    import lightgbm as lgb
    import numpy as np
    import numpy.typing as npt
    import pandas as pd
    
    from shared.constants.dataset import TRAIN_LAG_SECONDS
    from shared.constants.paths import (
        DATA_DIR,
        RESEARCH_MODEL_DIR,
        TRAINING_DATASET_PATH,
        VALIDATION_DATASET_PATH,
    )
    from shared.utils.gbm import (
        FEATURE_COLUMNS,
        GbmPredictor,
        HoldoutWindowStats,
        _fit_ensemble_members,
        build_ensemble_model_meta,
        build_model_metrics,
        build_price_delta_labels,
        fit_ensemble_member_booster,
        load_training_dataset,
        predict_future_prices,
        write_ensemble_members,
    )
    from shared.utils.hashing import sha256_file
    from shared.utils.market_scenario_report import (
        build_scenario_stats,
        full_window_scenario_stats,
        write_scenario_csv,
    )
    from shared.utils.model_registry import read_model_meta, write_model_meta
    from train_model.market_metrics import evaluate_validation_metrics
    from train_model.train_model import (
        build_validation_dataset_rows,
        load_validation_dataset,
        select_validation_fit_frame,
        select_validation_prediction_frame,
        slice_train_rows,
    )
    
    DROPPED_FEATURE = "radiant_xp_adv"
    NO_XP_FEATURES = tuple(column for column in FEATURE_COLUMNS if column != DROPPED_FEATURE)
    CATALOG_DIR = DATA_DIR / "experiments" / "no-xp" / "catalog"
    
    
    @dataclass(frozen=True, eq=False)
    class NoXpMemberFit:
        """Research member fit on every live feature except XP advantage."""
    
        valid_X: pd.DataFrame
        valid_y: npt.NDArray[np.float64]
        features: tuple[str, ...]
    
        def __call__(self, train: pd.DataFrame) -> lgb.Booster:
            """Fit one subsampled member with early stopping on the holdout set."""
            columns = list(self.features)
            return fit_ensemble_member_booster(
                train[columns],
                build_price_delta_labels(train),
                self.valid_X,
                self.valid_y,
            )
    
    
    def lagged_features(frame: pd.DataFrame, columns: tuple[str, ...]) -> pd.DataFrame:
        """Copy `columns` with second set to the snapshot clock (T - train lag)."""
        return frame[list(columns)].assign(second=frame["second"] - TRAIN_LAG_SECONDS)
    
    
    def main() -> None:
        """Fit K=10 without XP, write a loadable catalog, print holdout vs live json."""
        if DROPPED_FEATURE not in FEATURE_COLUMNS:
            raise SystemExit(f"{DROPPED_FEATURE} is not a live feature")
        if len(NO_XP_FEATURES) != len(FEATURE_COLUMNS) - 1:
            raise SystemExit("expected to drop exactly one live feature")
    
        train = slice_train_rows(load_training_dataset(TRAINING_DATASET_PATH))
        validation = load_validation_dataset(VALIDATION_DATASET_PATH)
        fit_frame = select_validation_fit_frame(validation)
        pred_frame = select_validation_prediction_frame(validation)
        fit_features = lagged_features(fit_frame, NO_XP_FEATURES)
        fit_target = build_price_delta_labels(fit_frame)
        pred_features = lagged_features(pred_frame, NO_XP_FEATURES)
        pred_current = pred_frame["market_p_radiant"].to_numpy(dtype=np.float64)
        train_matches = int(train["match_id"].nunique())
        print(
            f"no-xp: drop {DROPPED_FEATURE} | features {list(NO_XP_FEATURES)} | "
            f"train {len(train)} rows / {train_matches} matches | "
            f"holdout fit {len(fit_frame)} pred {len(pred_frame)}",
            flush=True,
        )
        boosters = _fit_ensemble_members(
            train,
            NoXpMemberFit(fit_features, fit_target, NO_XP_FEATURES),
        )
        member_trees = tuple(booster.num_trees() for booster in boosters)
        for booster in boosters:
            names = tuple(booster.feature_name())
            if names != NO_XP_FEATURES:
                raise SystemExit(f"booster features {names} != {NO_XP_FEATURES}")
        print(f"no-xp: member_trees={list(member_trees)}", flush=True)
            totals_by_event[event_id] = SeriesMetricTotals(
                numerator_sum=previous.numerator_sum + value,
                row_count=previous.row_count + 1,
            )
        return tuple(totals_by_event.values())
    
    
    def bootstrap_series_cluster_ci(
        series_totals: Sequence[SeriesMetricTotals],
    ) -> MetricConfidenceInterval | None:
        """Bootstrap row-weighted metric bounds by resampling event series."""
        if any(total.row_count < 0 for total in series_totals):
            raise ValueError("series row counts must be non-negative")
    
        positive_series = [total for total in series_totals if total.row_count > 0]
        if not positive_series:
            return None
    
        numerators = np.asarray(
            [total.numerator_sum for total in positive_series],
            dtype=np.float64,
        )
        counts = np.asarray(
            [total.row_count for total in positive_series],
            dtype=np.float64,
        )
        rng = np.random.default_rng(BOOTSTRAP_SEED)
        sampled_indices = rng.integers(
            0,
            len(positive_series),
            size=(BOOTSTRAP_REPLICATES, len(positive_series)),
        )
        replicate_numerators = numerators[sampled_indices].sum(axis=1)
        replicate_counts = counts[sampled_indices].sum(axis=1)
        replicate_estimates = replicate_numerators / replicate_counts
        percentiles = np.asarray(
            np.percentile(replicate_estimates, [2.5, 97.5]),
            dtype=np.float64,
        )
        return MetricConfidenceInterval(
            low=float(percentiles[0]),
            high=float(percentiles[1]),
        )
    
    
    def aggregate_scenario_bucket(
    ✓ • 588ms

## Activity

    $ UV_CACHE_DIR=/tmp/codex-uv-cache uv run --no-sync python - <<'PY'
        import json
        from pathlib import Path
        from collections import Counter
        rows=json.loads(Path('/tmp/dota-teacher-cache-audit.json').read_text());rec=json.loads(Path('/tmp/dota-teacher-replay-window-audit.json').read_text());full={r['id'] for r in rows if r.get('complex')}|{r['id'] for r in rec if r.get('xp')}
        b=1780563592-15*86400
        for name,ids in [('simple',{r['id'] for r in rows if r.get('simple')}),('full',full)]:
         c=Counter()
         for r in rows:
          if r['id'] not in ids:continue
          if r['split']=='train':c['train']+=1
          elif r['end']<b:c['early_stop']+=1
          elif r['start']>=b:c['evaluation']+=1
          else:c['cross_boundary']+=1
         print(name,dict(c))
        PY
    simple {'train': 13281, 'early_stop': 771, 'cross_boundary': 1, 'evaluation': 1193}
    full {'train': 2659, 'early_stop': 151, 'evaluation': 742}
    ✓ • 652ms
