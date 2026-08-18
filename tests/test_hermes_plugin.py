# SPDX-FileCopyrightText: 2026 Pedro Sordo Martínez <amurlaniakea@gmail.com>
#
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# Tests del adaptador Hermes y del plugin de middleware (H3).
# Verifican: traducción tool-call -> GenericToolCall, correlación de
# veredictos tool_request -> tool_execution, y bloqueo real (el executor
# nativo NO se invoca cuando el veredicto es block).

from __future__ import annotations

from unittest.mock import MagicMock

from agent_shield_runtime.adapters.generic import Channel
from agent_shield_runtime.adapters.hermes import HermesAdapter
from agent_shield_runtime.config import RuntimeConfig
from agent_shield_runtime.plugin import make_callbacks
from agent_shield_runtime.runtime import RuntimeVerdict, ShieldRuntime


def _make_runtime(executor=None) -> ShieldRuntime:
    cfg = RuntimeConfig(executor=executor, sensor_timeout=5.0)
    return ShieldRuntime(cfg)


class TestHermesAdapter:
    def test_to_generic_basic(self):
        runtime = _make_runtime()
        adapter = HermesAdapter(
            runtime=runtime,
            tool_config={"send_email": {"objective_arg": "to", "claimed_subobjective": "notify"}},
        )
        call = adapter.to_generic(
            tool_name="send_email",
            args={"to": "a@b.c", "subject": "hola"},
            task_id="t1",
            session_id="s1",
        )
        assert call.task_id == "t1"
        assert call.tool == "send_email"
        assert call.objective_arg == "to"
        assert call.claimed_subobjective == "notify"
        assert [a.name for a in call.args] == ["to", "subject"]
        assert all(a.channel == Channel.MODEL for a in call.args)

    def test_to_generic_prefers_task_id(self):
        runtime = _make_runtime()
        adapter = HermesAdapter(runtime=runtime)
        call = adapter.to_generic("x", {"a": 1}, task_id="task-x", session_id="session-y")
        assert call.task_id == "task-x"

    def test_to_generic_fallback_session_and_default(self):
        runtime = _make_runtime()
        adapter = HermesAdapter(runtime=runtime)
        c1 = adapter.to_generic("x", {"a": 1}, task_id="", session_id="sess")
        assert c1.task_id == "sess"
        c2 = adapter.to_generic("x", {"a": 1}, task_id="", session_id=None)
        assert c2.task_id == "default"

    def test_to_generic_tool_not_in_config(self):
        runtime = _make_runtime()
        adapter = HermesAdapter(runtime=runtime)
        call = adapter.to_generic("unknown_tool", {"p": "v"}, task_id="t", session_id=None)
        assert call.objective_arg is None
        assert call.claimed_subobjective == "unspecified"

    def test_to_generic_channel_infer_custom(self):
        runtime = _make_runtime()
        adapter = HermesAdapter(
            runtime=runtime,
            channel_infer=lambda v: Channel.TOOL_RESULT if "http" in v else Channel.MODEL,
        )
        call = adapter.to_generic("fetch", {"url": "https://x.com"}, task_id="t")
        assert call.args[0].channel == Channel.TOOL_RESULT


class TestPluginMiddleware:
    def _build(self, executor=None, verdict_decision="allow"):
        runtime = _make_runtime(executor=executor)
        adapter = HermesAdapter(runtime=runtime)
        cbs = make_callbacks(adapter, observe_only=False)
        return runtime, adapter, cbs

    def _verdict_for(self, runtime, call, decision):
        """Inyecta un veredicto directo (simula el resultado de evaluate)."""
        return RuntimeVerdict(decision, decision != "allow", [f"test:{decision}"])

    def test_block_does_not_invoke_next_call(self):
        """AC1: block => el tool nativo (next_call) NO se invoca."""
        next_call = MagicMock(return_value={"ok": True})
        _, _, cbs = self._build(executor=lambda *a: {"executed": True})

        # simular el flujo real: tool_request evalúa, tool_execution decide
        cbs["tool_request"](tool_name="terminal", args={"command": "rm -rf /"},
                            task_id="t1", session_id="s1", turn_id="turn1",
                            tool_call_id="tc1")
        # interceptar el veredicto: forzar block (dry-run real ya lo haría)
        from agent_shield_runtime import plugin as plugin_mod

        key = plugin_mod._verdict_key("tc1", "turn1", "terminal")
        plugin_mod._state.put(key, RuntimeVerdict("block", True, ["scope:deny"]))

        result = cbs["tool_execution"](
            tool_name="terminal", args={"command": "rm -rf /"},
            turn_id="turn1", tool_call_id="tc1", next_call=next_call,
        )
        assert result["error"] == "blocked_by_agent_shield"
        next_call.assert_not_called()

    def test_allow_invokes_next_call(self):
        next_call = MagicMock(return_value={"ok": True})
        _, _, cbs = self._build(executor=lambda *a: {"executed": True})
        cbs["tool_request"](tool_name="ls", args={}, task_id="t1",
                            session_id="s1", turn_id="turn1", tool_call_id="tc2")
        from agent_shield_runtime import plugin as plugin_mod

        key = plugin_mod._verdict_key("tc2", "turn1", "ls")
        plugin_mod._state.put(key, RuntimeVerdict("allow", False, ["ok"]))

        result = cbs["tool_execution"](
            tool_name="ls", args={}, turn_id="turn1", tool_call_id="tc2",
            next_call=next_call,
        )
        assert result == {"ok": True}
        next_call.assert_called_once()

    def test_confirm_does_not_invoke_next_call(self):
        next_call = MagicMock(return_value={"ok": True})
        _, _, cbs = self._build(executor=lambda *a: {"executed": True})
        cbs["tool_request"](tool_name="email", args={}, task_id="t1",
                            session_id="s1", turn_id="turn1", tool_call_id="tc3")
        from agent_shield_runtime import plugin as plugin_mod

        key = plugin_mod._verdict_key("tc3", "turn1", "email")
        plugin_mod._state.put(key, RuntimeVerdict("confirm", True, ["scope:confirm"]))

        result = cbs["tool_execution"](
            tool_name="email", args={}, turn_id="turn1", tool_call_id="tc3",
            next_call=next_call,
        )
        assert result["error"] == "confirmation_required"
        next_call.assert_not_called()

    def test_no_verdict_fail_open_executes(self):
        """Sin veredicto guardado => fail-open: se ejecuta (nunca rompe)."""
        next_call = MagicMock(return_value={"ok": True})
        _, _, cbs = self._build(executor=lambda *a: {"executed": True})
        result = cbs["tool_execution"](
            tool_name="ls", args={}, turn_id="turnX", tool_call_id="tc-none",
            next_call=next_call,
        )
        assert result == {"ok": True}
        next_call.assert_called_once()

    def test_observe_only_never_blocks(self):
        """AC6: modo observación registra pero NO bloquea."""
        runtime = _make_runtime(executor=lambda *a: {"executed": True})
        adapter = HermesAdapter(runtime=runtime)
        cbs = make_callbacks(adapter, observe_only=True)
        next_call = MagicMock(return_value={"ok": True})
        cbs["tool_request"](tool_name="rm", args={"path": "/x"}, task_id="t1",
                            session_id="s1", turn_id="turn1", tool_call_id="tc4")
        from agent_shield_runtime import plugin as plugin_mod

        key = plugin_mod._verdict_key("tc4", "turn1", "rm")
        plugin_mod._state.put(key, RuntimeVerdict("block", True, ["scope:deny"]))

        result = cbs["tool_execution"](
            tool_name="rm", args={"path": "/x"}, turn_id="turn1",
            tool_call_id="tc4", next_call=next_call,
        )
        assert result == {"ok": True}  # observa pero ejecuta
        next_call.assert_called_once()

    def test_prune_turn_limits_state(self):
        from agent_shield_runtime import plugin as plugin_mod

        plugin_mod._state.put("a:turn1", "v1")
        plugin_mod._state.put("b:turn1", "v2")
        plugin_mod._state.prune_turn("turn1")
        plugin_mod._state.put("c:turn2", "v3")
        plugin_mod._state.prune_turn("turn2")
        assert "a:turn1" not in plugin_mod._state.verdicts
        assert "c:turn2" in plugin_mod._state.verdicts
