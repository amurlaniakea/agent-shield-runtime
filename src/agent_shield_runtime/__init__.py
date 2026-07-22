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

"""agent-shield-runtime: hook de despliegue del ecosistema de defensa."""

from __future__ import annotations

from .adapters import Channel, GenericArg, GenericToolCall
from .config import RuntimeConfig
from .runtime import RuntimeVerdict, ShieldRuntime

__all__ = [
    "ShieldRuntime",
    "RuntimeVerdict",
    "RuntimeConfig",
    "Channel",
    "GenericArg",
    "GenericToolCall",
]
