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

## 12. Mejoras pendientes (post-comentario Dev.to + auditoría de Claude, 2026-07-19)

Estado de cada frente (rama `wip/paralelismo-timeout`, sin merge a main hasta
auditoría de Claude en clone fresco):

- **(A) Paralelismo + timeout + fail-open/close GLOBAL — HECHO (rama wip).**
  scope/adi/wallet en paralelo (ThreadPoolExecutor), `sensor_timeout` (0.5s),
  `fail_mode` global ("closed" por defecto). R3 mitigado.

- **(F) P0 — Envenenamiento permanente de tarea (auto-DoS) — HECHO (rama wip).**
  Hallazgo de auditoría de Claude (clone fresco 3c7d950): `correlate()` bloquea
  por cualquier `block` en TODO el historial de `trajectory-sentinel`, que
  nunca se poda -> la tarea queda bloqueada a perpetuidad tras el primer
  block real. Fix: `runtime.py` aplica ventana `correlation_window` (default
  10) de las últimas N señales por task_id antes de llamar a `correlate()`.
  Además se eliminó la doble publicación de adi-shield al bus (que distorsionaba
  la ventana). Test de regresión: `test_recovery.py` (recovery + persiste-en-
  ventana). Decision de diseño: primera iteración en agent-shield-runtime (no
  toca trajectory-sentinel); anotado para llevarlo al sensor si Claude lo
  valida.

- **(H) P0-bis — `progress=0.0` hardcodeado bloquea uso normal (auto-DoS) — HECHO (rama wip).**
  Hallazgo de auditoría de Claude (clone fresco `wip` @5245f70): `runtime.py`
  llamaba `wallet.evaluate(..., progress=0.0)` fijo. wallet-guard corta el
  bucle con `retry_loop_no_progress` cuando `attempts > max_retries` y el
  progreso no avanza; como progress nunca subia, CUALQUIER tarea que usara el
  mismo tool >3 veces (busquedas, paginas) quedaba bloqueada para siempre,
  sin ataque. Fix (opcion 2 de Claude): proxy de progreso en el runtime — por
  `(task_id, tool)` se guarda el hash de args y un contador; si los args
  cambian respecto a la llamada anterior, el contador sube 1.0 (avance); si son
  identicos, se queda (sin avance). Es una HEURISTICA, no progreso semantico
  real: el runtime no conoce el progreso de la tarea. Documentado aqui para no
  sobreprometer. Work futuro (opcion 1/3 de Claude, NO hecho): (1) que
  GenericToolCall/exponga progreso real del framework; (3) que wallet-guard
  tenga TTL para `attempts`/`last_progress` (tocaria el repo wallet-guard, SDD
  propio). **Riesgo residual (anotado por auditoria de Claude):** el proxy
  "args distintos = avance" es trivialmente evadible (paginacion, cache-busting,
  timestamp irrelevante) para el gate de `retry_loop_no_progress`. Mitigado por
  capa independiente: `_spend(category, cost)` descuenta del `admission_budget`
  en cada llamada y bloquea con `budget_exhausted` cuando se agota, sin depender
  del proxy de progreso. El gate de bucle sin progreso queda debilitado como
  deteccion temprana, pero el tope duro de coste total sigue intacto.
  Test: `test_P0bis_legit_repeated_calls_do_not_permablock` +
  `test_P0bis_identical_repeat_still_capped` (el corte de reintento identico
  legitimo se preserva).

- **(G) P1 — Criterio hardcodeado rompe la sub-senal de deriva — HECHO (rama wip).**
  `runtime.py` pasaba el literal `"iii_transitive"` a `goal_anchor.report_drift`
  en cada llamada. Ahora usa `scope_v.criterion` real (i_/ii_/iii_transitive/
  deny/none). Sin esto, toda tarea legítima contaba como 100% transitiva y
  podía generar falsos `confirm` por deriva.

- **(B) Política fail-open/fail-close POR SENSOR — PENDIENTE (diseño).**
  Hoy `fail_mode` es global. Decidir valor por defecto por sensor antes de
  tocar código (riesgo: fail-open en adi-shield = agujero de inyección).

- **(C) Cancelación temprana / modo `abort-on-block` — PENDIENTE (diseño).**
  Postura acordada: fail-fast en ejecución, preservar evidencia. Modo opt-in.

- **(D) Benchmark de latencia del paralelismo — PENDIENTE (R3 del SDD).**
  `benchmarks/bench_latency.py` comparando secuencial vs paralelo.

- **(E) H3 — Adaptadores de framework reales (LangChain/AutoGen) — PENDIENTE.**
  El hito que demuestra el hook en producción.

Orden tras P0/P1: E (H3) -> B/C -> D. Todo entra en auditoría Claude antes de
merge a main.

## 13. Estado de hitos (actualizado 2026-07-19, rama wip/paralelismo-timeout)

- H1: ✅ scaffold + CI verde.
- H2: ✅ ShieldRuntime + adaptador genérico + AC1-AC5.
- H2.5: ✅ paralelismo + timeout + fail-open/close global.
- F (P0): ✅ envenenamiento permanente arreglado (ventana de correlación).
- G (P1): ✅ criterio real en report_drift.
- H (P0-bis): ✅ proxy de progreso en wallet (args distintos => avance).
- H3: ⏳ adaptador de framework real.
- H4: ⏳ auditoría de Claude en clone fresco (repo ya PÚBLICO para que clone).
- B/C/D: ⏳ mejoras de diseño (ver §12).

## 11. Visibilidad / Gobernanza

- Repo PRIVADO en GitHub hasta estar MUY avanzado (Sil: "privado primero hasta
  que ya esté muy avanzado lo haremos público para que Claude lo pueda clonar
  y auditar").
- Flujo de siempre: commit local SÍ, push a rama `scaffold/*` NO a main sin OK
  de Sil; Claude audita en clone fresco antes de merge a main.
- SDD PRIMERO (esta pieza): no se escribe código hasta OK de Sil al spec.
