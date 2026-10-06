#!/bin/zsh
# usage: impl.sh <s#> <STEP-ID> <root-pane>
source ${0:A:h}/env.sh
S=$1; ID=$2
git -C $ET rev-parse HEAD > $O/$S-base
sed -e "s/STEP-XXX/$ID/g" -e "s/sX-impl/$S-impl/g" -e "s|\$RUN|$R|g" $R/briefs/impl-template.md > $R/briefs/$S-impl.md
$O/spawn.sh $S-impl $3 right swe-max
