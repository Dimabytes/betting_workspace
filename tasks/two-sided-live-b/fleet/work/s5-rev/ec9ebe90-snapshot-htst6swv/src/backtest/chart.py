"""Box-drawing equity chart for the terminal report."""

from collections.abc import Sequence

CHART_HEIGHT = 40


def _axis_through_start(lo: float, hi: float, start: float, height: int) -> tuple[float, float]:
    """Expand [lo, hi] so `start` lands on a tick of `height` rows."""
    span = hi - lo
    if span < 1e-12:
        return start - 1.0, start + 1.0
    from_bottom = round((start - lo) / span * (height - 1))
    from_bottom = min(height - 1, max(0, from_bottom))
    from_top = height - 1 - from_bottom
    step_up = (hi - start) / from_top if from_top > 0 else 0.0
    step_down = (start - lo) / from_bottom if from_bottom > 0 else 0.0
    step = max(step_up, step_down, 1e-9)
    return start - from_bottom * step, start + from_top * step


def _draw_step(grid: list[list[str]], column: int, from_row: int, to_row: int) -> None:
    """Draw one column of the path: a dash, or corners facing each other over a riser."""
    if from_row == to_row:
        grid[from_row][column] = "─"
        return
    if from_row > to_row:
        grid[from_row][column] = "╯"
        grid[to_row][column] = "╭"
    else:
        grid[from_row][column] = "╮"
        grid[to_row][column] = "╰"
    for row in range(min(from_row, to_row) + 1, max(from_row, to_row)):
        grid[row][column] = "│"


def render_balance_chart(values: Sequence[float], starting_capital: float) -> str:
    """Box-drawing equity path; Y ticks count from the starting-capital row."""
    if not values:
        return ""
    height = CHART_HEIGHT
    lo = min(min(values), starting_capital)
    hi = max(max(values), starting_capital)
    # The axis always spans at least one full step, so step is never zero here.
    ymin, ymax = _axis_through_start(lo, hi, starting_capital, height)
    step = (ymax - ymin) / (height - 1)

    def row_of(value: float) -> int:
        return (height - 1) - min(height - 1, max(0, round((value - ymin) / step)))

    start_row = row_of(starting_capital)

    rows_at = [row_of(value) for value in values]
    grid = [[" "] * len(values) for _ in range(height)]
    for column in range(len(values)):
        grid[start_row][column] = "─"
    for column in range(len(values) - 1):
        _draw_step(grid, column, rows_at[column], rows_at[column + 1])
    if grid[rows_at[-1]][-1] == " ":
        grid[rows_at[-1]][-1] = "─"

    return "\n".join(
        f"  {starting_capital + (start_row - row) * step:8.2f} "
        f"{'┼' if row == start_row else '┤'}{''.join(grid[row])}"
        for row in range(height)
    )
