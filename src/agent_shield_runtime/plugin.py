# SPDX-FileCopyrightText: 2026 Pedro Sordo Martínez <amurlaniakea@gmail.com>
#
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version.

"""Plugin Hermes: middleware de Agent Shield Runtime.

Registra `tool_request` (evalúa contra los 5 sensores SIN ejecutar) y
`tool_execution` (bloquea/confirma/permite según el veredicto guardado).

Semántica (spec H3, docs/middleware/README.md de Hermes):
- `tool_request` corre ANTES de guardrails/approvals. Aquí evaluamos y
  guardamos el veredicto correlacionado por `tool_call_id` (con fallback por
  (turn_id, tool_name)). NO reescribimos args: la policy de Hermes evalúa los
  args reales.
- `tool_execution` corre envuelto en la ejecución real. Si hay veredicto
  block/confirm, NO llamamos `next_call` (el tool nativo no se invoca) y
  devolvemos un resultado de bloqueo. Si allow o no hay veredicto, fail-open:
  ejecutamos.
- Fallo del plugin: fail-open (capturamos excepciones, logueamos warning y
  dejamos ejecutar) — un fallo del shield nunca rompe el agente.

Modo observación: con `observe_only=True` (config) NUNCA bloquea; solo
registra los veredictos (AC6 de la spec — primera integración conservadora).
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

try:
    from agent_shield_runtime.adapters.hermes import HermesAdapter
    from agent_shield_runtime.runtime import ShieldRuntime
except Exception:  # pragma: no cover - fail-open si los sensores no están
    HermesAdapter = None  # type: ignore[assignment,misc]
    ShieldRuntime = None  # type: ignore[assignment,misc]


class _ShieldState:
    """Estado del plugin: veredictos por tool_call_id, limpio por turno."""

    def __init__(self) -> None:
        self.verdicts: dict[str, Any] = {}
        self._seen_turns: set[str] = set()

    def put(self, key: str, verdict: Any) -> None:
        self.verdicts[key] = verdict

    def pop(self, key: str) -> Any | None:
        return self.verdicts.pop(key, None)

    def prune_turn(self, turn_id: str | None) -> None:
        """Limpia veredictos de turnos previos (evita fuga de memoria)."""
        if turn_id is None:
            return
        if turn_id not in self._seen_turns:
            self._seen_turns.add(turn_id)
            # conservar solo el turno actual
            self.verdicts = {k: v for k, v in self.verdicts.items() if k.endswith(f":{turn_id}")}


_state = _ShieldState()


def _verdict_key(tool_call_id: str | None, turn_id: str | None, tool_name: str) -> str:
    """Clave de correlación con fallback: tool_call_id si hay, si no (turn,tool)."""
    if tool_call_id:
        return f"{tool_call_id}:{turn_id or 'no-turn'}"
    return f"{tool_name}:{turn_id or 'no-turn'}"


def build_shield(
    runtime: ShieldRuntime,
    tool_config: dict[str, dict[str, Any]] | None = None,
    observe_only: bool = False,
) -> HermesAdapter:
    """Construye el adaptador listo para los callbacks del plugin."""
    return HermesAdapter(runtime=runtime, tool_config=tool_config)


def make_callbacks(adapter: HermesAdapter, observe_only: bool = False) -> dict[str, Any]:
    """Devuelve {tool_request, tool_execution} cerrados sobre el adaptador."""

    def on_tool_request(**kwargs: Any) -> None:
        """Evalúa el tool-call contra los 5 sensores (dry-run) y guarda el veredicto."""
        try:
            tool_name = kwargs.get("tool_name")
            args = kwargs.get("args") or {}
            task_id = kwargs.get("task_id") or ""
            session_id = kwargs.get("session_id")
            turn_id = kwargs.get("turn_id")
            tool_call_id = kwargs.get("tool_call_id")
            if not tool_name:
                return
            _state.prune_turn(turn_id)
            call = adapter.to_generic(tool_name, args, task_id, session_id)
            verdict = adapter.runtime.evaluate(call)  # dry-run, sin ejecutar
            key = _verdict_key(tool_call_id, turn_id, tool_name)
            _state.put(key, verdict)
            logger.info(
                "agent-shield evaluate %s -> %s (%s)",
                tool_name, verdict.decision, ";".join(verdict.reasons[:3]),
            )
        except Exception as exc:  # fail-open: el shield nunca rompe el agente
            logger.warning("agent-shield tool_request error (fail-open): %s", exc)

    def on_tool_execution(**kwargs: Any) -> Any:
        """Ejecuta o bloquea según el veredicto guardado en tool_request."""
        try:
            tool_name = kwargs.get("tool_name")
            turn_id = kwargs.get("turn_id")
            tool_call_id = kwargs.get("tool_call_id")
            args = kwargs.get("args") or {}
            next_call = kwargs.get("next_call")
            key = _verdict_key(tool_call_id, turn_id, tool_name)
            verdict = _state.pop(key)
            if verdict is None:
                # fail-open sin veredicto: ejecutar normal (warning en log)
                logger.warning(
                    "agent-shield sin veredicto para %s (fail-open)", tool_name
                )
                if next_call:
                    return next_call(args)
                return None
            if verdict.decision == "allow":
                if observe_only:
                    logger.info("agent-shield OBSERVE allow %s", tool_name)
                if next_call:
                    return next_call(args)
                return None
            if observe_only:
                # modo observación: registra pero NO bloquea (AC6)
                logger.warning(
                    "agent-shield OBSERVE %s %s (no bloqueado): %s",
                    verdict.decision, tool_name, ";".join(verdict.reasons[:3]),
                )
                if next_call:
                    return next_call(args)
                return None
            if verdict.decision == "block":
                return {
                    "error": "blocked_by_agent_shield",
                    "tool": tool_name,
                    "reasons": verdict.reasons,
                }
            if verdict.decision == "confirm":
                return {
                    "error": "confirmation_required",
                    "tool": tool_name,
                    "reasons": verdict.reasons,
                }
            # veredicto desconocido: fail-open
            if next_call:
                return next_call(args)
            return None
        except Exception as exc:  # fail-open: el shield nunca rompe el agente
            logger.warning("agent-shield tool_execution error (fail-open): %s", exc)
            next_call = kwargs.get("next_call")
            if next_call:
                return next_call(kwargs.get("args") or {})
            return None

    return {"tool_request": on_tool_request, "tool_execution": on_tool_execution}


def register(ctx: Any) -> None:
    """Registra el middleware en Hermes (contrato hermes.middleware.v1).

    `ctx.register_middleware("tool_request", fn)` /
    `ctx.register_middleware("tool_execution", fn)`.
    """
    # Opcional: el plugin puede leer su config del ctx si Hermes la expone.
    tool_config = getattr(ctx, "shield_tool_config", None) or {}
    observe_only = bool(getattr(ctx, "shield_observe_only", True))
    if ShieldRuntime is None or HermesAdapter is None:
        logger.warning("agent-shield plugin inerte: sensores no instalados")
        return
    # El runtime se construye desde el plugin; config mínima con executor None
    # (evaluate() nunca ejecuta; execute() no se usa desde el middleware).
    from agent_shield_runtime.config import RuntimeConfig

    runtime = ShieldRuntime(RuntimeConfig(policy_store_path="", executor=None))
    adapter = HermesAdapter(runtime=runtime, tool_config=tool_config)
    callbacks = make_callbacks(adapter, observe_only=observe_only)
    ctx.register_middleware("tool_request", callbacks["tool_request"])
    ctx.register_middleware("tool_execution", callbacks["tool_execution"])
    logger.info("agent-shield middleware registrado (observe_only=%s)", observe_only)
