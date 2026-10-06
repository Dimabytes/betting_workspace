#!/usr/bin/env python3
"""Browser acceptance pass for the STEP-004 fixture dashboard.

Drives the live Streamlit app at 127.0.0.1:8501 (fixture launcher) with
Playwright. Prints PASS/FAIL lines and saves screenshots into the work dir.
"""

import json
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

WORK = Path(
    "/Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace"
    "/tasks/live-trader-dashboard/run/work/s4-impl"
)
CONTROL = WORK / "control.json"
BASE = "http://127.0.0.1:8501"

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    RESULTS.append((name, ok, detail))
    print(("PASS" if ok else "FAIL"), "-", name, ("| " + detail) if detail else "")
    return ok


def ctl(**cmd: object) -> None:
    CONTROL.write_text(json.dumps(cmd))
    time.sleep(1.2)


def fv_elements(page):
    out = []
    for frame in page.frames:
        try:
            out.extend(frame.locator(".fv").all())
        except Exception:
            continue
    return out


def fv_states(page):
    states: dict[str, int] = {}
    for el in fv_elements(page):
        try:
            state = el.get_attribute("data-state") or "?"
            states[state] = states.get(state, 0) + 1
        except Exception:
            continue
    return states


def fv_by_state(page, state):
    for el in fv_elements(page):
        try:
            if el.get_attribute("data-state") == state:
                return el
        except Exception:
            continue
    return None


def wait_text(page, text, timeout=25000):
    page.wait_for_selector(f"text={text}", timeout=timeout)


def wait_state(page, state, timeout=40):
    deadline = time.time() + timeout
    while time.time() < deadline:
        el = fv_by_state(page, state)
        if el is not None:
            return el
        time.sleep(0.6)
    return None


def main() -> int:
    with sync_playwright() as p:
        browser = p.chromium.launch(
            executable_path=str(
                Path.home()
                / "Library/Caches/ms-playwright/chromium_headless_shell-1228"
                / "chrome-headless-shell-mac-arm64/chrome-headless-shell"
            )
        )
        ctx = browser.new_context(viewport={"width": 1366, "height": 768})
        page = ctx.new_page()
        page.goto(BASE)
        wait_text(page, "Торгуем сейчас")
        page.wait_for_timeout(3000)

        # -- layout / sections ------------------------------------------------
        heads = [t.strip() for t in page.locator("h3").all_inner_texts()]
        check(
            "four sections",
            heads
            == [
                "Торгуем сейчас",
                "Скоро / не торгуем",
                "Закрытые сегодня",
                "Остатки завершённых карт",
            ],
            str(heads),
        )
        check(
            "diagnostics line",
            "диагностика:" in page.locator("body").inner_text(),
        )
        page.screenshot(path=str(WORK / "pw-home-1366.png"))

        details = page.locator("details")
        labels = [d.locator("summary").inner_text() for d in details.all()]
        check(
            "active row",
            any("Team Radiant" in s and "карта 1" in s for s in labels),
            str(labels),
        )
        check("idle rows", any("только запись" in s for s in labels))
        check("residual row", any("redeem" in s for s in labels))
        check(
            "closed grid",
            page.locator('[role="gridcell"]')
            .filter(has_text="Team Radiant")
            .count()
            >= 1,
        )

        # -- status component DOM ----------------------------------------------
        els = fv_elements(page)
        check("status components >= 4", len(els) >= 4, f"{len(els)}")
        aria = [el.get_attribute("aria-live") for el in els]
        check("aria-live polite", all(a == "polite" for a in aria), str(aria))
        states = fv_states(page)
        check(
            "fresh + no_data states",
            states.get("fresh", 0) >= 1 and states.get("no_data", 0) >= 1,
            str(states),
        )
        nd = fv_by_state(page, "no_data")
        check(
            "no_data dash text",
            nd is not None and "—" in nd.inner_text() and "Нет данных" in nd.inner_text(),
            nd.inner_text() if nd is not None else "none",
        )

        # dot identity across fragment ticks
        dot_el = fv_by_state(page, "fresh") or els[0]
        try:
            dot_el.evaluate("el=>{window.__dot = el.querySelector('.fv-dot')}")
            page.wait_for_timeout(3200)
            same = dot_el.evaluate(
                "el=>el.querySelector('.fv-dot') === window.__dot"
            )
        except Exception as exc:
            same = False
            check("dot DOM persistent", same, f"eval error {exc}")
        else:
            check("dot DOM persistent", same)

        # -- routes -------------------------------------------------------------
        page.goto(BASE + "/?match=m-live")
        page.wait_for_timeout(2500)
        h1 = page.locator("h1")
        check(
            "match route title",
            h1.count() >= 1
            and "Team Radiant vs Team Dire · карта 1" in h1.first.inner_text(),
            h1.first.inner_text() if h1.count() else "no h1",
        )
        page.goto(BASE + "/?match=no-such")
        page.wait_for_timeout(2500)
        check(
            "unknown match warning",
            "не найдена" in page.locator("body").inner_text(),
        )
        home_link = page.locator("a", has_text="на главную").first
        check(
            "home link href",
            home_link.get_attribute("href") == "/",
            str(home_link.get_attribute("href")),
        )
        try:
            with ctx.expect_page(timeout=5000) as new_page_info:
                home_link.click()
            home_tab = new_page_info.value
            home_tab.wait_for_selector("text=Торгуем сейчас", timeout=25000)
            check("home return new tab", True, home_tab.url)
            home_tab.close()
        except Exception:
            wait_text(page, "Торгуем сейчас")
            check("home return same tab", "match" not in page.url, page.url)

        page.goto(BASE + "/")
        wait_text(page, "Торгуем сейчас")
        page.wait_for_timeout(2000)

        links = page.locator('a[href*="polymarket.com"]')
        check(
            "polymarket href",
            links.count() >= 1,
            links.first.get_attribute("href") if links.count() else "",
        )

        # -- balance fail -> stale + retained numbers -> recover -----------------
        ctl(balance="fail")
        stale_el = wait_state(page, "stale", timeout=30)
        check("balance fail -> stale", stale_el is not None)
        texts = " | ".join(el.inner_text() for el in fv_elements(page))
        check("retained number on fail", "$100.00" in texts, texts[:200])
        ctl(balance="ok")
        deadline = time.time() + 30
        states = fv_states(page)
        while time.time() < deadline:
            states = fv_states(page)
            if states.get("stale", 0) == 0 and states.get("fresh", 0) >= 1:
                break
            page.wait_for_timeout(800)
        check("balance recover -> fresh", states.get("stale", 0) == 0, str(states))

        # -- disconnected book -> book_stale mark ---------------------------------
        for d in page.locator("details").all():
            try:
                d.evaluate("el=>el.open=true")
            except Exception:
                continue
        page.wait_for_timeout(1500)
        cells = page.locator('[role="gridcell"]').all_inner_texts()
        quiet_book = "book" in cells
        check("connected book mark", quiet_book, " | ".join(cells[:60]))
        ctl(disconnect_yes=True)
        page.goto(BASE + "/")
        wait_text(page, "Торгуем сейчас")
        for d in page.locator("details").all():
            try:
                d.evaluate("el=>el.open=true")
            except Exception:
                continue
        deadline = time.time() + 80
        cells = []
        while time.time() < deadline:
            cells = page.locator('[role="gridcell"]').all_inner_texts()
            if "book_stale" in cells:
                break
            page.wait_for_timeout(1000)
        check(
            "book_stale mark", "book_stale" in cells, " | ".join(cells[:40])
        )
        ctl(reconnect_yes=True)
        page.goto(BASE + "/")
        wait_text(page, "Торгуем сейчас")
        for d in page.locator("details").all():
            try:
                d.evaluate("el=>el.open=true")
            except Exception:
                continue
        deadline = time.time() + 80
        cells = []
        while time.time() < deadline:
            cells = page.locator('[role="gridcell"]').all_inner_texts()
            if "book" in cells:
                break
            page.wait_for_timeout(1000)
        check(
            "reconnect mark usable",
            "book" in cells and "book_stale" not in cells,
            " | ".join(cells[:40]),
        )

        # -- updating state + pulse + reduced motion ------------------------------
        ctl(fetch_sleep=25)
        upd_el = wait_state(page, "updating", timeout=45)
        check("updating state", upd_el is not None, str(fv_states(page)))
        if upd_el is not None:
            anim = upd_el.locator(".fv-dot").evaluate(
                "el=>getComputedStyle(el).animationName"
            )
            check("updating pulse animation", anim != "none", anim)
            page.emulate_media(reduced_motion="reduce")
            page.wait_for_timeout(500)
            anim2 = upd_el.locator(".fv-dot").evaluate(
                "el=>getComputedStyle(el).animationName"
            )
            check("reduced-motion no animation", anim2 == "none", anim2)
            page.emulate_media(reduced_motion="no-preference")
        ctl(fetch_sleep=0)

        # -- xss map -------------------------------------------------------------
        ctl(xss_map=True)
        deadline = time.time() + 160
        found = False
        while time.time() < deadline:
            if "<img" in page.locator("body").inner_text():
                found = True
                break
            page.wait_for_timeout(2000)
        check("xss text visible (escaped)", found)
        injected = page.locator("img[onerror]").count()
        check("xss not injected", injected == 0, f"{injected}")

        # -- second tab shares the hub -------------------------------------------
        page2 = ctx.new_page()
        page2.goto(BASE)
        wait_text(page2, "Торгуем сейчас")
        check(
            "second tab same sections",
            "Остатки завершённых карт" in page2.locator("body").inner_text(),
        )
        st2 = fv_states(page2)
        deadline = time.time() + 30
        while time.time() < deadline and sum(st2.values()) < 4:
            page2.wait_for_timeout(800)
            st2 = fv_states(page2)
        check("second tab metrics", sum(st2.values()) >= 4, str(st2))
        page2.screenshot(path=str(WORK / "pw-second-tab.png"))
        page2.close()

        # -- 1440x900 -------------------------------------------------------------
        ctx2 = browser.new_context(viewport={"width": 1440, "height": 900})
        p1440 = ctx2.new_page()
        p1440.goto(BASE)
        wait_text(p1440, "Торгуем сейчас")
        p1440.wait_for_timeout(2500)
        p1440.screenshot(path=str(WORK / "pw-home-1440.png"))
        check(
            "1440 render",
            "диагностика:" in p1440.locator("body").inner_text(),
        )

        check(
            "quiet book mark usable",
            quiet_book,
            "YES mark stayed 'book' while quiet",
        )
        browser.close()

    fails = [r for r in RESULTS if not r[1]]
    print(f"\n{len(RESULTS) - len(fails)}/{len(RESULTS)} passed")
    for name, ok, detail in fails:
        print("FAIL:", name, "|", detail)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
