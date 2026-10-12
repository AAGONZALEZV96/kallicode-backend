# Cadena de auditoría: verificación, protección y qué hacer si se rompe

KC-DB-05-1 (hallazgo DB-F04) · migración `db/migrations/024_cadena_auditoria.sql`

## Cómo funciona

Cada evento de `audit.audit_events` es un eslabón de una cadena por tenant. Su `sello` es un
SHA-256 de su contenido y de `sello_previo`, el sello del eslabón anterior. El primer eslabón
de un tenant usa como `sello_previo` el génesis: `repeat('0', 64)`.

La cabeza de cada cadena vive en `audit.chain_heads` (`ultimo_sello` y `eventos`). Sirve para
serializar las escrituras de un tenant (la fila se bloquea hasta el `COMMIT`) y como contrapeso
del recuento de eventos.

Quién escribe qué:

| Objeto | Quién | Cómo |
|---|---|---|
| `audit.audit_events` | la aplicación | `INSERT` normal; `trg_audit_seal` calcula `sello_previo` y `sello` |
| `audit.chain_heads` | solo `audit.seal_event` | `SECURITY DEFINER`; `kallicode_app` solo tiene `SELECT` |

El `id` de un evento se asigna **antes** de que `seal_event` tome el candado de la cabeza. Con
escrituras simultáneas del mismo tenant, el orden por `id` puede no coincidir con el orden
real de la cadena. Por eso la verificación nunca asume que `id` ascendente es el orden de la
cadena.

## Procedencia del evento y versiones del sello (KC-DB-05-6, migración 029)

Cada evento guarda además desde dónde y en qué petición se originó: `ip` (`inet`), `user_agent`
(`text`) y `request_id` (`text`, texto libre, p. ej. un UUID). Los tres son opcionales.

La columna `version_sello` identifica el formato del canónico con el que se calculó el sello:

| `version_sello` | Canónico | Quién la tiene |
|---|---|---|
| 1 | el original, **sin** ip, user_agent ni request_id | los eventos anteriores a la migración 029 (`DEFAULT 1`) |
| 2 | el original **más** ip, user_agent y request_id, separados por `chr(31)` | todo evento nuevo |

- El trigger de sellado fija siempre la versión 2: la aplicación no puede elegirla (ni pedir la 1
  para que sus datos de red queden sin sellar).
- El canónico vive en una sola función, `audit.calcular_sello()`, que usan `seal_event` y
  `verify_chain`. **Cualquier cambio del formato debe hacerse ahí, como una versión nueva, y sin
  tocar jamás la fórmula de las versiones anteriores**: hacerlo invalidaría la cadena existente.
- Alterar `ip`, `user_agent` o `request_id` de un evento (v2) rompe la cadena, igual que alterar
  cualquier otro campo sellado.

## Verificación

```sql
SELECT * FROM audit.verify_chain('org_acme');
-- integra | eventos_verificados | primer_evento_roto | eventos_esperados | eventos_observados
```

`integra` es `false` si ocurre cualquiera de estas cosas:

- un evento no coincide con su propio sello (contenido alterado);
- un evento apunta a un eslabón anterior que ya no existe (se borró un evento intermedio);
- dos eventos comparten el mismo eslabón anterior (bifurcación);
- `eventos_esperados` (de `chain_heads`) difiere de `eventos_observados`.

`primer_evento_roto` es el menor `id` entre los tres primeros casos, es decir, el primer evento
que ya no encaja. Un recorte de la cola de la cadena no tiene un evento «roto»: se delata solo
porque `eventos_esperados` es mayor que `eventos_observados`.

Con una ventana (`p_desde` / `p_hasta`) no hay recuento esperado: `eventos_esperados` es `NULL`.

`core.validar_integridad()` incluye dos chequeos nocturnos, que deben dar `problemas = 0`:

- **V16:** tenants cuya `verify_chain` no es íntegra.
- **V17:** `chain_heads` que no cuadra con los eventos (último sello distinto, más o menos
  eventos) y tenants con eventos pero sin cabeza.

## Protección de `chain_heads`

- `kallicode_app` no puede `INSERT`, `UPDATE`, `DELETE` ni `TRUNCATE` la tabla.
- El trigger `trg_chain_heads_guard` solo admite que `eventos` avance de uno en uno. Rechaza
  retroceder, saltar o reescribir `ultimo_sello` sin avanzar, y borrar la cabeza de un tenant que
  todavía tiene eventos. Otro trigger prohíbe `TRUNCATE`.
- `seal_event` se niega a sellar un evento cuyo `tenant_id` no sea el de la sesión.

Cualquier código nuevo que necesite crear eventos debe hacer un `INSERT` normal en
`audit.audit_events`. **Nunca** debe escribir `chain_heads`: fallará, y es lo esperado.

## Si `verify_chain` devuelve `integra = false`

1. **No intentes «arreglar» la cadena.** Una cadena rota es evidencia de que se alteró o se borró
   algo. Reajustar la cabeza o los sellos para que los chequeos den cero destruye esa evidencia.
2. Anota el tenant, `primer_evento_roto`, `eventos_esperados` y `eventos_observados`.
3. Revisa quién tuvo acceso privilegiado a la base en el periodo (los borrados de eventos solo
   los puede hacer un rol que desactive `trg_audit_immutable`).
4. Restaura los eventos faltantes desde una copia de seguridad de la base de datos. Las
   exportaciones CSV de `core.audit_exports` sirven para comparar contenido, pero no recuperan
   los sellos.
5. Escala a seguridad antes de tocar nada en producción.

## Procedimiento excepcional (rol privilegiado, con aprobación)

Existen casos legítimos de mantenimiento (por ejemplo, limpiar un tenant de pruebas). Se hacen
**con un rol privilegiado, dentro de una transacción, con la aprobación de seguridad y dejando
constancia del ticket**. Desactivar un trigger es una decisión deliberada, nunca un atajo:

```sql
BEGIN;
ALTER TABLE audit.audit_events DISABLE TRIGGER trg_audit_immutable;
-- ... la operación aprobada ...
ALTER TABLE audit.audit_events ENABLE TRIGGER trg_audit_immutable;
SELECT * FROM audit.verify_chain('org_acme');   -- confirmar el resultado esperado
COMMIT;                                         -- o ROLLBACK si algo no cuadra
```

Para eliminar un tenant de pruebas, borra primero sus eventos (con el trigger desactivado como
arriba) y después su cabeza: `chain_heads_guard` permite borrar la cabeza solo cuando el tenant
ya no tiene eventos.

Si hay que desactivar también `trg_chain_heads_guard`, aplica la misma regla y, además, el
resultado debe pasar `verify_chain` y V17 antes del `COMMIT`.

## Pruebas

`packages/core/tests/integracion/test_cadena_auditoria.py` cubre: cadena intacta con génesis,
borrado del quinto de diez eventos (señala el sexto), borrado del primero y de la cola, sello
alterado, permisos de `kallicode_app`, el trigger de la cabeza, V16/V17, el orden de `id`
invertido respecto a la cadena y escrituras concurrentes del mismo tenant.
