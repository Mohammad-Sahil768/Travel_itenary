"""The LLM backend for the optimization agent, via LangChain.

Talks to Google AI Studio's Gemini API through its OpenAI-compatibility
endpoint, using langchain_openai.ChatOpenAI with LangChain's native
tool-calling (bind_tools) so the agent's tool-use loop in agent.py is
unchanged: it still just calls backend.send(text, ...) and gets back text +
any tool calls the model made, exactly as before.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import httpx
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_openai import ChatOpenAI

from .tools import TOOL_DEFINITIONS, execute_tool

MAX_TOOL_ROUNDS_PER_TURN = 6  # guards against a runaway tool-call loop within one outer iteration
REQUEST_TIMEOUT_SECONDS = 60.0  # bounds a single call so a stuck gateway/model fails fast, not silently


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


class LangChainBackend:
    """Wraps langchain_openai.ChatOpenAI pointed at an OpenAI-compatible
    endpoint (Google AI Studio's Gemini API). `base_url`/`model` come from
    the caller (see travel_agent.agent's constants) rather than being
    hardcoded here, so this class stays reusable if that endpoint changes.
    """

    def __init__(self, api_key: str, model: str, system_prompt: str, base_url: Optional[str] = None):
        # Bounded on both the transport and the client itself so a stuck/slow
        # backend fails with a clear error instead of hanging the Streamlit
        # session forever.
        http_client = httpx.Client(timeout=REQUEST_TIMEOUT_SECONDS)
        self.llm = ChatOpenAI(
            base_url=base_url,
            model=model,
            api_key=api_key,
            http_client=http_client,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        self.llm_with_tools = self.llm.bind_tools(_openai_tool_defs())
        self.messages: List = [SystemMessage(content=system_prompt)]

    def validate(self) -> Optional[str]:
        try:
            self.llm.invoke([HumanMessage(content="ping")])
            return None
        except Exception as exc:  # surfaced verbatim to the UI, any failure = invalid
            return str(exc)

    def send(self, user_text: str, stops_by_name, matrix, total_budget_minutes: int) -> TurnResult:
        self.messages.append(HumanMessage(content=user_text))
        result = TurnResult()

        for _ in range(MAX_TOOL_ROUNDS_PER_TURN):
            response: AIMessage = self.llm_with_tools.invoke(self.messages)
            self.messages.append(response)

            if isinstance(response.content, str) and response.content.strip():
                result.text = response.content

            if not response.tool_calls:
                break

            for tc in response.tool_calls:
                tool_result = execute_tool(tc["name"], tc["args"], stops_by_name, matrix, total_budget_minutes)
                result.tool_calls.append(ToolCallRecord(tc["name"], tc["args"], tool_result))
                self.messages.append(ToolMessage(content=json.dumps(tool_result), tool_call_id=tc["id"]))

        return result


def make_backend(provider: str, api_key: str, model: str, system_prompt: str, base_url: Optional[str] = None):
    return LangChainBackend(api_key, model, system_prompt, base_url=base_url)
