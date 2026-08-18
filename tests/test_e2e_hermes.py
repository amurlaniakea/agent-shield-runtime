# SPDX-FileCopyrightText: 2026 Pedro Sordo Martínez <amurlaniakea@gmail.com>
#
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# E2E: flujo completo middleware Hermes con los 5 sensores REALES instalados.
# AC1: tool-call cuyo destino está en `deny` de policy => block => el tool
# nativo (next_call) NO se invoca. Evidencia: next_call no llamado + veredicto.
# AC6: observe_only=True => registra pero ejecuta (no rompe el agente).
#
# Nota: el store de scope-lib persiste policies como dicts; para el E2E
# construimos la Policy directamente (mismo camino que tests/orchestration.py)
# y confirmamos el ancla con ella (el runtime cachea policy+anchor en memoria).

from __future__ import annotations

from scope_lib import Policy

from agent_shield_runtime.adapters.hermes import HermesAdapter
from agent_shield_runtime.config import RuntimeConfig
from agent_shield_runtime.plugin import make_callbacks
from agent_shield_runtime.runtime import ShieldRuntime


def _policy(task: str = "t1") -> Policy:
    return Policy(
        policy_id="p1",
        task_id=task,
        authorized_subobjectives=["leer_archivos"],
        authorized_resources={"path": ["/tmp/publico"]},
        deny=["/tmp/secretos/credenciales.txt"],  # igualdad exacta (scope-lib)
        admission_budget={"read_file": 100.0},
        allowed_transitions={},
        authorized_irreversible=[],
        authorized_flows=[],
    )


def _runtime(tmp_path) -> ShieldRuntime:

    store_path = str(tmp_path / "policy_store.json")
    runtime = ShieldRuntime(
        RuntimeConfig(
            policy_store_path=store_path,
            sensor_timeout=5.0,
            budget={"read_file": 100.0},
        )
    )
    runtime.confirm_anchor("t1", "leer archivos", ["leer_archivos"], policy=_policy())
    return runtime


def test_e2e_deny_blocks_and_does_not_execute(tmp_path):
    """AC1 real con los 5 sensores: deny en policy => block, sin ejecución."""
    runtime = _runtime(tmp_path)
    adapter = HermesAdapter(
        runtime=runtime,
        tool_config={"read_file": {"objective_arg": "path", "claimed_subobjective": "leer_archivos"}},
    )
    cbs = make_callbacks(adapter, observe_only=False)

    calls = []

    def next_call(args):
        calls.append(args)
        return {"executed": True}

    # tool-call a un path en deny
    cbs["tool_request"](
        tool_name="read_file",
        args={"path": "/tmp/secretos/credenciales.txt"},
        task_id="t1", session_id="s1", turn_id="turn1", tool_call_id="tc-deny",
    )
    result = cbs["tool_execution"](
        tool_name="read_file",
        args={"path": "/tmp/secretos/credenciales.txt"},
        turn_id="turn1", tool_call_id="tc-deny",
        next_call=next_call,
    )
    # el bloqueo es real
    assert result.get("error") == "blocked_by_agent_shield", f"result: {result}"
    # el tool nativo NO se invocó
    assert calls == [], f"next_call fue invocado: {calls}"


def test_e2e_allow_executes(tmp_path):
    """AC complementario: dentro de lo autorizado => se ejecuta."""
    runtime = _runtime(tmp_path)
    adapter = HermesAdapter(
        runtime=runtime,
        tool_config={"read_file": {"objective_arg": "path", "claimed_subobjective": "leer_archivos"}},
    )
    cbs = make_callbacks(adapter, observe_only=False)

    calls = []

    def next_call(args):
        calls.append(args)
        return {"executed": True}

    cbs["tool_request"](
        tool_name="read_file",
        args={"path": "/tmp/publico/nota.txt"},
        task_id="t1", session_id="s1", turn_id="turn1", tool_call_id="tc-allowed",
    )
    result = cbs["tool_execution"](
        tool_name="read_file",
        args={"path": "/tmp/publico/nota.txt"},
        turn_id="turn1", tool_call_id="tc-allowed",
        next_call=next_call,
    )
    assert result == {"executed": True}
    assert len(calls) == 1


def test_e2e_observe_only_logs_but_executes(tmp_path):
    """AC6: observe_only => aunque haya deny, se ejecuta (no rompe el agente)."""
    runtime = _runtime(tmp_path)
    adapter = HermesAdapter(
        runtime=runtime,
        tool_config={"read_file": {"objective_arg": "path", "claimed_subobjective": "leer_archivos"}},
    )
    cbs = make_callbacks(adapter, observe_only=True)

    calls = []

    def next_call(args):
        calls.append(args)
        return {"executed": True}

    cbs["tool_request"](
        tool_name="read_file",
        args={"path": "/tmp/secretos/credenciales.txt"},
        task_id="t1", session_id="s1", turn_id="turn1", tool_call_id="tc-obs",
    )
    result = cbs["tool_execution"](
        tool_name="read_file",
        args={"path": "/tmp/secretos/credenciales.txt"},
        turn_id="turn1", tool_call_id="tc-obs",
        next_call=next_call,
    )
    assert result == {"executed": True}
    assert len(calls) == 1  # observa pero ejecuta