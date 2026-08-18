# SPDX-FileCopyrightText: 2026 Pedro Sordo Martínez <amurlaniakea@gmail.com>
#
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# Plugin autocontenido para `hermes plugins install <dir>`:
# envuelve agent_shield_runtime.plugin.register(ctx) y declara capabilities.
"""Agent Shield Runtime — middleware Hermes (plugin).

Conecta los 5 sensores de defensa (scope-lib, adi-shield, wallet-guard,
goal-anchor, trajectory-sentinel) con el runtime de Hermes interceptando cada
tool-call vía middleware nativo:

- `tool_request`: evalúa el tool-call contra los sensores (dry-run).
- `tool_execution`: bloquea/confirma/permite según el veredicto.

Modo observación por defecto (NO bloquea; registra veredictos en el log).
Activar bloqueo: `ctx.shield_observe_only = False` o config del plugin.
"""

from __future__ import annotations

from typing import Any


def register(ctx: Any) -> None:
    """Registra el middleware de Agent Shield Runtime en Hermes."""
    # import diferido para que el plugin se cargue aunque los sensores
    # no estén instalados (fail-open: plugin inerte con warning)
    try:
        from agent_shield_runtime.plugin import register as register_shield
    except Exception as exc:  # pragma: no cover
        import logging

        logging.getLogger(__name__).warning(
            "agent-shield plugin inerte: %s", exc
        )
        return
    register_shield(ctx)


__all__ = ["register"]
