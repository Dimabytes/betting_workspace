"""Replay the archive-bound GRID Dota matches under synthetic grid-v1 signals.

Same argv as `python -m backtest.run`. Every SchedulePlan with a GRID feed
becomes a GridV1Plan (seeded cadence, synthetic board kills and kill gates);
every other match is excluded. Own-order stripping keeps the real archive
dirs, so the book stays the one the schedule replay saw. Answers whether
grid-v1 reproduces the schedule result on the same maps.

    cd ../esports-trader && PYTHONPATH=src:../prediction-market-backtesting \
      uv run --group backtest python <this file> --game dota --validation --name X ...
"""

from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path

from backtest import run
from backtest.feed_schedules import FeedPlanResult, GridV1Plan, MatchFeedPlan, SchedulePlan

ARCHIVE_DIRS: dict[int, Path] = {}
original_resolve = run.resolve_feed_plans


def resolve_archive_as_grid(*args: object, **kwargs: object) -> FeedPlanResult:
    feed = original_resolve(*args, **kwargs)  # type: ignore[arg-type]
    plans: dict[int, MatchFeedPlan] = {}
    exclusions = dict(feed.exclusions)
    for match_id, plan in feed.plans.items():
        if isinstance(plan, SchedulePlan) and plan.binding.feed_source == "grid":
            ARCHIVE_DIRS[match_id] = plan.binding.archive_dir
            plans[match_id] = GridV1Plan(match_id=match_id, model_dir=plan.model_dir)
        else:
            exclusions[match_id] = "not_archive_grid"
    return replace(feed, plans=plans, exclusions=exclusions)


def keep_archive_dirs(plans: Mapping[int, MatchFeedPlan]) -> dict[int, Path]:
    return {match_id: ARCHIVE_DIRS[match_id] for match_id in plans}


run.resolve_feed_plans = resolve_archive_as_grid
run.schedule_archive_dirs = keep_archive_dirs

if __name__ == "__main__":
    run.main()
