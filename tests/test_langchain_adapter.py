import pytest
from unittest.mock import MagicMock
from langchain_core.tools import BaseTool
from langchain_core.runnables import RunnableConfig
from agent_shield_runtime.adapters.langchain import ShieldedTool, ShieldBlockError, ShieldConfirmRequired
from agent_shield_runtime.verdict import ShieldVerdict

@pytest.fixture
def mock_tool():
    tool = MagicMock(spec=BaseTool)
    tool.name = "test_tool"
    tool.description = "A test tool"
    tool.args_schema = {}
    tool._run.return_value = "tool_result"
    return tool

@pytest.fixture
def shielded_tool(mock_tool):
    return ShieldedTool(mock_tool, claimed_subobjective={"test_tool": "test_subobjective"})

def test_shielded_tool_allow(shielded_tool, mock_tool):
    # Mock ShieldRuntime to return ALLOW verdict
    shielded_tool.runtime.execute.return_value = ShieldVerdict.ALLOW
    
    result = shielded_tool._run("arg1", "arg2", config=RunnableConfig(configurable={"thread_id": "task1"}))
    assert result == "tool_result"
    mock_tool._run.assert_called_once_with("arg1", "arg2", config=RunnableConfig(configurable={"thread_id": "task1"}))

def test_shielded_tool_block(shielded_tool):
    # Mock ShieldRuntime to return BLOCK verdict
    shielded_tool.runtime.execute.return_value = ShieldVerdict.BLOCK
    
    with pytest.raises(ShieldBlockError):
        shielded_tool._run("arg1", "arg2")

def test_shielded_tool_confirm(shielded_tool):
    # Mock ShieldRuntime to return CONFIRM verdict
    shielded_tool.runtime.execute.return_value = ShieldVerdict.CONFIRM
    
    with pytest.raises(ShieldConfirmRequired):
        shielded_tool._run("arg1", "arg2")

def test_shielded_tool_unknown_verdict(shielded_tool):
    # Mock ShieldRuntime to return an unknown verdict
    shielded_tool.runtime.execute.return_value = "UNKNOWN"
    
    with pytest.raises(ValueError):
        shielded_tool._run("arg1", "arg2")