#!/bin/zsh
# usage: wait.sh <report-basename>...   blocks until every report has Status: FINAL
source ${0:A:h}/env.sh
for f in "$@"; do until grep -q '^Status: FINAL' $R/reports/$f.md 2>/dev/null; do sleep 30; done; done; sleep 20; echo DONE "$@"
