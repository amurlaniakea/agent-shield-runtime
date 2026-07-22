"""Adaptador LangChain para Agent Shield Runtime.

Traduce tool-calls de LangChain al formato interno GenericToolCall que
el runtime entiende, preservando provenance (Channel) y objetivo declarado.
"""

from typing import Any
from uuid import uuid4

from langchain_core.messages import BaseMessage, ToolMessage
from langchain_core.tools import BaseTool

from ..runtime import ShieldRuntime
from .generic import Channel, GenericArg, GenericToolCall


class ShieldedTool(BaseTool):
    """A LangChain tool wrapped with Agent Shield runtime protection."""

    def __init__(
        self,
        tool: BaseTool,
        runtime: ShieldRuntime,
        claimed_subobjective: dict[str, str] | None = None,
        objective_arg: str | None = None,
    ):
        super().__init__(
            name=tool.name,
            description=tool.description,
            args_schema=tool.args_schema,
        )
        self._tool = tool
        self._runtime = runtime
        self._claimed_subobjective = claimed_subobjective or {}
        self._objective_arg = objective_arg

    def _run(self, *args: Any, **kwargs: Any) -> Any:
        """Intercept tool execution with Agent Shield runtime."""
        # Extract task_id from RunnableConfig or generate a new one
        config = kwargs.get("config", {})
        task_id = config.get("configurable", {}).get("thread_id") or str(uuid4())

        # Extract history from RunnableConfig or kwargs
        history = self._extract_history(kwargs)

        # Build GenericToolCall with proper Channel inference per argument
        call = self._build_tool_call(task_id, args, kwargs, history)

        # Execute through ShieldRuntime
        verdict = self._runtime.execute(call)

        if verdict.decision == "allow":
            return self._tool._run(*args, **kwargs)
        elif verdict.decision == "block":
            raise ShieldBlockError(
                f"Tool execution blocked by Agent Shield: {self.name}",
                verdict.reasons,
            )
        elif verdict.decision == "confirm":
            raise ShieldConfirmRequired(
                f"Tool execution requires confirmation: {self.name}",
                verdict.reasons,
            )
        else:
            raise ValueError(f"Unknown verdict decision: {verdict.decision}")

    def _extract_history(self, kwargs: dict[str, Any]) -> list[BaseMessage]:
        """Extract conversation history from LangChain context.

        LangChain typically passes history via RunnableConfig or a dedicated
        'history' kwarg. This method tries both and returns an empty list if
        neither is available.
        """
        # Try RunnableConfig first
        config = kwargs.get("config", {})
        if config and hasattr(config, "get"):
            # LangChain stores history under 'configurable' -> 'history'
            history = config.get("configurable", {}).get("history")
            if history:
                return history
        # Try explicit 'history' kwarg
        history = kwargs.get("history")
        if history:
            return history
        return []

    def _build_tool_call(
        self,
        task_id: str,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
        history: list[BaseMessage],
    ) -> GenericToolCall:
        """Build a GenericToolCall from LangChain tool invocation.

        Maps positional args and keyword args into GenericArg instances,
        inferring the Channel (provenance) for each argument based on the
        conversation history.
        """
        # Combine positional and keyword args into a single dict
        all_args = {}
        # Positional args are passed by name via the tool's args_schema
        # In LangChain, positional args are rare; kwargs dominate
        for k, v in kwargs.items():
            if k != "config" and k != "history":
                all_args[k] = v

        # Build GenericArg list with Channel inference
        generic_args = []
        for name, value in all_args.items():
            channel = self._infer_channel(str(value), history)
            generic_args.append(GenericArg(name=name, value=value, channel=channel))

        return GenericToolCall(
            task_id=task_id,
            tool=self.name,
            args=generic_args,
            objective_arg=self._objective_arg,
            claimed_subobjective=self._claimed_subobjective.get(self.name, "unspecified"),
        )

    def _infer_channel(self, value: str, history: list[BaseMessage]) -> Channel:
        """Infer the Channel (provenance) of an argument value.

        Implements the heuristic from SDD §4.5: if the exact token appears
        in any prior ToolMessage.content (word boundaries), it came from a
        tool result (untrusted). Otherwise, it came from the model (trusted).
        """
        import re

        token_pat = re.compile(rf"(?<!\w){re.escape(value)}(?!\w)")
        for msg in reversed(history):
            if isinstance(msg, ToolMessage) and msg.content and token_pat.search(str(msg.content)):
                return Channel.TOOL_RESULT
        return Channel.MODEL


class ShieldBlockError(Exception):
    """Exception raised when a tool call is blocked by Agent Shield."""

    def __init__(self, message: str, reasons: list[str] | None = None):
        super().__init__(message)
        self.reasons = reasons or []


class ShieldConfirmRequired(Exception):
    """Exception raised when a tool call requires human confirmation."""

    def __init__(self, message: str, reasons: list[str] | None = None):
        super().__init__(message)
        self.reasons = reasons or []
