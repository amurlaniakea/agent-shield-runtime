# SDD — H3: Adaptador LangChain para agent-shield-runtime

- **Proyecto:** agent-shield-runtime
- **Pieza:** adaptador LangChain (H3)
- **Estado:** SDD inicial (fase barrica, pre-código). GATE: tras OK de Sil
  + auditoría de Claude en clone fresco.
- **Licencia:** AGPL-3.0-or-later · **Autor:** Pedro Sordo Martínez
  (amurlaniakea@gmail.com) · **Año:** 2026
- **Dependencia base:** `agent-shield-runtime` main (`1e6face`) — el núcleo
  `ShieldRuntime` + `GenericToolCall` + `RuntimeVerdict` YA EXISTEN y NO se tocan.

---

## 1. Propósito (una frase)

Proveer un adaptador LangChain que traduzca tool-calls de LangChain al
formato interno `GenericToolCall` de `agent-shield-runtime`, los evalúe a
través de `ShieldRuntime.execute()`, y decida si ejecuta el tool real,
bloquea, o pausa para confirmación humana — sin reimplementar ninguna
lógica de decisión de los 5 sensores.

---

## 2. Por qué existe (problema verificado)

`ShieldRuntime` orquesta los 5 sensores y toma decisiones correctas, pero
hoy el agente debe llamar manualmente a `ShieldRuntime.execute()` en vez
de su executor nativo. El adaptador LangChain es el cableado que permite
usar el runtime de forma transparente dentro de un agente LangChain real
(AgentExecutor, LCEL `.bind_tools()`, o wrappers compatibles con
`BaseTool`), cerrando el hueco de despliegue real (RF1 del SDD general).

---

## 3. Usuarios / audiencia

- Desarrolladores que integran `agent-shield-runtime` en agentes LangChain
  (AgentExecutor, LCEL, o wrappers sobre `BaseTool`).
- El operador humano que define policy/anchor y resuelve `confirm`.

---

## 4. Decisiones de diseño (respuestas a las 6 preguntas obligatorias)

### 4.1 Paradigma de LangChain soportado (H3)

**Decisión:** Soportar **wrapping de `BaseTool`** que funciona tanto en
`AgentExecutor` clásico como en configuraciones LCEL (`.bind_tools()` con
tools que heredan de `BaseTool`).

**Fuera de alcance de H3 (anotado, no implementado):**
- `LangGraph` (grafo de estado nativo) — adaptador futuro si hace falta.
- Runnables puros sin `BaseTool` (p.ej. `Runnable.bind` sin tools) — no
  tienen surface de interceptación de tools clara.

**Razón:** `BaseTool` es la abstracción común a `AgentExecutor` y a la
mayoría de usos LCEL. Envolver `BaseTool._run`/`_arun` da control total,
determinista, y no depende de callbacks ni del orden de invocación de
LangChain.

---

### 4.2 Mecanismo de interceptación

**Decisión:** **Wrapping de `BaseTool`** (subclase `ShieldedTool` que
hereda de `BaseTool` y delega al tool real tras pasar por
`ShieldRuntime.execute()`). NO se usa `BaseCallbackHandler.on_tool_start`.

**Razón:**
- Control determinista: el wrapping decide ANTES de ejecutar si corre,
  bloquea o pausa. No hay ambigüedad sobre si el callback se invoca o en
  qué orden.
- Testable sin levantar un agente completo: se instancia `ShieldedTool`
  y se llama `.invoke()` directamente en tests.
- No depende de comportamiento no garantizado por contrato de LangChain
  (los callbacks pueden cambiar de orden o no invocarse en edge cases;
  el wrapping es explícito y documentado en la API de `BaseTool`).

---

### 4.3 `task_id`: procedencia y estabilidad

**Decisión:** Usar **`RunnableConfig["configurable"]["thread_id"]`** (o
`run_id` si `thread_id` no está) que LangChain propaga automáticamente
en `AgentExecutor` y LCEL. Si no está disponible, **fallback a UUID v4
generado una vez por invocación del agente** (expuesto como
`ShieldedAdapter.generate_task_id` para que quien integre pueda
sobrescribirlo).

**Justificación:** `thread_id` es estable durante toda la vida de una
tarea/turno del agente en LangChain (no cambia entre tool-calls del mismo
turno). Permite que `ShieldRuntime` mantenga policy/anchor por tarea
correctamente. El fallback UUID garantiza que el adaptador siempre
funcione aunque quien integre no configure `thread_id`.

---

### 4.4 `claimed_subobjective`: relleno

**Decisión:** **Mapeo estático `tool_name -> subobjective`** provisto por
quien integra al instanciar el adaptador (`tool_subobjective_map:
dict[str, str]`). Si un tool no está en el mapa, se usa `"unspecified"`
como subobjective (scope-lib lo trata como `criterion="none"` → fail-safe
a confirm/deny según policy).

**Fuera de alcance de H3:** extracción heurística del razonamiento del
LLM (Thought/Reasoning) — demasiado frágil y no determinista para H3.

---

### 4.5 `Channel` por argumento (provenance para adi-shield)

**Decisión:** Heurística por coincidencia de **token completo** con historial
de `ToolMessage`:

```python
import re

def _infer_channel(value: str, history: list[BaseMessage]) -> Channel:
    # coincidencia de TOKEN completo, no substring suelto:
    # evita falsos positivos de "1" dentro de cualquier texto largo
    # SIN crear punto ciego para valores cortos peligrosos (evil.io, IDs,
    # flags) que SÍ deben ser detectados como TOOL_RESULT.
    token_pat = re.compile(rf"(?<!\w){re.escape(value)}(?!\w)")
    for msg in reversed(history):
        if isinstance(msg, ToolMessage) and msg.content:
            if token_pat.search(str(msg.content)):
                return Channel.TOOL_RESULT  # untrusted
    return Channel.MODEL  # default: el LLM decidió el valor
```

- `ToolMessage` (resultado de tool previo) → `TOOL_RESULT` (untrusted).
- Si no hay coincidencia de token completo → `MODEL` (el LLM decidió el valor).
- NO se usa `USER` salvo que venga literalmente de `HumanMessage` (raro
  en tool args; se documenta como edge case).

**Justificación:** límite de palabra (`(?<!\w)...(?!\w)`) resuelve el
problema original (substring suelto "1" matcha dentro de cualquier texto)
SIN crear punto ciego para valores cortos peligrosos (`evil.io`, flags,
IDs) que SÍ deben ser detectados como provenientes de tool result.

**Limitación documentada:** si el LLM parafrasea contenido malicioso
en vez de copiarlo literal, no se detecta como `TOOL_RESULT`. No es
resoluble al 100% sin cambios más profundos (p.ej. watermarking en tool
outputs). Queda anotado como limitación conocida en el SDD.

---

### 4.6 Verdict → acción (qué devuelve `ShieldedTool._run`)

**Decisión:**

| RuntimeVerdict | Acción en `ShieldedTool._run` |
|----------------|-------------------------------|
| `allow`        | Ejecuta `tool_real._run(...)` y devuelve su resultado string. |
| `block`        | Lanza **`ShieldBlockError(reason: str)`** — excepción custom que rompe la ejecución del agente. NO devuelve string explicativo (riesgo de que el LLM itere para evadir). |
| `confirm`      | Lanza **`ShieldConfirmRequired(details: dict)`** — excepción custom. Quien integra el adaptador DEBE capturar esta excepción, presentar la decisión al humano, obtener respuesta, y luego reintentar (re-ejecutar la tool-call). El adaptador NO implementa la pausa/UI — es responsabilidad de quien integra. |

**Excepciones definidas en el adaptador:**
```python
class ShieldBlockError(Exception):
    def __init__(self, reason: str, verdict: RuntimeVerdict): ...

class ShieldConfirmRequired(Exception):
    def __init__(self, details: dict, verdict: RuntimeVerdict): ...
```

---

## 5. Requisitos Funcionales (RF)

- **RF1 (wrapping transparente):** `ShieldedTool` expone la misma interfaz
  que `BaseTool` (`name`, `description`, `args_schema`, `_run`,
  `_arun` opcional). Se usa como drop-in replacement en cualquier
  `AgentExecutor` o lista de tools LCEL.
- **RF2 (traducción completa):** Cada tool-call LangChain se traduce a
  `GenericToolCall` con todos los campos rellenos según §4.3–4.4.
- **RF3 (decisión delegada):** `ShieldedTool._run` llama a
  `ShieldRuntime.execute(generic_call)` y actúa según §4.5.
- **RF4 (configuración mínima):** Instanciación con
  `ShieldedAdapter(runtime: ShieldRuntime, tool_subobjective_map: dict,
  generate_task_id: Callable | None = None)` → devuelve lista de
  `ShieldedTool` listos para usar.
- **RF5 (async opcional H3.1):** `_arun` lanza `NotImplementedError`
  en H3 (se anota como V2).

---

## 6. Requisitos NO funcionales

- **NFR1:** 0-LLM en el adaptador (solo traducción determinista). La
  lógica de decisión vive en `ShieldRuntime` (ya 0-LLM).
- **NFR2:** Dependencia SOLO de `langchain-core` (base tool, messages,
  runnables config). NO `langchain` completo, NO `langgraph`.
- **NFR3:** Overhead por tool-call < 5ms (solo traducción + llamada a
  runtime, sin I/O extra).
- **NFR4:** Tests deterministas sin LLM (mock de `ShieldRuntime` +
  `BaseTool` real simple).

---

## 7. Criterios de Aceptación (AC)

- **AC1:** Un tool envuelto con `ShieldedTool` que dispara `block` por
  policy deny lanza `ShieldBlockError` y NO ejecuta el tool real.
- **AC2:** Un tool con inyección ADI (argumento valor desde `ToolMessage`)
  es traducido con `Channel.TOOL_RESULT` y bloqueado por `adi-shield` →
  `ShieldBlockError`.
- **AC3:** Un tool que dispara deriva brusca (subobjective fuera de
  ancla) produce `ShieldConfirmRequired` o `ShieldBlockError` según
  config.
- **AC4:** `task_id` se deriva de `RunnableConfig["thread_id"]` y es
  estable entre tool-calls del mismo turno (verificable en test con
  mock de config).
- **AC5:** `claimed_subobjective` se toma del mapa estático; tool sin
  mapa usa `"unspecified"`.
- **AC6:** `Channel` heurística: valor coincidente con `ToolMessage`
  previo → `TOOL_RESULT`; si no → `MODEL`.
- **AC7:** `ShieldConfirmRequired` expone `details` suficientes para que
  quien integre presente la decisión al humano y reintente.
- **AC8:** Tests pasan sin LLM real, ruff/bandit limpios, CI verde.
- **AC9 (interacción confirm + proxy de progreso P0-bis):** si un humano
  confirma varias veces la MISMA tool-call exacta (mismos argumentos)
  tras un `confirm`, el proxy de progreso de wallet-guard NO debe
  bloquear por `retry_loop_no_progress` — el reintento explícito tras
  confirmación humana cuenta como "avance explícito" y resetea el
  contador de progreso para esa `(task_id, tool)` en el runtime. Se
  verifica con un test que reproduce: confirm → humano aprueba → misma
  llamada → confirm → humano aprueba → misma llamada → NO bloqueo por
  wallet-guard. (Si no se resuelve en H3, queda documentado como
  limitación conocida y work futuro).

---

## 8. Fuera de alcance (H3)

- Soporte `async` (`_arun`) — V2.
- `LangGraph` nativo (grafo de estado) — adaptador aparte.
- Sub-objetivo dinámico vía parseo de `AIMessage.content` (Thought) —
  requiere LLM y no es determinista.
- Watermarking / firma criptográfica en tool outputs para provenance
  perfecta — fuera de alcance del adaptador.

---

## 9. Archivos esperados (estructura)

```
agent_shield_runtime/
  adapters/
    langchain.py        # ShieldedTool, ShieldedAdapter, excepciones
    __init__.py         # export público
tests/
  test_langchain_adapter.py   # AC1-AC8 con mocks deterministas
```

---

## 10. Referencia al SDD general

Este SDD extiende `SDD.md` §4 (RF4 adaptadores de framework) y §10
(Hitos H3). El núcleo `ShieldRuntime` NO se modifica.

---

## 11. Licencia / Autor

AGPL-3.0-or-later · Pedro Sordo Martínez (amurlaniakea@gmail.com) · 2026