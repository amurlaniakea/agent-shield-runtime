"""Tests for LangChain adapter integration with Agent Shield Runtime."""

from unittest.mock import MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool

from agent_shield_runtime.adapters.generic import Channel
from agent_shield_runtime.adapters.langchain import (
    ShieldBlockError,
    ShieldConfirmRequired,
    ShieldedTool,
)
from agent_shield_runtime.runtime import RuntimeVerdict, ShieldRuntime


@pytest.fixture
def mock_tool():
    tool = MagicMock(spec=BaseTool)
    tool.name = "test_tool"
    tool.description = "A test tool"
    tool.args_schema = {}
    tool._run.return_value = "tool_result"
    return tool


@pytest.fixture
def runtime():
    config = MagicMock()
    config.sensor_timeout = 1.0
    config.fail_mode = "closed"
    config.block_on_confirm = False
    return ShieldRuntime(config)


@pytest.fixture
def shielded_tool(mock_tool, runtime):
    return ShieldedTool(
        mock_tool,
        runtime=runtime,
        claimed_subobjective={"test_tool": "test_subobjective"},
    )


def test_shielded_tool_allow(shielded_tool, mock_tool, runtime):
    # Mock runtime to return ALLOW verdict
    with patch.object(
        runtime,
        "execute",
        return_value=RuntimeVerdict(
            decision="allow", blocked=False, reasons=["all sensors allow"], result={}
        ),
    ):
        result = shielded_tool._run(
            "arg1", "arg2", config=RunnableConfig(configurable={"thread_id": "task1"})
        )
        assert result == "tool_result"
        mock_tool._run.assert_called_once_with(
            "arg1", "arg2", config=RunnableConfig(configurable={"thread_id": "task1"})
        )


def test_shielded_tool_block(shielded_tool, runtime):
    # Mock runtime to return BLOCK verdict
    with patch.object(
        runtime,
        "execute",
        return_value=RuntimeVerdict(
            decision="block", blocked=True, reasons=["scope:block:out_of_scope"], result=None
        ),
    ):
        with pytest.raises(ShieldBlockError) as exc_info:
            shielded_tool._run("arg1", "arg2")

        assert "Tool execution blocked by Agent Shield" in str(exc_info.value)
        assert exc_info.value.reasons == ["scope:block:out_of_scope"]


def test_shielded_tool_confirm(shielded_tool, runtime):
    # Mock runtime to return CONFIRM verdict
    with patch.object(
        runtime,
        "execute",
        return_value=RuntimeVerdict(
            decision="confirm", blocked=True, reasons=["goal-anchor:drift"], result=None
        ),
    ):
        with pytest.raises(ShieldConfirmRequired) as exc_info:
            shielded_tool._run("arg1", "arg2")

        assert "Tool execution requires confirmation" in str(exc_info.value)
        assert exc_info.value.reasons == ["goal-anchor:drift"]


def test_channel_inference_tool_result(shielded_tool):
    # Mock runtime to return ALLOW verdict and capture the call
    captured = {}

    def fake_execute(call):
        captured["call"] = call
        return RuntimeVerdict(decision="allow", blocked=False, reasons=[], result={})

    # Create a new ShieldedTool with a history provider
    history = [
        HumanMessage(content="User query"),
        AIMessage(content="I will use the tool"),
        ToolMessage(content="Previous result: TOKEN123", tool_call_id="call1"),
    ]
    shielded_tool_with_provider = ShieldedTool(
        shielded_tool._tool,
        runtime=shielded_tool._runtime,
        claimed_subobjective={"test_tool": "test_subobjective"},
        history_provider=lambda: history,
    )

    with patch.object(shielded_tool_with_provider._runtime, "execute", side_effect=fake_execute):
        shielded_tool_with_provider._run(arg2="TOKEN123")

    # Verify the Channel for 'arg2' is TOOL_RESULT
    arg = next((a for a in captured["call"].args if a.name == "arg2"), None)
    assert arg is not None
    assert arg.channel == Channel.TOOL_RESULT  # el valor SI vino del ToolMessage


def test_channel_inference_model(shielded_tool):
    # Mock runtime to return ALLOW verdict and capture the call
    captured = {}

    def fake_execute(call):
        captured["call"] = call
        return RuntimeVerdict(decision="allow", blocked=False, reasons=[], result={})

    # Create a new ShieldedTool with a history provider
    history = [HumanMessage(content="User query")]  # sin ToolMessage relevante
    shielded_tool_with_provider = ShieldedTool(
        shielded_tool._tool,
        runtime=shielded_tool._runtime,
        claimed_subobjective={"test_tool": "test_subobjective"},
        history_provider=lambda: history,
    )

    with patch.object(shielded_tool_with_provider._runtime, "execute", side_effect=fake_execute):
        shielded_tool_with_provider._run(arg2="algo_no_visto_antes")

    # Verify the Channel for 'arg2' is MODEL
    arg = next((a for a in captured["call"].args if a.name == "arg2"), None)
    assert arg is not None
    assert arg.channel == Channel.MODEL


def test_unknown_verdict(shielded_tool, runtime):
    # Mock runtime to return an unknown verdict decision
    with patch.object(
        runtime,
        "execute",
        return_value=RuntimeVerdict(decision="unknown", blocked=False, reasons=[], result=None),
    ):
        with pytest.raises(ValueError) as exc_info:
            shielded_tool._run("arg1", "arg2")

        assert "Unknown verdict decision" in str(exc_info.value)


def test_no_history_provider_defaults_to_model(shielded_tool):
    # Mock runtime to return ALLOW verdict and capture the call
    captured = {}

    def fake_execute(call):
        captured["call"] = call
        return RuntimeVerdict(decision="allow", blocked=False, reasons=[], result={})

    with patch.object(shielded_tool._runtime, "execute", side_effect=fake_execute):
        # No history provider, so all args default to Channel.MODEL
        shielded_tool._run(arg1="value1", arg2="value2")

    # Verify all args have Channel.MODEL
    for arg in captured["call"].args:
        assert arg.channel == Channel.MODEL


def test_with_history_provider(shielded_tool):
    # Mock runtime to return ALLOW verdict and capture the call
    captured = {}

    def fake_execute(call):
        captured["call"] = call
        return RuntimeVerdict(decision="allow", blocked=False, reasons=[], result={})

    # Create a new ShieldedTool with a history provider
    history = [
        HumanMessage(content="User query"),
        ToolMessage(content="Previous result: TOKEN123", tool_call_id="call1"),
    ]
    shielded_tool_with_provider = ShieldedTool(
        shielded_tool._tool,
        runtime=shielded_tool._runtime,
        claimed_subobjective={"test_tool": "test_subobjective"},
        history_provider=lambda: history,
    )

    with patch.object(shielded_tool_with_provider._runtime, "execute", side_effect=fake_execute):
        shielded_tool_with_provider._run(arg2="TOKEN123")

    # Verify the Channel for 'arg2' is TOOL_RESULT
    arg = next((a for a in captured["call"].args if a.name == "arg2"), None)
    assert arg is not None
    assert arg.channel == Channel.TOOL_RESULT
