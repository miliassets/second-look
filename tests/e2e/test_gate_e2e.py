"""S15 GATE: goal -> two viewers, end to end, locally, fake LLM. `make e2e`."""
import pytest

from secondlook.runner import RunConfig

pytestmark = pytest.mark.e2e


def test_goal_factual_then_explanation_in_both_iframes(serve, browser, tmp_path):
    url = serve(RunConfig(speed="120x", llm="fake", state_dir=str(tmp_path / ".state")),
                autostart=False)
    page = browser.new_page(viewport={"width": 1500, "height": 900})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(url + "/")
    page.wait_for_selector("#start:not([hidden])")
    an, fan = page.frame_locator("#analyst"), page.frame_locator("#fan")
    an.locator("#status").filter(has_text="Live feed").wait_for(timeout=10_000)
    page.click("#start")
    f_an = an.locator('#log [data-overlay-id="ov-m001-067-goal-factual-analyst-en"]')
    e_an = an.locator('#log [data-overlay-id="ov-m001-067-goal-r1-analyst-en"]')
    f_fan = fan.locator('#log [data-overlay-id="ov-m001-067-goal-factual-fan-es"]')
    e_fan = fan.locator('#log [data-overlay-id="ov-m001-067-goal-r1-fan-es"]')
    for loc in (f_an, e_an, f_fan, e_fan):
        loc.wait_for(timeout=40_000)
    assert "Goal for Ironvale: Vask" in f_an.inner_text()
    assert "Ironvale moved the ball through 11 passes, then scored." in e_an.inner_text()
    assert "¡Gol de Ironvale!" in f_fan.inner_text()
    assert "Vask remató tras 11 pases seguidos." in e_fan.inner_text()
    # the explanation replaces the factual card and is marked "updated"
    assert e_an.get_attribute("data-supersedes") == "ov-m001-067-goal-factual-analyst-en"
    assert "superseded" in f_an.get_attribute("class")
    assert "updated" in e_an.inner_text()
    assert "actualizado" in e_fan.inner_text()
    assert e_an.inner_text() != e_fan.inner_text()
    an.locator("body[data-ended='1']").wait_for(timeout=30_000)
    assert errors == []
    page.close()
