# TTL de idempotencia y minimización de PII (KC-DB-10-3)

Migración `055_ttl_idempotencia_minimizacion_pii.sql`. Aditiva, re-ejecutable, sin índices nuevos.

## 1. TTL de `core.idempotency_keys`

`core.purgar_idempotency_keys(p_ahora, p_ttl)` elimina las claves con
`creado_en < p_ahora - p_ttl` en todos los tenants y devuelve cuántas. Por defecto el TTL es de
48 h (el mismo que ya usaba el housekeeping) y el mínimo aceptado es 1 h; un valor menor lanza
`22023` y no borra nada.

- Las claves dentro de su ventana no se tocan (el límite es exacto: una clave de exactamente 48 h
  se conserva).
- Una reserva en curso del worker de ingesta (KC-CM-10-1, `status_code = 0`) renueva `creado_en`
  con su latido, de modo que una reserva viva nunca está cerca del límite. Una reserva abandonada
  se purga como cualquier otra clave vencida.
- Si se purga la clave de un commit ya procesado, el worker no lo reprocesa: comprueba antes que
  no exista un snapshot `completo` de ese commit.
- Programada con `pg_cron` cada hora (`idempotencia_purga`, minuto 10). El housekeeping del worker
  llama a la misma función como respaldo; el TTL ya no está escrito en dos sitios.
- `kallicode_worker` solo recibe `EXECUTE` (la función es `SECURITY DEFINER`); `kallicode_app` no
  puede ejecutarla.

## 2. Minimización de PII

`core.minimizar_pii(p_ahora)` anonimiza datos personales fuera de su ventana operativa. Las
ventanas están en `core.pii_ventanas` y se ajustan con un `UPDATE`, sin tocar código:

| Regla | Dato | Ventana | Acción |
|---|---|---|---|
| `refresh_tokens_red` | `core.refresh_tokens.ip`, `user_agent` | 30 días desde `emitido_en` | pasan a `NULL` |
| `invitaciones_email` | `core.invitations.email` con estado distinto de `enviada` | 90 días desde `creado_en` | `anonimizado-<id>@anonimo.invalid` |

- Idempotente: una fila ya anonimizada no se vuelve a contar ni a modificar.
- Una invitación `enviada` (pendiente) no se toca, aunque sea antigua.
- Cada regla se puede desactivar (`activa = false`). La ventana mínima es 1 h.
- Programada una vez al día (`pii_minimizar`, 03:30). Solo la ejecuta `kallicode_admin`.

### Qué no se anonimiza, y por qué

- `audit.audit_events`: `ip` y `user_agent` forman parte del sello de la cadena de hashes
  (versión 2). Modificarlos rompería la verificación de no-alteración.
- `core.webhook_inbox` y `core.notifications`: están particionadas y se eliminan enteras por
  partición según `core.retencion_particiones` (036); no acumulan payloads antiguos.
- `core.users.email`: es dato operativo vigente; se elimina con el tenant (`core.suprimir_tenant`, 048).

## 3. Evidencia

`core.purgas_log` registra cada ejecución (`tarea`, `filas`, `ejecutada_en`): `idempotency_keys`,
`pii:refresh_tokens_red` y `pii:invitaciones_email`. Sirve para el informe de «fecha de última
purga» (KC-DB-10-4) y para el ensayo de retención (KC-DB-10-6), donde basta pasar `p_ahora` al
futuro para simular el paso del tiempo.

## 4. Si `pg_cron` no está disponible

La migración avisa con un `NOTICE` y no falla. Programar a mano, como `kallicode_admin`:

```sql
SELECT cron.schedule('idempotencia_purga', '10 * * * *', $$SELECT core.purgar_idempotency_keys()$$);
SELECT cron.schedule('pii_minimizar',      '30 3 * * *', $$SELECT * FROM core.minimizar_pii()$$);
```

## 5. Pruebas

`packages/core/tests/integracion/test_ttl_pii.py` (21 casos, transacción siempre revertida, el
tiempo se simula con `p_ahora`): vencidas frente a activas entre tenants, límite exacto, reserva con
latido, TTL configurable y su piso, registro, permisos por rol, anonimización por ventana,
idempotencia, regla desactivada, ventana ajustable, auditoría intacta y migración re-ejecutable.

## Decisiones a confirmar con Anderson (ciclo de retención)

1. Ventanas de 30 días (sesiones) y 90 días (invitaciones).
2. Que el TTL de 48 h se mantenga como valor por defecto.
3. Horarios de `pg_cron` y que `core.purgas_log` sea la fuente del informe de KC-DB-10-4.
