# Ensayo integral de retención y minimización de datos (KC-DB-10-6)

Herramienta `db/tools/ensayo_retencion.py` · prueba `packages/core/tests/integracion/test_ensayo_retencion.py` · paso del
CI «Ensayo integral de retención - Database» (publica el artefacto `evidencia-ensayo-retencion`).

## 1. Qué demuestra

El ciclo de vida completo del dato, con las piezas reales de 10-1 (purgas), 10-2 (export WORM) y 10-3 (TTL y PII), y que
el descarte seguro no pierde trazabilidad de auditoría. Es **evidencia reproducible**: cualquiera puede repetirlo con

```bash
KC_DATABASE_URL_MIGRATIONS=postgresql://... python db/tools/ensayo_retencion.py --destino ./evidencia --informe ./informe.md
```

Sale con código 0 si todos los pasos pasan y 1 si alguno falla.

## 2. Qué hace, en orden

| Paso | Qué comprueba |
|---|---|
| pg_cron | Los trabajos `retencion_purgas`, `idempotencia_purga` y `pii_minimizar` están definidos y llaman a las funciones que se ejecutan después (si pg_cron no está instalada, el paso se marca como no aplicable). |
| Datos históricos | Dos tenants con eventos de auditoría (del mes en curso y del siguiente), `webhook_inbox` y `notifications` (vencidas y vigentes), claves de idempotencia, sesiones con IP y navegador, invitaciones. |
| Línea base | Registra los 26 chequeos de `core.validar_integridad()` y las cadenas de auditoría. |
| Simulacro | `core.purgar_retencion(p_ahora, simular)` informa y no borra. |
| Purga **sin** export | Las colas vencidas se descartan, **la auditoría se bloquea** («sin comprobante de export WORM») y las vigentes se conservan. |
| Export WORM | Se exporta cada partición bloqueada (la más antigua primero) y pasa el verificador externo, sin base de datos. |
| Purga **con** comprobante | La partición antigua de auditoría se descarta y se anota el ancla de cada tenant. |
| Trazabilidad | `audit.verify_chain` sigue en «íntegra» y los eventos posteriores quedan verificados contra el ancla. |
| TTL y PII | `core.purgar_idempotency_keys` y `core.minimizar_pii` eliminan/anonimizan solo lo vencido (una reserva en curso y lo reciente se conservan); repetir no toca nada. |
| Registros | `core.retention_purgas` y `core.purgas_log` dejan constancia de cada purga. |
| Validación final | Los chequeos no empeoran (0 problemas en una base limpia) y los exports se re-verifican contra el hash del manifiesto registrado. |

## 3. Cómo se simula el tiempo

No se espera ni se toca el reloj: las funciones reciben `p_ahora`. El ensayo calcula un «ahora» ficticio = fin del mes en
curso + 91 días, de modo que las particiones del mes en curso están vencidas (90 días) y las del mes siguiente no. Los
eventos de auditoría del mes en curso nacen por el disparador real de sellado (que fija `creado_en = now()`); los del mes
siguiente, que necesitan una fecha posterior, se sellan con la propia `audit.calcular_sello` desactivando los disparadores
solo para esa inserción.

Todo ocurre en **una transacción que siempre se revierte**: no deja datos, ni cambia la política de la auditoría
(que se pone en `drop` solo dentro del ensayo). Lo único que persiste son el informe y los exports de `--destino`.

## 4. Límites

* **No dispara pg_cron.** Comprueba que los trabajos existen y llaman a las funciones; no espera a que cron los lance.
* **El reloj es simulado** (`p_ahora`), no el paso real de meses. Una purga real depende además de que pg_partman haya creado
  y mantenga las particiones.
* **El almacenamiento WORM no se ensaya**: los exports se escriben en disco de solo lectura, que no impide borrarlos a
  quien tenga acceso al disco (ver `docs/export_worm_auditoria.md`).
* **La política de la auditoría sigue en `conservar`**: el ensayo la cambia solo dentro de su transacción.
* **En una base con restos de otras pruebas** pueden existir problemas ajenos (p. ej. tenants sin suscripción): el ensayo los
  registra en la línea base y exige que no empeoren; la afirmación «0 problemas» vale en una base limpia, como la del CI.
* Se prueba la purga de un mes concreto con volúmenes mínimos; no es una prueba de carga (ver `docs/` de benchmarks de 08-x).
* Si una cadena de auditoría de **cualquier** tenant de la partición estuviera rota, `exportar` se niega: es el comportamiento
  buscado (no se exporta ni se descarta evidencia dañada), y el ensayo fallaría en ese paso.
