"""Per-provider LLM backends for the optimization agent.

Each backend exposes the same tiny interface — validate() and send(text) —
so agent.py's outer refine loop, the JSON contract, and tools.py's
validate_constraints/execute_tool stay 100% identical across providers.
Only "how do I make this specific API call and run its tool-calling turn"
differs per backend:

- AnthropicBackend: the Claude Messages API (content blocks, tool_use).
- OpenAICompatibleBackend: OpenAI's Chat Completions API (role/content
  messages, tool_calls). Used for both the "OpenAI" provider AND "Other
  LLM" — "other" is treated as any OpenAI-compatible /v1/chat/completions
  endpoint (Together, Groq, OpenRouter, a local vLLM/Ollama server, etc.),
  reached via a custom base_url. There is no single universal LLM protocol,
  so this is the most broadly useful concrete meaning of "generic endpoint".
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import anthropic
from openai import OpenAI

from .tools import TOOL_DEFINITIONS, execute_tool

MAX_TOOL_ROUNDS_PER_TURN = 6  # guards against a runaway tool-call loop within one outer iteration


@dataclass
class ToolCallRecord:
    name: str
    input: Dict
    result: Dict


@dataclass
class TurnResult:
    text: Optional[str] = None
    thinking: List[str] = field(default_factory=list)
    tool_calls: List[ToolCallRecord] = field(default_factory=list)


def _openai_tool_defs() -> List[Dict]:
    return [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t["description"],
                "parameters": t["input_schema"],
            },
        }
        for t in TOOL_DEFINITIONS
    ]


class AnthropicBackend:
    def __init__(self, api_key: str, model: str, system_prompt: str):
        self.client = anthropic.Anthropic(api_key=api_key)
        self.model = model
        self.system_prompt = system_prompt
        self.messages: List[Dict] = []

    def validate(self) -> Optional[str]:
        try:
            self.client.messages.create(
                model=self.model, max_tokens=16, messages=[{"role": "user", "content": "ping"}]
            )
            return None
        except Exception as exc:  # surfaced verbatim to the UI, any failure = invalid
            return str(exc)

    def send(self, user_text: str, stops_by_name, matrix, total_budget_minutes: int) -> TurnResult:
        self.messages.append({"role": "user", "content": user_text})
        result = TurnResult()

        for _ in range(MAX_TOOL_ROUNDS_PER_TURN):
            response = self.client.messages.create(
                model=self.model,
                max_tokens=16000,
                thinking={"type": "adaptive", "display": "summarized"},
                system=self.system_prompt,
                tools=TOOL_DEFINITIONS,
                messages=self.messages,
            )
            for block in response.content:
                if block.type == "thinking" and getattr(block, "thinking", ""):
                    result.thinking.append(block.thinking)
                elif block.type == "text" and block.text.strip():
                    result.text = block.text
            self.messages.append({"role": "assistant", "content": response.content})

            if response.stop_reason != "tool_use":
                break

            tool_use_blocks = [b for b in response.content if b.type == "tool_use"]
            tool_results = []
            for tb in tool_use_blocks:
                tool_result = execute_tool(tb.name, tb.input, stops_by_name, matrix, total_budget_minutes)
                result.tool_calls.append(ToolCallRecord(tb.name, tb.input, tool_result))
                tool_results.append({"type": "tool_result", "tool_use_id": tb.id, "content": json.dumps(tool_result)})
            self.messages.append({"role": "user", "content": tool_results})

        return result


class OpenAICompatibleBackend:
    def __init__(self, api_key: str, model: str, system_prompt: str, base_url: Optional[str] = None):
        self.client = OpenAI(api_key=api_key, base_url=base_url) if base_url else OpenAI(api_key=api_key)
        self.model = model
        self.messages: List[Dict] = [{"role": "system", "content": system_prompt}]

    def validate(self) -> Optional[str]:
        try:
            self.client.chat.completions.create(
                model=self.model, messages=[{"role": "user", "content": "ping"}], max_completion_tokens=16
            )
            return None
        except Exception as exc:
            return str(exc)

    def send(self, user_text: str, stops_by_name, matrix, total_budget_minutes: int) -> TurnResult:
        self.messages.append({"role": "user", "content": user_text})
        result = TurnResult()
        tool_defs = _openai_tool_defs()

        for _ in range(MAX_TOOL_ROUNDS_PER_TURN):
            response = self.client.chat.completions.create(
                model=self.model,
                messages=self.messages,
                tools=tool_defs,
                max_completion_tokens=8000,
            )
            msg = response.choices[0].message
            result.text = msg.content

            assistant_msg: Dict = {"role": "assistant", "content": msg.content}
            if msg.tool_calls:
                assistant_msg["tool_calls"] = [
                    {"id": tc.id, "type": "function", "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                    for tc in msg.tool_calls
                ]
            self.messages.append(assistant_msg)

            if not msg.tool_calls:
                break

            for tc in msg.tool_calls:
                try:
                    tool_input = json.loads(tc.function.arguments or "{}")
                except json.JSONDecodeError:
                    tool_input = {}
                tool_result = execute_tool(tc.function.name, tool_input, stops_by_name, matrix, total_budget_minutes)
                result.tool_calls.append(ToolCallRecord(tc.function.name, tool_input, tool_result))
                self.messages.append({"role": "tool", "tool_call_id": tc.id, "content": json.dumps(tool_result)})

        return result


def make_backend(provider: str, api_key: str, model: str, system_prompt: str, base_url: Optional[str] = None):
    """provider: "anthropic" | "openai" | "other". "openai" and "other" both
    use the OpenAI-compatible backend; "other" just supplies its own base_url."""
    if provider == "anthropic":
        return AnthropicBackend(api_key, model, system_prompt)
    return OpenAICompatibleBackend(api_key, model, system_prompt, base_url=base_url)
