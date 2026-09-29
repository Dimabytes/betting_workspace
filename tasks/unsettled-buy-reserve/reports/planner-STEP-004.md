# planner4: STEP-004
Status: FINAL

Plan: `plans/STEP-004.md:1`.

Prepared final activation plan covering HTTP atomic outcome/reserve writes, WS cancellation without matched-quantity proof, core order/record ownership, BUY/SELL event routing, and gone book filtering. Requirements: `feature.json:134`, `feature.json:135`, `feature.json:136`.

Covered WS-before-HTTP and zero-proof retirement races, full fills during HTTP await, FAILED/SUPERSEDED accounting, workerless settlement, restart/attach, operational notes, and local verification. Sources: `feature.json:137`, `feature.json:138`, `feature.json:140`, `feature.json:141`, `feature.json:142`, `feature.json:145`, `00-context.md:25`.

Planning only: no code changes, implementation tests, commit, push, SSH, or deployment. Code scope and final landed baseline: `00-context.md:12`, `00-context.md:14`, `00-context.md:15`, `progress.txt:62`, `progress.txt:67`.
