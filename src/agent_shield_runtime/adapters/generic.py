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

"""Adaptador genérico: formato interno del runtime.

El núcleo de ShieldRuntime SOLO conoce `GenericToolCall`. Los adaptadores de
framework (LangChain, AutoGen, ...) traducen el tool-call nativo a este formato
y viven aislados en `adapters/`. Así el runtime es reutilizable sin acoplarse
a ningún agente concreto (SDD R1).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class Channel(StrEnum):
    """Origen declarado de cada argumento (provenance)."""

    USER = "user"
    TOOL_RESULT = "tool_result"
    CONST = "const"
    MODEL = "model"

    @property
    def untrusted(self) -> bool:
        return self is Channel.TOOL_RESULT


@dataclass
class GenericArg:
    name: str
    value: Any
    channel: Channel = Channel.USER

    @property
    def is_untrusted(self) -> bool:
        return self.channel.untrusted


@dataclass
class GenericToolCall:
    """Tool-call neutral que el runtime entiende.

    `objective_arg` nombra el argumento que es el OBJETIVO/DESTINO de la
    acción (p.ej. el "to" de send_email) — adi-shield lo usa para
    INSTRUCTION_FROM_DATA. `claimed_subobjective` es el sub-objetivo que el
    agente declara servir (criterion i de scope-lib).
    """

    task_id: str
    tool: str
    args: list[GenericArg] = field(default_factory=list)
    objective_arg: str | None = None
    claimed_subobjective: str | None = None
    irreversible: bool = False

    def get(self, name: str) -> GenericArg | None:
        for a in self.args:
            if a.name == name:
                return a
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "tool": self.tool,
            "args": [
                {"name": a.name, "value": a.value, "channel": a.channel.value} for a in self.args
            ],
            "objective_arg": self.objective_arg,
            "claimed_subobjective": self.claimed_subobjective,
            "irreversible": self.irreversible,
        }
