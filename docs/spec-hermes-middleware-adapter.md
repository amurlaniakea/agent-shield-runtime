# SPEC — Adaptador Hermes para Agent Shield Runtime (H3 redefinido)

- **Proyecto:** agent-shield-runtime
- **Frente:** H3 del SDD — adaptador de framework REAL (redefinido de LangChain/AutoGen a Hermes)
- **Fecha:** 2026-08-18
- **Autor:** Pedro Sordo Martínez (amurlaniakea@gmail.com) · **Licencia:** AGPL-3.0-or-later · **Año:** 2026
- **Estado:** SPEC para OK de Sil. NADA de código hasta aprobación (regla SDD).

---

## 1. Descubrimiento que redefine H3

Hermes Agent expone un sistema de **middleware nativo de plugin** (fuente:
`~/.hermes/hermes-agent/docs/middleware/README.md`, contrato
`hermes.middleware.v1`) que permite interceptar tool-calls exactamente en el
punto que agent-shield-runtime necesita:

| Kind | Payload | Qué permite |
|---|---|---|
| `tool_request` | tool_name, args, original_args | Reescribe args ANTES de guardrails, approvals, hooks y ejecución |
| `tool_execution` | tool_name, args, original_args, next_call | Envuelve la ejecución real; puede NO llamar `next_call` (bloquear) |

Registro desde un plugin: `register(ctx)` →
`ctx.register_middleware("tool_request", fn)` /
`ctx.register_middleware("tool_execution", fn)`.

Propiedades del contrato que nos sirven:
- **Ejecución en cadena**: varios plugins se anidan en orden de registro.
- **Fail-open**: si un middleware lanza, Hermes logea warning y continúa con el
  resto de la cadena (el agente nunca se rompe por un fallo del shield).
- **El contexto por callback incluye** `session_id`, `task_id`, `turn_id`,
  `tool_name`, `tool_call_id` — identidad suficiente para scope-lib (policy por
  task) y para correlacionar tool_request → tool_execution.
- **Enablement**: `hermes plugins enable <plugin>`; testing aislado con un
  `HERMES_HOME` temporal (sin tocar el entorno real).

**Conclusión:** H3 = un plugin Hermes que traduce el tool-call nativo de Hermes
a `GenericToolCall`, lo pasa por `ShieldRuntime`, y usa `tool_execution` para
NO ejecutar cuando hay veredicto block/confirm. No hace falta LangChain ni
AutoGen: el framework de producción de Sil ES Hermes.

## 2. Estado actual del repo (verificado 2026-08-18)

- `runtime.py` — `ShieldRuntime.execute(call) -> RuntimeVerdict` completo:
  sensores en paralelo con timeout + fail_mode, proxy de progreso (P0-bis),
  ventana de correlación (P0), criterion real (P1). **Problema para Hermes:**
  `execute()` EJECUTA el tool si todo es allow (`config.executor`). Para
  middleware necesitamos separar **evaluar** (dry-run, sin ejecutar) de
  **ejecutar**. Refactor mínimo: `evaluate(call) -> RuntimeVerdict` y
  `execute(call)` = `evaluate` + ejecutar si allow. NO toca los sensores.
- `adapters/langchain.py` — `ShieldedTool(BaseTool)` ya implementado (rama
  feat/H3-langchain-adapter): patrón de traducción, inferencia de Channel,
  `history_provider` opcional. El adaptador Hermes reutiliza el MISMO patrón
  de construcción de `GenericToolCall` (extraer args por nombre, Channel por
  defecto MODEL sin history_provider).
- H1-H2.5 + P0/P1/P0-bis: ✅ (rama wip/paralelismo-timeout, ya en main).

## 3. Requisitos funcionales (RF)

- **RF1 (plugin Hermes):** paquete instalable como plugin Hermes con
  `register(ctx)` que registra `tool_request` + `tool_execution`.
- **RF2 (traducción):** `HermesAdapter` convierte (tool_name, args, task_id,
  session_id) → `GenericToolCall` (task_id = task_id o session_id si task_id
  ausente; Channel.MODEL por defecto; `objective_arg` y
  `claimed_subobjective` configurables por tool).
- **RF3 (evaluar sin ejecutar):** `tool_request` construye el call y llama
  `runtime.evaluate(call)` (dry-run). Guarda el veredicto correlacionado por
  `tool_call_id` en estado del plugin (dict en memoria, limpio por turno).
- **RF4 (bloquear de verdad):** `tool_execution` consulta el veredicto:
  - `block` → NO llamar `next_call`; devolver resultado de bloqueo
    (`{"error": "blocked_by_agent_shield", "reasons": [...]}`) — el tool
    nativo NO se invoca (verificable).
  - `confirm` → devolver `{"error": "confirmation_required", "reasons": [...]}`
    (fail-closed: no se ejecuta sin humano) o delegar según config
    `block_on_confirm`.
  - `allow` → `next_call(args)` normal.
  - sin veredicto guardado (p.ej. tool_request no corrió) → fail-open: ejecutar
    (principio del middleware Hermes) pero loguear warning.
- **RF5 (config):** umbrales, `fail_mode` por sensor, `block_on_confirm`,
  mapeo tool→(objective_arg, claimed_subobjective) desde un YAML de config del
  plugin (no hardcodeado).

## 4. Requisitos NO funcionales (NFR)

- **NFR1:** 0-LLM en el camino del shield (los sensores ya lo son; el plugin
  solo orquesta).
- **NFR2:** dependencias LOCALES de los 5 sensores (pip install -e desde el
  repo clonado); NUNCA red.
- **NFR3:** fail-open del propio plugin: un fallo del shield NO rompe el agente
  (el middleware de Hermes ya garantiza continuar la cadena; el plugin además
  captura excepciones propias y loguea).
- **NFR4:** ruff limpio + pytest crudo; AGPL-3.0-or-later 2026; sin
  Co-Authored-By.
- **NFR5:** overhead por tool-call medible (benchmark en H3.3, ver §8).

## 5. Criterios de aceptación (AC)

- **AC1:** con HERMES_HOME aislado, un tool-call cuyo destino está en `deny`
  de policy produce resultado `blocked_by_agent_shield` y el tool nativo NO se
  ejecuta (evidencia: efecto del tool ausente + log del middleware).
- **AC2:** un tool-call con inyección ADI (objetivo de datos no confiables) es
  bloqueado o requiere confirmación; no se ejecuta sin resolver.
- **AC3:** una secuencia que dispara deriva brusca produce confirm/bloqueo vía
  trajectory-sentinel.
- **AC4:** un bucle de reintentos tras denegación es cortado (wallet-guard).
- **AC5:** 100% tests verdes; integración SIN modificar ninguno de los 5
  sensores.
- **AC6:** la sesión real de Hermes (este entorno) sigue funcionando con el
  plugin habilitado en modo observación (evaluar + loguear, NO bloquear) —
  primera integración conservadora antes de activar bloqueo.

## 6. Diseño

```
agent-shield-runtime/
  src/agent_shield_runtime/
    runtime.py            # + evaluate() (dry-run); execute() lo reutiliza
    adapters/
      generic.py          # (existente)
      langchain.py        # (existente, intacto)
      hermes.py           # NUEVO: HermesAdapter + register(ctx) del plugin
    plugin.py             # NUEVO: register(ctx) -> middleware Hermes
  plugins/hermes/         # NUEVO: plugin autocontenido (para hermes plugins enable)
    __init__.py           # register(ctx), on_tool_request, on_tool_execution
    config.yaml.example   # mapeo tool->objective_arg/claimed_subobjective
  tests/
    test_hermes_adapter.py    # NUEVO: traducción + correlación veredictos
    test_plugin_middleware.py # NUEVO: block/confirm/allow con runtime real
    test_e2e_hermes_env.py    # NUEVO: HERMES_HOME aislado + hermes chat --query
```

`on_tool_request` (evaluar):
```python
def on_tool_request(**kw):
    call = adapter.to_generic(kw["tool_name"], kw["args"], kw.get("task_id"), kw.get("session_id"))
    verdict = runtime.evaluate(call)          # dry-run, sin executor
    state[kw.get("tool_call_id")] = verdict
    return None                               # no reescribimos args: la policy
                                              # de Hermes evalúa los args reales
```

`on_tool_execution` (decidir):
```python
def on_tool_execution(**kw):
    v = state.pop(kw.get("tool_call_id"), None)
    if v is None:                             # fail-open sin veredicto
        return kw["next_call"](kw["args"])
    if v.decision == "block":
        return {"error": "blocked_by_agent_shield", "reasons": v.reasons}
    if v.decision == "confirm":
        return {"error": "confirmation_required", "reasons": v.reasons}
    return kw["next_call"](kw["args"])
```

## 7. Riesgos

- **R1 (correlación tool_request→tool_execution):** si Hermes no rellena
  `tool_call_id` en algún path, fallamos a fail-open + warning (documentado,
  medible en tests). Mitigación: correlacionar también por (turn_id, tool_name)
  como fallback.
- **R2 (falso sentido de seguridad):** el hook extiende la COBERTURA de
  despliegue, no la detección de los sensores (WebTrap T1/T2/T4 sigue abierto).
  Documentado en README del plugin.
- **R3 (history_provider ausente):** sin historial, la inferencia de Channel
  degrada a MODEL → adi-shield pierde detección de provenance vía historial.
  Primera iteración: degradar igual que langchain.py (documentado); el
  history_provider de Hermes (memoria de sesión) es trabajo futuro.
- **R4 (impacto en sesión real):** mitigado por AC6 — primera integración en
  modo observación (no bloquea), luego se activa bloqueo por decisión de Sil.

## 8. Hitos

- H3.1: refactor `evaluate()`/`execute()` + tests de regresión (los existentes
  siguen verdes).
- H3.2: `hermes.py` + `plugin.py` + `plugins/hermes/` + tests unitarios
  (traducción, correlación, fail-open).
- H3.3: E2E con HERMES_HOME aislado (`hermes plugins enable`, `hermes chat
  --query`, verificar AC1-AC4) + benchmark de overhead.
- H3.4: modo observación en la sesión real de Sil (AC6) → revisión de logs →
  decisión de Sil de activar bloqueo.
- H3.5: auditoría de Claude en clone fresco (flujo de gobernanza).

## 9. Fuera de alcance (esta iteración)

- history_provider de Hermes (Channel real) — trabajo futuro documentado.
- UI de revisión humana para confirm (el plugin devuelve error; quién resuelve
  es el runtime del agente).
- Modificar los 5 sensores (prohibido por AC5).

## 10. Veredicto de la investigación (para Sil)

La API existe, es estable, es nativa y es exactamente el punto de
interceptación que el SDD pedía. El refactor necesario en runtime.py es
mínimo (separar evaluate/execute) y no toca sensores. H3 pasa de
"adaptador LangChain/AutoGen" a "plugin Hermes" — el framework que de verdad
usamos en producción. ¿OK para implementar?
