"""Tests for LangChain adapter integration with Agent Shield Runtime."""

from unittest.mock import MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool

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
    # Mock runtime to return ALLOW verdict
    with patch.object(
        shielded_tool._runtime,
        "execute",
        return_value=RuntimeVerdict(decision="allow", blocked=False, reasons=[], result={}),
    ):
        # Simulate history with a prior tool result containing the token
        history = [
            HumanMessage(content="User query"),
            AIMessage(content="I will use the tool"),
            ToolMessage(content="Previous result: TOKEN123", tool_call_id="call1"),
        ]

        # Call with a token that appears in the tool result
        result = shielded_tool._run("arg1", "arg2", history=history)
        # Should succeed (allow) but the Channel for 'arg2' should be TOOL_RESULT
        assert result == "tool_result"


def test_channel_inference_model(shielded_tool):
    # Mock runtime to return ALLOW verdict
    with patch.object(
        shielded_tool._runtime,
        "execute",
        return_value=RuntimeVerdict(decision="allow", blocked=False, reasons=[], result={}),
    ):
        # Simulate history without the token
        history = [
            HumanMessage(content="User query"),
            AIMessage(content="I will use the tool"),
        ]

        # Call with a token that does NOT appear in any tool result
        result = shielded_tool._run("arg1", "arg2", history=history)
        # Should succeed (allow) and the Channel for 'arg2' should be MODEL
        assert result == "tool_result"


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
