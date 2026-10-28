# Tablas probatorias: append-only y corrección por compensación

KC-DB-05-2 · migración `db/migrations/025_append_only_probatorias.sql`

## Qué son y qué se protege

Son las tablas que sirven de prueba de lo que ocurrió en el pipeline. Una vez escritas, no se
reescriben ni se borran.

| Tabla | Qué prueba |
|---|---|
| `core.gate_signatures` | la firma humana que autoriza cada gate (y, con ella, el despliegue) |
| `core.llm_steps` | cada paso del LLM: modelo, tokens, validación, referencias de entrada y salida |
| `core.job_transitions` | cada cambio de estado de un job |
| `core.qa_runs` / `core.qa_results` | las corridas de QA y el resultado por criterio |
| `core.usage_loc` | el consumo de líneas de código que se factura |
| `core.security_findings` | los hallazgos de seguridad y su veredicto |

Las seis primeras son **append-only**. `security_findings` admite una única modificación, la
transición de estado y de veredicto, descrita más abajo.

## Cómo se garantiza

1. **Permisos.** `kallicode_app` no tiene `UPDATE` ni `DELETE` sobre esas tablas. En
   `security_findings` solo puede actualizar las columnas `estado`, `veredicto`,
   `justificacion` y `actualizado_en`.
2. **Trigger.** `trg_probatoria_append_only` (y `trg_a_security_findings_transicion`) rechaza la
   mutación **a nivel de motor**, también para un rol privilegiado: para saltárselo hay que
   desactivar el trigger a propósito.

**Cascadas.** Borrar un ticket o un job arrastra a sus hijos (`ON DELETE CASCADE`) y deja
`usage_loc.ticket_id` en `NULL` (`ON DELETE SET NULL`). Esas acciones las dispara el padre y el
trigger las deja pasar (profundidad de trigger mayor que 1); solo bloquea la mutación directa.
La aplicación no borra tickets ni jobs: usa el borrado lógico `eliminado_en`.

## `security_findings`: la única transición permitida

Solo pueden cambiar `estado`, `veredicto` y `justificacion`. Toda transición de `estado` o de
`veredicto` exige una **justificación no vacía y distinta de la anterior**. Se rechaza cambiar
`regla`, `archivo`, `linea`, `severidad`, `titulo` (y también `origen`, `ticket_id`,
`fix_diff_ref`, `bloquea`, `creado_en`). Un hallazgo no se borra.

```sql
UPDATE core.security_findings
   SET estado = 'descartado', veredicto = 'falso_positivo',
       justificacion = 'El valor viene de una constante, no de entrada del usuario'
 WHERE id = 'sf_...';
```

## Cómo corregir un error: fila de compensación

No se edita ni se borra la fila equivocada. Se agrega una fila nueva que la compensa, y el
histórico muestra el error y su corrección.

### `usage_loc`: fila de ajuste

Se inserta una fila con `tipo = 'ajuste'`, `netas` con el signo contrario y `motivo_ajuste`
obligatorio (lo exige `loc_ajuste_chk`). La fila original queda intacta.

```sql
-- El PR 'https://git/pr/123' se contó dos veces (50 líneas): se compensa.
INSERT INTO core.usage_loc (tenant_id, ticket_id, ciclo, tipo, netas, motivo_ajuste)
VALUES ('org_acme', 'tk_...', '2026-10', 'ajuste', -50,
        'Compensa el PR contado dos veces por un reintento del webhook');
```

El consumo neto del ciclo es la suma de `netas`. Desde KC-DB-05-3 (migración 028) el agregado
`core.usage_cycles` respeta el ajuste: un `-500` sobre un ciclo con 1.200 consumidas lo deja en 700
y recalcula el umbral (`loc_netas` guarda la suma real, `loc_consumidas` es esa suma con piso en cero).

### `qa_runs`: corrida nueva

Una corrida de QA que quedó mal registrada no se reescribe: se agrega una **corrida nueva** del
mismo ticket (`corrida` siguiente, con sus `qa_results`). La vigente es la de mayor `corrida`;
las anteriores quedan como evidencia.

```sql
INSERT INTO core.qa_runs (id, tenant_id, ticket_id, spec_id, corrida, regresion)
VALUES ('qr_...', 'org_acme', 'tk_...', 'sp_...', 2, '{"dirigidos": 3, "dirigidos_ok": 3}');
-- y sus filas en core.qa_results para esa corrida
```

### Las demás tablas

`gate_signatures`, `llm_steps`, `job_transitions` y `qa_results` son registro de lo ocurrido y
no se corrigen reescribiéndolos: un hecho nuevo se registra como una fila nueva.

## Procedimiento excepcional (rol privilegiado, con aprobación)

Para el mantenimiento que de verdad lo necesite (por ejemplo, limpiar un tenant de pruebas), se
desactiva el trigger de la tabla dentro de una transacción, con aprobación de seguridad y dejando
constancia del ticket:

```sql
BEGIN;
ALTER TABLE core.usage_loc DISABLE TRIGGER trg_probatoria_append_only;
-- ... la operación aprobada ...
ALTER TABLE core.usage_loc ENABLE TRIGGER trg_probatoria_append_only;
COMMIT;
```

## Pruebas

`packages/core/tests/integracion/test_append_only_probatorias.py`: las siete mutaciones
prohibidas (una por tabla) frente a la aplicación y frente a un rol privilegiado, los permisos
por tabla y por columna, las transiciones válidas e inválidas de `security_findings`, las
cascadas y los dos procedimientos de compensación.
