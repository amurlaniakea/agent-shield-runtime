# agent-shield-runtime

Hook de despliegue del ecosistema de defensa de agentes IA.

Intercepta **cada tool-call** del agente y lo evalúa contra los 5 sensores
antes de ejecutarlo:

- `scope-lib` — evaluación de alcance (3 criterios, fail-safe).
- `adi-shield` — detección de inyección de prompt (ADI) en 5 vectores.
- `wallet-guard` — guardrails de bucle y presupuesto.
- `goal-anchor` — integridad de objetivo (deriva brusca).
- `trajectory-sentinel` — correlación agregada de señales.

Convierte los 5 sensores (hoy librerías en el estante) en una defensa activa:
ningún tool-call se ejecuta sin pasar por ellos.

## Estado

Implementado y auditado en iteración continua (2026-07-19+). Runtime orquesta
los 5 sensores reales vía `ShieldRuntime.execute()`; CI verde en GitHub
Actions; tests e2e (AC1–AC5) + orquestación + regresión de recuperación, todos
verdes. Ver [SDD.md](SDD.md) (§12 mejoras pendientes, §13 estado de hitos).

Limitación honesta: el hook cubre la **cobertura de despliegue**, no la de
detección. Los vectores de deriva sutil que preservan apariencia de alcance
(WebTrap T1/T2/T4 de goal-anchor) siguen sin cerrarse (benchmark corregido:
TPR=0.25 real, FPR=0.0). El runtime los orquesta, pero no inventa detección.

## Por qué existe

Los 5 sensores están implementados y auditados, pero nadie los invoca en un
agente real: con ellos instalados y sin hook, el agente sigue siendo
vulnerable. Este repo cierra ese hueco (RF1 de los SDD de los sensores).

## Licencia

AGPL-3.0-or-later · Autor: Pedro Sordo Martínez (amurlaniakea@gmail.com) ·
Año: 2026
