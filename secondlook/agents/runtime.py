"""Generic agent loop: tool rounds, structured output, one repair retry, timeout (design §3.4)."""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from ..matchstate.specs import TOOL_SPECS
from ..matchstate.tools import MatchTools, ToolError
from .llm import LLMRequest, ManagedLLM

T = TypeVar("T", bound=BaseModel)
PROMPTS = Path(__file__).parent / "prompts"


def prompt(name: str) -> str:
    return (PROMPTS / f"{name}.md").read_text()


@dataclass
class AgentRun:
    outcome: str  # ok | timeout | invalid | budget | error
    output: BaseModel | None = None
    call_ids: list[str] = field(default_factory=list)
    tool_trace: list[dict] = field(default_factory=list)
    error: str = ""


async def run_agent(llm: ManagedLLM, agent: str, instructions: str, payload: dict,
                    schema: type[T], tools: MatchTools | None = None,
                    tool_names: list[str] | None = None, moment_id: str = "",
                    max_tool_rounds: int = 2, max_tokens: int = 400,
                    timeout_s: float = 8.0) -> AgentRun:
    run = AgentRun("error")

    async def loop():
        messages: list[dict] = [{"role": "user", "content": json.dumps(payload, sort_keys=True)}]
        specs = [TOOL_SPECS[n] for n in (tool_names or [])] if tools else []
        rounds, repaired = 0, False
        while True:
            req = LLMRequest(agent=agent, instructions=instructions, messages=list(messages),
                             tools=specs if rounds < max_tool_rounds else [],
                             response_schema=schema.model_json_schema(), max_tokens=max_tokens)
            r = await llm.call(req, moment_id, timeout_s=timeout_s)
            run.call_ids.append(r.call_id)
            if r.outcome != "ok" or r.response is None:
                run.outcome, run.error = r.outcome, r.error
                return
            resp = r.response
            if resp.tool_calls and rounds < max_tool_rounds and tools is not None:
                rounds += 1
                messages.append({"role": "assistant", "content": resp.text or "", "tool_calls": [
                    {"id": c.id, "type": "function", "function": {
                        "name": c.name, "arguments": json.dumps(c.arguments)}}
                    for c in resp.tool_calls]})
                for c in resp.tool_calls:
                    try:
                        fn = tools.catalogue()[c.name]
                        result = fn(**c.arguments)
                    except (ToolError, KeyError, TypeError) as e:
                        result = {"error": f"{type(e).__name__}: {e}"}
                    run.tool_trace.append({"name": c.name, "arguments": c.arguments})
                    messages.append({"role": "tool", "tool_call_id": c.id,
                                     "content": json.dumps(result, default=str)})
                continue
            try:
                run.output = schema.model_validate_json(resp.text)
                run.outcome = "ok"
                return
            except (ValidationError, ValueError) as e:
                if repaired:
                    llm.mark_invalid(r.call_id)
                    run.outcome, run.error = "invalid", str(e)[:300]
                    return
                repaired = True
                messages.append({"role": "assistant", "content": resp.text})
                messages.append({"role": "user", "content":
                                 f"Your JSON failed validation: {str(e)[:300]}. "
                                 "Return only the corrected JSON."})

    try:
        await asyncio.wait_for(loop(), timeout_s * 3)
    except TimeoutError:
        run.outcome = "timeout"
    return run
