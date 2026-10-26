"""S9: both iframes show the goal factual card at 67' (deterministic path, no LLM)."""
import pytest

from secondlook.runner import RunConfig

pytestmark = pytest.mark.e2e


def test_both_viewers_show_goal_factual_card(serve, browser):
    url = serve(RunConfig(speed="120x"), autostart=False)
    page = browser.new_page(viewport={"width": 1500, "height": 900})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(url + "/")
    page.wait_for_selector("#start:not([hidden])")
    an = page.frame_locator("#analyst")
    fan = page.frame_locator("#fan")
    an.locator("#status").filter(has_text="Live feed").wait_for(timeout=10_000)
    page.click("#start")
    an_card = an.locator('#log [data-overlay-id="ov-m001-067-goal-factual-analyst-en"]')
    fan_card = fan.locator('#log [data-overlay-id="ov-m001-067-goal-factual-fan-es"]')
    an_card.wait_for(timeout=30_000)
    fan_card.wait_for(timeout=30_000)
    assert "Goal for Ironvale: Vask, 66:54. Score 1–1." in an_card.inner_text()
    assert "¡Gol de Ironvale! Marcó Vask. 1–1." in fan_card.inner_text()
    assert an.locator("#viewTitle").inner_text() != fan.locator("#viewTitle").inner_text()
    assert "Synthetic match" in an.locator("#synthetic").inner_text()
    assert "Partido sintético" in fan.locator("#synthetic").inner_text()
    an.locator("body[data-ended='1']").wait_for(timeout=30_000)
    assert an.locator("#score").inner_text() == "1 – 1"
    assert fan.locator("#score").inner_text() == "1 – 1"
    # the pitch really drew: not a single flat colour
    colours = page.frame(url=lambda u: "analyst-en" in u).evaluate("""() => {
      const c = document.getElementById('pitch'); const d = c.getContext('2d')
        .getImageData(0, 0, c.width, c.height).data; const s = new Set();
      for (let i = 0; i < d.length; i += 4000) s.add(d[i] + ',' + d[i+1] + ',' + d[i+2]);
      return s.size; }""")
    assert colours > 4
    assert errors == []
    page.close()


def test_transparent_mode_hides_chrome(serve, browser):
    url = serve(RunConfig(speed="max"))
    page = browser.new_page()
    page.goto(url + "/viewer.html?profile=fan-es&transparent=1")
    page.locator("body[data-ended='1']").wait_for(timeout=30_000)
    assert not page.locator("canvas").is_visible()
    assert not page.locator(".side").is_visible()
    page.close()
