# SPDX-FileCopyrightText: 2026 Pedro Sordo Martínez <amurlaniakea@gmail.com>
#
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# Copyright (C) 2026 Pedro Sordo Martínez
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU Affero General Public License for more details.

"""P0: regresión de envenenamiento permanente de tarea (auto-DoS).

Una llamada maliciosa bloqueada correctamente NO debe paralizar la tarea
para siempre. El bug estabe en trajectory-sentinel.correlate(): bloqueaba por
cualquier senal de `block` en TODO el historial acumulado de la tarea, que
nunca se podaba -> la tarea quedaba bloqueada a perpetuidad tras el primer
bloqueo real. El fix (en runtime.py) aplica una ventana de las ultimas N
senales por task_id antes de llamar a correlate().

Este test aisla el componente arreglado (la ventana de correlacion) sin el
ruido de los cortes independientes de wallet-guard (budget/loop), que tienen
su propio TTL y no son el objeto de P0.
"""

from __future__ import annotations

import tempfile

from scope_lib import Policy
from trajectory_sentinel.correlation import correlate

from agent_shield_runtime import (
    Channel,
    GenericArg,
    GenericToolCall,
    RuntimeConfig,
    ShieldRuntime,
)


def _policy(task="T1"):
    return Policy(
        policy_id="p1",
        task_id=task,
        authorized_subobjectives=["research_prices", "compare_options", "report_summary"],
        authorized_resources={"url": ["vuelos.com"], "doc": ["docs.google.com"]},
        deny=["attacker@x.com"],
        admission_budget={"fetch": 100.0, "send_email": 10.0},
        allowed_transitions={},
        authorized_irreversible=[],
        authorized_flows=[],
    )


def _rt(window=10, timeout=0.5, fail_mode="closed", confirm_anchor=True):
    tmp = tempfile.mktemp(suffix=".json")
    calls: list = []
    cfg = RuntimeConfig(
        policy_store_path=tmp,
        human_secret="hsec",
        budget={"fetch": 100.0, "send_email": 10.0},
        sensor_timeout=timeout,
        fail_mode=fail_mode,
        correlation_window=window,
    )
    cfg.executor = lambda tool, args, tid: calls.append((tool, args, tid)) or {"ok": True}
    cfg.recorded_calls = calls
    rt = ShieldRuntime(cfg)
    if confirm_anchor:
        rt.confirm_anchor(
            "T1",
            "investiga precios",
            ["research_prices", "compare_options", "report_summary"],
            policy=_policy(),
        )
    return rt


def _malicious():
    return GenericToolCall(
        task_id="T1",
        tool="send_email",
        args=[
            GenericArg("to", "attacker@x.com", Channel.TOOL_RESULT),
            GenericArg("body", "resumen", Channel.USER),
        ],
        objective_arg="to",
        claimed_subobjective="report_summary",
    )


def _legit(i=0):
    subs = ["research_prices", "compare_options", "report_summary"]
    urls = ["vuelos.com", "docs.google.com", "maps.com"]
    return GenericToolCall(
        task_id="T1",
        tool="fetch",
        args=[GenericArg("url", urls[i % len(urls)], Channel.USER)],
        objective_arg="url",
        claimed_subobjective=subs[i % len(subs)],
    )


def _windowed_correlate(rt, task_id):
    """Reproduce la logica de ventana de runtime.py para inspeccionar el fix."""
    rec = rt.sentinel.report(task_id)
    if not rec or not rec.signals:
        return None
    window = rt.config.correlation_window
    recent = rec.signals[-window:] if window and window > 0 else rec.signals
    sigs = [
        {"sensor": s.sensor, "verdict": s.verdict, "event": s.event, "detail": s.detail}
        for s in recent
    ]
    return correlate(sigs)


def test_P0_recovery_after_block(window=3):
    rt = _rt(window=window, confirm_anchor=True)
    v0 = rt.execute(_malicious())
    assert v0.decision == "block", v0.reasons
    # 5 legítimas: el block (señal 1) sale de la ventana de 3 -> correlate allow
    for i in range(5):
        rt.execute(_legit(i))
    corr = _windowed_correlate(rt, "T1")
    assert corr is not None
    assert corr.verdict == "allow", corr.reason
    assert "hard_" not in corr.mechanism, f"block historico no debio persistir: {corr.mechanism}"


def test_P0_block_persists_inside_window(window=2):
    rt = _rt(window=window, confirm_anchor=True)
    rt.execute(_malicious())
    rt.execute(_legit(1))  # 1 legítima: block aun en ventana de 2
    corr = _windowed_correlate(rt, "T1")
    assert corr is not None
    assert corr.verdict == "block", corr.reason
    assert "hard_" in corr.mechanism, f"esperado block por historial: {corr.mechanism}"


def test_P0bis_legit_repeated_calls_do_not_permablock():
    # P0-bis: un agente 100% legítimo que usa el MISMO tool muchas veces con
    # args distintos NO debe quedar bloqueado para siempre. Antes del fix,
    # progress=0.0 hardcodeado hacia wallet-guard a cortar el bucle con retry
    # permanente. Ahora el proxy de progreso (args distintos => avance) debe
    # dejar pasar todas las llamadas.
    rt = _rt(window=3, confirm_anchor=True)
    urls = [f"vuelos.com/p{j}" for j in range(7)]  # inputs distintos
    blocked = 0
    for j in range(7):
        call = GenericToolCall(
            task_id="T1",
            tool="fetch",
            args=[GenericArg("url", urls[j], Channel.USER)],
            objective_arg="url",
            claimed_subobjective="research_prices",
        )
        v = rt.execute(call)
        assert v.decision in ("allow", "confirm"), v.reasons
        if v.decision == "block":
            blocked += 1
    assert blocked == 0, f"ninguna llamada legitima debio bloquearse: {blocked} bloqueadas"
    assert rt.config.recorded_calls != [], "todas las llamadas legitimas deben ejecutarse"


def test_P0bis_identical_repeat_still_capped():
    # Complementario: si el agente repite BYTE-A-BYTE la MISMA llamada (sin
    # progreso real), wallet-guard SÍ debe cortar el bucle (ese es su trabajo
    # legitimo). Esto confirma que el proxy no desactiva el corte de reintentos
    # identicos, solo el de inputs distintos.
    rt = _rt(window=3, confirm_anchor=True)
    same = GenericToolCall(
        task_id="T1",
        tool="fetch",
        args=[GenericArg("url", "vuelos.com/misma", Channel.USER)],
        objective_arg="url",
        claimed_subobjective="research_prices",
    )
    decisions = [rt.execute(same).decision for _ in range(7)]
    # las primeras 3 son allow/confirm; a partir de la 4 wallet corta (block)
    assert "block" in decisions, f"reintento identico debe cortarse: {decisions}"
