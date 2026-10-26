import pytest

from secondlook.runner import RunConfig

pytestmark = pytest.mark.e2e


def test_workflow_panel_evidence_pulse_and_followed_halo(serve, browser, tmp_path):
    url = serve(RunConfig(speed="max", llm="fake", sync_agents=True, state_dir=str(tmp_path / "s")))
    page = browser.new_page(viewport={"width": 1500, "height": 950})
    page.goto(url + "/")
    an, fan = page.frame_locator("#analyst"), page.frame_locator("#fan")
    an.locator("body[data-ended='1']").wait_for(timeout=40_000)
    row = an.locator('#log [data-overlay-id="ov-m001-067-goal-r1-analyst-en"]')
    row.click()
    det = an.locator("#details:not([hidden])")
    det.wait_for(timeout=10_000)
    assert det.get_attribute("data-story") == "st-m001-067-goal"
    chosen = det.locator(".cand-chosen")
    assert "rhythm" in chosen.inner_text() and "sustained_possession" in chosen.inner_text()
    assert "rejected" in det.locator(".cand-rejected").first.inner_text()
    assert "✓" in chosen.inner_text()
    assert "fewer supporting claims" in det.locator(".verdict").inner_text()
    assert "editor" in det.locator(".calls").inner_text()
    before = int(an.locator("#pitch").get_attribute("data-pulses") or 0)
    det.locator("button.ev").first.click()
    after = int(an.locator("#pitch").get_attribute("data-pulses") or 0)
    assert after == before + 1  # the evidence event pulses on the pitch
    # the fan view follows a player and has no analyst panel
    assert fan.locator("#pitch").get_attribute("data-followed") == "IRV-10"
    assert fan.locator("#details").count() == 1 and not fan.locator("#details").is_visible()
    page.close()
