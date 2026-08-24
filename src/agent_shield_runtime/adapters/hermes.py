# SPDX-FileCopyrightText: 2026 Pedro Sordo Martínez <amurlaniakea@gmail.com>
#
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version.

"""Adaptador Hermes para Agent Shield Runtime.

Traduce tool-calls del middleware nativo de Hermes (contrato
`hermes.middleware.v1`) al formato interno `GenericToolCall` que el runtime
entiende, preservando task_id/session_id y el mapeo tool → (objective_arg,
claimed_subobjective) configurable.

Nota de Channel (misma limitación que `adapters/langchain.py`): sin un
`history_provider`, toda detección de provenance vía historial queda
desactivada y los argumentos degradan a `Channel.MODEL`. El middleware de
Hermes recibe args ya resueltos (sin el historial de mensajes); conectar el
historial real de la sesión es trabajo futuro documentado en la spec.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ..runtime import ShieldRuntime
from .generic import Channel, GenericArg, GenericToolCall


class HermesAdapter:
    """Traduce tool-calls de Hermes a GenericToolCall para ShieldRuntime.

    `tool_config`: mapeo `{tool_name: {"objective_arg": str|None,
    "claimed_subobjective": str|None}}`. Si un tool no está en el mapa, se
    degrada a objective_arg=None, claimed_subobjective="unspecified".
    """

    def __init__(
        self,
        runtime: ShieldRuntime,
        tool_config: dict[str, dict[str, Any]] | None = None,
        channel_infer: Callable[[str], Channel] | None = None,
    ) -> None:
        self.runtime = runtime
        self.tool_config = tool_config or {}
        self._channel_infer = channel_infer or (lambda _value: Channel.MODEL)
        # Usado por plugin.py (register()) para detectar configuración ciega de
        # untrusted-to-action. NO renombrar sin actualizar register().
        self._channel_infer_is_default = channel_infer is None

    def to_generic(
        self,
        tool_name: str,
        args: dict[str, Any],
        task_id: str,
        session_id: str | None = None,
    ) -> GenericToolCall:
        """Construye GenericToolCall desde el payload del middleware Hermes.

        `task_id` se prefiere sobre `session_id` (scope-lib ancla por task);
        si ambos faltan, se usa "default" (nunca None, el runtime lo requiere).
        """
        cfg = self.tool_config.get(tool_name, {})
        generic_args = [
            GenericArg(name=k, value=v, channel=self._channel_infer(str(v)))
            for k, v in (args or {}).items()
        ]
        return GenericToolCall(
            task_id=task_id or session_id or "default",
            tool=tool_name,
            args=generic_args,
            objective_arg=cfg.get("objective_arg"),
            claimed_subobjective=cfg.get("claimed_subobjective", "unspecified"),
        )
