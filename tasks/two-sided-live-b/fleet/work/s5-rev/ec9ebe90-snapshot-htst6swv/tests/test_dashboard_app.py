from collections.abc import Generator
from pathlib import Path

import dashboard_fixture as fx
import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

import dashboard.live_hub as live_hub_module
import dashboard.match_view as match_view
from dashboard.live_hub import LiveHub

APP = str(Path(__file__).resolve().parents[1] / "src" / "dashboard" / "app.py")

World = tuple[fx.DashboardFixture, LiveHub]


@pytest.fixture
def world(tmp_path: Path) -> Generator[World]:
    fixture = fx.enable_balance(fx.build(tmp_path))
    hub = fixture.start_hub()
    try:
        fx.wait_for(
            lambda: any(view.entry.match_id == "m-live" for view in hub.get_snapshot().maps)
        )
        fx.wait_for(lambda: hub.get_snapshot().health.ok)
        yield fixture, hub
    finally:
        hub.close()
        live_hub_module.close_live_hub()


def _noop(*args: object, **kwargs: object) -> None:
    pass


def _patch_hub(monkeypatch: pytest.MonkeyPatch, hub: LiveHub) -> None:
    monkeypatch.setattr(live_hub_module, "get_live_hub", lambda: hub)
    monkeypatch.setattr(match_view, "render_fresh", _noop)
    st.cache_resource.clear()


def _run_app(monkeypatch: pytest.MonkeyPatch, hub: LiveHub) -> AppTest:
    _patch_hub(monkeypatch, hub)
    return AppTest.from_file(APP).run()


def test_home_renders_all_sections(monkeypatch: pytest.MonkeyPatch, world: World) -> None:
    _, hub = world
    at = _run_app(monkeypatch, hub)
    heads = [h.value for h in at.subheader]
    assert heads == [
        "Торгуем сейчас",
        "Закрытые сегодня",
        "Скоро",
        "Остатки завершённых карт",
    ]
    markdowns = [m.value for m in at.markdown]
    frames = "\n".join(str(frame.value) for frame in at.dataframe)
    assert any("class='sb'" in value for value in markdowns)
    assert any("system state" in value for value in markdowns)
    assert not any("зелёное, всё хорошо" in value for value in markdowns)
    assert any("доступно" in value for value in markdowns)
    assert not any("риск чист" in value for value in markdowns)
    assert not any("детали" in value for value in markdowns)
    assert "Team Radiant vs Team Dire" in frames
    assert "только запись" in frames
    labels = [e.label for e in at.expander]
    assert any("доступен redeem" in label for label in labels)
    assert len(at.dataframe) >= 1


def test_match_route_renders_detail(monkeypatch: pytest.MonkeyPatch, world: World) -> None:
    _, hub = world
    _patch_hub(monkeypatch, hub)
    at = AppTest.from_file(APP)
    at.query_params["match"] = "m-live"
    at.run()
    markdowns = [m.value for m in at.markdown]
    assert any("Team Radiant vs Team Dire · карта 1" in m for m in markdowns)
    assert any("сейчас:" in m for m in markdowns)
    assert any("mp-decision" in m for m in markdowns)
    assert any(m == "**позиция**" for m in markdowns)
    captions = [c.value for c in at.caption]
    assert any("mp-status" in value and ">live<" in value for value in markdowns)
    assert not any("YES =" in value for value in captions)
    assert not any("статус " in value for value in captions)
    assert not any(value.startswith("диагностика:") for value in captions)
    labels = [e.label for e in at.expander]
    assert "график" not in labels
    assert "лента событий" not in labels
    assert "детали" not in labels


def test_match_route_unknown(monkeypatch: pytest.MonkeyPatch, world: World) -> None:
    _, hub = world
    _patch_hub(monkeypatch, hub)
    at = AppTest.from_file(APP)
    at.query_params["match"] = "no-such-match"
    at.run()
    assert any("не найдена" in w.value for w in at.warning)


def test_lists_refresh_reads_new_snapshot(monkeypatch: pytest.MonkeyPatch, world: World) -> None:
    fixture, hub = world
    at = _run_app(monkeypatch, hub)
    assert not any("Natus" in e.label for e in at.expander)
    archive = fx.write_match(
        fixture.live_root / "m-fresh",
        "m-fresh",
        cid="0xfresh",
        yes="y-f",
        no="n-f",
        teams=("Natus", "Vincere"),
    )
    fx.write_journal(
        archive,
        [
            {"kind": "session_start", "execution_mode": "live", "schema_version": 7},
            {
                "kind": "signal",
                "second": 10.0,
                "reason": "min_delta",
                "model_evaluated": False,
                "recorded_at_utc": fx.utc_now_iso(),
                "feed_received_at_utc": fx.utc_now_iso(),
                "game_snapshot": {"second": 10.0, "phase": "playing", "paused": False},
            },
        ],
    )
    fx.wait_for(lambda: any(v.entry.match_id == "m-fresh" for v in hub.get_snapshot().maps))
    at.run()
    frames = "\n".join(str(frame.value) for frame in at.dataframe)
    assert "Natus" in frames
