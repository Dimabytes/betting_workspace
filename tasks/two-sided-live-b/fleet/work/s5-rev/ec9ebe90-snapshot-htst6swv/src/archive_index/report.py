"""Archive index report: headline counts and the markdown artifact."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from archive_index.schedule import ADMISSION_RULES_VERSION, EXTRACTION_RULES_VERSION

REPORT_FILENAME = "report.md"


@dataclass(frozen=True)
class IndexSummary:
    """The headline counts the report prints; link counts stay apart from fit."""

    dirs_scanned: int
    dirs_without_meta: int
    meta_ok: int
    meta_unreadable: int
    identity_ok: int
    feed_ok: int
    record_ok: int
    admitted: int
    schedules_published: int
    unique_match_ids: int
    unique_condition_ids: int


def format_report(
    roots: Mapping[str, Path],
    out_dir: Path,
    frame: pd.DataFrame,
    summary: IndexSummary,
) -> str:
    """Render report.md: link counts first, then the three verdicts, then rejects."""
    lines = [
        "# Archive index report",
        "",
        f"generated: {datetime.now(UTC).isoformat(timespec='seconds')}",
        f"rules: {EXTRACTION_RULES_VERSION} / {ADMISSION_RULES_VERSION}",
        f"roots: {', '.join(f'{label}={path}' for label, path in roots.items())}",
        f"out: {out_dir}",
        "",
        "## Totals",
        "",
        f"- dirs scanned: {summary.dirs_scanned}",
        f"- dirs without match.json (ignored): {summary.dirs_without_meta}",
        f"- archives audited: {len(frame)}",
        f"- match.json ok: {summary.meta_ok}",
        f"- match.json missing/unreadable: {summary.meta_unreadable}",
        "",
        "## Links (not suitability)",
        "",
        "An established identity is a link, not a ready match: feed and record",
        "verdicts below decide admission.",
        "",
        f"- unique match_ids: {summary.unique_match_ids}",
        f"- unique condition_ids: {summary.unique_condition_ids}",
        f"- identity established: {summary.identity_ok}",
        "",
        "## Admission",
        "",
        f"- feed ok: {summary.feed_ok}",
        f"- record ok (terminal): {summary.record_ok}",
        f"- admitted (all three verdicts): {summary.admitted}",
        f"- schedules published: {summary.schedules_published}",
        "",
        "## Verdicts",
        "",
        "| admission | archives |",
        "|---|---|",
    ]
    counts = frame["admission"].value_counts().sort_index()
    for verdict, count in counts.items():
        lines.append(f"| {verdict} | {count} |")
    lines += ["", "## Rejections", ""]
    rejected = frame[frame["admission"] != "admitted"]
    if rejected.empty:
        lines.append("none")
    else:
        for _, row in rejected.sort_values(["admission", "archive_id"]).iterrows():
            detail = _first_detail(row)
            lines.append(f"- `{row['archive_id']}` — {row['admission']} {detail}".rstrip())
    lines.append("")
    return "\n".join(lines)


def _first_detail(row: pd.Series) -> str:
    """The first non-empty detail string on a rejected index row."""
    for column in ("meta_error", "identity_detail", "feed_detail", "record_detail"):
        value = row[column]
        if isinstance(value, str) and value:
            return value
    return ""


def write_report(out_dir: Path, content: str) -> None:
    """Atomically write report.md."""
    out_dir.mkdir(parents=True, exist_ok=True)
    tmp = out_dir / f".{REPORT_FILENAME}.tmp"
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(out_dir / REPORT_FILENAME)
