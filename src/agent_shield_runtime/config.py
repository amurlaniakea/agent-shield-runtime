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

"""Configuración del runtime: paths, modo de acción, umbrales."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass
class RuntimeConfig:
    """Configuración de ShieldRuntime.

    `policy_store_path`: ruta al PolicyStore de scope-lib (policy + anchors).
    `human_secret`: secreto para firmar/verificar anclas (goal-anchor).
    `on_confirm`: qué hacer ante un veredicto 'confirm' (pausar/devolver).
      Por defecto devuelve un veredicto 'confirm' y NO ejecuta (fail-safe).
    `block_on_confirm`: si True, un 'confirm' se trata como bloqueo (más
      estricto). Por defecto False (pausa a humano).
    """

    policy_store_path: str = ""
    human_secret: str = ""
    block_on_confirm: bool = False
    # Budget de admisión por categoría para wallet-guard (p.ej. {"search": 100.0}).
    budget: dict[str, float] = field(default_factory=dict)
    # Inyección del executor nativo del agente. El runtime lo llama SOLO
    # cuando todos los sensores dicen allow.
    executor: Callable[[str, list, str], Any] | None = None

    # Contenedor para que los tests inspeccionen ejecuciones (no usado en
    # produccion). Se puebla por el executor inyectado si se desea.
    recorded_calls: list = field(default_factory=list, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.executor is None:
            # Executor por defecto: simula la ejecución nativa. Los tests lo
            # sustituyen por un mock que registra si fue llamado.
            self.executor = _default_executor


def _default_executor(tool: str, args: list, task_id: str) -> dict:
    return {"executed": True, "tool": tool, "task_id": task_id}
