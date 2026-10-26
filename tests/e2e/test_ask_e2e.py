import pytest

from secondlook.runner import RunConfig

pytestmark = pytest.mark.e2e


def test_ask_why_box_answers_in_each_language(serve, browser, tmp_path):
    url = serve(RunConfig(speed="max", llm="fake", sync_agents=True, state_dir=str(tmp_path / "s")))
    page = browser.new_page(viewport={"width": 1500, "height": 900})
    page.goto(url + "/")
    an, fan = page.frame_locator("#analyst"), page.frame_locator("#fan")
    an.locator("body[data-ended='1']").wait_for(timeout=40_000)
    an.locator("#askInput").fill("Why did Ironvale score?")
    an.locator("#askBtn").click()
    an.locator("#answer:not([hidden])").wait_for(timeout=15_000)
    assert an.locator("#answer").inner_text().startswith("Score 1–1.")
    fan.locator("#askInput").fill("¿Quién ganará la liga?")
    fan.locator("#askBtn").click()
    fan.locator("#answer:not([hidden])").wait_for(timeout=15_000)
    assert fan.locator("#answer").inner_text().startswith("No puedo respaldar")
    assert fan.locator("#answer").get_attribute("data-supported") == "false"
    page.close()
