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

"""ShieldRuntime: hook de despliegue que intercepta cada tool-call.

Orquesta los 5 sensores (scope-lib, adi-shield, wallet-guard, goal-anchor,
trajectory-sentinel) y ACTÚA sobre el veredicto agregado. NO reimplementa
ningún sensor: solo los llama y decide block/confirm/allow.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from adi_shield.bus import LocalSignalBus, Signal
from adi_shield.detector import ADIShield
from adi_shield.detector import ToolCall as ADIToolCall
from adi_shield.provenance import Arg as ADIArg
from adi_shield.provenance import Channel as ADIChannel
from goal_anchor.anchor import GoalAnchor
from scope_lib import Action, evaluate_scope, load_policy_store
from trajectory_sentinel.correlation import correlate
from trajectory_sentinel.monitor import TrajectorySentinel
from wallet_guard.detector import WalletGuard

from .adapters.generic import GenericToolCall
from .config import RuntimeConfig


@dataclass
class RuntimeVerdict:
    decision: str  # block | confirm | allow | executed
    blocked: bool
    reasons: list[str] = field(default_factory=list)
    result: dict | None = None  # resultado del executor si allow


SEVERITY = {"allow": 0, "confirm": 1, "block": 2}


class ShieldRuntime:
    """Intercepta tool-calls y los evalúa contra los 5 sensores."""

    def __init__(self, config: RuntimeConfig) -> None:
        self.config = config
        self.bus = LocalSignalBus()
        self.adi = ADIShield(
            bus=self.bus,
            policy_store_path=config.policy_store_path or None,
        )
        self.wallet = WalletGuard(bus=self.bus, budget=config.budget)
        self.goal_anchor = GoalAnchor(
            policy_store_path=config.policy_store_path,
            human_secret=config.human_secret,
        )
        self.sentinel = TrajectorySentinel(self.bus)
        self._anchors: dict = {}
        self._policies: dict = {}

    # ---- traducción GenericToolCall -> formatos de cada sensor ----
    def _to_action(self, call: GenericToolCall, anchor) -> Action:
        obj_arg = call.get(call.objective_arg) if call.objective_arg else None
        target = obj_arg.value if obj_arg is not None else None
        return Action(
            task_id=call.task_id,
            tool=call.tool,
            args={a.name: a.value for a in call.args},
            claimed_subobjective=call.claimed_subobjective,
            target=target,
            irreversible=call.irreversible,
        )

    def _to_adi_call(self, call: GenericToolCall) -> ADIToolCall:
        args = []
        for a in call.args:
            ch = ADIChannel.TOOL_RESULT if a.channel.untrusted else ADIChannel.USER
            args.append(ADIArg(name=a.name, value=a.value, channel=ch))
        return ADIToolCall(
            task_id=call.task_id,
            tool=call.tool,
            args=args,
            objective_arg=call.objective_arg,
            claimed_subobjective=call.claimed_subobjective,
        )

    # ---- ciclo de vida del ancla (RF5) ----
    def confirm_anchor(
        self, task_id: str, objective: str, subobjectives: list[str], policy=None
    ) -> None:
        from goal_anchor.anchor import AnchorProposal

        proposal = AnchorProposal(task_id=task_id, objective=objective, subobjectives=subobjectives)
        anchor = self.goal_anchor.propose(proposal)
        anchor = self.goal_anchor.confirm(anchor, policy=policy)
        # cache local para evaluate_scope
        self._anchors[task_id] = anchor
        self._policies[task_id] = policy

    def _load_anchor_policy(self, task_id: str):
        anchor = self._anchors.get(task_id)
        policy = self._policies.get(task_id)
        if anchor is None and self.config.policy_store_path:
            try:
                store = load_policy_store(self.config.policy_store_path)
                d = store.anchors.get(task_id)
                if d:
                    from scope_lib import Anchor

                    anchor = Anchor.from_dict(d)
                p = store.policies.get(task_id)
                if p:
                    from scope_lib import Policy

                    policy = Policy.from_dict(p)
            except (FileNotFoundError, ValueError):
                pass
        return anchor, policy

    # ---- interceptación principal ----
    def execute(self, call: GenericToolCall) -> RuntimeVerdict:
        anchor, policy = self._load_anchor_policy(call.task_id)

        # 1. scope-lib
        action = self._to_action(call, anchor)
        scope_v = evaluate_scope(action, policy, anchor)

        # 2. adi-shield
        adi_call = self._to_adi_call(call)
        adi_dec = self.adi.evaluate(adi_call)
        self.bus.publish(
            Signal(
                sensor="adi-shield",
                task_id=call.task_id,
                event="denial" if adi_dec.verdict == "block" else "tool_call",
                verdict=adi_dec.verdict,
                confidence=adi_dec.confidence,
                detail=adi_dec.mechanism,
            )
        )

        # 3. wallet-guard
        w_dec = self.wallet.evaluate(call.task_id, call.tool, cost=1.0, progress=0.0)
        # 4. goal-anchor (deriva) — lo reporta solo si hay un ancla activa
        if anchor is not None and anchor.confirmed_by_user:
            drift = self.goal_anchor.report_drift(
                call.task_id, "iii_transitive", call.claimed_subobjective, effect_text=action.target
            )
            if drift is not None and drift.alert:
                self.bus.publish(drift.to_signal(call.task_id))

        # 5. trajectory-sentinel (correlación agregada)
        rec = self.sentinel.report(call.task_id)
        signals = (
            [
                {"sensor": s.sensor, "verdict": s.verdict, "event": s.event, "detail": s.detail}
                for s in rec.signals
            ]
            if rec
            else []
        )
        corr = correlate(signals) if signals else None

        # ---- decisión agregada ----
        blockers = []
        if scope_v.verdict.value == "block":
            blockers.append(f"scope:{scope_v.reason}")
        if adi_dec.verdict == "block":
            blockers.append(f"adi:{adi_dec.mechanism}")
        if w_dec.verdict == "block":
            blockers.append(f"wallet:{w_dec.reason}")
        if corr is not None and corr.verdict == "block":
            blockers.append(f"correlate:{corr.mechanism}")

        confirms = []
        if scope_v.verdict.value == "confirm":
            confirms.append(f"scope:{scope_v.reason}")
        if adi_dec.verdict == "confirm":
            confirms.append(f"adi:{adi_dec.mechanism}")
        if w_dec.verdict == "confirm":
            confirms.append(f"wallet:{w_dec.reason}")
        if corr is not None and corr.verdict == "confirm":
            confirms.append(f"correlate:{corr.mechanism}")

        if blockers:
            return RuntimeVerdict("block", True, blockers)
        if confirms:
            if self.config.block_on_confirm:
                return RuntimeVerdict("block", True, confirms)
            return RuntimeVerdict("confirm", True, confirms)
        # todos allow -> ejecutar
        executor = self.config.executor
        if executor is None:  # defensivo: nunca debería ser None tras __post_init__
            return RuntimeVerdict("block", True, ["no executor configured"])
        result = executor(call.tool, [a.value for a in call.args], call.task_id)
        return RuntimeVerdict("allow", False, ["all sensors allow"], result)
