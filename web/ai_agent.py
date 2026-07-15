"""OpenAI-compatible AI agent with Minecraft server tools."""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

import httpx
from openai import OpenAI

from mc_server import manager
from system_prompt import SYSTEM_PROMPT


def _normalize_proxy_url(url: str) -> str:
    """httpx/openai reject bare socks:// — need socks5:// or socks4://."""
    u = (url or "").strip()
    if u.startswith("socks://"):
        return "socks5://" + u[len("socks://") :]
    return u


def make_openai_client(*, api_key: str, base_url: str) -> OpenAI:
    """
    Build OpenAI client that does not choke on system ALL_PROXY=socks://...

    - By default ignore env proxies (trust_env=False) — works for direct API access.
    - Optional AI_HTTP_PROXY / HTTPS_PROXY override if you really need a proxy
      (use socks5://127.0.0.1:10808, not socks://).
    """
    explicit = (
        os.environ.get("AI_HTTP_PROXY")
        or os.environ.get("AI_HTTPS_PROXY")
        or ""
    ).strip()

    if explicit:
        proxy = _normalize_proxy_url(explicit)
        http_client = httpx.Client(proxy=proxy, trust_env=False, timeout=120.0)
    else:
        # Ignore broken system ALL_PROXY=socks://... from xray/v2ray shells
        http_client = httpx.Client(trust_env=False, timeout=120.0)

    return OpenAI(
        api_key=api_key,
        base_url=base_url.rstrip("/"),
        http_client=http_client,
    )


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "run_server_command",
            "description": (
                "Execute a Minecraft server console command. "
                "Do NOT include a leading slash. Example: 'list', 'give Steve minecraft:diamond 1'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "Console command without leading slash",
                    }
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_console_output",
            "description": "Read the latest lines from the Minecraft server console log.",
            "parameters": {
                "type": "object",
                "properties": {
                    "lines": {
                        "type": "integer",
                        "description": "How many recent lines to return (1-500)",
                        "default": 50,
                    }
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_server_status",
            "description": "Get whether the Minecraft server process is running and ready for players.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "wait_for_console",
            "description": (
                "Wait until a console line matches a regex or substring. "
                "Useful after run_server_command to confirm success."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "Regex or plain substring to search in new console lines",
                    },
                    "timeout_seconds": {
                        "type": "number",
                        "description": "Max seconds to wait",
                        "default": 10,
                    },
                },
                "required": ["pattern"],
            },
        },
    },
]


def execute_tool(name: str, arguments: Dict[str, Any]) -> str:
    try:
        if name == "run_server_command":
            result = manager.send_command(str(arguments.get("command", "")))
            # brief pause is handled by model via wait/get tools
            return json.dumps(result, ensure_ascii=False)

        if name == "get_console_output":
            lines = int(arguments.get("lines") or 50)
            lines = max(1, min(lines, 500))
            logs = manager.get_logs(lines)
            return json.dumps({"ok": True, "lines": logs, "count": len(logs)}, ensure_ascii=False)

        if name == "get_server_status":
            return json.dumps({"ok": True, **manager.status()}, ensure_ascii=False)

        if name == "wait_for_console":
            pattern = str(arguments.get("pattern", ""))
            timeout = float(arguments.get("timeout_seconds") or 10)
            result = manager.wait_for_console(pattern, timeout=timeout)
            return json.dumps(result, ensure_ascii=False)

        return json.dumps({"ok": False, "message": f"Unknown tool: {name}"})
    except Exception as exc:  # noqa: BLE001
        return json.dumps({"ok": False, "message": str(exc)}, ensure_ascii=False)


def chat_with_tools(
    *,
    messages: List[Dict[str, Any]],
    api_key: str,
    base_url: str,
    model: str,
    max_tool_rounds: int = 8,
) -> Dict[str, Any]:
    """Run a chat completion loop with tool calls. Returns assistant text + trace."""
    if not api_key:
        raise ValueError("API key is required. Open Settings and set your OpenAI-compatible API key.")
    if not base_url:
        raise ValueError("Base URL is required (e.g. https://api.x.ai/v1 or https://api.openai.com/v1).")
    if not model:
        raise ValueError("Model name is required (e.g. grok-4.5, gpt-4o-mini).")

    client = make_openai_client(api_key=api_key, base_url=base_url)

    working: List[Dict[str, Any]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        *messages,
    ]
    tool_trace: List[Dict[str, Any]] = []

    try:
        for _ in range(max_tool_rounds):
            response = client.chat.completions.create(
                model=model,
                messages=working,
                tools=TOOLS,
                tool_choice="auto",
                temperature=0.2,
            )
            choice = response.choices[0]
            msg = choice.message

            # Append assistant message (may include tool_calls)
            assistant_entry: Dict[str, Any] = {
                "role": "assistant",
                "content": msg.content or "",
            }
            if msg.tool_calls:
                assistant_entry["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments or "{}",
                        },
                    }
                    for tc in msg.tool_calls
                ]
            working.append(assistant_entry)

            if not msg.tool_calls:
                return {
                    "ok": True,
                    "content": msg.content or "",
                    "tool_trace": tool_trace,
                    "finish_reason": choice.finish_reason,
                }

            for tc in msg.tool_calls:
                name = tc.function.name
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}
                result = execute_tool(name, args)
                tool_trace.append({"tool": name, "arguments": args, "result": result})
                working.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": result,
                    }
                )

        # Exceeded tool rounds — force a final answer without tools
        response = client.chat.completions.create(
            model=model,
            messages=working
            + [
                {
                    "role": "user",
                    "content": "Tool round limit reached. Summarize what you did and what remains.",
                }
            ],
            temperature=0.2,
        )
        content = response.choices[0].message.content or ""
        return {
            "ok": True,
            "content": content,
            "tool_trace": tool_trace,
            "finish_reason": "tool_round_limit",
        }
    finally:
        # Close custom httpx client to avoid resource leaks
        try:
            client.close()
        except Exception:
            pass
