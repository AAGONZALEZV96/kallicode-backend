> **Acta generada por `python db/tools/ensayo_despliegue.py --salida <dir>`** en un staging simulado (Docker Desktop en Windows: PostgreSQL 16 de `db/postgres` con volúmenes persistentes y WAL archivado, datos de 3 tenants con cadena de auditoría real). Es el resultado de UNA ejecución local; los tiempos miden el procedimiento con datos mínimos, no una promesa de producción. Procedimiento: [aceptacion_ga_esquema.md](aceptacion_ga_esquema.md). **Esta acta recoge la evidencia técnica; las firmas son humanas y siguen PENDIENTES.**

# Acta del ensayo de despliegue de GA (KC-DB-12-5)

Ejecutado: 2026-11-18T03:14:42.721089+00:00 · PostgreSQL 16.15 (Debian 16.15-1.pgdg12+2) · duración total 59.8 s
Resultado técnico: **CUMPLE los criterios técnicos** ·
Aceptación: **PENDIENTE DE FIRMAS** (este ensayo no firma ni aprueba nada)

## Criterio 1 · migraciones y smoke

| Comprobación | Resultado |
|---|---|
| Migraciones del artefacto aplicadas en orden sobre la base persistente | **SÍ**: 68 en el ledger de 68 que trae el artefacto; 10 en el paso de despliegue sobre una base ya en `060_vigencia_embeddings_hnsw.sql` |
| Dentro del presupuesto | **SÍ**: suma 3.9 s (presupuesto 120.0 s) · la más lenta, `036_particion_colas.sql`, 1.02 s (presupuesto 30.0 s por migración) · el paso de despliegue completo, 1.06 s |
| Smoke de release (administrador) | **SÍ** |
| Smoke post-deploy (aplicación) | **SÍ** |
| El smoke falla con una versión equivocada (control negativo) | **SÍ** |
| Base persistente: los datos sobreviven a un reinicio | **SÍ** (3000 → 3000 eventos) |

Lo que verifica el smoke de release (versión `v2.0.0`, última migración `070_smoke_release.sql`, 68 migraciones):

```
version_esquema                  | OK     | etiqueta v2.0.0 (esperada v2.0.0), última migración 070_smoke_release.sql (esperada 070_smoke_release.sql), 3 posterior(es) a la etiqueta
migraciones_aplicadas            | OK     | 68 en el ledger (el artefacto trae 68)
ledger_en_orden                  | OK     | 0 migración(es) aplicada(s) antes que una de nombre anterior
integridad_26_chequeos           | OK     | 26 chequeos (esperados 26), 0 hallazgo(s)
cadena_auditoria                 | OK     | 3 tenant(s) con cadena verificados, 0 rota(s)
tablas_sin_politica_retencion    | OK     | 0 tabla(s) sin política de retención (V26)
tablas_sin_politica_recuperacion | OK     | 0 tabla(s) de cm y vec sin política de recuperación
aislamiento_sin_incumplimientos  | OK     | 0 tabla(s) o política(s) que incumplen el aislamiento (RLS, FORCE, cláusula TO, particiones)
particiones_default_vacias       | OK     | todas las particiones DEFAULT de core, audit, vec y cm están vacías
```

El presupuesto es una **propuesta** (nadie lo había definido): unas veinte veces lo medido en Docker local. Hay que ratificarlo con el volumen del staging real.

## Criterio 2 · segundo ensayo de restauración PITR

Objetivo de RTO: 60 min (3600 s), estado **propuesto** en `core.objetivos_recuperacion`.

| | RTO medido | ¿Dentro del objetivo? | Cadena de auditoría y 26 chequeos | Estado restaurado |
|---|---|---|---|---|
| (a) al estado posterior al despliegue | 4.0 s | **SÍ** | SÍ (3 tenants, 0 cadenas rotas, 0 hallazgos) | `070_smoke_release.sql`, 3003/3003 eventos; smoke de release SÍ |
| (b) al instante previo al despliegue (vuelta atrás) | 24.1 s | **SÍ** | SÍ (3 tenants, 0 cadenas rotas, 0 hallazgos) | `060_vigencia_embeddings_hnsw.sql`, 3000/3000 eventos |

El RTO cuenta desde que empieza el procedimiento de restauración hasta que la instancia nueva acepta conexiones fuera de recuperación
(no incluye detectar el desastre ni decidir restaurar ni la verificación posterior).

## Acta de aceptación

**Pendiente de firmas.** Las firmas son decisión de personas; esta acta recoge la evidencia para que decidan.

| Rol | Nombre | Decisión | Fecha | Firma |
|---|---|---|---|---|
| Product Owner | _pendiente_ | _pendiente_ | _pendiente_ | _pendiente_ |
| Architecture Board | _pendiente_ | _pendiente_ | _pendiente_ | _pendiente_ |
| Tech Lead de Testing | _pendiente_ | _pendiente_ | _pendiente_ | _pendiente_ |
| DBA: firma del despliegue del esquema | Anderson González | _pendiente_ | _pendiente_ | _pendiente_ |

## Límites de este ensayo

* **No es el staging real.** No hay un staging en el repositorio: es una simulación en Docker local con volúmenes persistentes y datos mínimos. La confirmación definitiva requiere repetirlo en el staging real con `docs/aceptacion_ga_esquema.md`.
* El RTO se mide contra un objetivo que sigue **propuesto** (no aprobado por el Architecture Board).
* El presupuesto de migraciones es una propuesta; el del staging real dependerá de su volumen.
* «Release anterior» = el esquema hasta `060_vigencia_embeddings_hnsw.sql`: es un punto de partida arbitrario para ensayar el despliegue sobre datos existentes.
* Los tiempos miden el procedimiento con un volumen mínimo, no una promesa para producción.
