from pathlib import Path

import pytest


@pytest.fixture(scope="session")
def browser():
    from playwright.sync_api import sync_playwright
    exe = next(Path("/opt/pw-browsers").glob("chromium-*/chrome-linux/chrome"), None)
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=str(exe) if exe else None,
                              args=["--no-sandbox"])
        yield b
        b.close()
