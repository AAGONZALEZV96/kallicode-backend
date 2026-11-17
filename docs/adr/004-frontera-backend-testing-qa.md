# ADR 004 — Frontera entre Backend y Testing para la etapa QA

- **Estado:** Aceptado
- **Fecha:** 2026-11-15
- **Ticket:** KC-BE-06-2
- **Contexto:** Sprint 6, épica EBE5

## Contexto

El ticket KC-BE-06-2 exige que el backend **no ejecute** las pruebas del
pipeline. El backend deriva casos de prueba desde los criterios de aceptación,
digiere los resultados que produce el motor de pruebas, verifica visualmente
la evidencia y diagnostica fallos. La **ejecución** de las pruebas es
responsabilidad del equipo de Testing (decisión D19 del plan de programa).

## Decisión

1. **Contrato de invocación.** El backend invoca al motor de pruebas a través
   de la interfaz `MotorPruebas` definida en
   `apps/workers/src/kallicode_workers/orquestador/qa.py`.
2. **Contrato de publicación.** La matriz se publica por
   `POST /internal/v1/tickets/{numero}/evidence` con `Idempotency-Key`
   derivada de `{numero}:evidence:{corrida}`.
3. **Independencia de Build.** `qa.derive_cases` deriva casos **solo** de los
   criterios de aceptación (`AC-n`), nunca del diff de Build.
4. **Semántica de idempotencia.** La misma `Idempotency-Key` devuelve el
   mismo resultado sin reescribir `qa_runs` ni `qa_results`.
5. **Fallback en local y en tests.** `MotorPruebasNulo` lee la matriz del
   contexto del job (`contexto.qa_matrix`) para permitir integración sin
   Testing desplegado.

## Consecuencias

**Positivas:**
- El backend puede mergear y testear sin Testing desplegado.
- La frontera está tipada y documentada.
- La idempotencia de `/evidence` garantiza convergencia en reintentos.
- La independencia QA-Build se preserva: QA no ve el diff.

**Negativas:**
- El doble `MotorPruebasNulo` puede divergir del cliente real.
- El backend depende de que Testing publique su cliente antes de producción.

## Referencias

- Ticket KC-BE-06-2
- Decisión D19 del plan de programa
- `apps/workers/src/kallicode_workers/orquestador/qa.py`
- `apps/api/src/kallicode_api/routers/internal.py` (endpoint `/evidence`)