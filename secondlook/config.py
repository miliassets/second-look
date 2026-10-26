"""All thresholds, timeouts and budgets in one place."""
import os
from dataclasses import dataclass

PITCH_L = 105.0
PITCH_W = 68.0
FRAME_HZ = 2
SCHEMA_VERSION = "1.0"
DEMO_SEED = 20261024
MATCH_ID = "m001"

# Claim tolerances (design §3.5)
TOL_SPEED_KMH = 2.0
TOL_RATE_PTS = 2.0
TOL_GAP_S = 1.0
TOL_PHYS_SPEED_KMH = 1.0
TOL_PHYS_DIST_FRAC = 0.05

# Phase rules (design §3.5; thresholds UNVERIFIED, tuned on generator output)
PHASE_CONTROL_POSSESSION = 58.0
PHASE_CONTROL_COMPLETION = 80.0
PHASE_CHAOS_CHANGES_PER_MIN = 1.2
PHASE_CHAOS_COMPLETION = 72.0

# Moments
MOMENT_LOOKBACK_MS = 120_000
SHOT_SPEED_MOMENT_KMH = 100.0

# Text limits
MAX_LEN = {"analyst": 220, "fan": 140}

# Timeouts (seconds)
TIMEOUT_SCOUT = 8
TIMEOUT_EDITOR = 8
TIMEOUT_PRODUCER = 6
TIMEOUT_ASKWHY = 10
TIMEOUT_SINGLE = 16  # the single agent does the scouts', Editor's and producers' work in one go
MOMENT_BUDGET_S = 20


@dataclass(frozen=True)
class LLMConfig:
    provider: str
    daily_cap: int
    run_cap: int


def llm_config() -> LLMConfig:
    return LLMConfig(
        provider=os.environ.get("LLM_PROVIDER", "fake"),
        daily_cap=int(os.environ.get("LLM_DAILY_CALL_CAP", "120")),
        run_cap=int(os.environ.get("LLM_RUN_CALL_CAP", "60")),
    )

# Physical (frames at 2 Hz)
SPRINT_KMH = 25.0
SPRINT_MIN_S = 1.0
ATT_THIRD_X = 70.0  # normalised x (metres from own goal line) where the attacking third begins
POSSESSION_SEG_CAP_MS = 20_000  # longest stretch attributed to one possession owner
PHASE_WINDOW_MS = 180_000
PHASE_STEP_MS = 30_000
