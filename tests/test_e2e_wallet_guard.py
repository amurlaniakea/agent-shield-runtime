# SPDX-FileCopyrightText: 2026 Pedro Sordo Martínez <amurlaniakea@gmail.com>
# SPDX-License-Identifier: AGPL-3.0-or-later
"""E2E wallet-guard: verifica con evidencia REAL (no mock) que el runtime llama
a `WalletGuard.evaluate(task_id, tool, cost, progress)` y que, al agotarse el
presupuesto de admision, el bloqueo proviene expresamente de wallet-guard.

Aísla wallet-guard del resto de sensores:
- policy autoriza read_file sobre /tmp/publico (=> scope=allow).
- args de Channel.MODEL (=> adi=allow, args non-untrusted).
- budget read_file=1.0 => 1er call allow (wallet tiene budget), 2º call block
  SOLO por wallet (presupuesto agotado). La aserción 'wallet' in reasons
  fallaría si otro sensor bloqueara en su lugar (falso negativo evitado).
"""

from __future__ import annotations

from agent_shield_runtime.adapters.hermes import HermesAdapter
from agent_shield_runtime.config import RuntimeConfig
from agent_shield_runtime.plugin import make_callbacks
from agent_shield_runtime.runtime import ShieldRuntime

try:
    from scope_lib import Policy
except ImportError:  # pragma: no cover - deps del entorno de prueba
    Policy = None  # type: ignore[assignment]


def _policy(task: str = "t1"):
    if Policy is None:  # pragma: no cover
        return None
    return Policy(
        policy_id="p1",
        task_id=task,
        authorized_subobjectives=["leer_archivos"],
        authorized_resources={"path": ["/tmp/publico"]},
        deny=["/tmp/secretos/credenciales.txt"],  # recurso DISTINTO al autorizado
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
            budget={"read_file": 1.0},  # 1 unidad: agota en el primer call
        )
    )
    runtime.confirm_anchor("t1", "leer archivos", ["leer_archivos"], policy=_policy())
    return runtime


def test_wallet_guard_blocks_after_budget_exhaustion(tmp_path):
    """E2E REAL: runtime.evaluate() -> WalletGuard.evaluate() -> block por budget.

    Usa HermesAdapter real (tiene .runtime, que make_callbacks necesita), no un
    noop. El runtime corre los 5 sensores para read_file sobre /tmp/publico:
    scope allow (recurso autorizado), adi allow (Channel.MODEL), wallet permite
    en el 1er call (budget disponible) y BLOQUEA en el 2º (budget agotado).
    La aserción 'wallet' in reasons verifica que el bloqueo es de wallet-guard,
    no de otro sensor.
    """
    runtime = _runtime(tmp_path)
    adapter = HermesAdapter(
        runtime=runtime,
        tool_config={
            "read_file": {
                "objective_arg": "path",
                "claimed_subobjective": "leer_archivos",
            }
        },
    )
    cbs = make_callbacks(adapter, observe_only=False)

    executed = []

    def next_call(args):
        executed.append(args)
        return "native_result"

    call_kwargs = dict(
        tool_name="read_file",
        args={"path": "/tmp/publico/hola.txt"},  # AUTORIZADO (no secreto)
        task_id="t1",
        session_id="s1",
        turn_id="turn1",
        tool_call_id="call_1",
    )

    # ---- Paso 1: primer call => wallet tiene budget => todo allow => ejecuta ----
    cbs["tool_request"](**call_kwargs)
    r1 = cbs["tool_execution"](**call_kwargs, next_call=next_call)
    assert r1 == "native_result", f"1er call debería allow+executar, fue: {r1!r}"
    assert len(executed) == 1, "el 1er call ejecutó el native tool (wallet no bloqueó)"

    # ---- Paso 2: segundo call => wallet agota budget => block POR wallet ----
    call_kwargs["tool_call_id"] = "call_2"
    cbs["tool_request"](**call_kwargs)
    r2 = cbs["tool_execution"](**call_kwargs, next_call=next_call)

    assert r2["error"] == "blocked_by_agent_shield", f"no fue bloqueado: {r2!r}"
    wallet_blocked = any("wallet" in x for x in r2["reasons"])
    assert wallet_blocked, (
        f"el block NO viene de wallet-guard (otro sensor bloqueó?): {r2['reasons']}"
    )
    assert len(executed) == 1, "el 2º call NO debería ejecutar el native tool (wallet bloqueó)"
