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

"""Tests de la orquestación: paralelismo, timeout y fail-open/fail-close."""

from __future__ import annotations

import time

from scope_lib import Policy

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
        authorized_resources={"url": ["vuelos.com"]},
        deny=["attacker@x.com"],
        admission_budget={"fetch": 100.0},
        allowed_transitions={},
        authorized_irreversible=[],
        authorized_flows=[],
    )


def _legit_call(task="T1"):
    return GenericToolCall(
        task_id=task,
        tool="fetch",
        args=[GenericArg("url", "vuelos.com", Channel.USER)],
        objective_arg="url",
        claimed_subobjective="research_prices",
    )


def _rt(timeout=0.5, fail_mode="closed"):
    import tempfile

    tmp = tempfile.mktemp(suffix=".json")
    calls: list = []
    cfg = RuntimeConfig(
        policy_store_path=tmp,
        human_secret="hsec",
        budget={"fetch": 100.0},
        sensor_timeout=timeout,
        fail_mode=fail_mode,
    )
    cfg.executor = lambda tool, args, tid: calls.append((tool, args, tid)) or {"ok": True}
    cfg.recorded_calls = calls
    rt = ShieldRuntime(cfg)
    rt.confirm_anchor(
        "T1",
        "investiga precios",
        ["research_prices", "compare_options", "report_summary"],
        policy=_policy(),
    )
    return rt


def test_parallel_timeout_fail_closed_blocks_on_slow_sensor():
    rt = _rt(timeout=0.2, fail_mode="closed")
    # Monkeypatch wallet para que se cuelgue mas alla del timeout
    orig = rt.wallet.evaluate

    def _slow(*a, **k):
        time.sleep(1.0)  # > timeout 0.2
        return orig(*a, **k)

    rt.wallet.evaluate = _slow
    v = rt.execute(_legit_call())
    # fail-closed: sensor lento cuenta como block -> decision block, no ejecuta
    assert v.decision == "block", v.reasons
    assert v.blocked
    assert any("wallet:sensor_unavailable" in r for r in v.reasons)
    assert rt.config.recorded_calls == [], "executor NO debe llamarse en fail-closed"


def test_parallel_timeout_fail_open_allows():
    rt = _rt(timeout=0.2, fail_mode="open")
    orig = rt.wallet.evaluate

    def _slow(*a, **k):
        time.sleep(1.0)
        return orig(*a, **k)

    rt.wallet.evaluate = _slow
    v = rt.execute(_legit_call())
    # fail-open: sensor lento cuenta como allow -> si todo lo demas allow, ejecuta
    assert v.decision == "allow", v.reasons
    assert rt.config.recorded_calls != [], "executor SÍ debe llamarse en fail-open"


def _rt_no_anchor(timeout=0.5, fail_mode="closed"):
    """Runtime SIN ancla confirmada (para probar timeout de scope sin anchor)."""
    import tempfile

    tmp = tempfile.mktemp(suffix=".json")
    calls: list = []
    cfg = RuntimeConfig(
        policy_store_path=tmp,
        human_secret="hsec",
        budget={"fetch": 100.0},
        sensor_timeout=timeout,
        fail_mode=fail_mode,
    )
    cfg.executor = lambda tool, args, tid: calls.append((tool, args, tid)) or {"ok": True}
    cfg.recorded_calls = calls
    rt = ShieldRuntime(cfg)
    # NO llamamos confirm_anchor -> no hay ancla confirmada
    return rt


def test_scope_timeout_fail_closed_with_confirmed_anchor():
    """
    Timeout de scope CON ancla confirmada.
    Debe degradar a block (fail-closed) SIN AttributeError en scope_v.criterion.
    """
    rt = _rt(timeout=0.2, fail_mode="closed")
    orig = rt.goal_anchor.report_drift  # no usado en este test, pero scope sí se llama

    def _slow_scope(*a, **k):
        time.sleep(1.0)  # > timeout 0.2
        return orig(*a, **k)

    # Monkeypatchear evaluate_scope a nivel de módulo para que se cuelgue
    import agent_shield_runtime.runtime as rtmod

    orig_eval = rtmod.evaluate_scope

    def _slow_eval(*a, **k):
        time.sleep(1.0)
        return orig_eval(*a, **k)

    rtmod.evaluate_scope = _slow_eval
    try:
        v = rt.execute(_legit_call())
        # fail-closed: sensor lento cuenta como block -> decision block, no ejecuta
        assert v.decision == "block", f"Esperado block, got {v.decision}: {v.reasons}"
        assert v.blocked
        assert any("scope:sensor_unavailable" in r for r in v.reasons)
        assert rt.config.recorded_calls == [], "executor NO debe llamarse en fail-closed"
    finally:
        rtmod.evaluate_scope = orig_eval


def test_scope_timeout_fail_closed_without_anchor():
    """
    Timeout de scope SIN ancla confirmada.
    Debe degradar a block (fail-closed) SIN AttributeError en scope_v.verdict.value.
    """
    rt = _rt_no_anchor(timeout=0.2, fail_mode="closed")

    import agent_shield_runtime.runtime as rtmod

    orig_eval = rtmod.evaluate_scope

    def _slow_eval(*a, **k):
        time.sleep(1.0)
        return orig_eval(*a, **k)

    rtmod.evaluate_scope = _slow_eval
    try:
        v = rt.execute(_legit_call())
        # fail-closed: sensor lento cuenta como block -> decision block, no ejecuta
        assert v.decision == "block", f"Esperado block, got {v.decision}: {v.reasons}"
        assert v.blocked
        assert any("scope:sensor_unavailable" in r for r in v.reasons)
        assert rt.config.recorded_calls == [], "executor NO debe llamarse en fail-closed"
    finally:
        rtmod.evaluate_scope = orig_eval


def test_scope_timeout_fail_open_allows():
    """
    Timeout de scope en fail-open.
    Debe degradar a allow y ejecutar (si los demás sensores allow).
    """
    rt = _rt(timeout=0.2, fail_mode="open")

    import agent_shield_runtime.runtime as rtmod

    orig_eval = rtmod.evaluate_scope

    def _slow_eval(*a, **k):
        time.sleep(1.0)
        return orig_eval(*a, **k)

    rtmod.evaluate_scope = _slow_eval
    try:
        v = rt.execute(_legit_call())
        # fail-open: sensor lento cuenta como allow -> si todo lo demas allow, ejecuta
        assert v.decision == "allow", f"Esperado allow, got {v.decision}: {v.reasons}"
        assert rt.config.recorded_calls != [], "executor SÍ debe llamarse en fail-open"
    finally:
        rtmod.evaluate_scope = orig_eval
