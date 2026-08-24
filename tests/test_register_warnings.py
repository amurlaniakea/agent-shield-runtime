# SPDX-FileCopyrightText: 2026 Pedro Sordo Martínez <amurlaniakea@gmail.com>
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests de los avisos de degradación de cobertura en register().

Usa el CONTRATO REAL del harness: ctx.get_config(key, default) lee de
plugins.entries.<plugin_id>.settings.* del config.yaml. Los atributos
shield_* NO existen en el ctx real (PluginContext no los expone), así que
el mock debe implementar get_config, no poblar atributos shield_*.

Caso malo (sin settings): 2 warnings visibles (channel ciego + policy vacía).
Caso sano (settings con policy_store_path real): 0 warnings de policy, 1 de
channel (porque channel_infer no es serializable en YAML => default ciego).
Sigue la disciplina de contraste: probar AMBOS lados del if.
"""

from __future__ import annotations

import logging

from agent_shield_runtime.plugin import register


class _CtxReal:
    """Mock fiel al contrato PluginContext.get_config del harness real."""

    def __init__(self, settings: dict | None = None):
        self._settings = settings or {}
        self.registered = []

    def get_config(self, key, default=None):
        return self._settings.get(key, default)

    def register_middleware(self, name, fn):
        self.registered.append(name)


def test_register_default_settings_emits_two_blindness_warnings(capsys, caplog):
    ctx = _CtxReal()  # sin settings => observe_only=True, policy_store_path=""
    with caplog.at_level(logging.WARNING, logger="agent_shield_runtime.plugin"):
        register(ctx)

    out = capsys.readouterr().out
    printed = [line for line in out.splitlines() if line.startswith("[AGENT-SHIELD]")]
    assert len(printed) == 2, f"default debe dar 2 avisos, fueron: {printed}"

    agent_warns = [
        r.getMessage()
        for r in caplog.records
        if r.levelno == logging.WARNING and r.getMessage().startswith("AGENT-SHIELD CONFIG")
    ]
    assert len(agent_warns) == 2
    joined = " ".join(agent_warns)
    assert "UNTRUSTED-TO-ACTION DESACTIVADO" in joined
    assert "no_active_anchor" in joined and "FileNotFoundError" in joined
    # el middleware SÍ se registró pese a los warnings
    assert {"tool_request", "tool_execution"}.issubset(set(ctx.registered))


def test_register_with_real_policy_store_path_emits_only_channel_warning(capsys, caplog, tmp_path):
    # Caso sano de policy: settings.policy_store_path real => warning 2 (policy)
    # NO se dispara. El warning 1 (channel) SÍ se dispara porque channel_infer
    # no es configurable vía YAML (callable no serializable) => default ciego.
    ps_path = str(tmp_path / "ps.json")
    ctx = _CtxReal({"policy_store_path": ps_path, "observe_only": False})
    with caplog.at_level(logging.WARNING, logger="agent_shield_runtime.plugin"):
        register(ctx)

    out = capsys.readouterr().out
    printed = [line for line in out.splitlines() if line.startswith("[AGENT-SHIELD]")]
    # exactamente 1 warning: solo el de channel (policy resuelto con path real)
    assert len(printed) == 1, f"con policy real debe dar 1 aviso (channel), fue: {printed}"

    agent_warns = [
        r.getMessage()
        for r in caplog.records
        if r.levelno == logging.WARNING and r.getMessage().startswith("AGENT-SHIELD CONFIG")
    ]
    assert len(agent_warns) == 1
    assert "UNTRUSTED-TO-ACTION DESACTIVADO" in agent_warns[0]
    assert "no_active_anchor" not in agent_warns[0], "policy resuelto => no warning 2"
    # el middleware se registra igualmente (y con observe_only=False real)
    assert {"tool_request", "tool_execution"}.issubset(set(ctx.registered))
