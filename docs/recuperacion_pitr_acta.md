> **Acta generada por `python db/tools/ensayo_pitr.py --salida <dir> --duracion 150`** (Docker Desktop en Windows, base mínima). Es el resultado de UNA ejecución local; el ensayo semanal (`.github/workflows/pitr_semanal.yml`) publica una nueva en cada corrida. **Los objetivos siguen propuestos: este documento no es la aprobación del Architecture Board.** Procedimiento y límites: [recuperacion_pitr.md](recuperacion_pitr.md).

# Acta del ensayo de restauración PITR (KC-DB-11-1)

Ejecutado: 2026-11-15T18:42:35.114598+00:00 · PostgreSQL 16.15 (Debian 16.15-1.pgdg12+2) · imagen kallicode-postgres:16
Resultado global: **CUMPLE** los objetivos propuestos (RPO ≤ 5 min, RTO ≤ 60 min)

## Objetivos y medición real

| Objetivo | Propuesto | Medido | ¿Cumple? |
|---|---|---|---|
| RPO (pérdida efectiva de datos) | ≤ 300 s | 42.9 s (209 eventos confirmados perdidos) | SÍ |
| RTO, restauración a un punto T | ≤ 3600 s | 3.1 s | SÍ |
| RTO, hasta el final del WAL | ≤ 3600 s | 2.7 s | SÍ |

El RTO cuenta desde que empieza el procedimiento de restauración (la copia base y el WAL archivado existen, el origen
no) hasta que la instancia nueva acepta conexiones fuera de recuperación; **no** incluye la verificación posterior ni
detectar el desastre ni decidir restaurar.

## Datos del ensayo

* Base de datos: 22.1 MB; copia base 61 MB en 17.9 s.
* Archivado: 7 segmentos de WAL archivados (97 MB), 0 fallos; archive_timeout = 60 s.
* Carga durante el ensayo: 727 eventos de auditoría confirmados; último a las 2026-11-15T18:45:55.638727+00:00.

## A. Restauración a un punto en el tiempo (T = 2026-11-15 18:44:08.619217+00)

* Copia base: 1.3 s · reproducción del WAL y arranque: 1.8 s · segmentos reproducidos: 2.
* Eventos de auditoría hasta T: esperados 3228, restaurados 3228, posteriores a T: 0.
* Claves de idempotencia (borradas por el «accidente» posterior a T): esperadas 100, restauradas 100.
* **Punto correcto: SÍ.**
* audit.verify_chain(): 3 tenants, 0 cadenas rotas · core.validar_integridad(): 26 chequeos, 0 hallazgos (0.5 s).

## B. Restauración hasta el final del WAL archivado (desastre: origen perdido con SIGKILL)

* Copia base: 0.9 s · reproducción del WAL y arranque: 1.8 s · segmentos reproducidos: 5.
* Último evento restaurado: 2026-11-15T18:45:12.737331+00:00 · confirmados perdidos: 209 · pérdida efectiva: 42.9 s.
* audit.verify_chain(): 3 tenants, 0 cadenas rotas · core.validar_integridad(): 26 chequeos, 0 hallazgos (0.5 s).

## Aprobación (pendiente)

Los objetivos RPO ≤ 5 min y RTO ≤ 60 min están en core.objetivos_recuperacion como **propuestos**. Este ensayo no los
aprueba: lo hace el Architecture Board (core.aprobar_objetivos_recuperacion).

| Aprobado por | Fecha | Referencia del acta |
|---|---|---|
| _pendiente_ | _pendiente_ | _pendiente_ |

## Límites de esta medición

* Docker local con un volumen mínimo y el archivo de WAL en un volumen del mismo anfitrión: los tiempos son del procedimiento, **no** una promesa para producción con otro volumen, red y almacenamiento.
* El origen se mata con SIGKILL pero su disco no se usa: se supone perdido. Con el disco accesible se podría recuperar más WAL (RPO menor).
* La pérdida depende de cuándo se corta: hasta archive_timeout (60 s) más el tiempo de archivado. Una sola ejecución mide un punto de esa distribución, no su máximo.
