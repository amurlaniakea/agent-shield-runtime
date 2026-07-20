# SDD — agent-shield-runtime (Hook de Despliegue / Orquestador de Sensores)

- **Proyecto:** agent-shield-runtime
- **Frente:** capa de DESPLIEGUE del ecosistema de defensa de agentes IA
  (scope-lib, adi-shield, wallet-guard, goal-anchor, trajectory-sentinel)
- **Estado:** SDD inicial (fase barrica, pre-código). GATE de aceptación:
  tras OK de Sil + auditoría de Claude, en session dedicada.
- **Licencia:** AGPL-3.0-or-later · **Autor:** Pedro Sordo Martínez
  (amurlaniakea@gmail.com) · **Año:** 2026

---

## 1. Propósito (una frase)

Conecta los 5 sensores de defensa (que hoy son librerías en el estante) con
el runtime del agente, interceptando CADA tool-call y evaluándolo a través de
los sensores antes de ejecutarlo — convirtiendo el ecosistema de "piezas de
laboratorio" en una defensa activa.

## 2. Por qué existe (problema verificado)

Los 5 sensores están implementados y auditados, pero NADIE los invoca en un
agente real. Demostrado empíricamente (2026-07-19): en un harness end-to-end
manual los 5 detectan y cortan (ADI block, deriva alert, bucle cortado), pero
en un agente sin hook, esas llamadas nunca ocurren y el agente queda
vulnerable pese a tener los paquetes instalados. Este repo cierra ese hueco
(RF1 de los 4 SDD de sensores). Es el eslabón que falta.

## 3. Usuarios / audiencia

- Cualquier agente (LangChain, AutoGen, loop propio, o framework futuro)
  cuyo runtime exponga "antes de ejecutar un tool, dame el tool-call".
- El operador humano que define policy/anchor (scope-lib) y revisa confirm.

## 4. Requisitos funcionales (MVP)

- **RF1 (interceptación):** envuelve el executor de tools del agente de forma
  que NINGÚN tool-call se ejecute sin pasar antes por los sensores. Si el
  framework no expone hook, el runtime provee un `ToolExecutor` propio que el
  agente DEBE usar en lugar del executor nativo.
- **RF2 (despacho a sensores):** por cada tool-call, invoca en orden:
  1. `scope_lib.evaluate_scope(action, policy, anchor)` → alcance.
  2. `adi_shield.ADIShield.evaluate(tool_call)` → inyección ADI.
  3. `wallet_guard.WalletGuard.evaluate(...)` → bucle/coste.
  4. `goal_anchor.GoalAnchor.report_drift(...)` → deriva de objetivo.
  5. publica todo al `LocalSignalBus` (de adi-shield).
  6. `trajectory_sentinel.correlate(signals)` o `TrajectorySentinel.report`.
- **RF3 (decisión agregada y ACCIÓN):** combina veredictos:
  - cualquier `block` → BLOQUEAR la ejecución (no se ejecuta el tool).
  - cualquier `confirm` y ningún block → PAUSAR y pedir humano (o denegar
    según config).
  - todos `allow` → ejecutar.
- **RF4 (adaptadores de framework):** un adaptador por framework que traduce
  el tool-call nativo al formato interno del runtime. MVP: adaptador
  genérico (`GenericToolCall`) + al menos uno real (LangChain o AutoGen, según
  Sil). El resto se añade sin tocar la lógica de despacho.
- **RF5 (contexto de tarea):** mantiene policy/anchor por `task_id` (cargados
  de scope-lib) y el `DriftMonitor` por tarea (goal-anchor). El runtime es el
  dueño del ciclo de vida del ancla (propose→confirm→report_drift→amplify).

## 5. Requisitos NO funcionales

- **NFR1:** determinista, 0-LLM en el núcleo de despacho (los sensores ya lo
  son). El runtime solo ORQUESTA; no inventa veredictos.
- **NFR2:** dependencia LOCAL de los 5 sensores (pip install -e). NUNCA red
  para consultarlos (mismo principio de trajectory-sentinel §3).
- **NFR3:** el overhead por tool-call debe ser acotado y medible (benchmark de
  latencia en el MVP tarde).
- **NFR4:** ruff limpio + pytest con output crudo. AGPL-3.0-or-later 2026.

## 6. Criterios de aceptación

- **AC1:** un tool-call con destino en `deny` de policy es BLOQUEADO y NO
  ejecutado (verificable: el executor nativo no se invoca).
- **AC2:** un tool-call con inyección ADI (objective_arg de untrusted data)
  es BLOQUEADO o CONFIRMADO según adi-shield, y no se ejecuta sin resolver.
- **AC3:** una secuencia que dispara deriva brusca de objetivo produce
  `confirm`/bloqueo agregado vía trajectory-sentinel.
- **AC4:** un bucle de reintentos tras denegación de adi-shield es CORTADO
  (wallet-guard recibe la señal del bus).
- **AC5:** 100% tests verdes; el runtime se integra con los 5 SIN modificar
  ninguno de ellos (prueba de que la separación de responsabilidades hold).

## 7. Diseño (estado previsto, NO implementado aún)

```
agent-shield-runtime/
  src/agent_shield_runtime/
    runtime.py        # ShieldRuntime: envuelve executor, despacha, decide
    adapters/         # un adaptador por framework
      generic.py      # GenericToolCall (formato interno del ecosistema)
      langchain.py    # (según Sil) traduce LC tool-call -> GenericToolCall
    config.py         # umbrales, modo (block/confirm), paths de policy/anchor
  tests/
    test_integration_e2e.py  # usa los 5 sensores reales instalados
  pyproject.toml     # dependencies: scope-lib, adi-shield, wallet-guard,
                     # goal-anchor, trajectory-sentinel (todas -e o versiones)
```

`ShieldRuntime.execute(task_id, tool_call)`:
  1. action = traducir tool_call -> Action (scope-lib) + ToolCall (adi-shield)
  2. scope_v, adi_dec, w_dec, drift = invocar los 4 sensores
  3. publicar en LocalSignalBus
  4. corr = correlate(history)  # o TrajectorySentinel.report
  5. si block en {scope_v, adi_dec, w_dec, corr} -> raise/return Blocked
     si confirm -> pausa/humano
     sino -> ejecutar tool nativo y devolver resultado

## 8. Fuera de alcance (MVP)

- Implementar los sensores (ya existen en los otros 5).
- Entrenar/descargar modelo semántico para Capa 2 de goal-anchor (Opción A,
  descartada por peso; el runtime la soporta si se activa, pero no la built).
- UI de revisión humana (el runtime PAUSA en confirm; quién lo resuelve es
  responsabilidad del agente/humano, no de este repo).

## 9. Riesgos

- **R1 (acoplamiento a framework):** si el adaptador se filtra al núcleo, se
  rompe la reutilización. Mitigar: el núcleo solo conoce `GenericToolCall`;
  los adaptadores son pluggable y aislados.
- **R2 (falso sentido de seguridad):** el runtime SOLO cubre lo que cubren
  los 5. WebTrap sutil (T1/T2/T4) sigue sin cerrarse (TPR=0.25 de goal-anchor
  Capa 1). Documentar que el hook no es magia: extiende la COBERTURA de
  despliegue, no la de detección de los sensores.
- **R3 (orden de sensores / latencia):** si un sensor es lento, el agente se
  congela. MVP: secuencial con timeout por sensor; futuro: paralelo.

## 10. Hitos

- H1: scaffold + CI verde (sin lógica, solo estructura + imports de los 5).
- H2: `ShieldRuntime` con adaptador genérico + AC1-AC5 contra los 5 reales.
- H3: al menos un adaptador de framework real (LangChain/AutoGen).
- H4: review honesto + auditoría de Claude en clone fresco (repo PRIVADO
  hasta entonces; público solo cuando Sil lo decida muy avanzado).

## 11. Visibilidad / Gobernanza

- Repo PRIVADO en GitHub hasta estar MUY avanzado (Sil: "privado primero hasta
  que ya esté muy avanzado lo haremos público para que Claude lo pueda clonar
  y auditar").
- Flujo de siempre: commit local SÍ, push a rama `scaffold/*` NO a main sin OK
  de Sil; Claude audita en clone fresco antes de merge a main.
- SDD PRIMERO (esta pieza): no se escribe código hasta OK de Sil al spec.
