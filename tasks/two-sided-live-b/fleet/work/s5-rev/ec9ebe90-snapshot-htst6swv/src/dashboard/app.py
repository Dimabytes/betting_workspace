import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import streamlit as st

from dashboard.home_diag import SellWatcher, build_diagnostics
from dashboard.home_lists import build_lists
from dashboard.home_strip import build_strip
from dashboard.home_view import inject_chrome, render_active, render_lists, render_status
from dashboard.live_hub import LiveHub, get_live_hub
from dashboard.match_page import render_match_page

if __name__ == "__main__":
    st.set_page_config(page_title="esports-trader", layout="wide")


@st.cache_resource(on_release=lambda hub: hub.close())
def _hub() -> LiveHub:
    return get_live_hub()


@st.cache_resource
def _sell_watcher() -> SellWatcher:
    return SellWatcher()


@st.fragment(run_every=1)
def _fast() -> None:
    snap = _hub().get_snapshot()
    now_s = time.time()
    render_status(build_strip(snap, now_s), build_diagnostics(snap, _sell_watcher(), now_s), now_s)
    ready = snap.subscriptions.catalog_built_at is not None
    active = () if not ready else build_lists(snap, now_s).active
    render_active(active, loading=not ready)


@st.fragment(run_every=5)
def _lists() -> None:
    snap = _hub().get_snapshot()
    ready = snap.subscriptions.catalog_built_at is not None
    view = None if not ready else build_lists(snap, time.time())
    render_lists(view, loading=not ready)


if __name__ == "__main__":
    inject_chrome()
    _match = st.query_params.get("match")
    if _match:
        render_match_page(_match, _hub())
    else:
        _fast()
        _lists()
