# PGL delay from two local captures, 2026-09-19

Inputs supplied by the user: `/Users/dimabytes/Desktop/pgl`,
`/Users/dimabytes/Desktop/steam`, `/Users/dimabytes/Desktop/hawk`.

The Steam capture is `scripts/watch_top_live.py` / GetTopLiveGame, **not**
GetRealtimeStats. Its advertised `tv_delay=900s` is not applied to the captured
GetTopLiveGame clock. The source publishes sparse snapshots and the watcher polls
every 5 seconds. Hawk contains matching clock/score/lead states, usually received
earlier than this Steam polling capture; treat this as an additional delivery
comparison, not independent proof of server time.

## Method

For each newly observed Steam game second, find the PGL log entry with exactly
the same game second and match ID. Compute PGL receipt minus Steam receipt.
All eight accepted pairs are exact clock matches; no interpolation was needed.
Exclude initial cached snapshots, repeated clocks (the Hokori 19:02 heartbeat),
and seconds outside PGL recording coverage (Hokori 21:04). Do not compare latest
displayed clocks at arbitrary wall times: sparse reference updates bias that
measurement. All timestamps below are the capture's local wall-clock strings.

| Match | Clock | Steam receipt | PGL receipt | Difference, seconds |
|---|---|---|---|---:|
| 9005981988, Nemesis–1w | 31:50 | 11:23:41.085 | 11:24:07.912 | 26.827 |
| 9005981988 | 32:21 | 11:24:11.145 | 11:24:38.920 | 27.775 |
| 9005981988 | 32:51 | 11:24:40.965 | 11:25:08.930 | 27.965 |
| 9006010487, Hokori–NaVi | 18:41 | 11:32:29.810 | 11:32:56.058 | 26.248 |
| 9006010487 | 19:02 | 11:32:49.657 | 11:33:17.059 | 27.402 |
| 9006010487 | 19:41 | 11:33:29.661 | 11:33:56.060 | 26.399 |
| 9006010487 | 20:04 | 11:33:55.011 | 11:34:19.062 | 24.051 |
| 9006010487 | 20:41 | 11:34:29.918 | 11:34:56.071 | 26.153 |

Nemesis–1w mean: **27.522s**, n=3. Hokori–NaVi mean: **26.051s**, n=5.
Pooled mean: **26.602s**, median **26.613s**, range **24.051–27.965s**.

PGL minus Hawk on exact matching clocks, excluding initial HTML snapshots:

- Nemesis–1w: 28.579s, n=1 (33:19).
- Hokori–NaVi: 29.525, 29.532, 29.529, 30.469, 29.490s; mean 29.709s, n=5.
- Pooled: mean **29.521s**, median **29.527s**, range **28.579–30.469s**.

## Interpretation and experiment choice

Use **30 seconds as the main assumed PGL lag**, with 25 seconds as a sensitivity
case. Compare train10/execute30 against train30/execute30 on the same held-out
maps and execution schedule; these captures do not establish that retraining
helps. This is a recommendation for an experiment, not a measured universal
PGL configuration or a production model change.

Limitations: two short late-game recording windows (PGL clocks 27:48–33:19 and
18:13–20:49), sparse reference observations, clock quantization, no independent
server timestamp, and no per-player Steam features. Clock-relative delay does
not establish exact net-worth/level/death feature latency. The PGL diagnostic
prints GameState and accumulated PlayerStats, which can be updated separately.
Score-change arrival alone is unsuitable: Steam can batch several kills and
repeat old scores while advancing its clock. Do not mechanically add the
training constant 10s to this relative measurement; it is not a measured delay
of the GetTopLiveGame capture.

Reproduction for this session: `/private/tmp/analyze_pgl_delay_20260919.py`;
parsed records, accepted/excluded pairs and summaries in
`/private/tmp/pgl-delay-analysis-20260919/`.
