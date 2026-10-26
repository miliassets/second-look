import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_env_is_git_ignored():
    r = subprocess.run(["git", "check-ignore", "-q", str(ROOT / ".env")], cwd=ROOT)
    assert r.returncode == 0


def test_env_example_not_ignored_and_has_no_secrets():
    r = subprocess.run(["git", "check-ignore", "-q", str(ROOT / ".env.example")], cwd=ROOT)
    assert r.returncode == 1
    for line in (ROOT / ".env.example").read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            if k in ("LLM_PROVIDER", "LLM_DAILY_CALL_CAP", "LLM_RUN_CALL_CAP"):
                continue
            assert v == "", f"{k} must be empty in .env.example"


def test_default_provider_is_fake(monkeypatch):
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    from secondlook.config import llm_config

    assert llm_config().provider == "fake"
