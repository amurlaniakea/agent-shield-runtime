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

from collections.abc import Callable
from dataclasses import dataclass, field

from adi_shield.bus import LocalSignalBus
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
        # Estado de progreso por (task_id, tool) para el proxy de wallet-guard
        # (P0-bis): {"args": tupla, "progress": float}. Ver SDD §12 (P0-bis).
        self._progress_state: dict[tuple[str, str], dict] = {}

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

        # Sensores INDEPENDIENTES (scope / adi / wallet) corren en PARALELO
        # para recortar latencia al del mas lento. Cada uno se envuelve con
        # timeout + fail_mode: si no responde o lanza, se trata como block
        # (fail-closed por defecto) o allow (fail-open), segun config.
        action = self._to_action(call, anchor)
        adi_call = self._to_adi_call(call)

        # P0-bis: proxy de progreso para wallet-guard. El runtime no conoce el
        # progreso real de la tarea; usa como heuristica si los args de esta
        # llamada difieren de los de la llamada anterior para (task_id, tool).
        # Args distintos => el contador de progreso sube 1.0 (avance); iguales
        # => se queda igual (sin avance). wallet-guard compara progress >
        # last_progress (estricto), asi que subir monotonicamente es lo que
        # interpreta como "la tarea avanza" y no corta el bucle. No es progreso
        # semantico real, es una heuristica de "la llamada no es un reintento
        # byte-a-byte del mismo input". Ver SDD §12 (P0-bis).
        key = (call.task_id, call.tool)
        prev = self._progress_state.get(key)
        cur_args = tuple((a.name, a.value, a.channel.name) for a in call.args)
        if prev is None:
            progress = 1.0  # primer intento: arranca con avance
        elif prev["args"] == cur_args:
            progress = prev["progress"]  # reintento byte-a-byte: sin avance
        else:
            progress = prev["progress"] + 1.0  # input distinto: avanza 1 paso

        def _scope() -> object:
            return evaluate_scope(action, policy, anchor)

        def _adi() -> object:
            return self.adi.evaluate(adi_call)

        def _wallet() -> object:
            return self.wallet.evaluate(call.task_id, call.tool, cost=1.0, progress=progress)

        scope_v, adi_dec, w_dec = self._run_parallel(
            {"scope": _scope, "adi": _adi, "wallet": _wallet}
        )

        # actualizar estado de progreso para la proxima llamada (mismo task+tool)
        self._progress_state[key] = {"args": cur_args, "progress": progress}

        # adi-shield YA publica su veredicto al bus en evaluate() (ver
        # adi_shield/detector.py). NO se republica aqui: duplicar distorsiona
        # la ventana de correlacion (P0) y el bus del trajectory-sentinel.
        # goal-anchor publica su propia senal de deriva en el bloque de abajo.

        # 4. goal-anchor (deriva) — SECUENCIAL: depende del ancla confirmada
        if anchor is not None and anchor.confirmed_by_user:
            # P1: usar el criterion REAL que emite scope-lib (i_/ii_/iii_transitive/
            # deny/none), NO el literal "iii_transitive". Pasar el literal sesgaba
            # la sub-senal de deriva (siempre 100% transitivo) y podia generar
            # falsos confirm en tareas largas legítimas.
            drift = self.goal_anchor.report_drift(
                call.task_id,
                scope_v.criterion,
                call.claimed_subobjective,
                effect_text=action.target,
            )
            if drift is not None and drift.alert:
                self.bus.publish(drift.to_signal(call.task_id))

        # 5. trajectory-sentinel (correlación agregada) — SECUENCIAL: depende del bus
        rec = self.sentinel.report(call.task_id)
        # P0: podar el historial antes de correlar. Sin esto, un block
        # historico nunca desaparece y paraliza la tarea para siempre
        # (auto-DoS). Solo se pasan las ultimas N senales por task_id.
        if rec and rec.signals:
            window = self.config.correlation_window
            recent = rec.signals[-window:] if window and window > 0 else rec.signals
            signals = [
                {"sensor": s.sensor, "verdict": s.verdict, "event": s.event, "detail": s.detail}
                for s in recent
            ]
        else:
            signals = []
        corr = correlate(signals) if signals else None

        # ---- decisión agregada (worst-verdict, sin cambios) ----
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

    def _run_parallel(self, tasks: dict[str, Callable[[], object]]) -> tuple:
        """Corre sensores independientes en paralelo con timeout + fail_mode.

        Devuelve una tupla con los resultados en el MISMO orden que `tasks`.
        Si un sensor excede `sensor_timeout` o lanza, se sustituye por un
        veredicto de fallo segun `fail_mode`:
          - "closed" (defecto): cuenta como block (fail-closed).
          - "open": cuenta como allow (fail-open, riesgoso).
        """
        from concurrent.futures import Future, ThreadPoolExecutor

        timeout = self.config.sensor_timeout
        fail_closed = self.config.fail_mode != "open"

        def _fake(blocked: bool, why: str, sensor_name: str = "") -> object:
            """
            Veredicto sustituto que soporta TODOS los sensores:
            - ADI/Wallet: verdict (str), reason, mechanism, confidence
            - Scope: verdict (con .value como Verdict enum), criterion, reason
            """

            class _FakeVerdict:
                """Proxy que funciona como string para ADI/Wallet y como enum para Scope."""

                def __init__(self, value: str):
                    self._value = value

                @property
                def value(self) -> str:
                    return self._value

                def __eq__(self, other: object) -> bool:
                    if isinstance(other, str):
                        return self._value == other
                    if isinstance(other, _FakeVerdict):
                        return self._value == other._value
                    return False

                def __str__(self) -> str:
                    return self._value

                def __repr__(self) -> str:
                    return f"Verdict({self._value})"

            v = _FakeVerdict("block" if blocked else "allow")

            class _V:
                verdict = v
                reason = why
                mechanism = why
                confidence = 1.0
                criterion = "sensor_unavailable"

            return _V()

        results: dict[str, object] = {}
        with ThreadPoolExecutor(max_workers=len(tasks)) as ex:
            futures: dict[str, Future] = {k: ex.submit(fn) for k, fn in tasks.items()}
            for k, fut in futures.items():
                try:
                    if timeout > 0:
                        results[k] = fut.result(timeout=timeout)
                    else:
                        results[k] = fut.result()
                except Exception:  # timeout o excepcion del sensor
                    results[k] = _fake(
                        fail_closed,
                        f"{k}:sensor_unavailable(fail_{'closed' if fail_closed else 'open'})",
                    )
        return tuple(results[k] for k in tasks)
