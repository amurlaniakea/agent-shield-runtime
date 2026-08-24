# SPDX-FileCopyrightText: 2026 Pedro Sordo Martínez <amurlaniakea@gmail.com>
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Test de regresión del refactor _fake (Bloque 2):

El _fake de H3 (y su corrección Opción A) debe exponer `.value` y `.criterion`
porque el código de agregación de evaluate() lee `scope_v.verdict.value` y
`scope_v.criterion` SOBRE el veredicto de Scope. Si Scope es el sensor que
timeout/excepción y el _fake no tiene esos atributos, la agregación lanza
AttributeError. Este test fuerza explícitamente que SEA SCOPE el sensor roto
(no adi/wallet como el test existente), y verifica que tanto `verdict.value`
como `criterion` funcionan sin crash, y que el veredicto resultante es block.
"""
from __future__ import annotations

from unittest import mock

from agent_shield_runtime.adapters.hermes import HermesAdapter
from agent_shield_runtime.config import RuntimeConfig
from agent_shield_runtime.plugin import make_callbacks
from agent_shield_runtime.runtime import ShieldRuntime

from scope_lib import Policy


def _policy(task: str = "t1") -> Policy:
    return Policy(
        policy_id="p1", task_id=task,
        authorized_subobjectives=["leer_archivos"],
        authorized_resources={"path": ["/tmp/publico"]},
        deny=["/tmp/secretos/credenciales.txt"],
        admission_budget={"read_file": 100.0},
        allowed_transitions={}, authorized_irreversible=[], authorized_flows=[],
    )


def _runtime(tmp_path, fail_mode="closed") -> ShieldRuntime:
    return ShieldRuntime(RuntimeConfig(
        policy_store_path=str(tmp_path / "ps.json"),
        sensor_timeout=5.0, budget={"read_file": 100.0}, fail_mode=fail_mode,
    ))


def test_scope_sensor_timeout_exposes_value_and_criterion(tmp_path):
    """Scope (sensor 1) lanza DENTRO de _run_parallel => _fake block.
    El código de agregación accede a scope_v.verdict.value y scope_v.criterion.
    Con el _fake corregido (Opción A) ambos existen => NO AttributeError, y
    el veredicto agregado es block (fail-closed)."""
    runtime = _runtime(tmp_path, fail_mode="closed")
    runtime.confirm_anchor("t1", "leer archivos", ["leer_archivos"], policy=_policy())
    adapter = HermesAdapter(
        runtime=runtime,
        tool_config={"read_file": {"objective_arg": "path", "claimed_subobjective": "leer_archivos"}},
    )
    cbs = make_callbacks(adapter, observe_only=False)

    executed = []
    def next_call(args):
        executed.append(args)
        return "native_result"

    # Scope lanza dentro de _run_parallel => _fake block con .value/.criterion.
    # Parchear evaluate_scope (scope_lib) para que Scope explote exactamente
    # igual que un timeout/excepción real del sensor.
    with mock.patch("agent_shield_runtime.runtime.evaluate_scope", side_effect=RuntimeError("scope exploded")):
        cbs["tool_request"](
            tool_name="read_file", args={"path": "/tmp/publico/hola.txt"},
            task_id="t1", session_id="s1", turn_id="turn1", tool_call_id="tc-scope-err",
        )
        r = cbs["tool_execution"](
            tool_name="read_file", args={"path": "/tmp/publico/hola.txt"},
            turn_id="turn1", tool_call_id="tc-scope-err", next_call=next_call,
        )

    # Sin AttributeError: el veredicto debe ser block (fail-closed por scope roto)
    assert isinstance(r, dict), f"esperado dict de bloqueo, fue: {r!r}"
    assert r["error"] == "blocked_by_agent_shield", f"scope roto debe bloquear: {r!r}"
    joined = " ".join(r["reasons"])
    assert "scope:" in joined, f"el reason debe venir de scope_v: {r['reasons']}"
    assert len(executed) == 0, "scope roto => block => no ejecuta next_call"
