# Second Look

Live football companion over a **synthetic** match (invented clubs and players, no real data). Scout agents propose competing explanations of a moment, an Editor rules between them, and a fail-closed checker blocks any claim the match data cannot support. Two viewers (analyst in English, fan in Spanish) get different overlays, and corrections to the feed (a goal ruled offside) are propagated to the explanations already on air.

Built by its author with AI coding agents working under his direction. No real clubs, players, footage or brands.

## Which model is behind it

| Mode | What answers | Status |
| :- | :- | :- |
| `fake` (default) | A deterministic rule-based stand-in. **Not a language model.** Used by tests and the offline demo. | Always works |
| `gemini` | **Gemini 3.5 Flash through Google's Gemini API** (native `generateContent`, prepaid paid tier), used because a Microsoft Foundry account was not available in time. We request `gemini-3.5-flash`; the API reports the version that served each call (`gemini-3.6-flash` in our recording) and the recorder stores it in the run manifest. | Used for the real recording. The free tier alone (20 requests per model per day) is too small for a full match |
| `foundry` / `github` | OpenAI-compatible clients, written but **not exercised** against a real endpoint | Untested |

Recorded runs say what produced them: the replay banner shows the provider and model for real-model runs and "not live inference" for the fake run. A recorded replay is a recording, not live inference.

## Microsoft technology used

- **Microsoft Agent Framework** (`agent-framework`, Python): the per-moment workflow is a graph of executors (dispatch, scouts in parallel, collect, Editor) with file checkpointing, so a killed run resumes without repeating scout calls.
- **Model Context Protocol (MCP)**: the match-state tools the agents call (events, stats, dossier, evidence) are served over MCP with a per-moment token that hides anything after the moment.

Not used: Azure hosting, Cosmos DB or Foundry. Those need an Azure account that is not set up.

## Run it

```
pip install pydantic numpy fastapi uvicorn sse-starlette httpx "agent-framework>=1.21,<2" "mcp<2" pytest pytest-asyncio ruff
make test        # lint + unit/integration, offline (fake model)
make e2e         # Playwright browser test (needs: pip install playwright && playwright install chromium)
make demo-slice  # http://127.0.0.1:8000  (55'-70' slice, fake model, 10x)
python -m secondlook run --headless --speed max --llm fake   # print the overlays
```

Real model, with credentials supplied outside the repo (full match is about 175 calls):

```
export LLM_PROVIDER=gemini LLM_RUN_CALL_CAP=40 LLM_TIMEOUT_SCALE=4
python -m secondlook record --llm gemini --full --out runs   # full match (omit --full for the 55'-70' slice)
python -m secondlook export-replay                       # copy newest run to web/runs
python -m secondlook harness --llm gemini --limit 5      # comparison, marked PARTIAL
```

No key is read or stored by this code. Copy `.env.example` to `.env` for the other providers; `.env` is git-ignored. Replays in `web/runs/` are static files and need no model.

## Recordings in `web/runs/`

- `20261027T040325Z-demo-20261024` (default): **real model run**, full 90-minute synthetic match. Provider `gemini`, requested `gemini-3.5-flash`, served `gemini-3.6-flash`. 16 moments, 25 explanations, no moment fell back to a factual card, and the 81:40 offside correction was reflected. 175 model calls: 171 ok, 4 failed (invalid JSON after one repair; those agents' output was dropped). The replay itself is a recording, not live inference.
- `20261014T125233Z-demo-20261024` (fallback): **fake-model** recording, labelled "not live inference".

## Comparison harness (multi-agent vs single agent vs no Editor)

`harness/results/2026-10-27-*-gemini-partial.md` is a **PARTIAL** real-model run: 5 of 30 sequences, so the intervals are wide and it must not be read as a result. The 5 sequences were 2 clean, 2 misleading-possession and 1 late-correction, none from the injected-timeout group. The injected-timeout sequences only simulate a delay with the fake model and were not exercised live. The full 30-sequence run was not done.
