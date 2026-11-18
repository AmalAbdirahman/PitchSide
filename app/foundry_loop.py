"""The function-calling loop shared by the build-time agents (Scout, Analyst).

Only the --foundry paths call this; `client` is an OpenAI-compatible client from the Foundry
project, so this module imports nothing from Azure.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.scout_tools import ToolBox, ToolCall


@dataclass(frozen=True)
class Conversation:
    text: str  # the final reply
    rounds: int  # model responses (1 + rounds of tool calls)
    tool_calls: list[ToolCall]


def converse(
    client: Any, kw: dict[str, Any], user: str, tools: ToolBox | None, max_rounds: int, who: str
) -> Conversation:
    """Answer each round of function calls with `tools` until the model gives a final reply."""
    resp, rounds = client.responses.create(input=user, **kw), 1
    while True:
        calls = [o for o in resp.output if getattr(o, "type", None) == "function_call"]
        if not calls:
            return Conversation(resp.output_text, rounds, list(tools.calls) if tools else [])
        if tools is None or rounds >= max_rounds:
            raise RuntimeError(f"{who} asked for tools after {rounds} responses")
        outputs = [
            {
                "type": "function_call_output",
                "call_id": c.call_id,
                "output": tools.call(c.name, c.arguments),
            }
            for c in calls
        ]
        resp = client.responses.create(input=outputs, previous_response_id=resp.id, **kw)
        rounds += 1
