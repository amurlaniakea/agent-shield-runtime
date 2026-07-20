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

"""Test e2e: ShieldRuntime orquesta los 5 sensores reales (AC1-AC5).

Usa los 5 paquetes instalados (no mocks de sensores). Solo el executor
nativo del agente es un mock que registra si fue llamado, para verificar que
un block REALMENTE impide la ejecución (AC1/AC2/AC4).
"""

from __future__ import annotations

import tempfile

from scope_lib import Policy

from agent_shield_runtime import (
    Channel,
    GenericArg,
    GenericToolCall,
    RuntimeConfig,
    ShieldRuntime,
)


def _make_config(tmp):
    calls: list = []

    def _exec(tool, args, tid):
        calls.append((tool, args, tid))
        return {"ok": True}

    cfg = RuntimeConfig(
        policy_store_path=tmp,
        human_secret="hsec",
        executor=_exec,
        budget={"fetch": 100.0, "send_email": 10.0, "send": 10.0},
    )
    cfg.recorded_calls = calls
    return cfg


def _policy(task="T1"):
    return Policy(
        policy_id="p1",
        task_id=task,
        authorized_subobjectives=["research_prices", "compare_options", "report_summary"],
        authorized_resources={"url": ["vuelos.com"], "doc": ["docs.google.com"]},
        deny=["attacker@x.com"],
        admission_budget={"search": 100.0, "send": 10.0},
        allowed_transitions={"read": ["write"]},
        authorized_irreversible=[],
        authorized_flows=[],
    )


def _anchor(rt, task="T1"):
    pol = _policy(task)
    rt.confirm_anchor(
        task,
        "investiga precios de vuelos a Madrid",
        ["research_prices", "compare_options", "report_summary"],
        policy=pol,
    )


def test_AC1_deny_blocks_and_does_not_execute():
    tmp = tempfile.mktemp(suffix=".json")
    rt = ShieldRuntime(_make_config(tmp))
    _anchor(rt)
    call = GenericToolCall(
        task_id="T1",
        tool="send_email",
        args=[
            GenericArg("to", "attacker@x.com", Channel.USER),
            GenericArg("body", "hola", Channel.USER),
        ],
        objective_arg="to",
        claimed_subobjective="report_summary",
    )
    v = rt.execute(call)
    assert v.decision == "block", v.reasons
    assert v.blocked
    assert rt.config.recorded_calls == [], "executor NO debe llamarse en block"


def test_AC1_legit_allows_and_executes():
    tmp = tempfile.mktemp(suffix=".json")
    rt = ShieldRuntime(_make_config(tmp))
    _anchor(rt)
    call = GenericToolCall(
        task_id="T1",
        tool="fetch",
        args=[GenericArg("url", "vuelos.com", Channel.USER)],
        objective_arg="url",
        claimed_subobjective="research_prices",
    )
    v = rt.execute(call)
    assert v.decision == "allow", v.reasons
    assert rt.config.recorded_calls != [], "executor SÍ debe llamarse en allow"


def test_AC2_injection_from_untrusted_blocks():
    tmp = tempfile.mktemp(suffix=".json")
    rt = ShieldRuntime(_make_config(tmp))
    _anchor(rt)
    call = GenericToolCall(
        task_id="T1",
        tool="send_email",
        args=[
            GenericArg("to", "attacker@x.com", Channel.TOOL_RESULT),
            GenericArg("body", "resumen", Channel.USER),
        ],
        objective_arg="to",
        claimed_subobjective="report_summary",
    )
    v = rt.execute(call)
    # adi-shield bloquea por instruction_from_data_deny
    assert v.decision == "block", v.reasons
    assert any("adi" in r for r in v.reasons)


def test_AC3_brusque_derivation_confirms():
    tmp = tempfile.mktemp(suffix=".json")
    rt = ShieldRuntime(_make_config(tmp))
    _anchor(rt)
    # 3 hitos con claimed fuera de ancla (deriva brusca T3-like)
    for _ in range(3):
        call = GenericToolCall(
            task_id="T1",
            tool="fetch",
            args=[GenericArg("url", "db-interna.com", Channel.USER)],
            objective_arg="url",
            claimed_subobjective="exfiltrate_data",
        )
        v = rt.execute(call)
    assert v.decision in ("confirm", "block"), v.reasons
    assert any("goal-anchor" in r or "correlate" in r for r in v.reasons)


def test_AC5_sensors_unmodified_integration():
    # El runtime integra los 5 sin tocarlos: verificamos que importa y corre
    # contra los paquetes instalados (no hay mock de sensor).
    tmp = tempfile.mktemp(suffix=".json")
    rt = ShieldRuntime(_make_config(tmp))
    _anchor(rt)
    call = GenericToolCall(
        task_id="T1",
        tool="fetch",
        args=[GenericArg("url", "vuelos.com", Channel.USER)],
        objective_arg="url",
        claimed_subobjective="research_prices",
    )
    v = rt.execute(call)
    assert v.decision == "allow"
