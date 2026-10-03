# Brief: glm-hist — independent check of LoL history-feature parity across train, backtest and live

Do `briefs/hist-dev.md` as agent `glm-hist`, independently: do not read `reports/hist-dev.md` or `work/hist-dev/` until your own numbers are in your report (then you may add a short "comparison" section listing agreements and disagreements with numbers).

Your report: `reports/glm-hist.md`. Scratch: `work/glm-hist/`.

Priority order if time is short: brief item 1 (invalid-tick counts per month in the backtest tick generation and in archived live schedules), item 3 (importance vs NaN/staleness of the 60 history columns), item 2 (value parity dense vs received tape and the resulting delta/threshold/direction flips), then items 4 and 5.
