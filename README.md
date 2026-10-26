# Second Look (build in progress)

Live football companion over a **synthetic** match (invented clubs and players): scout agents propose competing explanations, an Editor rules, a fail-closed checker blocks any claim the match data cannot support, and two viewers (analyst in English, fan in Spanish) get different overlays.

Built by Milan with AI coding agents under his direction: Claude Code wrote the code; OpenAI Codex and Gemini gave input on the concept. No real clubs, players or brands. Default model provider is `fake` (a deterministic rule-based stand-in, not a real model).

```
make test        # unit + integration, LLM_PROVIDER=fake
make e2e         # Playwright gate test (goal -> both viewers)
make demo-slice  # http://127.0.0.1:8000  (55'-70' slice, fake LLM, 10x)
python -m secondlook run --headless --speed max --llm fake   # print the overlays
```
