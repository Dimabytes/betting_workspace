"""Fixture-backed dashboard launcher for browser verification (STEP-004).

Run: PYTHONPATH=src:tests uv run --group dashboard streamlit run <this file>
Patches dashboard.live_hub.get_live_hub before app.py executes so the app's
cached _hub() returns the fixture hub. Control file: work/s4-impl/control.json —
write {"balance": "fail"} / {"balance": "ok"} / {"disconnect_no": true} /
{"xss_map": true} / {"fetch_error": "..." | null} to flip fixture state live.
"""

import json
import runpy
import sys
import tempfile
import threading
import time
from dataclasses import replace
from pathlib import Path

ROOT = Path("/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader")
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

import streamlit as st  # noqa: E402

import dashboard_fixture as fx  # noqa: E402
import dashboard.live_hub as live_hub_module  # noqa: E402
from dashboard.balance import BalanceRead  # noqa: E402
from dashboard.live_hub import PRODUCTION_TIMING  # noqa: E402

WORK = Path(
    "/Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace"
    "/tasks/live-trader-dashboard/run/work/s4-impl"
)
CONTROL = WORK / "control.json"
COUNTERS = WORK / "counters.jsonl"

TIMING = replace(
    PRODUCTION_TIMING,
    day_s=15.0,
    logs_s=10.0,
    balance_min_s=2.0,
    balance_period_s=10.0,
)


@st.cache_resource
def _world() -> dict[str, object]:
    fixture = fx.enable_balance(
        fx.build(Path(tempfile.mkdtemp(prefix="s4-fixture-")), timing=TIMING)
    )
    hub = fixture.start_hub()
    fx.insert_session(fixture, qty=4.0)
    fx.wait_for(lambda: len(fixture.books) > 0)
    fx.emit_books(
        fixture,
        (
            fx.make_book(fx.YES, bid=0.55, ask=0.57, cid=fx.CID),
            fx.make_book(fx.NO, bid=0.50, ask=0.52, cid=fx.CID),
        ),
    )
    world: dict[str, object] = {"fixture": fixture, "hub": hub, "applied": set()}
    threading.Thread(target=_control_loop, args=(world,), daemon=True).start()
    threading.Thread(target=_counter_loop, args=(world,), daemon=True).start()
    return world


def _apply(world: dict[str, object], cmd: dict) -> None:
    fixture = world["fixture"]
    applied = world["applied"]
    state = fixture.state
    with (WORK / "applied.log").open("a") as fh:
        fh.write(json.dumps({"t": time.time(), "cmd": cmd}) + "\n")
    if cmd.get("balance") == "fail" and "bal_fail" not in applied:
        applied.add("bal_fail")
        state.balance = BalanceRead(
            ok=False,
            collateral_usdc=None,
            error="fixture outage",
            status_code=None,
            retry_after_s=None,
        )
    if cmd.get("balance") == "ok":
        applied.discard("bal_fail")
        state.balance = BalanceRead(
            ok=True,
            collateral_usdc=100.0,
            error=None,
            status_code=None,
            retry_after_s=None,
        )
    if cmd.get("disconnect_no") and "dc_no" not in applied:
        applied.add("dc_no")
        fx.emit_books(
            fixture,
            (
                replace(fx.make_book(fx.NO, bid=0.50, ask=0.52, cid=fx.CID), connected=False),
            ),
        )
    if cmd.get("reconnect_no") and "dc_no" in applied:
        applied.discard("dc_no")
        fx.emit_books(
            fixture,
            (fx.make_book(fx.NO, bid=0.50, ask=0.52, cid=fx.CID),),
        )
    if cmd.get("disconnect_yes") and "dc_yes" not in applied:
        applied.add("dc_yes")
        fx.emit_books(
            fixture,
            (replace(fx.make_book(fx.YES, bid=0.55, ask=0.57, cid=fx.CID), connected=False),),
        )
    if cmd.get("reconnect_yes") and "dc_yes" in applied:
        applied.discard("dc_yes")
        fx.emit_books(
            fixture,
            (fx.make_book(fx.YES, bid=0.56, ask=0.58, cid=fx.CID),),
        )
    if cmd.get("xss_map") and "xss" not in applied:
        applied.add("xss")
        fx.write_match(
            fixture.live_root / "m-xss",
            "m-xss",
            cid="0xxss",
            yes="y-xss",
            no="n-xss",
            teams=("<img src=x onerror=alert(1)>", "Team & <script>"),
        )
    if "fetch_error" in cmd:
        state.fetch_error = cmd["fetch_error"]
        applied.add("fetch_err")
    if cmd.get("fetch_ok"):
        state.fetch_error = None
    if "fetch_sleep" in cmd:
        state.fetch_sleep_s = float(cmd["fetch_sleep"])


def _control_loop(world: dict[str, object]) -> None:
    while True:
        try:
            if CONTROL.exists():
                cmd = json.loads(CONTROL.read_text())
                CONTROL.unlink()
                if isinstance(cmd, dict):
                    _apply(world, cmd)
        except Exception as exc:
            with (WORK / "applied.log").open("a") as fh:
                fh.write(json.dumps({"t": time.time(), "error": repr(exc)}) + "\n")
        time.sleep(0.5)


def _counter_loop(world: dict[str, object]) -> None:
    fixture = world["fixture"]
    hub = world["hub"]
    while True:
        try:
            snap = hub.get_snapshot()
            row = {
                "t": round(time.time(), 2),
                "published_at": snap.published_at,
                "books": len(fixture.books),
                "conn": {b.token_id: b.connected for b in snap.books},
                **fixture.counters,
            }
            with COUNTERS.open("a") as fh:
                fh.write(json.dumps(row) + "\n")
        except Exception as exc:
            with COUNTERS.open("a") as fh:
                fh.write(json.dumps({"t": time.time(), "error": str(exc)}) + "\n")
        time.sleep(1.0)


_world_state = _world()
_fixture = _world_state["fixture"]
_hub = _world_state["hub"]
_applied = _world_state["applied"]
live_hub_module.get_live_hub = lambda: _hub  # type: ignore[method-assign]

runpy.run_path(str(ROOT / "src" / "dashboard" / "app.py"), run_name="__main__")
