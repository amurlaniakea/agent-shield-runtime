# SPDX-FileCopyrightText: 2026 Pedro Sordo Martínez <amurlaniakea@gmail.com>
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Test de contraste determinista: un sensor que LANZA DENTRO de _run_parallel

NO debe disparar el fail-closed del except de on_tool_request. El fail-closed
de la Opción A es SOLO para excepciones del adapter/runtime que ocurren
ANTES/AFUERA de _run_parallel (zero sensores evaluaron). Dentro de _run_parallel,
las excepciones de sensores individuales son atrapadas por _fake(fail_mode)
y producen un veredicto usable (block o allow según config) -> el middleware
NO debe bloquear todo el tool por eso (seguir fail-open si fail_mode="open",
o acceptar el _fake block si fail_mode="closed").

Esto confirma que el fix de fail-closed no rompe el comportamiento EXISTENTE de
degradación tolerante para sensores individuales.
"""
from __future__ import annotations

from unittest import mock

from agent_shield_runtime.adapters.generic import Channel, GenericArg, GenericToolCall
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


def _runtime(tmp_path, fail_mode="open") -> ShieldRuntime:
    return ShieldRuntime(RuntimeConfig(
        policy_store_path=str(tmp_path / "ps.json"),
        sensor_timeout=5.0, budget={"read_file": 100.0}, fail_mode=fail_mode,
    ))


def test_sensor_exception_inside_run_parallel_does_not_fail_closed(tmp_path):
    """Un sensor que lanza DENTRO de _run_parallel => _fake(fail_mode) =>
    veredicto usable. NO dispara el except de on_tool_request.

    Forzado de forma DETERMINISTA: monkeypatch runtime.wallet.evaluate para que
    lance RuntimeError (como si wallet-guard hubiera un bug interno). Con
    fail_mode="open", _fake => allow => next_call ejecuta. Esto prueba que:
    (a) el except fail-closed NO capta excepciones de sensores individuales.
    (b) el comportamiento fail-open para sensores degradados sigue igual.
    """
    runtime = _runtime(tmp_path, fail_mode="open")
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

    # wallet-guard (un sensor) lanza DENTRO de _run_parallel => debe ser atrapado
    # por _fake(fail_mode="open") => allow, NO por el except de on_tool_request.
    with mock.patch.object(runtime.wallet, "evaluate", side_effect=RuntimeError("sensor exploded")):
        cbs["tool_request"](
            tool_name="read_file", args={"path": "/tmp/publico/hola.txt"},
            task_id="t1", session_id="s1", turn_id="turn1", tool_call_id="tc-sensor-err",
        )
        r = cbs["tool_execution"](
            tool_name="read_file", args={"path": "/tmp/publico/hola.txt"},
            turn_id="turn1", tool_call_id="tc-sensor-err", next_call=next_call,
        )

    # fail_mode=open + sensor lanzó => _fake(allow) => SÍ ejecuta (no fail-closed)
    assert r == "native_result", f"sensor roto en _run_parallel + fail_mode=open debe allow+executar, fue: {r!r}"
    assert len(executed) == 1, "el sensor roto no debe bloquear todo el tool (fail-open para sensores)"
