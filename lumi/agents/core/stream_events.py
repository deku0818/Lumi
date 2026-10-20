"""LangGraph tool event fields shared by the main bridge and subagent timeline."""

from typing import Any

_TOOL_INTERNAL_KEYS = frozenset({"tool_call_id", "runtime"})


def tool_event_call_id(event: dict[str, Any]) -> str:
    """Prefer ToolExecutor's stable ID so streamed tools match checkpoint history."""
    inp = event.get("data", {}).get("input")
    return (
        event.get("metadata", {}).get("lumi_tool_call_id")
        or (inp.get("tool_call_id") if isinstance(inp, dict) else None)
        or event.get("run_id", "")
    )


def tool_event_args(event: dict[str, Any]) -> dict[str, Any]:
    """Expose tool arguments without the framework's injected runtime and ID."""
    inp = event.get("data", {}).get("input")
    if not isinstance(inp, dict):
        return {}
    return {key: value for key, value in inp.items() if key not in _TOOL_INTERNAL_KEYS}
